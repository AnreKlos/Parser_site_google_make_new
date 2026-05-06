# TZ Stage 5.1: Folder cleanup + EXIF screenshot filter

> **Two bugs found in mood-26 audit:**
> 1. **CRITICAL:** `team/photo_1.jpg` from previous run still on disk after team became disabled. Builder may pick up stale files.
> 2. **HIGH:** HDR screenshot ("photo_3" with "HDR" badge) bypassed Vision text-prompt filter. Need a deterministic check, not prompt-based.
>
> **Principles:**
> - Folder state must always match `photo_map.json`. No orphans.
> - Reject screenshots before Vision call (saves tokens) using EXIF + dimension heuristics.

---

## Patch 1 — Clean block folders on every run

### 1.1 New helper in `enrichment/photos_v2.py`

```python
import shutil

def clean_block_folder(slug: str, lead_id: int, block: str) -> int:
    """
    Remove all files inside public/{slug}-{lead_id}/{block}/.
    Folder itself is kept (recreated empty).
    Returns number of files removed.
    """
    folder = Path("public") / f"{slug}-{lead_id}" / block
    if not folder.exists():
        return 0
    removed = 0
    for f in folder.iterdir():
        if f.is_file():
            f.unlink()
            removed += 1
    return removed
```

### 1.2 Wire into pipeline

In `process_lead` (or wherever final layout is written), **before** writing
new files, call `clean_block_folder` for each of the 5 blocks:

```python
# Before writing picked photos to disk
for block in ("hero", "about", "gallery", "team", "services"):
    removed = clean_block_folder(slug, lead_id, block)
    if removed:
        write_log(f"   🧹 cleaned {block}/: removed {removed} stale files")
```

This runs **always**, not only on `--force`. Rationale: every successful
pipeline run produces a definitive `photo_map.json`. The folder must
match it. If a block becomes disabled, its folder must be empty.

For disabled blocks (`block.enabled == false`), no new files are written
after cleaning — the folder stays empty. Builder reads `enabled` flag
from photo_map.json and skips disabled blocks; the empty folder is a
secondary safety net.

---

## Patch 2 — EXIF/dimension screenshot filter

### 2.1 Where it runs

Insert into the **download stage**, immediately after each photo is
downloaded to a temp file but **before** Vision is called. If a photo is
detected as a screenshot, mark it `rejected=True` with
`reject_reason="screenshot_exif"` and skip the Vision call entirely.

### 2.2 New helper in `enrichment/photos_v2.py`

```python
from PIL import Image
from PIL.ExifTags import TAGS

# Common phone screenshot resolutions (width x height, portrait)
PHONE_SCREEN_DIMENSIONS = {
    # iPhone
    (1170, 2532),  # 12, 13, 14
    (1179, 2556),  # 14 Pro, 15
    (1290, 2796),  # 14/15 Pro Max
    (1284, 2778),  # 12/13 Pro Max
    (1125, 2436),  # X, XS, 11 Pro
    (828, 1792),   # XR, 11
    (750, 1334),   # 6/7/8
    (1080, 1920),  # generic Android FHD
    (1440, 2560),  # generic Android QHD
    (1080, 2340),
    (1080, 2400),
    (1440, 3120),
}

# Screenshot software signatures (case-insensitive substring match)
SCREENSHOT_SOFTWARE_HINTS = (
    "screenshot",
    "screen shot",
    "screencapture",
)


def is_likely_screenshot(local_path: Path) -> Tuple[bool, str]:
    """
    Returns (is_screenshot, reason).

    Heuristics (any one triggers screenshot=True):
      A. EXIF Software field contains "screenshot" / similar
      B. Image dimensions match a known phone screen size (W x H or H x W)
      C. EXIF is completely empty AND dimensions are tall portrait (>1.7 ratio)
         AND width is in [750, 1500] (typical phone widths) — soft signal
    """
    try:
        with Image.open(local_path) as img:
            w, h = img.size
            exif = img._getexif() or {}
    except Exception as e:
        return False, f"exif_read_failed: {e}"

    # A — explicit Software tag
    software_value = ""
    for tag_id, value in exif.items():
        tag = TAGS.get(tag_id, "")
        if tag == "Software" and isinstance(value, str):
            software_value = value.lower()
            break
    for hint in SCREENSHOT_SOFTWARE_HINTS:
        if hint in software_value:
            return True, f"software_tag={software_value!r}"

    # B — dimensions match known phone screen
    if (w, h) in PHONE_SCREEN_DIMENSIONS or (h, w) in PHONE_SCREEN_DIMENSIONS:
        return True, f"dimensions_match_phone_screen={w}x{h}"

    # C — empty EXIF + portrait + phone-typical width
    has_camera_exif = any(
        TAGS.get(t, "") in ("Make", "Model", "DateTimeOriginal", "FNumber", "ExposureTime")
        for t in exif.keys()
    )
    ratio = h / w if w else 0
    if not has_camera_exif and ratio >= 1.7 and 700 <= w <= 1500:
        return True, f"no_camera_exif+portrait_phone_size={w}x{h}"

    return False, ""
```

### 2.3 Wire into download flow

In the existing download function (or wherever photo bytes hit disk):

```python
# After download succeeds
local_path = Path(temp_dir) / f"{candidate_id}.jpg"
# ... write bytes to local_path ...

is_shot, reason = is_likely_screenshot(local_path)
if is_shot:
    write_log(f"   🚫 screenshot rejected: {reason} | {url}")
    candidate["profile"] = {
        "rejected": True,
        "reject_reason": "screenshot_exif",
        "fit_scores": {role: {"score": 0, "reason": "screenshot detected via EXIF"}
                       for role in ("hero","about","gallery","team","services")},
        "_skipped_vision": True,
    }
    local_path.unlink(missing_ok=True)
    return candidate  # skip Vision

# Otherwise continue to Vision call as before
```

Note: candidates with `rejected=True` flow through the existing
`lay_out_blocks` filter (they're already excluded). Just make sure the
profile structure is well-formed so downstream code doesn't crash.

### 2.4 Stats

In `photo_map.json`, add to `stats`:

```python
stats["screenshots_filtered"] = number_of_photos_rejected_by_exif_check
```

---

## DoD

- [ ] `clean_block_folder()` implemented and called for all 5 blocks before writing
- [ ] After running on lead 26, `public/mood-26/team/` is **empty** (or folder doesn't exist)
- [ ] `is_likely_screenshot()` implemented in `enrichment/photos_v2.py`
- [ ] Pipeline calls `is_likely_screenshot` after download, before Vision
- [ ] Screenshot photos skip Vision (saves tokens)
- [ ] `photo_map.json.stats.screenshots_filtered` reports the count
- [ ] **Visual check on `public/mood-26/gallery/`:** no HDR badge, no battery icons, no UI overlays
- [ ] **Visual check on `public/mood-26/team/`:** folder empty (matches `team.enabled=false`)

---

## Test commands

```powershell
cd "D:\1 KURSOR_PROJ\11 PARSER"
python -m enrichment.photos_v2 26 --force

# Verify cleanup
python -c "from pathlib import Path; t=Path('public/mood-26/team'); print('team contents:', list(t.iterdir()) if t.exists() else 'no folder')"
# Expected: team contents: []

# Verify screenshot filter ran
python -c "import json; m=json.load(open('data/yandex/mood-26/photo_map.json',encoding='utf-8')); print('screenshots filtered:', m['stats'].get('screenshots_filtered'))"
# Expected: at least 1 (the HDR photo)

# Visual check
explorer "D:\1 KURSOR_PROJ\11 PARSER\public\mood-26"
```

---

## Notes for Codex

- Pillow is already a project dependency (used elsewhere) — no new install
- If a photo's EXIF read raises any exception, treat it as "not a screenshot" (fail-open) — we don't want to lose good photos due to library hiccups
- Logging: keep `write_log()` style with timestamp + emoji prefix
- Don't delete the block folder itself — only its contents (mkdir might be needed elsewhere)
- For Yandex CDN photos, EXIF is usually preserved on get-altay URLs, so this check has signal
