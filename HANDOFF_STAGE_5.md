# TZ Stage 5: Gallery diversity + screenshot rejection

> **Goal:** fix three concrete issues found in mood-26 visual audit:
> 1. UI screenshot ("HDR" overlay) passed Vision filter — should have been rejected
> 2. No deduplication for gallery — six near-identical blonde shots in a row
> 3. No category diversity — gallery shows only one service (coloring), product positioned as full-service salon catalog
>
> **Principle:** landing = full service catalog. Gallery must showcase variety of services the salon offers, not just the strongest.
>
> **ASCII-only file/folder names. No new dependencies.**

---

## Patch A — Reject UI screenshots (`llm/curator.py`)

In the prompt for `analyze_photo_rich`, in the HARD REJECT block, replace the current screenshot rule with stricter wording:

```
- Screenshot of phone/computer UI: any of these visible — "HDR" badge,
  battery indicator, clock/time in corner, signal bars, app icons,
  status bar, "1x"/"2x" zoom indicator, camera mode labels, recording
  dot, photo gallery thumbnails. If you see any device interface
  element overlaid on the photo, set rejected=true with
  reject_reason="screenshot".
```

Also bump `flags.is_screenshot` detection — the validator should treat
`flags.is_screenshot == true` as auto-reject in `lay_out_blocks` even if
Vision forgot to set `rejected=true`:

```python
# In lay_out_blocks, after the rejected check:
if (p.get("flags") or {}).get("is_screenshot"):
    continue
```

---

## Patch B — Deduplicate gallery (`enrichment/photos_v2.py`)

Currently `deduplicate_visually` is applied only to `about`. Apply it to
`gallery` too, with a softer threshold (0.50 vs 0.65 for about):

```python
# Inside lay_out_blocks, after sorting:

if block == "about":
    filtered = deduplicate_visually(filtered, threshold=0.65)
elif block == "gallery":
    filtered = deduplicate_visually(filtered, threshold=0.50)
```

Rationale: gallery photos are more permissive (different services
naturally have different objects), but six shots of the same blonde
hair from different angles must not all pass.

---

## Patch C — Category quotas in gallery (`enrichment/photos_v2.py`)

Replace the simple "top N by fit_score" pick for gallery with quota-based selection.

### C.1 New constant

```python
# Maximum photos per service category in gallery
# Sum equals FOLDER_LIMITS['gallery'] = 6
GALLERY_QUOTAS = {
    "coloring":      2,   # окрашивание / blonds / balayage
    "haircut":       1,   # стрижка
    "hair_treatment": 1,  # уход / botox / keratin (overflow bucket for hair)
    "manicure":      1,
    "nails_general": 1,   # alias for manicure if Vision picks it
    "pedicure":      1,
    "lashes":        1,
    "brows":         1,
    "makeup":        1,
    "facial":        1,
    "other":         1,
}

# Service families — photos in same family share quota slots
SERVICE_FAMILY = {
    "coloring":      "hair",
    "haircut":       "hair",
    "hair_treatment": "hair",
    "manicure":      "nails",
    "nails_general": "nails",
    "pedicure":      "nails",
    "lashes":        "eyes",
    "brows":         "eyes",
    "makeup":        "face",
    "facial":        "face",
    "other":         "other",
}

# Max photos per family (so "hair" can't take all 6 slots even if quotas sum higher)
GALLERY_FAMILY_CAP = {
    "hair":  3,   # max 3 hair photos out of 6 total
    "nails": 2,
    "eyes":  2,
    "face":  1,
    "other": 1,
}
```

### C.2 New picker function

```python
def pick_gallery_with_quotas(
    sorted_candidates: List[Dict[str, Any]],
    total_limit: int,
) -> List[Dict[str, Any]]:
    """
    Pick photos for gallery enforcing category and family caps.
    Input: candidates already sorted by gallery fit_score DESC, deduplicated.
    Output: up to total_limit photos with category diversity.

    Algorithm:
      1. Walk candidates in score order.
      2. For each candidate, check if its service_ref still has quota
         AND its family still has cap.
      3. If yes — pick it, decrement counters.
      4. If no — skip.
      5. After first pass, if we have less than total_limit picked
         (e.g. salon truly is hair-only), do a second pass and
         relax quotas: take any remaining top-scored photos to fill
         up to total_limit, ignoring quotas but still respecting
         family cap +1 (so we don't end with 6 identical-family shots).
    """
    picked = []
    cat_taken = {}
    fam_taken = {}

    def family_of(svc):
        return SERVICE_FAMILY.get(svc, "other")

    # Pass 1 — strict quotas
    for c in sorted_candidates:
        if len(picked) >= total_limit:
            break
        svc = ((c.get("profile") or {}).get("service_ref")) or "other"
        fam = family_of(svc)

        cat_quota = GALLERY_QUOTAS.get(svc, 1)
        fam_cap = GALLERY_FAMILY_CAP.get(fam, 1)

        if cat_taken.get(svc, 0) >= cat_quota:
            continue
        if fam_taken.get(fam, 0) >= fam_cap:
            continue

        picked.append(c)
        cat_taken[svc] = cat_taken.get(svc, 0) + 1
        fam_taken[fam] = fam_taken.get(fam, 0) + 1

    # Pass 2 — fill remaining slots (salon may genuinely be hair-only)
    if len(picked) < total_limit:
        already_picked = {id(c) for c in picked}
        for c in sorted_candidates:
            if len(picked) >= total_limit:
                break
            if id(c) in already_picked:
                continue
            svc = ((c.get("profile") or {}).get("service_ref")) or "other"
            fam = family_of(svc)
            fam_cap_relaxed = GALLERY_FAMILY_CAP.get(fam, 1) + 1
            if fam_taken.get(fam, 0) >= fam_cap_relaxed:
                continue
            picked.append(c)
            fam_taken[fam] = fam_taken.get(fam, 0) + 1

    return picked
```

### C.3 Wire into `lay_out_blocks`

Replace the current `picked = filtered[:FOLDER_LIMITS[block]]` line for gallery:

```python
# In the loop, after dedup:
if block == "gallery":
    picked = pick_gallery_with_quotas(filtered, FOLDER_LIMITS["gallery"])
else:
    picked = filtered[:FOLDER_LIMITS[block]]
```

### C.4 Diagnostic log

In `photo_map.json` stats section, add per-service breakdown for gallery:

```python
stats["gallery_by_service"] = {
    svc: count for svc, count in cat_taken.items()
}
```

---

## DoD

- [ ] Patch A: `analyze_photo_rich` prompt updated; `lay_out_blocks` rejects `is_screenshot=true`
- [ ] Patch B: `deduplicate_visually` applied to gallery with threshold 0.50
- [ ] Patch C: `pick_gallery_with_quotas` implemented and wired in
- [ ] `python -m enrichment.photos_v2 26 --force` runs without errors
- [ ] Visual check on `public/mood-26/gallery/`:
  - [ ] No screenshots / UI elements visible
  - [ ] Photos show **at least 3 different service categories** (hair + nails + lashes minimum)
  - [ ] No two near-identical photos (same person, same hair from different angles)
  - [ ] Family cap respected: max 3 hair photos
- [ ] `photo_map.json` has `stats.gallery_by_service` showing the distribution

---

## Test commands

```powershell
cd "D:\1 KURSOR_PROJ\11 PARSER"
python -m enrichment.photos_v2 26 --force

# Verify breakdown
python -c "import json; m=json.load(open('data/yandex/mood-26/photo_map.json',encoding='utf-8')); print('gallery breakdown:', m['stats'].get('gallery_by_service'))"

# Expected: at least 3 different keys, e.g.
# {'coloring': 2, 'manicure': 1, 'lashes': 1, 'pedicure': 1, 'brows': 1}

# Open visually
explorer "D:\1 KURSOR_PROJ\11 PARSER\public\mood-26\gallery"
```

---

## What's NOT in this stage

- Builder integration — separate stage
- TapLink scraping — separate stage
- Tuning of family caps for salons with extreme distributions — wait for second test lead
