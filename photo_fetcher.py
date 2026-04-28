#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import json
import os
import random
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from gemma_curator import analyze_image_url

load_dotenv()

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "leads.db"
YANDEX_DIR = BASE_DIR / "data" / "yandex"
EXTRACTED_DIR = BASE_DIR / "data" / "extracted"
PUBLIC_DIR = BASE_DIR / "public"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
]

FOLDER_LIMITS = {"hero": 1, "gallery": 10, "team": 5, "about": 3, "services": 8}
MIN_SCORE_DOWNLOAD = 5
MIN_SCORE_HERO = 7

SIZE_SUFFIX_RE = re.compile(r"/(S|M|L|XL|XXL|M_height|L_height|XL_height|XXL_height|orig)$", re.IGNORECASE)
SKIP_URL_MARKERS = ("icon", "logo", "sprite", "pixel", "1x1", "favicon")
VK_API_VERSION = "5.199"


@dataclass
class PhotoCandidate:
    url: str
    preview_url: str
    source: str
    score: int = 0
    best_block: str = ""
    photo_type: str = ""
    orientation: str = ""
    has_person: bool = False
    has_logo_watermark: bool = False
    quality: int = 0
    reason: str = ""
    master_name: str = ""


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{timestamp()}] {message}", flush=True)


def choose_user_agent() -> str:
    return random.choice(USER_AGENTS)


def sync_pause(min_seconds: float, max_seconds: float, reason: str) -> None:
    seconds = random.uniform(min_seconds, max_seconds)
    log(f"⏳ Пауза {seconds:.1f} сек ({reason})")
    time.sleep(seconds)


async def async_pause(min_seconds: float, max_seconds: float, reason: str) -> None:
    seconds = random.uniform(min_seconds, max_seconds)
    log(f"⏳ Пауза {seconds:.1f} сек ({reason})")
    await asyncio.sleep(seconds)


def slugify_name(name: str, lead_id: int) -> str:
    translit_map = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    lower = (name or "").strip().lower()
    translit = "".join(translit_map.get(ch, ch) for ch in lower)
    translit = re.sub(r"[^a-z0-9]+", "-", translit)
    translit = re.sub(r"-+", "-", translit).strip("-")
    return translit or f"lead-{lead_id}"


def load_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    if not DB_PATH.exists():
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row) if row else None


def read_json_if_exists(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def normalize_yandex_photo_url(url: str, size: str) -> str:
    if not isinstance(url, str):
        return ""
    value = url.strip()
    if not value:
        return ""
    if SIZE_SUFFIX_RE.search(value):
        return SIZE_SUFFIX_RE.sub(f"/{size}", value)
    return value.rstrip("/") + f"/{size}"


def strip_size_suffix(url: str) -> str:
    base = re.sub(r"[#?].*$", "", (url or "").strip())
    base = SIZE_SUFFIX_RE.sub("", base)
    return base.rstrip("/")


def guess_extension_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ext
    return ".jpg"


def _clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        return low in {"1", "true", "yes", "да"}
    return False


def score_photo(preview_url: str) -> Dict[str, Any]:
    prompt = """Оцени фотографию для сайта салона красоты.
Верни СТРОГО JSON:
{
  "score": 0-10,
  "best_block": "hero" или "gallery" или "team" или "about" или "services" или "skip",
  "photo_type": "master_at_work" или "work_result" или "portrait" или "interior" или "exterior" или "product" или "logo_qr" или "other",
  "orientation": "vertical" или "horizontal" или "square",
  "has_person": true или false,
  "has_logo_watermark": true или false,
  "quality": 1-10,
  "reason": "одна строка на русском"
}
Правила:
- hero: вертикальное, мастер/клиент, нет логотипов, score 7-10
- gallery: результат работы (маникюр, стрижка, брови, макияж), score 5-10
- team: портрет одного мастера, лицо хорошо видно
- about: интерьер/атмосфера, горизонталь
- services: крупный план услуги без лица
- skip: логотипы, QR, скриншоты, низкое качество score<3
"""
    parsed = analyze_image_url(preview_url, prompt)
    if not isinstance(parsed, dict):
        return {
            "score": 0,
            "best_block": "skip",
            "photo_type": "other",
            "orientation": "square",
            "has_person": False,
            "has_logo_watermark": False,
            "quality": 0,
            "reason": "error",
        }

    try:
        score = int(parsed.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(10, score))

    best_block = str(parsed.get("best_block", "skip") or "skip").strip().lower()
    if best_block not in {"hero", "gallery", "team", "about", "services", "skip"}:
        best_block = "skip"

    photo_type = str(parsed.get("photo_type", "other") or "other").strip().lower()
    if photo_type not in {"master_at_work", "work_result", "portrait", "interior", "exterior", "product", "logo_qr", "other"}:
        photo_type = "other"

    orientation = str(parsed.get("orientation", "square") or "square").strip().lower()
    if orientation not in {"vertical", "horizontal", "square"}:
        orientation = "square"

    try:
        quality = int(parsed.get("quality", 0))
    except (TypeError, ValueError):
        quality = 0
    quality = max(0, min(10, quality))

    reason = re.sub(r"\s+", " ", str(parsed.get("reason", "") or "").strip()) or "no_reason"

    return {
        "score": score,
        "best_block": best_block,
        "photo_type": photo_type,
        "orientation": orientation,
        "has_person": _clean_bool(parsed.get("has_person", False)),
        "has_logo_watermark": _clean_bool(parsed.get("has_logo_watermark", False)),
        "quality": quality,
        "reason": reason,
    }


async def collect_from_site(website_url: str) -> List[PhotoCandidate]:
    if not website_url:
        return []

    user_agent = choose_user_agent()
    log(f"🌐 Site scrape: {website_url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = await context.new_page()
        try:
            await page.goto(website_url, wait_until="domcontentloaded", timeout=40000)
            await page.wait_for_timeout(2200)

            for _ in range(8):
                await page.mouse.wheel(0, random.randint(1000, 2200))
                await page.wait_for_timeout(random.randint(700, 1400))

            raw_urls = await page.evaluate(
                r"""
                () => {
                  const out = [];
                  const add = (v) => {
                    if (!v || typeof v !== 'string') return;
                    const x = v.trim();
                    if (!x) return;
                    out.push(x);
                  };

                  document.querySelectorAll('img').forEach((img) => {
                    add(img.getAttribute('src'));
                    add(img.getAttribute('data-src'));
                    add(img.getAttribute('data-lazy'));
                    add(img.getAttribute('data-lazy-src'));
                    add(img.getAttribute('data-original'));
                  });

                  document.querySelectorAll('*').forEach((el) => {
                    const styleAttr = el.getAttribute('style') || '';
                    const attrMatches = styleAttr.match(/background-image\s*:\s*url\(([^)]+)\)/gi) || [];
                    for (const m of attrMatches) {
                      const n = m.match(/url\(([^)]+)\)/i);
                      if (n && n[1]) add(n[1].replace(/^['\"]|['\"]$/g, ''));
                    }

                    const bg = window.getComputedStyle(el).backgroundImage || '';
                    const compMatches = bg.match(/url\(([^)]+)\)/gi) || [];
                    for (const m of compMatches) {
                      const n = m.match(/url\(([^)]+)\)/i);
                      if (n && n[1]) add(n[1].replace(/^['\"]|['\"]$/g, ''));
                    }
                  });

                  return Array.from(new Set(out));
                }
                """
            )
        finally:
            await context.close()
            await browser.close()

    out: List[PhotoCandidate] = []
    for raw in raw_urls or []:
        full = urljoin(website_url, str(raw))
        low = full.lower()
        if any(marker in low for marker in SKIP_URL_MARKERS):
            continue
        if not low.startswith(("http://", "https://")):
            continue
        out.append(PhotoCandidate(url=full, preview_url=full, source="site"))

    log(f"📦 Site candidates: {len(out)}")
    return out


def collect_from_yandex(slug: str) -> List[PhotoCandidate]:
    path = YANDEX_DIR / f"{slug}.json"
    payload = read_json_if_exists(path) or {}
    yandex = payload.get("yandex") if isinstance(payload.get("yandex"), dict) else {}
    photos = yandex.get("photos") if isinstance(yandex.get("photos"), list) else []

    out: List[PhotoCandidate] = []
    for item in photos:
        if not isinstance(item, str):
            continue
        original = normalize_yandex_photo_url(item, "XXL_height")
        preview = normalize_yandex_photo_url(item, "M_height")
        if original and preview:
            out.append(PhotoCandidate(url=original, preview_url=preview, source="yandex"))

    log(f"📦 Yandex candidates: {len(out)}")
    return out


def collect_team_from_extracted(slug: str) -> List[PhotoCandidate]:
    path = EXTRACTED_DIR / f"{slug}.json"
    payload = read_json_if_exists(path) or {}
    team = payload.get("team") if isinstance(payload.get("team"), list) else []

    out: List[PhotoCandidate] = []
    for item in team:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        photo_url = str(item.get("photo_url") or "").strip()
        if not name or not photo_url:
            continue
        if not photo_url.lower().startswith(("http://", "https://")):
            continue
        out.append(
            PhotoCandidate(
                url=photo_url,
                preview_url=photo_url,
                source="site_team",
                best_block="team",
                score=9,
                photo_type="portrait",
                master_name=slugify_name(name, 0),
            )
        )

    log(f"📦 Extracted team candidates: {len(out)}")
    return out


def _vk_api_call(method: str, params: Dict[str, Any], token: str) -> Optional[Dict[str, Any]]:
    api_url = f"https://api.vk.com/method/{method}"
    q = dict(params)
    q["access_token"] = token
    q["v"] = VK_API_VERSION

    try:
        response = requests.get(api_url, params=q, timeout=25)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        log(f"⚠️ VK API error {method}: {exc}")
        return None

    if "error" in data:
        log(f"⚠️ VK API method={method} error={data['error']}")
        return None
    return data.get("response") if isinstance(data.get("response"), dict) else None


def _extract_vk_owner_id(vk_url: str, token: str) -> Optional[int]:
    if not vk_url:
        return None

    normalized = vk_url.strip().rstrip("/")
    screen_name = normalized.split("/")[-1]
    if not screen_name:
        return None

    if screen_name.startswith("public") and screen_name[6:].isdigit():
        return -int(screen_name[6:])
    if screen_name.startswith("club") and screen_name[4:].isdigit():
        return -int(screen_name[4:])
    if screen_name.startswith("id") and screen_name[2:].isdigit():
        return int(screen_name[2:])

    response = _vk_api_call("utils.resolveScreenName", {"screen_name": screen_name}, token)
    if not response:
        return None

    obj_type = str(response.get("type") or "")
    obj_id = response.get("object_id")
    if not isinstance(obj_id, int):
        return None

    if obj_type in {"group", "page", "event"}:
        return -obj_id
    return obj_id


def _pick_vk_size(sizes: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    usable = [s for s in sizes if isinstance(s, dict) and isinstance(s.get("url"), str)]
    if not usable:
        return None

    def area(item: Dict[str, Any]) -> int:
        w = item.get("width") or 0
        h = item.get("height") or 0
        if isinstance(w, int) and isinstance(h, int):
            return w * h
        return 0

    usable.sort(key=area)
    preview_idx = 1 if len(usable) > 1 else 0
    preview = usable[preview_idx].get("url", "")
    original = usable[-1].get("url", "")
    if not preview or not original:
        return None
    return {"preview": preview, "original": original}


def collect_from_vk(vk_url: str) -> List[PhotoCandidate]:
    token = os.getenv("VK_ACCESS_TOKEN", "").strip()
    if not token:
        log("⚠️ VK_ACCESS_TOKEN не задан — источник VK пропущен")
        return []

    owner_id = _extract_vk_owner_id(vk_url, token)
    if owner_id is None:
        log("⚠️ Не удалось определить owner_id VK")
        return []

    albums_resp = _vk_api_call("photos.getAlbums", {"owner_id": owner_id, "need_system": 1, "count": 100}, token)
    if not albums_resp:
        return []

    albums = albums_resp.get("items") if isinstance(albums_resp.get("items"), list) else []
    albums = albums[:10]

    out: List[PhotoCandidate] = []
    for album in albums:
        if len(out) >= 100:
            break
        if not isinstance(album, dict):
            continue
        album_id = album.get("id")
        if not isinstance(album_id, int):
            continue

        photos_resp = _vk_api_call(
            "photos.get",
            {
                "owner_id": owner_id,
                "album_id": album_id,
                "count": 50,
                "photo_sizes": 1,
                "rev": 1,
            },
            token,
        )
        sync_pause(0.4, 0.8, "между VK API запросами")

        if not photos_resp:
            continue

        items = photos_resp.get("items") if isinstance(photos_resp.get("items"), list) else []
        for photo in items:
            if len(out) >= 100:
                break
            if not isinstance(photo, dict):
                continue
            sizes = photo.get("sizes") if isinstance(photo.get("sizes"), list) else []
            picked = _pick_vk_size(sizes)
            if not picked:
                continue
            out.append(
                PhotoCandidate(
                    url=picked["original"],
                    preview_url=picked["preview"],
                    source="vk",
                )
            )

    log(f"📦 VK candidates: {len(out)}")
    return out


def deduplicate_candidates(candidates: List[PhotoCandidate]) -> List[PhotoCandidate]:
    seen = set()
    out: List[PhotoCandidate] = []
    for c in candidates:
        key = strip_size_suffix(c.url)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    log(f"🧹 После дедупликации: {len(out)}")
    return out


def evaluate_candidates(candidates: List[PhotoCandidate]) -> List[PhotoCandidate]:
    for idx, c in enumerate(candidates, start=1):
        if c.source == "site_team":
            log(
                f"🔍 [{idx}/{len(candidates)}] {c.source} score={c.score} block={c.best_block} "
                f"type={c.photo_type} person={c.has_person} quality={c.quality}"
            )
            continue
        result = score_photo(c.preview_url)
        c.score = int(result.get("score", 0) or 0)
        c.best_block = str(result.get("best_block", "skip") or "skip")
        c.photo_type = str(result.get("photo_type", "other") or "other")
        c.orientation = str(result.get("orientation", "square") or "square")
        c.has_person = bool(result.get("has_person", False))
        c.has_logo_watermark = bool(result.get("has_logo_watermark", False))
        c.quality = int(result.get("quality", 0) or 0)
        c.reason = str(result.get("reason", "") or "")

        log(
            f"🔍 [{idx}/{len(candidates)}] {c.source} score={c.score} block={c.best_block} "
            f"type={c.photo_type} person={c.has_person} quality={c.quality}"
        )
        sync_pause(0.5, 1.0, "между Gemini Vision оценками")
    return candidates


def _download_to_path(url: str, path: Path) -> bool:
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(response.content)
        return True
    except Exception as exc:
        log(f"⚠️ Ошибка скачивания {url}: {exc}")
        return False


def distribute_and_download(candidates: List[PhotoCandidate], slug: str, dry_run: bool = False) -> Dict[str, List[str]]:
    photo_map: Dict[str, List[str]] = {k: [] for k in FOLDER_LIMITS.keys()}

    for c in candidates:
        if c.source == "yandex" and c.best_block == "team":
            c.best_block = "gallery" if c.score >= MIN_SCORE_DOWNLOAD else "skip"

    eligible = [c for c in candidates if c.best_block != "skip" and c.score >= MIN_SCORE_DOWNLOAD]
    if not eligible:
        return photo_map

    sorted_all = sorted(eligible, key=lambda x: (x.score, x.quality), reverse=True)

    used_keys = set()

    hero_pool = [c for c in sorted_all if c.score >= MIN_SCORE_HERO]
    if hero_pool:
        hero = hero_pool[0]
        ext = guess_extension_from_url(hero.url)
        filename = f"hero_1{ext}"
        rel = f"/{slug}/hero/{filename}"
        out_path = PUBLIC_DIR / slug / "hero" / filename
        if dry_run or _download_to_path(hero.url, out_path):
            photo_map["hero"].append(rel)
            used_keys.add(strip_size_suffix(hero.url))
            if not dry_run:
                sync_pause(0.3, 0.7, "между скачиваниями")

    for folder, limit in FOLDER_LIMITS.items():
        if folder == "hero":
            continue
        pool = [c for c in sorted_all if c.best_block == folder and strip_size_suffix(c.url) not in used_keys]
        pool = pool[:limit]

        for idx, c in enumerate(pool, start=1):
            ext = guess_extension_from_url(c.url)
            if folder == "team" and c.source == "site_team" and c.master_name:
                filename = f"team_{c.master_name}.jpg"
            elif folder == "team":
                filename = f"team_{idx}.jpg"
            else:
                filename = f"{folder}_{idx}{ext}"
            rel = f"/{slug}/{folder}/{filename}"
            out_path = PUBLIC_DIR / slug / folder / filename

            if dry_run or _download_to_path(c.url, out_path):
                photo_map[folder].append(rel)
                used_keys.add(strip_size_suffix(c.url))
                if not dry_run:
                    sync_pause(0.3, 0.7, "между скачиваниями")

    return photo_map


def update_extracted_json(slug: str, photo_map: Dict[str, List[str]]) -> None:
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    path = EXTRACTED_DIR / f"{slug}.json"
    payload = read_json_if_exists(path) or {}
    payload["photos_by_block"] = photo_map

    team_photos: List[Dict[str, str]] = []
    team_paths = photo_map.get("team") if isinstance(photo_map.get("team"), list) else []
    path_by_master_slug: Dict[str, str] = {}
    for rel in team_paths:
        if not isinstance(rel, str):
            continue
        filename = Path(rel).name
        stem = Path(filename).stem
        if not stem.startswith("team_"):
            continue
        master_slug = stem[len("team_") :].strip()
        if master_slug:
            path_by_master_slug[master_slug] = rel

    team_raw = payload.get("team") if isinstance(payload.get("team"), list) else []
    for item in team_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        master_slug = slugify_name(name, 0)
        photo_rel = path_by_master_slug.get(master_slug)
        if photo_rel:
            team_photos.append({"name": name, "photo": photo_rel})

    payload["team_photos"] = team_photos
    payload["photos_fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    log(f"💾 Обновлён extracted: {path}")


def resolve_vk_from_yandex(slug: str) -> str:
    path = YANDEX_DIR / f"{slug}.json"
    payload = read_json_if_exists(path) or {}
    yandex = payload.get("yandex") if isinstance(payload.get("yandex"), dict) else {}
    social_links = yandex.get("social_links") if isinstance(yandex.get("social_links"), dict) else {}
    vk_list = social_links.get("vk") if isinstance(social_links.get("vk"), list) else []
    if vk_list and isinstance(vk_list[0], str):
        return vk_list[0].strip()
    return ""


async def fetch_photos(
    lead_id: int,
    sources: List[str] = ["site", "yandex", "vk"],
    dry_run: bool = False,
) -> Dict[str, Any]:
    lead = load_lead(lead_id)
    if not lead:
        raise RuntimeError(f"Лид ID={lead_id} не найден")

    lead_name = str(lead.get("name") or f"Lead {lead_id}")
    slug = slugify_name(lead_name, lead_id)
    website = str(lead.get("website") or "").strip()
    vk_url = str(lead.get("vk_url") or "").strip()
    if not vk_url:
        vk_url = resolve_vk_from_yandex(slug)

    collected: List[PhotoCandidate] = []

    if "site" in sources:
        if website:
            collected.extend(await collect_from_site(website))
            await async_pause(0.8, 1.5, "между источниками")
        else:
            log("⚠️ Website отсутствует — source=site пропущен")

    if "yandex" in sources:
        collected.extend(collect_from_yandex(slug))
        await async_pause(0.8, 1.5, "между источниками")

    if "vk" in sources:
        if vk_url:
            collected.extend(collect_from_vk(vk_url))
        else:
            log("⚠️ VK URL отсутствует — source=vk пропущен")

    collected = collect_team_from_extracted(slug) + collected

    deduped = deduplicate_candidates(collected)
    evaluated = evaluate_candidates(deduped)
    photo_map = distribute_and_download(evaluated, slug=slug, dry_run=dry_run)

    if not dry_run:
        update_extracted_json(slug, photo_map)

    result = {
        "lead_id": lead_id,
        "slug": slug,
        "dry_run": dry_run,
        "sources": sources,
        "total_collected": len(collected),
        "total_deduplicated": len(deduped),
        "photo_map": photo_map,
    }
    return result


def parse_sources(source_arg: str) -> List[str]:
    value = (source_arg or "all").strip().lower()
    if value == "all":
        return ["site", "yandex", "vk"]
    if value in {"site", "yandex", "vk"}:
        return [value]
    raise ValueError("--source должен быть: site | yandex | vk | all")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart photo fetcher for beauty salon websites")
    parser.add_argument("lead_id", type=int, help="Lead ID")
    parser.add_argument("--dry-run", action="store_true", help="Оценка и распределение без скачивания и записи JSON")
    parser.add_argument("--source", default="all", help="site|vk|yandex|all")
    args = parser.parse_args()

    try:
        sources = parse_sources(args.source)
        result = asyncio.run(fetch_photos(args.lead_id, sources=sources, dry_run=args.dry_run))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        log(f"❌ Ошибка: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
