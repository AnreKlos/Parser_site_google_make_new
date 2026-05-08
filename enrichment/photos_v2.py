#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Парсер v2: фото-обогащение на основе card.json (state-view).

Использует aspects[] из card.json как primary source, Vision для rich-profile,
раскладывает фото по папкам (hero, gallery, about, services, team).
"""
import argparse
import asyncio
import json
import random
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from PIL import Image
from PIL.ExifTags import TAGS

from enrichment.yandex import slugify_name, write_log
from llm.curator import analyze_photo_rich

BASE_DIR = Path(__file__).parent.parent
YANDEX_DATA_DIR = BASE_DIR / "data" / "yandex"
TAPLINK_DATA_DIR = BASE_DIR / "data" / "taplink"
PUBLIC_DIR = BASE_DIR / "public"

ASPECT_TO_BLOCK = {
    # interior / atmosphere
    "Интерьер": "about",
    "Атмосфера": "about",
    "Уютная атмосфера": "about",
    "Чистота": "about",

    # work results / services in action
    "Маникюр": "gallery",
    "Окрашивание волос": "gallery",
    "Окрашивание": "gallery",
    "Стрижка": "gallery",
    "Педикюр": "gallery",
    "Уход за ресницами": "gallery",
    "Наращивание ресниц": "gallery",
    "Брови": "gallery",
    "Макияж": "gallery",
    "Волосы": "gallery",
    "Ногти": "gallery",

    # team
    "Персонал": "team",
    "Мастера": "team",
    "Компетентность": "team",

    # not photo-relevant (text-only aspects)
    "Время ожидания": None,
    "Кофе": None,
    "Цены": None,
    "Расположение": None,
    "Обслуживание": None,
}

# Minimum fit_score for a photo to be CONSIDERED for a block
MIN_FIT_SCORE = {
    "hero": 7,   # only "good story" photos
    "about": 6,
    "gallery": 6,
    "team": 8,   # STRICT — explained below
    "services": 5,
}

# Block enabled if it has at least N photos meeting the threshold
MIN_PHOTOS_FOR_BLOCK = {
    "hero": 1,
    "about": 1,
    "gallery": 3,
    "team": 1,
    "services": 1,
}

FOLDER_LIMITS = {
    "hero": 1,
    "gallery": 6,
    "about": 2,
    "services": 8,
    "team": 6,
}

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

MIN_QUALITY = 4

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

# Block priority for deduplication (higher priority = first pick)
BLOCK_PRIORITY = ["hero", "services", "gallery", "about", "team"]


def clean_block_folder(slug: str, lead_id: int, block: str) -> int:
    """
    Remove all files inside public/{slug}-{lead_id}/{block}/.
    Folder itself is kept (recreated empty).
    Returns number of files removed.
    """
    folder = PUBLIC_DIR / f"{slug}-{lead_id}" / block
    if not folder.exists():
        return 0
    removed = 0
    for f in folder.iterdir():
        if f.is_file():
            f.unlink()
            removed += 1
    return removed


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


def strip_size_suffix(url: str) -> str:
    """Remove size suffix from Yandex photo URL for deduplication."""
    import re
    base = re.sub(r"[#?].*$", "", (url or "").strip())
    base = re.sub(r"/(S|M|L|XL|XXL|M_height|L_height|XL_height|XXL_height|orig|%)$", "", base)
    return base.rstrip("/")


def normalize_yandex_photo_url(url: str, size: str = "orig") -> str:
    """Add size suffix to Yandex photo URL for downloading."""
    import re
    if not isinstance(url, str):
        return ""
    value = url.strip()
    if not value:
        return ""
    # Remove existing suffix and add new one
    base = re.sub(r"/(S|M|L|XL|XXL|M_height|L_height|XL_height|XXL_height|orig|%)$", "", value)
    return base.rstrip("/") + f"/{size}"


@dataclass
class PhotoCandidate:
    url: str
    normalized_url: str
    sources: List[str] = field(default_factory=list)
    block_hint: str = "gallery"
    service_ref: Optional[str] = None
    profile: Optional[Dict[str, Any]] = None


async def vision_with_backoff(url: str, hint: str, attempt: int = 0) -> Dict[str, Any]:
    """Call Vision with exponential backoff on 429."""
    try:
        # Normalize URL for Yandex before Vision
        vision_url = url
        if "avatars.mds.yandex.net" in url or "get-altay" in url or "get-sprav" in url:
            vision_url = normalize_yandex_photo_url(url, "orig")
        
        # analyze_photo_rich is synchronous, run in thread pool
        return await asyncio.to_thread(analyze_photo_rich, vision_url, hint)
    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "rate" in error_str or "quota" in error_str:
            if attempt >= 4:
                raise
            delay = (2 ** attempt) + random.uniform(0, 1)
            write_log(f"⏳ 429, backoff {delay:.1f}s (attempt {attempt+1})")
            await asyncio.sleep(delay)
            return await vision_with_backoff(url, hint, attempt + 1)
        raise


async def process_candidate(candidate: PhotoCandidate, sem: asyncio.Semaphore) -> PhotoCandidate:
    """Process a single candidate with Vision analysis."""
    async with sem:
        # Download to temp file for screenshot detection
        import tempfile
        temp_dir = Path(tempfile.gettempdir())
        temp_path = temp_dir / f"candidate_{id(candidate)}.jpg"

        # Download photo
        download_success = download_photo(candidate.normalized_url, temp_path)
        if not download_success:
            # Mark as rejected if download failed
            candidate.profile = {
                "rejected": True,
                "reject_reason": "download_failed",
                "fit_scores": {role: {"score": 0, "reason": "download failed"}
                               for role in ("hero", "about", "gallery", "team", "services")},
            }
            return candidate

        # Check for screenshot
        is_shot, reason = is_likely_screenshot(temp_path)
        if is_shot:
            write_log(f"   🚫 screenshot rejected: {reason} | {candidate.normalized_url}")
            candidate.profile = {
                "rejected": True,
                "reject_reason": "screenshot_exif",
                "fit_scores": {role: {"score": 0, "reason": "screenshot detected via EXIF"}
                               for role in ("hero", "about", "gallery", "team", "services")},
                "_skipped_vision": True,
            }
            temp_path.unlink(missing_ok=True)
            return candidate

        # Clean up temp file
        temp_path.unlink(missing_ok=True)

        # Proceed to Vision analysis
        candidate.profile = await vision_with_backoff(candidate.normalized_url, candidate.block_hint)
        return candidate


def load_taplink_photos(slug: str, lead_id: int) -> List[PhotoCandidate]:
    """Load photos from TapLink if available."""
    taplink_file = TAPLINK_DATA_DIR / f"{slug}-{lead_id}" / "photos.json"
    if not taplink_file.exists():
        return []
    
    try:
        with open(taplink_file, "r", encoding="utf-8") as f:
            taplink_data = json.load(f)
    except Exception as e:
        write_log(f"⚠️ Failed to load taplink photos.json: {e}")
        return []
    
    photos = taplink_data.get("photos") or []
    candidates = []
    
    for photo in photos:
        url = photo.get("url")
        if not url:
            continue
        normalized = strip_size_suffix(url)
        candidates.append(PhotoCandidate(
            url=url,
            normalized_url=normalized,
            sources=["taplink"],
            block_hint="gallery",  # TapLink photos default to gallery
        ))
    
    write_log(f"📸 Loaded {len(candidates)} photos from TapLink")
    return candidates


def build_candidate_pool(card: Dict[str, Any]) -> List[PhotoCandidate]:
    """Build candidate pool from card.json aspects, services, and TapLink."""
    candidates = []

    # From aspects
    for asp in card.get("aspects") or []:
        aspect_text = asp.get("text") or ""
        block_hint = ASPECT_TO_BLOCK.get(aspect_text, "gallery")
        if block_hint is None:
            continue

        for url in asp.get("photos") or []:
            if not url:
                continue
            normalized = strip_size_suffix(url)
            candidates.append(PhotoCandidate(
                url=url,
                normalized_url=normalized,
                sources=[aspect_text],
                block_hint=block_hint,
            ))

    # From services
    for svc in card.get("services") or []:
        url = svc.get("photo_url")
        if url:
            normalized = strip_size_suffix(url)
            candidates.append(PhotoCandidate(
                url=url,
                normalized_url=normalized,
                sources=[f"service:{svc.get('title', '')}"],
                block_hint="services",
                service_ref=svc.get("title"),
            ))

    # From TapLink (if available)
    slug = card.get("slug", f"lead-{card.get('lead_id', 0)}")
    lead_id = card.get("lead_id")
    taplink_candidates = load_taplink_photos(slug, lead_id)
    candidates.extend(taplink_candidates)

    return candidates


def composite_score(profile: Dict[str, Any], block: str) -> float:
    """
    Финальный score для сортировки в блоке: fit_score (роль) + marketing_grade (вес 0.4).

    Логика:
    - Хорошее фото для роли + хороший marketing = высший приоритет
    - Высокий fit, но низкий marketing (бытовое фото пусть и в тему) — теряет позиции
    - Среднее фото с pro-маркетингом обыграет идеальное по теме, но любительское
    """
    fit = ((profile.get("fit_scores") or {}).get(block) or {}).get("score") or 0
    mg = (profile.get("marketing_grade") or {}).get("score") or 5
    return fit + mg * 0.4


def deduplicate_candidates(candidates: List[PhotoCandidate]) -> List[PhotoCandidate]:
    """Deduplicate by normalized_url, merge sources, keep most specific block_hint."""
    by_url: Dict[str, PhotoCandidate] = {}
    block_priority = {"services": 0, "about": 1, "gallery": 2, "team": 3, "hero": 4}

    for c in candidates:
        if c.normalized_url not in by_url:
            by_url[c.normalized_url] = c
        else:
            existing = by_url[c.normalized_url]
            # Merge sources
            for src in c.sources:
                if src not in existing.sources:
                    existing.sources.append(src)
            # Keep most specific block_hint (lower priority number = more specific)
            if block_priority.get(c.block_hint, 99) < block_priority.get(existing.block_hint, 99):
                existing.block_hint = c.block_hint
            if c.service_ref and not existing.service_ref:
                existing.service_ref = c.service_ref

    return list(by_url.values())


def lay_out_blocks(candidates: List[PhotoCandidate]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int], int]:
    """
    Build the photo_map blocks structure using role-specific fit scores.

    For each block:
      1. Filter candidates that pass the role's gate (rejection + min fit_score)
      2. Sort by fit_score DESC
      3. (about only) apply visual deduplication
      4. (team only) apply strict portrait+caption gate
      5. Take top FOLDER_LIMITS[block]
      6. If count < MIN_PHOTOS_FOR_BLOCK[block] → block disabled

    Returns: (blocks, gallery_by_service, hero_text_rejected)
    """
    blocks: Dict[str, Dict[str, Any]] = {}
    gallery_by_service = {}
    hero_text_rejected = 0

    for block in ("hero", "about", "gallery", "team", "services"):
        # 1) Gate: not rejected + meets min fit_score for THIS role
        threshold = MIN_FIT_SCORE[block]
        filtered = []
        for c in candidates:
            p = c.profile
            if not p:
                continue
            if p.get("rejected"):
                continue
            # Auto-reject screenshots even if Vision forgot to set rejected=true
            if (p.get("flags") or {}).get("is_screenshot"):
                continue
            # Auto-reject face-covered photos
            if (p.get("flags") or {}).get("face_covered"):
                continue
            # Hard-gate: marketing_grade <= 2 (amateur_home/unusable)
            mg_score = (p.get("marketing_grade") or {}).get("score") or 5
            if mg_score <= 2:
                continue  # любительское дно — не пускаем никуда
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
                # NEW: hero is the face of the landing — zero tolerance for any text/UI overlay
                flags = p.get("flags") or {}
                if flags.get("has_text_overlay") or flags.get("has_watermark") or flags.get("is_screenshot"):
                    hero_text_rejected += 1
                    continue

            filtered.append(c)

        # 2) Sort by composite score (fit + marketing_grade)
        if block == "services":
            # Prefer work_result over master_at_work for services + marketing_grade
            def services_sort_key(c):
                p = c.profile or {}
                fit = ((p.get("fit_scores") or {}).get("services") or {}).get("score") or 0
                mg = (p.get("marketing_grade") or {}).get("score") or 5
                cat = p.get("category") or ""
                # Boost work_result, slight penalty for master_at_work
                cat_bonus = {"work_result": 1.5, "master_at_work": -0.5}.get(cat, 0)
                return fit + cat_bonus + mg * 0.4
            filtered.sort(key=services_sort_key, reverse=True)
        else:
            filtered.sort(
                key=lambda c: composite_score(c.profile or {}, block),
                reverse=True,
            )

        # 3) Visual dedup for about and gallery
        if block == "about":
            filtered = deduplicate_visually(filtered, threshold=0.65)
        elif block == "gallery":
            filtered = deduplicate_visually(filtered, threshold=0.50)

        # 4) Take top N (gallery uses quota-based selection)
        if block == "gallery":
            picked, gallery_by_service = pick_gallery_with_quotas(filtered, FOLDER_LIMITS["gallery"])
        else:
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
                    "url": c.url,
                    "normalized_url": c.normalized_url,
                    "alt": ((c.profile or {}).get("content") or {}).get("alt_text", ""),
                    "description": ((c.profile or {}).get("content") or {}).get("description", ""),
                    "fit_score": ((c.profile or {}).get("fit_scores") or {}).get(block, {}).get("score", 0),
                    "fit_reason": ((c.profile or {}).get("fit_scores") or {}).get(block, {}).get("reason", ""),
                    "category": (c.profile or {}).get("category"),
                    "service_ref": (c.profile or {}).get("service_ref"),
                    "sources": c.sources,
                    "marketing_grade": ((c.profile or {}).get("marketing_grade") or {}).get("score", 5),
                    "marketing_tier": ((c.profile or {}).get("marketing_grade") or {}).get("tier", "casual_acceptable"),
                }
                for c in picked
            ],
        }

    return blocks, gallery_by_service, hero_text_rejected


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


def deduplicate_visually(candidates: List[PhotoCandidate], threshold: float = 0.65) -> List[PhotoCandidate]:
    """
    Greedy de-duplication. Iterate candidates in their current order
    (assumed pre-sorted by relevance/score). For each candidate, drop it
    if it overlaps any previously-kept candidate by >= threshold.
    """
    kept: List[PhotoCandidate] = []
    kept_sigs: List[set] = []
    for c in candidates:
        sig = _signature(c.profile or {})
        if not sig:
            kept.append(c)
            kept_sigs.append(set())
            continue
        is_dup = any(_visual_overlap(sig, s) >= threshold for s in kept_sigs)
        if not is_dup:
            kept.append(c)
            kept_sigs.append(sig)
    return kept


async def verify_finalists_no_ui(blocks: Dict[str, Dict]) -> Set[str]:
    """
    Run UI-overlay check on every picked photo. If detected — drop the photo
    and mark its slot. Caller is responsible for backfilling from candidate pool.
    """
    from llm.curator import detect_ui_overlay

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


def deduplicate_across_blocks(blocks: Dict[str, Dict]) -> Dict[str, Dict]:
    """
    Walk blocks in priority order. Each photo URL can appear in only one block.
    If URL is already claimed by a higher-priority block, drop it from the
    current block. The dropped slot is NOT auto-backfilled here — caller must
    re-run layout if backfilling is needed (or accept smaller block).
    """
    claimed: Dict[str, str] = {}  # normalized_url -> block_name
    deduped = {b: dict(blocks[b]) for b in blocks}
    dropped_count = 0

    for block_name in BLOCK_PRIORITY:
        block = deduped.get(block_name)
        if not block or not block.get("enabled"):
            continue
        kept = []
        dropped = 0
        for photo in (block.get("photos") or []):
            url_key = photo["normalized_url"] if photo.get("normalized_url") else photo["url"]
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
        dropped_count += dropped

    return deduped, dropped_count


def backfill_block(
    block_name: str,
    block: Dict,
    all_candidates: List[PhotoCandidate],
    claimed_urls: Set[str],
    min_fit: int,
) -> None:
    """In-place backfill of a block's photos[] up to FOLDER_LIMITS, skipping claimed URLs."""
    target = FOLDER_LIMITS.get(block_name, 0)
    if len(block.get("photos") or []) >= target:
        return

    # Sorted by fit_score for this block, descending
    def fit_key(c):
        p = c.profile
        if not p:
            return 0
        return ((p.get("fit_scores") or {}).get(block_name, {}).get("score") or 0)
    sorted_pool = sorted(all_candidates, key=fit_key, reverse=True)

    for c in sorted_pool:
        if len(block["photos"]) >= target:
            break
        url_key = c.normalized_url or c.url
        if url_key in claimed_urls:
            continue
        p = c.profile
        if not p:
            continue
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
            "url": c.url,
            "normalized_url": c.normalized_url,
            "alt": ((p.get("content") or {}).get("alt_text") or ""),
            "description": ((p.get("content") or {}).get("description") or ""),
            "fit_score": score,
            "fit_reason": ((p.get("fit_scores") or {}).get(block_name) or {}).get("reason") or "",
            "category": p.get("category"),
            "service_ref": p.get("service_ref"),
            "sources": c.sources,
        })
        claimed_urls.add(url_key)


def pick_gallery_with_quotas(
    sorted_candidates: List[PhotoCandidate],
    total_limit: int,
) -> Tuple[List[PhotoCandidate], Dict[str, int]]:
    """
    Pick photos for gallery enforcing category and family caps.
    Input: candidates already sorted by gallery fit_score DESC, deduplicated.
    Output: (picked photos, category_taken dict) with category diversity.

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
        svc = ((c.profile or {}).get("service_ref")) or "other"
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
            svc = ((c.profile or {}).get("service_ref")) or "other"
            fam = family_of(svc)
            fam_cap_relaxed = GALLERY_FAMILY_CAP.get(fam, 1) + 1
            if fam_taken.get(fam, 0) >= fam_cap_relaxed:
                continue
            picked.append(c)
            fam_taken[fam] = fam_taken.get(fam, 0) + 1

    return picked, cat_taken


def download_photo(url: str, path: Path) -> bool:
    """Download photo from URL to path."""
    try:
        # Normalize URL for Yandex
        if "avatars.mds.yandex.net" in url or "get-altay" in url or "get-sprav" in url:
            download_url = normalize_yandex_photo_url(url, "orig")
        else:
            download_url = url
        
        response = requests.get(download_url, timeout=30)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(response.content)
        return True
    except Exception as exc:
        write_log(f"⚠️ Ошибка скачивания {url}: {exc}")
        return False


async def process_lead(
    lead_id: int,
    force: bool = False,
    dry_run: bool = False,
    skip_secondary: bool = False,
) -> Optional[Dict[str, Any]]:
    """Main pipeline for a single lead."""
    write_log(f"🚀 [v2] Старт photos_v2 для lead_id={lead_id}")

    # Step 1: Read card.json
    card_paths = list(YANDEX_DATA_DIR.glob(f"*-{lead_id}/card.json"))
    if not card_paths:
        write_log(f"❌ card.json не найден для lead_id={lead_id}")
        return None

    card_path = card_paths[0]
    with open(card_path, "r", encoding="utf-8") as f:
        card = json.load(f)

    # Check qualification
    qualification = card.get("qualification", {})
    if not qualification.get("qualified"):
        write_log(f"🚫 Lead not qualified, skipping: {qualification.get('reason', 'unknown')}")
        return None

    slug = card.get("slug", f"lead-{lead_id}")

    # Check cache
    photo_map_path = card_path.parent / "photo_map.json"
    if photo_map_path.exists() and not force:
        import time
        age_sec = time.time() - photo_map_path.stat().st_mtime
        if age_sec < 7 * 24 * 3600:  # 7 days
            # Check schema version
            try:
                with open(photo_map_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("schema_version") == 2:
                    write_log(f"♻️ Использую кеш (age={age_sec/3600:.0f}h)")
                    return cached
                else:
                    write_log(f"🔄 Schema version mismatch (cached={cached.get('schema_version')}, need=2), регенерирую")
            except Exception as exc:
                write_log(f"⚠️ Ошибка чтения кеша: {exc}")

    if dry_run:
        write_log("🔍 DRY-RUN mode: только планирование, без скачиваний и Vision")
        candidates = build_candidate_pool(card)
        unique = deduplicate_candidates(candidates)
        write_log(f"   Кандидатов: {len(candidates)}, после дедупликации: {len(unique)}")
        for c in unique[:5]:
            write_log(f"   - {c.block_hint}: {c.normalized_url} (sources: {c.sources})")
        return None

    # Step 2: Build candidate pool
    candidates = build_candidate_pool(card)
    write_log(f"   Кандидатов из card.json: {len(candidates)}")

    # Step 3: Deduplicate
    unique_candidates = deduplicate_candidates(candidates)
    write_log(f"   После дедупликации: {len(unique_candidates)}")

    # Step 4: Vision rich-profile pass
    write_log(f"   🔍 Анализ Vision для {len(unique_candidates)} фото...")
    sem = asyncio.Semaphore(5)
    tasks = [process_candidate(c, sem) for c in unique_candidates]
    processed = await asyncio.gather(*tasks, return_exceptions=True)

    # Count failures
    vision_failures = sum(1 for r in processed if isinstance(r, Exception))
    if vision_failures > 0:
        write_log(f"   ⚠️ Vision failures: {vision_failures}")

    valid_candidates = [r for r in processed if isinstance(r, PhotoCandidate)]
    write_log(f"   Успешно проанализировано: {len(valid_candidates)}")

    # Step 5: Quality gate
    after_quality = [c for c in valid_candidates if c.profile and not c.profile.get("rejected")]
    write_log(f"   После quality gate: {len(after_quality)}")

    # Step 6: Layout into folders with role-specific fit scores
    blocks, gallery_by_service, hero_text_rejected = lay_out_blocks(after_quality)
    write_log(f"   Раскладка по папкам: { {k: (v['enabled'], len(v['photos'])) for k, v in blocks.items()} }")

    # Step 7: UI overlay check on finalists
    ui_rejected = await verify_finalists_no_ui(blocks)
    ui_overlays_rejected = len(ui_rejected)

    if ui_rejected:
        # Mark rejected candidates and re-run lay_out_blocks
        for c in valid_candidates:
            if c.url in ui_rejected or c.normalized_url in ui_rejected:
                if c.profile is None:
                    c.profile = {}
                c.profile["rejected"] = True
                c.profile["reject_reason"] = "ui_overlay_detected"
        # Re-filter and re-layout
        after_quality = [c for c in valid_candidates if c.profile and not c.profile.get("rejected")]
        blocks, gallery_by_service, hero_text_rejected = lay_out_blocks(after_quality)
        write_log(f"   🔁 Rebuilt blocks after UI rejection ({len(ui_rejected)} photos)")

    # Step 8: Deduplicate across blocks
    blocks, cross_block_duplicates_removed = deduplicate_across_blocks(blocks)
    if cross_block_duplicates_removed > 0:
        write_log(f"   🔁 Removed {cross_block_duplicates_removed} cross-block duplicates")

    # Step 9: Backfill gallery and services
    claimed = {(p["normalized_url"] or p["url"])
               for b in blocks.values() if b.get("enabled")
               for p in (b.get("photos") or [])}

    for block_name in ("services", "gallery"):
        if blocks[block_name].get("enabled"):
            backfill_block(block_name, blocks[block_name], after_quality, claimed, MIN_FIT_SCORE[block_name])

    # Re-evaluate enabled flag (block may now be too small)
    for block_name in blocks:
        photos = blocks[block_name].get("photos") or []
        enabled = len(photos) >= MIN_PHOTOS_FOR_BLOCK[block_name]
        blocks[block_name]["enabled"] = enabled
        if not enabled:
            blocks[block_name]["reason"] = f"after dedup+backfill: only {len(photos)} photos (need {MIN_PHOTOS_FOR_BLOCK[block_name]})"

    # Step 10: Build stats with fit_distribution
    rejected_breakdown = {}
    screenshots_filtered = 0
    face_covered_rejected = 0
    for c in valid_candidates:
        if c.profile and c.profile.get("rejected"):
            reason = c.profile.get("reject_reason", "unknown")
            rejected_breakdown[reason] = rejected_breakdown.get(reason, 0) + 1
            if reason == "screenshot_exif":
                screenshots_filtered += 1
            if reason == "face_covered":
                face_covered_rejected += 1

    fit_distribution = {}
    for block in ("hero", "about", "gallery", "team", "services"):
        threshold = MIN_FIT_SCORE[block]
        available = sum(
            1 for c in after_quality
            if ((c.profile or {}).get("fit_scores") or {}).get(block, {}).get("score", 0) >= threshold
        )
        fit_distribution[block] = {"available": available, "after_gate": 0}
        if block == "team":
            fit_distribution[block]["captioned"] = sum(
                1 for c in after_quality
                if (c.profile or {}).get("text_in_image", {}).get("looks_like_caption_for_person")
            )

    # Calculate marketing_grade_distribution for non-rejected photos
    marketing_grade_distribution = {
        "pro_studio": 0,
        "pro_salon": 0,
        "casual_acceptable": 0,
        "amateur_outdoor": 0,
        "amateur_home": 0,
        "unusable": 0,
    }
    for c in after_quality:
        tier = ((c.profile or {}).get("marketing_grade") or {}).get("tier", "casual_acceptable")
        if tier in marketing_grade_distribution:
            marketing_grade_distribution[tier] += 1

    # Step 8: Download + save metadata
    public_lead_dir = PUBLIC_DIR / f"{slug}-{lead_id}"

    photo_map = {
        "schema_version": 2,
        "lead_id": lead_id,
        "slug": slug,
        "generated_at": datetime.now().isoformat(),
        "qualification": "qualified",
        "stats": {
            "total_candidates": len(candidates),
            "after_dedup": len(unique_candidates),
            "after_quality_gate": len(after_quality),
            "vision_calls": len(unique_candidates),
            "vision_failures": vision_failures,
            "rejected_breakdown": rejected_breakdown,
            "fit_distribution": fit_distribution,
            "gallery_by_service": gallery_by_service,
            "screenshots_filtered": screenshots_filtered,
            "ui_overlays_rejected": ui_overlays_rejected,
            "face_covered_rejected": face_covered_rejected,
            "cross_block_duplicates_removed": cross_block_duplicates_removed,
            "hero_text_rejected": hero_text_rejected,
            "marketing_grade_distribution": marketing_grade_distribution,
        },
        "blocks": blocks,
    }

    # Step 9: Clean block folders before writing
    for block in ("hero", "about", "gallery", "team", "services"):
        removed = clean_block_folder(slug, lead_id, block)
        if removed:
            write_log(f"   🧹 cleaned {block}/: removed {removed} stale files")

    # Step 10: Download photos and save metadata
    for block, block_data in blocks.items():
        if not block_data["enabled"]:
            continue

        for idx, photo_info in enumerate(block_data["photos"]):
            # Find the original candidate
            c = next((cand for cand in after_quality if cand.url == photo_info["url"]), None)
            if not c:
                continue

            # Generate filename (ASCII-only)
            if c.service_ref and block == "services":
                filename = slugify_name(c.service_ref, lead_id)
            else:
                filename = f"photo_{idx + 1}"
            ext = ".jpg"
            full_filename = f"{filename}{ext}"

            # Download
            photo_path = public_lead_dir / block / full_filename
            if not dry_run:
                if download_photo(c.url, photo_path):
                    # Save profile metadata
                    profile_path = photo_path.with_suffix(".json")
                    with open(profile_path, "w", encoding="utf-8") as f:
                        json.dump(c.profile, f, ensure_ascii=False, indent=2)
                else:
                    write_log(f"   ⚠️ Не удалось скачать: {c.url}")

    # Update fit_distribution with final counts
    for block in blocks:
        fit_distribution[block]["after_gate"] = len(blocks[block]["photos"])

    photo_map["stats"]["fit_distribution"] = fit_distribution

    # Step 10: Save photo_map.json
    with open(photo_map_path, "w", encoding="utf-8") as f:
        json.dump(photo_map, f, ensure_ascii=False, indent=2)
    write_log(f"✅ photo_map.json сохранён: {photo_map_path}")

    return photo_map


def main():
    parser = argparse.ArgumentParser(description="Yandex photos v2 (Vision rich profile)")
    parser.add_argument("lead_ids", nargs="+", type=int)
    parser.add_argument("--force", action="store_true", help="ignore cache, re-call Vision")
    parser.add_argument("--dry-run", action="store_true", help="no downloads, no Vision calls — show plan only")
    parser.add_argument("--skip-secondary", action="store_true", help="skip VK/website sources, use only card.json")
    args = parser.parse_args()

    for lead_id in args.lead_ids:
        try:
            result = asyncio.run(process_lead(lead_id, force=args.force, dry_run=args.dry_run, skip_secondary=args.skip_secondary))
            if result:
                print(f"OK: lead_id={lead_id}")
            else:
                print(f"SKIP: lead_id={lead_id}")
        except Exception as exc:
            print(f"ERROR lead_id={lead_id}: {exc}")


if __name__ == "__main__":
    main()
