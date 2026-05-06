# TZ Stage 5.2: Corner-UI check + any-mask rejection + photo uniqueness

> **Three issues found in mood-26 audit after Stage 5.1:**
> 1. HDR screenshot still passes EXIF filter (clean EXIF + non-standard dimensions)
> 2. Photos with **black/cloth masks** pass through ("medical mask" check too narrow)
> 3. Same photo appears in both `gallery/` and `services/` (no per-photo uniqueness)
>
> **Principles:**
> - Any face-covering object signals distance/distrust — reject regardless of mask type
> - Each photo belongs to exactly one folder
> - Detect UI elements via targeted Vision call on corners, not full-image prompt

---

## Patch A — Targeted Vision call for UI overlay detection

### A.1 New helper in `llm/curator.py`

Single-purpose Vision call. Cheap, narrow, deterministic.

```python
def detect_ui_overlay(image_url: str) -> Dict[str, Any]:
    """
    Tight Vision call: does the image have ANY device UI element overlaid?
    Returns:
        {
            "has_ui_overlay": bool,
            "evidence": str,   # what was seen, e.g. "HDR badge top-left"
        }
    Use ONLY for finalists (after main fit_score sort) to avoid waste.
    """
    prompt = """Look ONLY at the four corners and the top/bottom edges of this image. Ignore the main subject.

Is there ANY device-interface element overlaid on the photo?
Examples that MUST trigger has_ui_overlay=true:
- "HDR" badge or label
- "Live" badge
- Battery percentage indicator
- Clock / time display
- Signal bars / wifi / cellular icons
- "1x", "0.5x", "2x" zoom indicators
- Camera mode labels ("PHOTO", "VIDEO", "PORTRAIT")
- Recording dot (red circle)
- Screenshot framing
- App UI chrome (status bar, navigation buttons)

Return STRICT JSON, no markdown:
{
  "has_ui_overlay": true|false,
  "evidence": "1 short sentence describing what you see, or null"
}

If you see only photo content with no overlays — has_ui_overlay=false.
"""
    parsed = analyze_image_url(image_url, prompt)
    if not isinstance(parsed, dict):
        return {"has_ui_overlay": False, "evidence": "parse_error"}
    return {
        "has_ui_overlay": bool(parsed.get("has_ui_overlay", False)),
        "evidence": parsed.get("evidence") or "",
    }
```

### A.2 Wire into `enrichment/photos_v2.py`

Run **after** `lay_out_blocks` produces the picked photos — only on finalists.
This is ~10-15 extra Vision calls per lead (the count of all picked photos
across blocks). Acceptable cost.

```python
async def verify_finalists_no_ui(blocks: Dict[str, Dict]) -> Dict[str, Dict]:
    """
    Run UI-overlay check on every picked photo. If detected — drop the photo
    and mark its slot. Caller is responsible for backfilling from candidate pool.
    """
    rejected_urls = set()
    for block_name, block in blocks.items():
        if not block.get("enabled"):
            continue
        for photo in list(block.get("photos") or []):
            url = photo.get("url")
            if not url:
                continue
            check = await asyncio.to_thread(detect_ui_overlay, url)
            if check["has_ui_overlay"]:
                write_log(f"   🚫 UI overlay found in {block_name}: {check['evidence']} | {url}")
                rejected_urls.add(url)
    return rejected_urls
```

### A.3 Backfill after rejection

After the UI check, rebuild blocks excluding rejected URLs:

```python
# After lay_out_blocks
ui_rejected = await verify_finalists_no_ui(blocks)

if ui_rejected:
    # Mark rejected candidates and re-run lay_out_blocks
    for c in unique_candidates:
        if c.get("url") in ui_rejected or c.get("normalized_url") in ui_rejected:
            profile = c.setdefault("profile", {})
            profile["rejected"] = True
            profile["reject_reason"] = "ui_overlay_detected"
    blocks = lay_out_blocks(unique_candidates)
    write_log(f"   🔁 Rebuilt blocks after UI rejection ({len(ui_rejected)} photos)")
```

---

## Patch B — Reject ANY face covering, not just medical mask

### B.1 Update prompt in `analyze_photo_rich` (`llm/curator.py`)

In the HARD REJECT section, replace the medical-mask line:

```
- ANY face-covering object on a person:
    medical/surgical mask, cloth mask, fabric mask of any color,
    respirator, balaclava, scarf covering nose/mouth, neck gaiter
    pulled up over face. If a person's nose AND mouth are both
    obscured by an object, set rejected=true with
    reject_reason="face_covered".
```

In the JSON schema, rename and broaden:

```
"flags": {
    "has_logo": true|false,
    "has_text_overlay": true|false,
    "has_watermark": true|false,
    "is_screenshot": true|false,
    "face_covered": true|false,        ← was "medical_mask"
    "low_resolution": true|false,
    "stock_photo_signals": true|false
}
```

### B.2 Update validator `_validate_rich_profile`

Replace the field name in the defaults:

```python
flags = data.get("flags") or {}
for f in ("has_logo", "has_text_overlay", "has_watermark", "is_screenshot",
          "face_covered", "low_resolution", "stock_photo_signals"):
    flags.setdefault(f, False)

# Backward compatibility: if old "medical_mask" exists, fold into face_covered
if (data.get("flags") or {}).get("medical_mask") and not flags.get("face_covered"):
    flags["face_covered"] = True
data["flags"] = flags
```

### B.3 Auto-reject in `lay_out_blocks`

Add the check alongside `is_screenshot`:

```python
flags = (p.get("flags") or {})
if flags.get("is_screenshot"):
    continue
if flags.get("face_covered"):
    continue
```

---

## Patch C — Each photo belongs to exactly one folder

### C.1 Priority order

```python
BLOCK_PRIORITY = ["hero", "services", "gallery", "about", "team"]
```

Rationale:
- `hero` (1 photo) is the rarest and most prominent — gets first pick
- `services` cards must show specific service results — second pick
- `gallery` is the showcase reel — backfilled from remaining
- `about` is interior shots, low collision risk
- `team` rarely overlaps anyway

### C.2 New helper

```python
def deduplicate_across_blocks(blocks: Dict[str, Dict]) -> Dict[str, Dict]:
    """
    Walk blocks in priority order. Each photo URL can appear in only one block.
    If URL is already claimed by a higher-priority block, drop it from the
    current block. The dropped slot is NOT auto-backfilled here — caller must
    re-run layout if backfilling is needed (or accept smaller block).
    """
    claimed: Dict[str, str] = {}  # normalized_url -> block_name
    deduped = {b: dict(blocks[b]) for b in blocks}

    for block_name in BLOCK_PRIORITY:
        block = deduped.get(block_name)
        if not block or not block.get("enabled"):
            continue
        kept = []
        dropped = 0
        for photo in (block.get("photos") or []):
            url_key = photo.get("normalized_url") or photo.get("url")
            if not url_key:
                continue
            if url_key in claimed:
                dropped += 1
                continue
            claimed[url_key] = block_name
            kept.append(photo)
        block["photos"] = kept
        if dropped:
            write_log(f"   ♻️  {block_name}: dropped {dropped} duplicates already claimed by other blocks")

    return deduped
```

### C.3 Backfill phase for gallery/services

After deduplication, gallery/services may have fewer photos than `FOLDER_LIMITS`.
Backfill from the candidate pool, picking next-best photos that pass the same
gates:

```python
def backfill_block(
    block_name: str,
    block: Dict,
    all_candidates: List[Dict],
    claimed_urls: Set[str],
    min_fit: int,
) -> None:
    """In-place backfill of a block's photos[] up to FOLDER_LIMITS, skipping claimed URLs."""
    target = FOLDER_LIMITS.get(block_name, 0)
    if len(block.get("photos") or []) >= target:
        return

    # Sorted by fit_score for this block, descending
    fit_key = lambda c: ((c.get("profile") or {}).get("fit_scores") or {}).get(block_name, {}).get("score") or 0
    sorted_pool = sorted(all_candidates, key=fit_key, reverse=True)

    for c in sorted_pool:
        if len(block["photos"]) >= target:
            break
        url_key = c.get("normalized_url") or c.get("url")
        if url_key in claimed_urls:
            continue
        p = c.get("profile") or {}
        if p.get("rejected"):
            continue
        if (p.get("flags") or {}).get("is_screenshot"):
            continue
        if (p.get("flags") or {}).get("face_covered"):
            continue
        score = fit_key(c)
        if score < min_fit:
            continue
        # Build photo entry same shape as lay_out_blocks
        block["photos"].append({
            "url": c["url"],
            "normalized_url": c.get("normalized_url"),
            "alt": ((p.get("content") or {}).get("alt_text") or ""),
            "description": ((p.get("content") or {}).get("description") or ""),
            "fit_score": score,
            "fit_reason": ((p.get("fit_scores") or {}).get(block_name) or {}).get("reason") or "",
            "category": p.get("category"),
            "service_ref": p.get("service_ref"),
            "sources": c.get("sources", []),
        })
        claimed_urls.add(url_key)
```

### C.4 Wire it together

After `lay_out_blocks` and `verify_finalists_no_ui` and rebuild:

```python
blocks = deduplicate_across_blocks(blocks)
claimed = {(p.get("normalized_url") or p.get("url"))
           for b in blocks.values() if b.get("enabled")
           for p in (b.get("photos") or [])}

# Backfill gallery and services (they suffered most dedup loss)
for block_name in ("services", "gallery"):
    if blocks[block_name].get("enabled"):
        backfill_block(block_name, blocks[block_name], unique_candidates, claimed, MIN_FIT_SCORE[block_name])

# Re-evaluate enabled flag (block may now be too small)
for block_name in blocks:
    photos = blocks[block_name].get("photos") or []
    enabled = len(photos) >= MIN_PHOTOS_FOR_BLOCK[block_name]
    blocks[block_name]["enabled"] = enabled
    if not enabled:
        blocks[block_name]["reason"] = f"after dedup+backfill: only {len(photos)} photos (need {MIN_PHOTOS_FOR_BLOCK[block_name]})"
```

---

## Patch D — Optional polish: prefer clean work_result for services

For `services` block, in addition to fit_score, prefer photos where:
- `category == "work_result"` (clean close-ups)
- over `category == "master_at_work"` (atmospheric, often dimmer, often masked)

In the sort step for services (within `lay_out_blocks`), use a composite key:

```python
def services_sort_key(c):
    p = c.get("profile") or {}
    fit = ((p.get("fit_scores") or {}).get("services") or {}).get("score") or 0
    cat = p.get("category") or ""
    # Boost work_result, slight penalty for master_at_work
    cat_bonus = {"work_result": 1.5, "master_at_work": -0.5}.get(cat, 0)
    return fit + cat_bonus

filtered.sort(key=services_sort_key, reverse=True)
```

This keeps high-fit master-at-work photos as fallback but prefers clean
result close-ups when available.

---

## DoD

- [ ] `detect_ui_overlay()` implemented in `llm/curator.py`
- [ ] `verify_finalists_no_ui()` runs after lay_out_blocks
- [ ] Photos rejected by UI check do not appear in any folder
- [ ] Prompt updated to reject ANY face covering (not just medical)
- [ ] `flags.face_covered` replaces `flags.medical_mask` (with backcompat)
- [ ] `deduplicate_across_blocks()` enforces one-photo-one-folder rule with priority
- [ ] `backfill_block()` fills gallery/services after deduplication
- [ ] Services sort prefers `work_result` over `master_at_work`
- [ ] **Visual check on `public/mood-26/`**:
  - [ ] No HDR badge / UI overlay anywhere
  - [ ] No photos with masks of any color
  - [ ] No photo appears in two folders simultaneously
  - [ ] Services photos look clean and professional (mostly work_result)
- [ ] `photo_map.json.stats` includes:
  - [ ] `ui_overlays_rejected: N`
  - [ ] `face_covered_rejected: N`
  - [ ] `cross_block_duplicates_removed: N`

---

## Test commands

```powershell
cd "D:\1 KURSOR_PROJ\11 PARSER"
python -m enrichment.photos_v2 26 --force

# Check stats
python -c "import json; m=json.load(open('data/yandex/mood-26/photo_map.json',encoding='utf-8')); print({k: m['stats'].get(k) for k in ('screenshots_filtered','ui_overlays_rejected','face_covered_rejected','cross_block_duplicates_removed','rejected_breakdown')})"

# Check uniqueness across blocks
python -c "import json; m=json.load(open('data/yandex/mood-26/photo_map.json',encoding='utf-8')); urls=[]; [urls.append((b,p['url'])) for b,d in m['blocks'].items() for p in (d.get('photos') or [])]; from collections import Counter; c=Counter(u for _,u in urls); print('duplicates:', {u:n for u,n in c.items() if n>1})"
# Expected: duplicates: {}

explorer "D:\1 KURSOR_PROJ\11 PARSER\public\mood-26"
```

---

## Notes for Codex

- UI-overlay check adds ~10-15 Vision calls per lead. Acceptable.
- `detect_ui_overlay` uses the same `analyze_image_url` transport as the main rich-profile call.
- Backward-compat: old cached profiles with `medical_mask` flag should be honored as `face_covered`.
- Don't run the UI check on photos already rejected for other reasons — waste.
- ASCII-only filenames preserved.
