# TZ Stage 4: Vision rich profile v2 — role-specific photo ranking

> **Goal:** fix photo selection quality. Currently Vision recognizes photos correctly (descriptions are good), but ranks them poorly for landing-page roles. Replace generic `quality.score` ranking with **role-specific fit scores** + hard gates per block + visual deduplication for `about`.
>
> **Source of truth:** beauty industry research (NN/g, Baymard, CXL, EmbedSocial, real client reviews from 2GIS/iRecommend) compiled in project knowledge.
>
> **ASCII-only file/folder names. No new dependencies. Reuse existing infrastructure.**

---

## Critical principle — for every prompt rule, the rationale comes from research

The Vision prompt below is built on these evidence-based rules:

| Rule | Source / rationale |
|---|---|
| Stock photos = automatic reject | Lindgaard 50ms first impression; "девушка в полотенце с огурцами" instantly destroys trust |
| Hero must show face/eyes/action, not back/back-of-head | NN/g F-pattern — first 3-5 sec must answer "что это, для меня ли" |
| Team photo accepted only with visible name/role caption | "72% клиентов выбирают мастера, а не салон" + anti-pattern "мастера-призраки" |
| About — clean interior, NO close-up procedure shots | About is "atmosphere/space", not "work in progress" (which is gallery) |
| Gallery — close-up of result (nails, brows, hair), no master in frame | "Фото до/после = +25-45% конверсии" — focus on result, not process |
| Visual deduplication of about (same desk, same angle = waste) | NN/g: each photo must add new information |
| No medical masks anywhere | Already implemented, keep |
| No watermarks, no text overlays, no UI screenshots | Trust-killers, already implemented, strengthen |

---

## Part A — Vision prompt rewrite (`llm/curator.py`)

### A.1 Replace `analyze_photo_rich()` with role-specific scoring

```python
def analyze_photo_rich(image_url: str, hint: str = None) -> Dict[str, Any]:
    """
    Returns rich photo profile WITH role-specific fit scores.
    Hint = Yandex aspect tag (e.g. "Интерьер", "Маникюр") — bias only, not authority.
    """
    hint_text = (
        f"\nYandex auto-tagged this photo as: '{hint}'. Use as a hint, "
        f"but YOU decide the final category based on what you see."
        if hint else ""
    )

    prompt = f"""You are a beauty-salon landing page editor. Analyze this photo and decide HOW WELL it fits each landing-page role.{hint_text}

Return STRICT JSON (no markdown, no comments):

{{
  "rejected": true|false,
  "reject_reason": null | "stock_photo" | "medical_mask" | "low_quality" | "watermark" | "screenshot" | "text_overlay_heavy" | "ui_interface" | "irrelevant" | "blurry" | "duplicate_pattern",

  "category": "interior" | "work_result" | "master_at_work" | "team_portrait" | "exterior" | "service_card" | "logo" | "tools_products" | "selfie" | "other",

  "quality": {{
    "score": 1-10,
    "sharpness": 1-10,
    "lighting": 1-10,
    "composition": 1-10
  }},

  "content": {{
    "description": "1-2 sentence Russian description of what's in the photo",
    "alt_text": "short Russian alt for HTML (max 80 chars), describes service+result",
    "objects": ["object1", "object2", "object3"],
    "dominant_colors": ["color1", "color2"],
    "mood": "calm" | "energetic" | "luxurious" | "cozy" | "clinical" | "neutral"
  }},

  "people": {{
    "present": true|false,
    "count": 0,
    "faces_visible": true|false,
    "primary_subject": null | "client" | "master" | "team_group" | "model" | "unclear",
    "back_or_side_only": true|false,
    "covered_face": true|false
  }},

  "text_in_image": {{
    "present": true|false,
    "looks_like_caption_for_person": true|false,
    "extracted_text": null | "string with text from photo (name/role if any)"
  }},

  "service_ref": null | "manicure" | "pedicure" | "haircut" | "coloring" | "lashes" | "brows" | "makeup" | "facial" | "hair_treatment" | "nails_general" | "other",

  "fit_scores": {{
    "hero":     {{"score": 0-10, "reason": "1 sentence why"}},
    "about":    {{"score": 0-10, "reason": "1 sentence why"}},
    "gallery":  {{"score": 0-10, "reason": "1 sentence why"}},
    "team":     {{"score": 0-10, "reason": "1 sentence why"}},
    "services": {{"score": 0-10, "reason": "1 sentence why"}}
  }},

  "flags": {{
    "has_logo": true|false,
    "has_text_overlay": true|false,
    "has_watermark": true|false,
    "is_screenshot": true|false,
    "medical_mask": true|false,
    "low_resolution": true|false,
    "stock_photo_signals": true|false
  }}
}}

============================================================
HARD REJECT (set rejected=true) if ANY of these is true:
- Person wearing a medical/surgical mask
- Watermark, signature, or stock-photo provider mark visible
- Screenshot of a phone/computer UI, app interface, calendar, messenger
- Heavy text overlay covering >30% of image
- Blurry, dark, severely underexposed
- Pure selfie of a face with no salon context
- Stock-photo signals: airbrushed model, white teeth, generic studio backdrop, towel-on-head cliche, cucumber-on-eyes cliche

============================================================
ROLE-SPECIFIC FIT SCORING — score 0-10 PER ROLE based on these criteria:

HERO (the very first photo a visitor sees, must hook in 3 seconds):
  10 = master in action with visible face/hands + clear visual story (e.g. coloring hair, drawing brow, nail art close-up with hands of master)
   8 = beautiful interior with a person in frame OR striking result close-up (manicure macro, hair after coloring) with composition that draws eye
   6 = clean stylish interior, no people, but good light and depth
   4 = generic interior shot, slightly cluttered or flat
   2 = back of person, back of head, person walking away, no face/no action
   0 = anything that "doesn't tell a story in 3 seconds"

  HARD ZERO if: person shown only from back or side, face not visible AT ALL, only logo or signage, screenshot, dark/blurry

ABOUT (interior / atmosphere, "what kind of place is this"):
  10 = clean, well-lit interior shot of the salon space (chairs, mirrors, work area visible), no close-up procedures
   8 = same, with optional distant master/client visible (sets atmosphere)
   6 = corner/detail of interior (welcome desk, single chair) — usable but secondary
   4 = close-up of work surface (table with tools) — only if no broader shots available
   2 = procedure happening in frame (master + client closeup) — wrong role
   0 = no salon space visible at all

  HARD ZERO if: it's actually a portrait, work result close-up, or master-at-work shot

GALLERY (showcase of WORK RESULTS, before/after style, what client will get):
  10 = sharp close-up of finished work — manicure with detail visible, hair after coloring, brows after correction, lashes after extension
   8 = result shot with hands/face area but the WORK is the subject
   6 = before/after pair OR work-in-progress where the work itself dominates frame
   4 = master-at-work shot where you can see the result forming
   2 = full salon interior, no specific work showcased
   0 = no beauty service result visible

  HARD ZERO if: it's just an interior shot, just a portrait, or a logo

TEAM (master portraits — strict gates):
  10 = single person portrait, face fully visible, professional pose, AND text_in_image.looks_like_caption_for_person == true (caption with name/role on the photo itself)
   8 = single person portrait, face fully visible, professional pose, in clearly recognizable salon setting
   6 = single person portrait, face visible but unclear if it's a master or client
   4 = group photo of 2-3 people, all faces visible, looks like staff
   2 = person shown but face not clearly visible OR back/side only OR multiple people in disorganized scene
   0 = no person, or face hidden by hair/object/angle

  HARD ZERO if: any of these — face not fully visible, person from back, face covered by hair, multiple people without clear "team" framing, person looks like a client (mid-procedure), child in frame

SERVICES (one photo per service card):
  10 = clean shot of the procedure or result of a SPECIFIC service (manicure macro, hair coloring tool in hand, brow shape close-up)
   8 = product/tool arrangement clearly representing a service category
   6 = atmospheric interior shot of the service zone (manicure desk, hair-wash chair)
   4 = generic salon photo
   0 = irrelevant to any service

  HARD ZERO if: it's a portrait of a person without a service context

============================================================
DEDUPLICATION HINT (output only, used downstream):

Fill content.objects with 3-7 most prominent objects in the photo. Be specific:
  GOOD: ["manicure_desk", "uv_lamp", "client_hands", "nail_polish_bottles"]
  BAD:  ["table", "items", "stuff"]

Fill content.dominant_colors with 2-3 main colors:
  GOOD: ["beige", "white", "rose_gold"]

This lets the downstream code detect near-duplicate photos (same desk, same angle).

============================================================
TEXT-IN-IMAGE DETECTION (critical for team gate):

If you see ANY text/caption on the photo (name, job title, watermark, logo with text):
- text_in_image.present = true
- text_in_image.extracted_text = the text you see (max 100 chars)
- If the text appears to be a person's name AND/OR a job role (мастер, стилист, бровист, etc.) attached to a person in the photo → text_in_image.looks_like_caption_for_person = true

If no text → all three text_in_image fields are false/null.

This is the GATE for team-photo acceptance.

============================================================
Return ONLY the JSON. No prose, no markdown, no explanation outside the JSON.
"""

    parsed = analyze_image_url(image_url, prompt)
    return _validate_rich_profile(parsed)
```

### A.2 Update `_validate_rich_profile()`

Add defaults for new fields:

```python
def _validate_rich_profile(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {"rejected": True, "reject_reason": "parse_error", "fit_scores": {
            "hero": {"score": 0, "reason": "parse_error"},
            "about": {"score": 0, "reason": "parse_error"},
            "gallery": {"score": 0, "reason": "parse_error"},
            "team": {"score": 0, "reason": "parse_error"},
            "services": {"score": 0, "reason": "parse_error"},
        }}

    # Ensure fit_scores exists with all 5 roles
    fit_scores = data.get("fit_scores") or {}
    for role in ("hero", "about", "gallery", "team", "services"):
        if role not in fit_scores or not isinstance(fit_scores[role], dict):
            fit_scores[role] = {"score": 0, "reason": "missing"}
        else:
            fit_scores[role].setdefault("score", 0)
            fit_scores[role].setdefault("reason", "")
    data["fit_scores"] = fit_scores

    # Ensure text_in_image exists
    text_in_image = data.get("text_in_image") or {}
    text_in_image.setdefault("present", False)
    text_in_image.setdefault("looks_like_caption_for_person", False)
    text_in_image.setdefault("extracted_text", None)
    data["text_in_image"] = text_in_image

    # Ensure people block has new fields
    people = data.get("people") or {}
    people.setdefault("present", False)
    people.setdefault("count", 0)
    people.setdefault("faces_visible", False)
    people.setdefault("primary_subject", None)
    people.setdefault("back_or_side_only", False)
    people.setdefault("covered_face", False)
    data["people"] = people

    # Ensure flags block
    flags = data.get("flags") or {}
    for f in ("has_logo", "has_text_overlay", "has_watermark", "is_screenshot",
              "medical_mask", "low_resolution", "stock_photo_signals"):
        flags.setdefault(f, False)
    data["flags"] = flags

    # Ensure content
    content = data.get("content") or {}
    content.setdefault("description", "")
    content.setdefault("alt_text", "")
    content.setdefault("objects", [])
    content.setdefault("dominant_colors", [])
    content.setdefault("mood", "neutral")
    data["content"] = content

    # Defaults
    data.setdefault("rejected", False)
    data.setdefault("reject_reason", None)
    data.setdefault("category", "other")
    data.setdefault("service_ref", None)

    return data
```

---

## Part B — Layout logic rewrite (`enrichment/photos_v2.py`)

### B.1 New constants

```python
# Minimum fit_score for a photo to be CONSIDERED for a block
MIN_FIT_SCORE = {
    "hero":     7,   # only "good story" photos
    "about":    6,
    "gallery":  6,
    "team":     8,   # STRICT — explained below
    "services": 5,
}

# Block enabled if it has at least N photos meeting the threshold
MIN_PHOTOS_FOR_BLOCK = {
    "hero":     1,
    "about":    1,
    "gallery":  3,
    "team":     1,
    "services": 1,
}

FOLDER_LIMITS = {
    "hero":     1,
    "gallery":  6,
    "about":    2,
    "services": 8,
    "team":     6,
}
```

### B.2 New strict gate for `team`

```python
def passes_team_gate(profile: Dict[str, Any]) -> bool:
    """
    Photo is accepted into team/ folder ONLY IF ALL of these hold:
      - faces visible
      - exactly one person in frame
      - face NOT covered (no hair-on-face, no objects)
      - person NOT shown from back/side only
      - team_fit_score >= MIN_FIT_SCORE['team'] (=8)
      - text_in_image.looks_like_caption_for_person == True
        (the photo itself has a caption identifying this as a master)

    If text caption is missing, photo is REJECTED for team even if it
    looks like a portrait. We do not invent masters.
    """
    if profile.get("rejected"):
        return False

    people = profile.get("people") or {}
    if not people.get("faces_visible"):
        return False
    if people.get("count") != 1:
        return False
    if people.get("covered_face"):
        return False
    if people.get("back_or_side_only"):
        return False

    fit = (profile.get("fit_scores") or {}).get("team") or {}
    if (fit.get("score") or 0) < MIN_FIT_SCORE["team"]:
        return False

    # Critical: only accept if photo itself is captioned as a master
    text = profile.get("text_in_image") or {}
    if not text.get("looks_like_caption_for_person"):
        return False

    return True
```

### B.3 Visual deduplication for about

```python
def _signature(profile: Dict[str, Any]) -> set:
    """Build a comparable signature from objects + colors."""
    content = profile.get("content") or {}
    objs = set(o.lower() for o in (content.get("objects") or []))
    colors = set(c.lower() for c in (content.get("dominant_colors") or []))
    # Combine, weighting objects more
    return objs | {f"color:{c}" for c in colors}


def _visual_overlap(sig_a: set, sig_b: set) -> float:
    """Jaccard similarity between two signatures."""
    if not sig_a or not sig_b:
        return 0.0
    inter = len(sig_a & sig_b)
    union = len(sig_a | sig_b)
    return inter / union if union else 0.0


def deduplicate_visually(candidates: List[Dict[str, Any]], threshold: float = 0.65) -> List[Dict[str, Any]]:
    """
    Greedy de-duplication. Iterate candidates in their current order
    (assumed pre-sorted by relevance/score). For each candidate, drop it
    if it overlaps any previously-kept candidate by >= threshold.
    """
    kept: List[Dict[str, Any]] = []
    kept_sigs: List[set] = []
    for c in candidates:
        sig = _signature(c.get("profile") or {})
        if not sig:
            kept.append(c)
            kept_sigs.append(set())
            continue
        is_dup = any(_visual_overlap(sig, s) >= threshold for s in kept_sigs)
        if not is_dup:
            kept.append(c)
            kept_sigs.append(sig)
    return kept
```

### B.4 New layout function

```python
def lay_out_blocks(candidates: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Build the photo_map blocks structure using role-specific fit scores.

    For each block:
      1. Filter candidates that pass the role's gate (rejection + min fit_score)
      2. Sort by fit_score DESC
      3. (about only) apply visual deduplication
      4. (team only) apply strict portrait+caption gate
      5. Take top FOLDER_LIMITS[block]
      6. If count < MIN_PHOTOS_FOR_BLOCK[block] → block disabled
    """
    blocks: Dict[str, Dict[str, Any]] = {}

    for block in ("hero", "about", "gallery", "team", "services"):
        # 1) Gate: not rejected + meets min fit_score for THIS role
        threshold = MIN_FIT_SCORE[block]
        filtered = []
        for c in candidates:
            p = c.get("profile") or {}
            if p.get("rejected"):
                continue
            score = ((p.get("fit_scores") or {}).get(block) or {}).get("score") or 0
            if score < threshold:
                continue

            # Block-specific extra gates
            if block == "team" and not passes_team_gate(p):
                continue
            if block == "hero":
                people = p.get("people") or {}
                if people.get("present") and people.get("back_or_side_only"):
                    continue  # no backs in hero, period

            filtered.append(c)

        # 2) Sort by role-specific fit_score
        filtered.sort(
            key=lambda c: ((c.get("profile") or {}).get("fit_scores") or {}).get(block, {}).get("score") or 0,
            reverse=True,
        )

        # 3) Visual dedup for about
        if block == "about":
            filtered = deduplicate_visually(filtered, threshold=0.65)

        # 4) Take top N
        picked = filtered[:FOLDER_LIMITS[block]]

        # 5) Decide enabled/disabled
        enabled = len(picked) >= MIN_PHOTOS_FOR_BLOCK[block]

        blocks[block] = {
            "enabled": enabled,
            "reason": (
                f"{len(picked)} photos meet criteria"
                if enabled else
                f"only {len(picked)} photos pass gate (need {MIN_PHOTOS_FOR_BLOCK[block]})"
            ),
            "photos": [
                {
                    "url": c["url"],
                    "normalized_url": c.get("normalized_url"),
                    "alt": ((c.get("profile") or {}).get("content") or {}).get("alt_text", ""),
                    "description": ((c.get("profile") or {}).get("content") or {}).get("description", ""),
                    "fit_score": ((c.get("profile") or {}).get("fit_scores") or {}).get(block, {}).get("score", 0),
                    "fit_reason": ((c.get("profile") or {}).get("fit_scores") or {}).get(block, {}).get("reason", ""),
                    "category": (c.get("profile") or {}).get("category"),
                    "service_ref": (c.get("profile") or {}).get("service_ref"),
                    "sources": c.get("sources", []),
                }
                for c in picked
            ],
        }

    return blocks
```

### B.5 Stats reporting

In `photo_map.json`, add diagnostic stats for tuning:

```python
stats = {
    "total_candidates": len(all_candidates),
    "after_dedup_url": len(unique_candidates),
    "vision_calls": vision_call_count,
    "vision_failures": vision_failure_count,
    "rejected_breakdown": {
        # count per reject_reason
    },
    "fit_distribution": {
        # for each block, how many photos hit fit_score >= threshold
        "hero":     {"available": N, "after_gate": M},
        "about":    {"available": N, "after_gate": M},
        "gallery":  {"available": N, "after_gate": M},
        "team":     {"available": N, "after_gate": M, "captioned": K},
        "services": {"available": N, "after_gate": M},
    },
}
```

### B.6 Output: photo_map.json structure

```json
{
  "schema_version": 2,
  "lead_id": 26,
  "slug": "mood",
  "generated_at": "...",
  "qualification": "qualified",
  "stats": { ... see B.5 ... },
  "blocks": {
    "hero":     {"enabled": true,  "reason": "...", "photos": [...]},
    "about":    {"enabled": true,  "reason": "...", "photos": [...]},
    "gallery":  {"enabled": true,  "reason": "...", "photos": [...]},
    "services": {"enabled": true,  "reason": "...", "photos": [...]},
    "team":     {"enabled": false, "reason": "0 photos pass gate (need 1) — no captioned master photos found", "photos": []}
  }
}
```

If `team.enabled == false` because no captioned photo found — that's the **correct behavior**, not a bug. The block is skipped on the landing page.

---

## Part C — Cache invalidation

Old `photo_map.json` files use schema_version=1 with old fit logic. Force regeneration:

```python
# In photos_v2.py, when loading cached photo_map:
cached = load_photo_map(lead_id)
if cached and cached.get("schema_version") == 2 and not args.force:
    return cached  # use cache
# else: regenerate
```

Also: when running with `--force`, regenerate Vision profiles (don't use cached profiles either).

---

## Part D — Test plan

### D.1 Run on MOOD (lead 26)

```powershell
cd "D:\1 KURSOR_PROJ\11 PARSER"
python -m enrichment.photos_v2 26 --force
```

### D.2 Visual check (the real test)

Open `public\mood-26\` and verify EACH folder by eye:

| Folder | Pass criteria |
|---|---|
| **hero/** | The 1 photo: shows a face/eyes/action OR striking result. NO BACKS. NO BACK-OF-HEAD. |
| **about/** | The 2 photos: both interior shots; **visually different** from each other (different angle, area, or composition). NO duplicates of the same desk. |
| **gallery/** | All photos: clear close-up of work result (nails, hair, brows, lashes). No interiors. No master portraits. |
| **services/** | One photo per service ideally; each photo logically matches the service title |
| **team/** | EITHER (a) only photos with a visible name/role caption on the photo itself, **OR** (b) folder is EMPTY and `team.enabled == false` in photo_map.json. There must be NO random people. |

### D.3 Verify photo_map.json shows reasoning

```powershell
python -c "import json; m=json.load(open('data/yandex/mood-26/photo_map.json',encoding='utf-8')); [print(k, '->', v['enabled'], v['reason']) for k,v in m['blocks'].items()]"
```

For team specifically:
```powershell
python -c "import json; m=json.load(open('data/yandex/mood-26/photo_map.json',encoding='utf-8')); print(m['stats']['fit_distribution']['team'])"
```

Expected: `{'available': N, 'after_gate': M, 'captioned': K}` — K should be small or 0 for most leads (most don't caption masters), which means team is correctly disabled.

### D.4 Sanity test on weaker lead

```powershell
# Pick a lead with mediocre data but qualified — let me know which one
python -m enrichment.photos_v2 <lead_id> --force
```

Verify:
- No medical masks anywhere
- Team is correctly disabled (most likely outcome)
- Hero photo is at least minimally compelling

### D.5 Profile inspection

For one photo in each folder, open its `.json` and verify:
- `fit_scores` for the role of its folder is high (≥ threshold)
- `fit_scores` for other roles is lower (sanity: hero photo should not also be perfect for team)
- `content.description` matches what you see
- For team: `text_in_image.looks_like_caption_for_person == true`

---

## DoD

- [ ] `analyze_photo_rich()` rewritten with role-specific scoring
- [ ] `_validate_rich_profile()` handles all new fields with defaults
- [ ] `passes_team_gate()` implemented
- [ ] Visual deduplication for about implemented
- [ ] `lay_out_blocks()` uses role-specific fit_scores instead of global quality
- [ ] `photo_map.json` schema bumped to 2 with diagnostic stats
- [ ] Old cache invalidated (regenerate on first run)
- [ ] **Visual check on mood-26**: hero is not "back of person", about has 2 visually different shots, team is either captioned-only or disabled
- [ ] Test on a second lead — confirm behavior generalizes

---

## What's NOT in this stage

- Builder integration (separate stage)
- Improvements to qualify_lead thresholds (already calibrated)
- VK / website secondary photo sources (separate stage)
- TapLink scraping (separate stage)

---

## Notes for Codex

- Reuse all existing infrastructure: `analyze_image_url()`, candidate-pool construction, semaphore, backoff
- Only changes: prompt text in `analyze_photo_rich()`, validator defaults, block layout function
- All Russian text in JSON: `ensure_ascii=False`
- Logging: keep `write_log()` style
- ASCII-only filenames as before
- Cost estimate: same as before (1 Vision call per unique photo). No new API calls introduced.