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
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from enrichment.yandex import slugify_name, write_log
from llm.curator import analyze_photo_rich

BASE_DIR = Path(__file__).parent.parent
YANDEX_DATA_DIR = BASE_DIR / "data" / "yandex"
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

FOLDER_LIMITS = {
    "hero": 1,
    "gallery": 6,
    "about": 2,
    "services": 8,
    "team": 6,
}

MIN_BLOCK_THRESHOLDS = {
    "hero": 1,
    "gallery": 3,
    "about": 1,
    "services": 1,
    "team": 0,
}

MIN_QUALITY = 4


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
        candidate.profile = await vision_with_backoff(candidate.normalized_url, candidate.block_hint)
        return candidate


def build_candidate_pool(card: Dict[str, Any]) -> List[PhotoCandidate]:
    """Build candidate pool from card.json aspects and services."""
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

    return candidates


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


def layout_into_folders(candidates: List[PhotoCandidate]) -> Dict[str, List[PhotoCandidate]]:
    """Layout candidates into folders based on Vision profiles."""
    layout = {block: [] for block in FOLDER_LIMITS.keys()}

    for c in candidates:
        if not c.profile:
            continue

        profile = c.profile
        if profile.get("rejected") or profile.get("quality", {}).get("score", 0) < MIN_QUALITY:
            continue

        # Check each block
        for block in FOLDER_LIMITS.keys():
            marketing = profile.get("marketing", {})
            usable_key = f"usable_in_{block}"
            
            # Explicit usable_in flag
            if marketing.get(usable_key):
                layout[block].append(c)
            # Fallback: block matches hint and no explicit flags
            elif c.block_hint == block and not any(marketing.get(f"usable_in_{b}") for b in FOLDER_LIMITS):
                layout[block].append(c)

    # Sort by quality score and take top N
    for block in layout:
        layout[block].sort(
            key=lambda x: x.profile.get("quality", {}).get("score", 0),
            reverse=True
        )
        layout[block] = layout[block][:FOLDER_LIMITS[block]]

    return layout


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
            write_log(f"♻️ Использую кеш photo_map.json (age={age_sec/3600:.0f}h)")
            with open(photo_map_path, "r", encoding="utf-8") as f:
                return json.load(f)

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

    # Step 6: Layout into folders
    layout = layout_into_folders(after_quality)
    write_log(f"   Раскладка по папкам: { {k: len(v) for k, v in layout.items()} }")

    # Step 7: Download + save metadata
    public_lead_dir = PUBLIC_DIR / f"{slug}-{lead_id}"
    rejected_counts = {}

    photo_map = {
        "schema_version": 1,
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
            "rejected": {},
        },
        "blocks": {block: {"enabled": False, "photos": []} for block in FOLDER_LIMITS.keys()},
    }

    for block, block_candidates in layout.items():
        block_enabled = len(block_candidates) >= MIN_BLOCK_THRESHOLDS[block]
        block_data = photo_map["blocks"][block]
        block_data["enabled"] = block_enabled
        block_data["photos"] = []

        if not block_enabled:
            block_data["reason"] = f"below threshold ({len(block_candidates)} < {MIN_BLOCK_THRESHOLDS[block]})"
            continue

        for idx, c in enumerate(block_candidates):
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

                    # Add to photo_map
                    block_data["photos"].append({
                        "path": f"/{slug}-{lead_id}/{block}/{full_filename}",
                        "alt": c.profile.get("content", {}).get("alt_text", ""),
                        "score": c.profile.get("quality", {}).get("score", 0),
                    })
                else:
                    write_log(f"   ⚠️ Не удалось скачать: {c.url}")

    # Count rejections
    for c in valid_candidates:
        if c.profile and c.profile.get("rejected"):
            reason = c.profile.get("reject_reason", "unknown")
            photo_map["stats"]["rejected"][reason] = photo_map["stats"]["rejected"].get(reason, 0) + 1

    # Step 8: Save photo_map.json
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
