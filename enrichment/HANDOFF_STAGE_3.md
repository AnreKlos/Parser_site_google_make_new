# TZ Stage 3: qualify_lead + photos_v2 (Vision rich profile)

> **Goal:** add lead qualification gate after card.json is built; rewrite photo enricher to use aspects[] from card.json as primary source; Vision returns rich per-photo profile with quality + category + description + masks-reject; lay photos out into folders by category.
>
> **Scope:** ASCII-only file/folder names. Lead is processed only if `qualification_status == "qualified"`. Run `python -m enrichment.photos_v2 26 --force` after `yandex_state.py`.

---

## Part A — Lead qualification (in `enrichment/yandex_state.py`)

### A.1 Add `qualify_lead(card)` function

```python
QUALIFY_THRESHOLDS = {
    "min_rating": 4.0,
    "min_review_count": 20,
    "min_unique_aspect_photos": 8,
    "min_services_with_photos": 3,
}

# At least one of these channels must be present
CONTACT_CHANNELS = ("taplink", "vk", "website", "whatsapp", "booking", "instagram", "telegram")


def qualify_lead(card: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns:
        {
            "qualified": bool,
            "reason": str,            # human readable, e.g. "OK" or "rating 3.7 < 4.0"
            "score": int,             # 0-100, qualitative summary
            "checks": {               # which checks passed
                "rating": bool,
                "reviews": bool,
                "photos": bool,
                "services": bool,
                "contact": bool,
            },
            "details": {              # raw values for debugging
                "rating": float | None,
                "review_count": int,
                "unique_aspect_photos": int,
                "services_with_photos": int,
                "contact_channels": [str, ...],
            },
        }
    """
```

**Logic:**

```python
rating = card.get("rating") or 0
review_count = card.get("review_count") or 0

# Unique photos across all aspects (dedup by URL)
aspect_photos = set()
for asp in card.get("aspects") or []:
    for url in asp.get("photos") or []:
        if url:
            aspect_photos.add(_strip_size_suffix(url))
unique_photos = len(aspect_photos)

# Services that have photo_url
services_with_photos = sum(
    1 for s in (card.get("services") or [])
    if s.get("photo_url")
)

# At least one contact channel
urls = card.get("urls") or {}
present_channels = [k for k in CONTACT_CHANNELS if urls.get(k)]
# website also counts if non-empty all[] has any non-yandex url
if not present_channels and urls.get("all"):
    present_channels = ["other"]

checks = {
    "rating":   rating   >= QUALIFY_THRESHOLDS["min_rating"],
    "reviews":  review_count >= QUALIFY_THRESHOLDS["min_review_count"],
    "photos":   unique_photos >= QUALIFY_THRESHOLDS["min_unique_aspect_photos"],
    "services": services_with_photos >= QUALIFY_THRESHOLDS["min_services_with_photos"],
    "contact":  bool(present_channels),
}

qualified = all(checks.values())
score = int(100 * sum(checks.values()) / len(checks))

# Human-readable reason
if qualified:
    reason = "OK"
else:
    fails = []
    if not checks["rating"]:   fails.append(f"rating {rating} < {QUALIFY_THRESHOLDS['min_rating']}")
    if not checks["reviews"]:  fails.append(f"reviews {review_count} < {QUALIFY_THRESHOLDS['min_review_count']}")
    if not checks["photos"]:   fails.append(f"unique_photos {unique_photos} < {QUALIFY_THRESHOLDS['min_unique_aspect_photos']}")
    if not checks["services"]: fails.append(f"services_with_photos {services_with_photos} < {QUALIFY_THRESHOLDS['min_services_with_photos']}")
    if not checks["contact"]:  fails.append("no contact channel")
    reason = "; ".join(fails)

return {
    "qualified": qualified,
    "reason": reason,
    "score": score,
    "checks": checks,
    "details": {
        "rating": rating,
        "review_count": review_count,
        "unique_aspect_photos": unique_photos,
        "services_with_photos": services_with_photos,
        "contact_channels": present_channels,
    },
}
```

### A.2 Embed into `enrich_lead_v2`

After `normalize_state()` and **before** writing card.json:

```python
qualification = qualify_lead(card)
card["qualification"] = qualification  # add to card.json
```

### A.3 DB write

Add column `qualification_status TEXT` to `leads` table (values: `qualified` / `not_qualified` / `pending`). After `card.json` is saved:

```python
async with get_async_session() as session:
    await session.execute(
        update(Lead).where(Lead.id == lead_id).values(
            qualification_status="qualified" if qualification["qualified"] else "not_qualified"
        )
    )
    await session.commit()
```

If `Lead` model doesn't have this column — add it via migration script `scripts/add_qualification_column.py` (simple `ALTER TABLE`).

### A.4 CLI output

```
[2026-...] ✅ [v2] card.json saved: ...
[2026-...]    services=10, aspects=10, videos=9, reviews_preview=3
[2026-...]    🎯 Qualification: QUALIFIED (score=100/100) — OK
```

For not_qualified:

```
[2026-...]    🚫 Qualification: NOT QUALIFIED (score=60/100) — reviews 12 < 20; unique_photos 5 < 8
```

---

## Part B — `enrichment/photos_v2.py` (new module)

### B.1 Source of truth: card.json

No more separate Yandex scraping for photos. Inputs come from `data/yandex/{slug}-{lead_id}/card.json`:

- `aspects[]` — primary photo source with semantic hints
- `services[].photo_url` — service photos (1 per service)
- `videos[].thumbnail_url` — for hero/about (high-res)
- `logo_url` — for navbar/footer
- `panorama.preview_url` — exterior fallback

Optional secondary sources (kept from old `photos.py`):

- VK album photos (if `urls.vk` present and VK_ACCESS_TOKEN set)
- Website scrape (if `urls.website` present and not socials)

### B.2 Aspect → block mapping

```python
ASPECT_TO_BLOCK = {
    # interior / atmosphere
    "Интерьер":      "about",
    "Атмосфера":     "about",
    "Уютная атмосфера": "about",
    "Чистота":       "about",

    # work results / services in action
    "Маникюр":          "gallery",
    "Окрашивание волос": "gallery",
    "Окрашивание":      "gallery",
    "Стрижка":          "gallery",
    "Педикюр":          "gallery",
    "Уход за ресницами": "gallery",
    "Наращивание ресниц": "gallery",
    "Брови":            "gallery",
    "Макияж":           "gallery",
    "Волосы":           "gallery",
    "Ногти":            "gallery",

    # team
    "Персонал":      "team",
    "Мастера":       "team",
    "Компетентность": "team",  # often photos of work, but Vision will reclassify

    # not photo-relevant (text-only aspects)
    "Время ожидания": None,
    "Кофе":          None,
    "Цены":          None,
    "Расположение":  None,
    "Обслуживание":  None,
}
```

For unknown aspect texts: default to `gallery` (Vision will validate).

### B.3 Pipeline

```
Step 1: Pre-flight check
  - Read card.json from data/yandex/{slug}-{id}/card.json
  - If qualification.qualified == False → exit early with reason
  - If card not found → error

Step 2: Build candidate pool
  For each aspect:
    block_hint = ASPECT_TO_BLOCK.get(aspect.text, "gallery")
    if block_hint is None: skip
    for url in aspect.photos:
      candidates.append({
        url: url,
        normalized_url: strip_size_suffix(url),
        sources: [aspect.text],
        block_hint: block_hint,
      })
  
  For each service:
    if service.photo_url:
      candidates.append({
        url: service.photo_url,
        block_hint: "services",
        sources: ["service:" + service.title],
        service_ref: service.title,
      })
  
  Optional: pull from VK / website (existing logic from photos.py).
    block_hint: "auto"  # let Vision decide

Step 3: Deduplicate by normalized_url
  - If URL appears in multiple aspects, merge sources[] — keep the most specific block_hint
  - Priority: services > about > gallery > team

Step 4: Vision rich-profile pass
  For each unique candidate (in parallel, semaphore=5):
    profile = analyze_photo_rich(url, hint=block_hint)
    candidate.profile = profile
    backoff on 429

Step 5: Quality gate
  Drop candidates where:
    profile.rejected == True
    profile.quality.score < MIN_QUALITY (=4)

Step 6: Layout into folders
  For each block (hero, gallery, about, services, team):
    pool = candidates where:
      profile.usable_in_<block> == True
      OR (block matches block_hint AND no explicit usable_in flags)
    sort by profile.quality.score desc
    take top N (FOLDER_LIMITS)

Step 7: Download + save metadata
  For each picked candidate:
    download to public/{slug}-{id}/{block}/{filename}
    save profile.json next to it: {filename}.json

Step 8: Aggregate metadata
  Write data/yandex/{slug}-{id}/photo_map.json with all pickings
  Update extracted/{slug}-{id}.json with photos_by_block (for builder compat)
```

### B.4 Vision rich-profile prompt

In `llm/curator.py` add new function `analyze_photo_rich(url, hint=None)`:

```python
def analyze_photo_rich(image_url: str, hint: str = None) -> Dict[str, Any]:
    """
    Returns rich per-photo profile. Single API call. Hint is the Yandex aspect tag
    (e.g. "Интерьер", "Маникюр") if available — used to bias classification.
    """
    hint_text = f"\nYandex tagged this photo as: '{hint}'. Verify and override if wrong." if hint else ""

    prompt = f"""Analyze a photo for a beauty salon landing page.{hint_text}
Return STRICT JSON:
{{
  "rejected": true|false,
  "reject_reason": null | "medical_mask" | "low_quality" | "watermark" | "screenshot" | "text_overlay" | "person_face_only" | "irrelevant",
  
  "category": "interior" | "work_result" | "master_at_work" | "team_portrait" | "exterior" | "service_card" | "logo" | "other",
  
  "quality": {{
    "score": 1-10,
    "sharpness": 1-10,
    "lighting": 1-10,
    "composition": 1-10
  }},
  
  "content": {{
    "description": "1-2 sentence description in Russian",
    "alt_text": "short alt text for HTML in Russian, max 80 chars",
    "objects": ["object1", "object2"],
    "colors": ["color1", "color2"],
    "mood": "calm | energetic | luxurious | cozy | clinical | other"
  }},
  
  "people": {{
    "present": true|false,
    "count": 0,
    "faces_visible": true|false,
    "type": null | "client" | "master" | "team_group" | "model"
  }},
  
  "service_ref": null | "manicure" | "pedicure" | "haircut" | "coloring" | "lashes" | "brows" | "makeup" | "facial" | "hair_treatment" | "other",
  
  "marketing": {{
    "usable_in_hero": true|false,
    "usable_in_about": true|false,
    "usable_in_gallery": true|false,
    "usable_in_team": true|false,
    "usable_in_services": true|false,
    "social_proof_value": "low" | "medium" | "high"
  }},
  
  "flags": {{
    "has_logo": true|false,
    "has_text_overlay": true|false,
    "has_watermark": true|false,
    "is_screenshot": true|false,
    "medical_mask": true|false,
    "low_resolution": true|false
  }}
}}

Reject rules (set rejected=true):
- medical mask visible on face
- watermark or stock-photo signature
- screenshot of UI / phone interface
- heavy text overlay covering image
- blurry / dark / underexposed
- only a person's face with no context (selfie without setting)

Hero criteria: vertical OR landscape, sharp, well-lit, has visual hook, no text overlay.
About criteria: interior, atmosphere, salon space, no people OR distant people.
Gallery criteria: clear work result (manicure close-up, hair, brows etc).
Team criteria: portrait of a single person, professional, face visible.
"""
    parsed = analyze_image_url(image_url, prompt)
    return _validate_rich_profile(parsed)
```

`_validate_rich_profile` — defensive: any missing field gets a default, type-checked. Bad input → `{rejected: True, reject_reason: "parse_error"}`.

### B.5 429 backoff

```python
async def vision_with_backoff(url: str, hint: str, attempt: int = 0) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(analyze_photo_rich, url, hint)
    except RateLimitError as e:
        if attempt >= 4: raise
        delay = (2 ** attempt) + random.uniform(0, 1)  # 1, 2, 4, 8 sec
        log(f"⏳ 429, backoff {delay:.1f}s (attempt {attempt+1})")
        await asyncio.sleep(delay)
        return await vision_with_backoff(url, hint, attempt + 1)
```

If `analyze_image_url` doesn't raise typed errors — wrap in try/except `Exception` and check `str(e)` for "429" / "rate" / "quota". Adapt as needed.

### B.6 Concurrency

```python
SEM = asyncio.Semaphore(5)

async def process_candidate(c):
    async with SEM:
        c.profile = await vision_with_backoff(c.normalized_url, c.block_hint)
        return c

results = await asyncio.gather(*[process_candidate(c) for c in unique_candidates])
```

### B.7 Folder layout

```python
FOLDER_LIMITS = {
    "hero":     1,
    "gallery":  6,
    "about":    2,
    "services": 8,   # 1 per service ideally
    "team":     6,
}

MIN_BLOCK_THRESHOLDS = {
    "hero":     1,
    "gallery":  3,   # less = block disabled
    "about":    1,   # less = block disabled
    "services": 1,
    "team":     0,   # team is optional
}
```

If a block doesn't reach minimum → mark `block_disabled[block] = True` in photo_map. Builder will skip the block on the landing page.

### B.8 Output structure

```
data/yandex/mood-26/
  card.json
  raw_state.json
  photo_map.json          ← NEW

public/mood-26/
  hero/
    photo_1.jpg
    photo_1.json          ← Vision profile
  gallery/
    photo_1.jpg
    photo_1.json
    photo_2.jpg
    photo_2.json
    ...
  about/
    photo_1.jpg
    photo_1.json
    photo_2.jpg
    photo_2.json
  services/
    manikyur.jpg
    manikyur.json
    ...
  team/
    photo_1.jpg
    photo_1.json
```

`photo_map.json`:

```json
{
  "schema_version": 1,
  "lead_id": 26,
  "slug": "mood",
  "generated_at": "2026-...",
  "qualification": "qualified",
  "stats": {
    "total_candidates": 47,
    "after_dedup": 38,
    "after_quality_gate": 31,
    "vision_calls": 38,
    "vision_failures": 2,
    "rejected": {
      "medical_mask": 3,
      "low_quality": 2,
      "watermark": 0
    }
  },
  "blocks": {
    "hero": {
      "enabled": true,
      "photos": [
        {"path": "/mood-26/hero/photo_1.jpg", "alt": "...", "score": 9}
      ]
    },
    "gallery": {
      "enabled": true,
      "photos": [...]
    },
    "about": {
      "enabled": true,
      "photos": [...]
    },
    "services": {
      "enabled": true,
      "photos": [
        {"path": "/mood-26/services/smart_pedikyur.jpg", "service_ref": "SMART педикюр без покрытия гель-лаком", "alt": "..."}
      ]
    },
    "team": {
      "enabled": false,
      "reason": "below threshold (0 < 0)",
      "photos": []
    }
  }
}
```

### B.9 CLI

```python
# enrichment/photos_v2.py

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lead_ids", nargs="+", type=int)
    parser.add_argument("--force", action="store_true", help="ignore cache, re-call Vision")
    parser.add_argument("--dry-run", action="store_true", help="no downloads, no Vision calls — show plan only")
    parser.add_argument("--skip-secondary", action="store_true", help="skip VK/website sources, use only card.json")
    args = parser.parse_args()

    for lead_id in args.lead_ids:
        asyncio.run(process_lead(lead_id, force=args.force, dry_run=args.dry_run, skip_secondary=args.skip_secondary))
```

`--force` re-runs Vision. Without it, cached `photo_map.json` (younger than 7 days) is reused.

`--dry-run` reads card.json, builds candidate pool, prints plan, exits without API calls or downloads.

---

## Part C — Migration safety

- Old `enrichment/photos.py` is **NOT** deleted. Stays as fallback.
- Old `public/{slug}/` (without `-{id}` suffix) is left alone.
- New code writes to `public/{slug}-{id}/`. Builder will need an update later (not in this stage).

---

## DoD

- [ ] `enrichment/yandex_state.py` has `qualify_lead()` and writes `qualification` field into card.json + DB
- [ ] DB column `qualification_status` exists (migration script ran)
- [ ] `enrichment/photos_v2.py` exists
- [ ] `llm/curator.py` has `analyze_photo_rich()`
- [ ] `python -m enrichment.photos_v2 26 --force` runs end-to-end without errors
- [ ] `data/yandex/mood-26/photo_map.json` exists, `qualification: "qualified"`, all 5 blocks have data
- [ ] `public/mood-26/{hero,gallery,about,services,team}/` populated; each photo has matching `.json` profile next to it
- [ ] **At least 1 photo in `about/` folder** (was 0 before — main complaint)
- [ ] **0 photos with `medical_mask: true`** in any folder
- [ ] Test on second lead: `python -m enrichment.photos_v2 80` (alina-80, weaker data) — runs without crashes; if not qualified → clean exit with reason
- [ ] Test on third lead with bad data: confirm `not_qualified` is set, no Vision calls happened, `photo_map.json` not generated

---

## Test commands

```powershell
cd "D:\1 KURSOR_PROJ\11 PARSER"

# 1) Top-tier lead — full pipeline
python -m enrichment.yandex_state 26 --force
python -m enrichment.photos_v2 26 --force

# 2) Verify
python -c "import json; m=json.load(open('data/yandex/mood-26/photo_map.json',encoding='utf-8')); print('blocks:', {k: (v['enabled'], len(v['photos'])) for k,v in m['blocks'].items()})"

# Expected: blocks: {'hero': (True, 1), 'gallery': (True, 6), 'about': (True, 2), 'services': (True, N), 'team': (...)}

# 3) Dry-run on weaker lead
python -m enrichment.yandex_state 80 --force
python -m enrichment.photos_v2 80 --dry-run

# 4) Disqualified lead
# pick any lead known to have low review_count
python -m enrichment.yandex_state <weak_id> --force
# observe in log: 🚫 Qualification: NOT QUALIFIED
python -m enrichment.photos_v2 <weak_id>
# expected: exits with "Lead not qualified, skipping Vision"
```

---

## What's NOT in this stage

- Builder integration (still reads old format) — separate stage
- TapLink scraping — separate stage
- Full gallery (121 photos via /gallery/ scroll) — deferred, aspects suffice
- Removing legacy `photos.py` — kept as fallback

---

## Notes for Codex

- **Reuse** existing `analyze_image_url()` in `llm/curator.py` as transport — only add `analyze_photo_rich()` wrapper with new prompt.
- **Reuse** `slugify_name()`, `strip_size_suffix()`, `_download_to_path()` from `enrichment/photos.py` — copy or import.
- **Reuse** `normalize_yandex_photo_url()` for size suffix manipulation.
- All dictionary access must use `.get()` with defaults — Yandex state structure can vary.
- Russian text in JSON files: `ensure_ascii=False`.
- Logging: use existing `write_log()` style with timestamps.
- ASCII-only filenames: when generating `services/{slug}.jpg`, transliterate the service title via `slugify_name()`.