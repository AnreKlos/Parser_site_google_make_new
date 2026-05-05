#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

# Добавляем корень проекта в путь для импортов (ДО остальных импортов!)
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
import httpx
from urllib.parse import quote_plus, urljoin
import aiohttp

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from sqlalchemy import select

from db.database import get_async_session
from db.models import Lead

BASE_DIR = Path(__file__).resolve().parent.parent
YANDEX_DATA_DIR = BASE_DIR / "data" / "yandex"
PUBLIC_DIR = Path(r"D:\2 Clode Proj\1\neuralsync\src\public")
LOG_PATH = YANDEX_DATA_DIR / "scrape_log.txt"
CAPTCHA_LOG_PATH = YANDEX_DATA_DIR / "captcha_log.txt"
SESSION_STATE_PATH = YANDEX_DATA_DIR / ".session.json"
LAST_RUNS_PATH = YANDEX_DATA_DIR / ".last_live_runs.json"
MAX_LEADS_PER_RUN = 30


async def geocode_address(address: str, url: str = "") -> Optional[Dict[str, float]]:
    """Geocode address by extracting from Yandex Maps URL as fallback.
    
    Args:
        address: Address string (e.g., "Брянск, Московский просп., 10/11")
        url: Yandex Maps URL (e.g., "https://yandex.ru/maps/org/mood/96195832006/")
    
    Returns:
        Dict with lat/lng or None if geocoding fails
    """
    try:
        # Try to extract coordinates from the Yandex Maps URL
        # Yandex Maps URLs have coordinates in format: ll=lat,lng
        if url:
            # Parse URL for ll=lat,lng parameter
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            
            # Check for ll parameter
            if 'll' in query_params:
                ll_str = query_params['ll'][0]
                parts = ll_str.split(',')
                if len(parts) == 2:
                    lat = float(parts[1])
                    lng = float(parts[0])
                    return {"lat": lat, "lng": lng}
            
            # Check for z and ll in path (alternative format)
            # e.g., /maps/?ll=lat,lng&z=15
            if 'll' in parsed.fragment:
                ll_str = parsed.fragment.split('ll=')[1].split('&')[0]
                parts = ll_str.split(',')
                if len(parts) == 2:
                    lat = float(parts[1])
                    lng = float(parts[0])
                    return {"lat": lat, "lng": lng}
        
        return None
    except Exception as e:
        write_log(f"⚠️ Geocoding failed for '{address}': {e}")
        return None
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def safe_print(text: str) -> None:
    try:
        print(text, flush=True)
    except (UnicodeEncodeError, UnicodeError):
        print(text.encode("cp1251", errors="replace").decode("cp1251"), flush=True)


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_data_dirs() -> None:
    YANDEX_DATA_DIR.mkdir(parents=True, exist_ok=True)


def write_log(message: str) -> None:
    ensure_data_dirs()
    line = f"[{timestamp()}] {message}"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    safe_print(line)


def write_captcha_log(lead_id: Optional[int], url: str) -> None:
    ensure_data_dirs()
    line = f"[{timestamp()}] lead_id={lead_id if lead_id is not None else '-'} url={url or '-'}"
    with open(CAPTCHA_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def choose_user_agent() -> str:
    return random.choice(USER_AGENTS)


def get_context_kwargs(user_agent: str) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "user_agent": user_agent,
        "viewport": {"width": 1920, "height": 1080},
        "locale": "ru-RU",
    }
    if SESSION_STATE_PATH.exists():
        kwargs["storage_state"] = str(SESSION_STATE_PATH)
    return kwargs


async def save_session_state(context) -> None:
    try:
        ensure_data_dirs()
        await context.storage_state(path=str(SESSION_STATE_PATH))
        write_log(f"💾 Сессия сохранена: {SESSION_STATE_PATH}")
    except Exception as exc:
        write_log(f"⚠️ Не удалось сохранить session state: {exc}")


def load_last_live_runs() -> Dict[str, float]:
    if not LAST_RUNS_PATH.exists():
        return {}
    try:
        with open(LAST_RUNS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            out: Dict[str, float] = {}
            for k, v in raw.items():
                try:
                    out[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            return out
    except Exception:
        return {}
    return {}


def save_last_live_runs(data: Dict[str, float]) -> None:
    ensure_data_dirs()
    with open(LAST_RUNS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def mark_live_run(lead_id: int) -> None:
    data = load_last_live_runs()
    data[str(lead_id)] = time.time()
    save_last_live_runs(data)


def get_minutes_since_last_live_run(lead_id: int) -> Optional[int]:
    data = load_last_live_runs()
    last_ts = data.get(str(lead_id))
    if not last_ts:
        return None
    elapsed = max(0, int((time.time() - float(last_ts)) // 60))
    return elapsed


def get_cache_age_seconds(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    return max(0.0, time.time() - path.stat().st_mtime)


def read_cached_payload(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        write_log(f"⚠️ Не удалось прочитать кеш {path}: {exc}")
        return None


def summarize_result_from_payload(payload: Dict[str, Any], output_path: Path, cached: bool) -> Dict[str, Any]:
    yandex = payload.get("yandex") if isinstance(payload.get("yandex"), dict) else {}
    reviews_list = yandex.get("reviews_list") if isinstance(yandex.get("reviews_list"), list) else []
    reviews_list = reviews_list[:30]  # берём только последние 30
    photos = yandex.get("photos") if isinstance(yandex.get("photos"), list) else []
    return {
        "lead_id": payload.get("lead_id"),
        "slug": output_path.stem,
        "output_path": str(output_path),
        "reviews_total": int(payload.get("merged_reviews_count") or len(reviews_list)),
        "yandex_reviews": len(reviews_list),
        "photos": len(photos),
        "rating": yandex.get("rating"),
        "cached": cached,
    }


def random_pause(min_seconds: int, max_seconds: int, reason: str = "") -> None:
    duration = random.uniform(min_seconds, max_seconds)
    label = f" ({reason})" if reason else ""
    write_log(f"⏳ Пауза {duration:.1f} сек{label}")
    time.sleep(duration)


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


def detect_city(address: Optional[str]) -> str:
    if not address:
        return ""
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    if not parts:
        return ""

    street_markers = (
        "ул", "улица", "пр-т", "проспект", "пер", "переулок", "шоссе", "б-р", "бул", "наб", "дом", "д.",
    )
    for part in parts:
        cleaned = re.sub(r"^г\.?\s*", "", part, flags=re.IGNORECASE).strip()
        low = cleaned.lower()
        if not cleaned:
            continue
        if any(low.startswith(m) for m in street_markers):
            continue
        if re.search(r"\d", low):
            continue
        return cleaned

    first = re.sub(r"^г\.?\s*", "", parts[0], flags=re.IGNORECASE).strip()
    if first and not re.match(r"^\d", first):
        return first
    return ""


def extract_street_keyword(address: Optional[str]) -> str:
    if not address:
        return ""

    marker_pattern = (
        r"(?:ул\.?|улица|пр\.?-?т|проспект|пер\.?|переулок|шоссе|"
        r"б-р|бул(?:ьвар)?|наб\.?|набережная|пл\.?|площадь)"
    )
    parts = [p.strip() for p in str(address).split(",") if p.strip()]

    for part in parts:
        if not re.search(marker_pattern, part, flags=re.IGNORECASE):
            continue
        tail = re.sub(marker_pattern, "", part, count=1, flags=re.IGNORECASE).strip(" .,-")
        words = re.findall(r"[A-Za-zА-Яа-яЁё-]+", tail)
        for word in words:
            token = word.strip("-").lower()
            if len(token) < 3:
                continue
            return token
    return ""


def extract_house_number(address: Optional[str]) -> str:
    if not address:
        return ""
    text = str(address)
    explicit = re.search(r"(?:\bд\.?\s*|\bдом\s+)(\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?)", text, flags=re.IGNORECASE)
    if explicit:
        return explicit.group(1).lower()

    fallback = re.search(r"\b(\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?)\b", text, flags=re.IGNORECASE)
    return fallback.group(1).lower() if fallback else ""


def normalize_name(text: str) -> str:
    value = (text or "").lower().strip()
    value = re.sub(r"[^\w\sа-яё]", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def fuzzy_match_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


BEAUTY_KEYWORDS = {
    "салон", "студия красоты", "парикмахерская", "барбершоп",
    "маникюр", "педикюр", "косметология", "брови", "ресницы",
    "окрашивание", "стрижка", "укладка", "макияж", "волосы",
    "эпиляция", "депиляция", "наращивание", "ламинирование",
    "кератин", "ботокс", "филлеры", "чистка", "пилинг",
    "массаж", "спа", "wellness", "солярий", "загар"
}

NON_BEAUTY_KEYWORDS = {
    "стоматология", "клиника", "больница", "аптека", "ресторан",
    "кафе", "бар", "паб", "магазин", "супермаркет", "рынок",
    "авто", "сервис", "ремонт", "стройка", "недвижимость",
    "юридический", "адвокат", "нотариус", "бухгалтер", "аудит"
}


def calculate_category_match(text: str) -> float:
    """Calculate category match score based on beauty-related keywords."""
    if not text:
        return 0.5  # Neutral if no text
    
    text_lower = text.lower()
    
    # Check for non-beauty keywords (strong negative signal)
    for keyword in NON_BEAUTY_KEYWORDS:
        if keyword in text_lower:
            return 0.0
    
    # Check for beauty keywords (positive signal)
    beauty_count = sum(1 for keyword in BEAUTY_KEYWORDS if keyword in text_lower)
    
    if beauty_count >= 2:
        return 1.0
    elif beauty_count == 1:
        return 0.8
    else:
        return 0.5  # Neutral


def address_contains_city(card_address: str, city: str) -> bool:
    card_norm = normalize_name(card_address)
    city_norm = normalize_name(city)
    return bool(card_norm and city_norm and city_norm in card_norm)


def address_contains_street(card_address: str, street_keyword: str) -> bool:
    card_norm = normalize_name(card_address)
    street_norm = normalize_name(street_keyword)
    return bool(card_norm and street_norm and street_norm in card_norm)


def address_contains_house(card_address: str, house_number: str) -> bool:
    if not card_address or not house_number:
        return False
    card_compact = re.sub(r"\s+", "", card_address.lower())
    house_compact = re.sub(r"\s+", "", house_number.lower())
    return bool(house_compact and house_compact in card_compact)


async def has_captcha_signals(page) -> bool:
    url = (page.url or "").lower()
    if "showcaptcha" in url or "captcha" in url:
        return True
    try:
        body_text = (await page.inner_text("body") or "").lower()
    except Exception:
        body_text = ""
    markers = ["капча", "подтвердите, что вы не робот", "я не робот", "captcha"]
    return any(marker in body_text for marker in markers)


def parse_float(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"(\d+[\.,]?\d*)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def parse_russian_date(text: str) -> Optional[str]:
    """Парсит русскую дату в YYYY-MM-DD."""
    if not text:
        return None
    text = text.strip()
    
    # Месяцы на русском
    months = {
        "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
        "мая": "05", "июня": "06", "июля": "07", "августа": "08",
        "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12"
    }
    
    # Формат: "21 июля 2025"
    match = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", text, flags=re.IGNORECASE)
    if match:
        day, month, year = match.groups()
        month_num = months.get(month.lower())
        if month_num:
            return f"{year}-{month_num}-{day.zfill(2)}"
    
    # Формат: "21.07.2025" или "21/07/2025"
    match = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    # Формат: "2025-07-21" (ISO)
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        return match.group(0)
    
    return None


def parse_int(text: str) -> Optional[int]:
    if not text:
        return None
    match = re.search(r"(\d[\d\s\u00A0]*)", text)
    if not match:
        return None
    value = re.sub(r"\D", "", match.group(1))
    return int(value) if value else None


def dedupe_reviews(reviews: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen_by_text: Dict[str, int] = {}
    result: List[Dict[str, Any]] = []
    for review in reviews:
        text = re.sub(r"\s+", " ", str(review.get("text", "")).strip().lower())
        author = re.sub(r"\s+", " ", str(review.get("author", "")).strip().lower())
        if not text:
            continue
        if text in seen_by_text:
            idx = seen_by_text[text]
            prev_author = str(result[idx].get("author") or "").strip().lower()
            if prev_author in {"", "клиент"} and author not in {"", "клиент"}:
                result[idx]["author"] = review.get("author")
            if not result[idx].get("date") and review.get("date"):
                result[idx]["date"] = review.get("date")
            continue
        seen_by_text[text] = len(result)
        result.append(review)
    return result


def clean_author(text: str) -> str:
    author = re.sub(r"\s+", " ", (text or "").strip())
    author = re.sub(r"Знаток\s+города\s*\d*\s*уровня", "", author, flags=re.IGNORECASE)
    author = re.sub(r"\b\d+\s*уровня\b", "", author, flags=re.IGNORECASE)
    author = author.replace("Подписаться", "")
    author = re.sub(r"\s+", " ", author).strip(" .,")
    return author or "Клиент"


def clean_review_text(text: str) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    value = re.sub(r"Знаток\s+города\s*\d+\s*уровня", "", value, flags=re.IGNORECASE)
    value = value.replace("Подписаться", "")
    value = value.replace("Посмотреть ответ организации", "")
    value = re.sub(r"\bЕщё$", "", value).strip()
    value = re.sub(r"\s+", " ", value).strip(" .,")
    return value


def is_noise_review_text(text: str) -> bool:
    value = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not value:
        return True
    noise_phrases = {
        "подписаться",
        "посмотреть ответ организации",
        "знаток города",
        "ответ организации",
    }
    if value in noise_phrases:
        return True
    if len(value) < 20 and "подпис" in value:
        return True
    if re.fullmatch(r"\d{1,2}\s+[а-яё]+\s+\d{4}", value):
        return True
    if len(value) < 15 and len(value.split()) <= 2:
        return True
    return False


def normalize_price_text(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        return ""
    text = re.sub(r"(?i)(\d+)\s*(?:руб\.?|р\.?)(?!\w)", r"\1 ₽", text)
    text = re.sub(r"\s*₽\s*", " ₽", text)
    return text.strip()


def clean_service_name(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = re.sub(r"(?<=[а-яё])(?=[А-ЯЁ])", " ", text)
    text = re.sub(r"(?i)\b(услуга|цена|стоимость)\b", "", text)
    text = re.split(r"(?i)\b(снятие|стоимость|бесплатно|на первое посещение|в зависимости)\b", text, maxsplit=1)[0]
    text = re.sub(r"\b\d+\s*шт\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .,")
    return text


def normalize_services(raw_services: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result: List[Dict[str, Any]] = []
    for item in raw_services[:40]:
        name = clean_service_name(str(item.get("name", "")))
        price = normalize_price_text(str(item.get("price", "")))
        description = str(item.get("description", "")).strip()
        duration_min = item.get("duration_min")

        if not name or not price:
            continue
        if len(name) < 4 or re.fullmatch(r"[\d\s]+", name):
            continue
        key = (name.lower(), price.lower())
        if key in seen:
            continue
        seen.add(key)

        service_item: Dict[str, Any] = {"name": name, "price": price}
        if description:
            service_item["description"] = description
        if duration_min and isinstance(duration_min, (int, float)):
            service_item["duration_min"] = int(duration_min)

        result.append(service_item)
        if len(result) >= 20:
            break
    return result


async def download_photos(photo_urls: List[str], slug: str, lead_id: int) -> List[Dict[str, Any]]:
    """Downloads photos from Yandex URLs to public directory."""
    if not photo_urls:
        return []

    target_dir = PUBLIC_DIR / f"{slug}-{lead_id}" / "yandex"
    target_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        for idx, url in enumerate(photo_urls[:10]):  # Max 10 photos
            try:
                # Get image first to check dimensions
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        continue
                    content_type = resp.headers.get("Content-Type", "")
                    if "gif" in content_type.lower():
                        continue  # Skip GIFs

                    image_data = await resp.read()

                # Try to get image dimensions (simple check)
                import io
                from PIL import Image

                try:
                    img = Image.open(io.BytesIO(image_data))
                    width, height = img.size
                    if width < 800:
                        continue  # Skip small images
                except Exception:
                    continue  # Skip if can't read dimensions

                # Check format
                allowed_formats = ("jpg", "jpeg", "png", "webp")
                if img.format and img.format.lower() not in allowed_formats:
                    continue

                # Save file
                ext = img.format.lower() if img.format else "jpg"
                filename = f"photo_{idx + 1}.{ext}"
                filepath = target_dir / filename

                with open(filepath, "wb") as f:
                    f.write(image_data)

                downloaded.append({
                    "filename": filename,
                    "size_bytes": len(image_data),
                    "width": width,
                    "height": height,
                    "original_url": url
                })

                write_log(f"📸 Фото скачано: {filename} ({len(image_data)} bytes, {width}x{height})")

            except Exception as e:
                write_log(f"⚠️ Ошибка скачивания фото {idx}: {e}")
                continue

    write_log(f"📸 Скачано {len(downloaded)} фото в {target_dir}")
    return downloaded


def parse_existing_reviews(raw_reviews: Optional[str]) -> List[Dict[str, Any]]:
    if not raw_reviews:
        return []
    try:
        parsed = json.loads(raw_reviews)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []

    normalized = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("review") or item.get("content") or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "author": str(item.get("author") or item.get("author_name") or "").strip() or "Клиент",
                "text": text,
                "rating": item.get("rating"),
                "date": item.get("date"),
                "source": item.get("source") or "google",
            }
        )
    return normalized


async def search_lead_on_yandex(name: str, city: str, lead_address: str = "", lead_id: Optional[int] = None) -> Optional[str]:
    street_keyword = extract_street_keyword(lead_address)
    house_number = extract_house_number(lead_address)
    query = " ".join(part for part in [name, city, street_keyword] if part).strip()
    search_url = f"https://yandex.ru/maps/?text={quote_plus(query)}"
    write_log(f"🔎 Поиск в Яндекс.Картах: {query}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        user_agent = choose_user_agent()
        write_log(f"🕵️ UA (поиск): {user_agent.split(' Chrome/')[1].split(' ')[0] if ' Chrome/' in user_agent else user_agent}")
        context = await browser.new_context(**get_context_kwargs(user_agent))
        page = await context.new_page()

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            if await has_captcha_signals(page):
                await save_session_state(context)
                write_captcha_log(lead_id, page.url)
                raise RuntimeError("Обнаружена капча на этапе поиска")

            try:
                await page.wait_for_selector("a[href*='/maps/org/']", timeout=10000)
            except PlaywrightTimeoutError:
                pass

            candidates = await page.evaluate(
                r"""
                () => {
                  const out = [];
                  const links = Array.from(document.querySelectorAll("a[href*='/maps/org/']"));
                  for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    if (!href.includes('/maps/org/')) continue;
                    const title = (a.textContent || '').trim();
                    out.push({ href, title });
                    if (out.length >= 20) break;
                  }
                  return out;
                }
                """
            )

            if not candidates:
                html = await page.content()
                raw_urls = re.findall(r"https?://yandex\.ru/maps/org/[^\"\'\s<]+", html)
                candidates = [{"href": u, "title": ""} for u in raw_urls[:20]]

            best_url = None
            best_score = 0.0

            def normalize_candidate_url(raw_href: str) -> str:
                full = urljoin("https://yandex.ru", raw_href)
                full = re.sub(r"[#?].*$", "", full)
                match = re.match(r"^(https?://yandex\.(?:ru|com)/maps/org/[^/]+/\d+)/?.*$", full)
                if match:
                    return match.group(1) + "/"
                return full

            normalized_candidates = []
            seen_urls = set()
            for item in candidates:
                href = str(item.get("href") or "")
                if not href:
                    continue
                normalized_url = normalize_candidate_url(href)
                if "/maps/org/" not in normalized_url or normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)
                normalized_candidates.append(
                    {
                        "url": normalized_url,
                        "title": str(item.get("title") or ""),
                    }
                )

            async def read_candidate_meta(card_url: str) -> Dict[str, str]:
                card_page = await context.new_page()
                try:
                    await card_page.goto(card_url, wait_until="domcontentloaded", timeout=25000)
                    await card_page.wait_for_timeout(1800)
                    if await has_captcha_signals(card_page):
                        return {"title": "", "address": ""}
                    raw_meta = await card_page.evaluate(
                        r"""
                        () => {
                          const pickText = (selectors) => {
                            for (const sel of selectors) {
                              const el = document.querySelector(sel);
                              if (el && el.textContent && el.textContent.trim()) return el.textContent.trim();
                            }
                            return "";
                          };
                          const title = pickText([
                            "h1",
                            "[class*='orgpage-header-view__header']",
                            "[class*='business-title-view__title']"
                          ]);
                          const address = pickText([
                            "[class*='business-contacts-view__address-link']",
                            "[class*='business-contacts-view__address']",
                            "[class*='toponym-card-title-view__container']"
                          ]);
                          return { title, address };
                        }
                        """
                    )
                    return {
                        "title": str(raw_meta.get("title") or "").strip(),
                        "address": str(raw_meta.get("address") or "").strip(),
                    }
                except Exception:
                    return {"title": "", "address": ""}
                finally:
                    await card_page.close()

            for item in normalized_candidates[:20]:
                full_url = str(item.get("url") or "")
                listed_title = str(item.get("title") or "")
                if not full_url:
                    continue

                meta = await read_candidate_meta(full_url)
                card_title = meta.get("title") or listed_title
                card_address = meta.get("address") or ""

                # Calculate individual signal scores
                name_score = max(
                    fuzzy_match_score(name, card_title) if card_title else 0.0,
                    fuzzy_match_score(name, listed_title) if listed_title else 0.0
                )
                
                # City match: must be in address or fuzzy match
                city_score = 0.0
                if city:
                    if address_contains_city(card_address, city):
                        city_score = 1.0
                    else:
                        city_score = fuzzy_match_score(city, card_address) if card_address else 0.0
                
                # Address match: soft signal, not a hard gate
                address_score = fuzzy_match_score(lead_address, card_address) if lead_address and card_address else 0.0
                
                # Category match: beauty-related keywords
                category_text = f"{card_title} {card_address}"
                category_score = calculate_category_match(category_text)

                # Weighted final score
                final_score = (
                    name_score * 0.5 +
                    city_score * 0.2 +
                    address_score * 0.15 +
                    category_score * 0.15
                )

                # Hard rejection rules
                # Reject if city is clearly wrong (city_score < 0.5)
                if city and city_score < 0.5:
                    write_log(f"↪ Пропуск: неверный город (city_score={city_score:.2f}): {full_url}")
                    continue
                
                # Reject if name is completely different (name_score < 0.5)
                if name_score < 0.5:
                    write_log(f"↪ Пропуск: название не совпадает (name_score={name_score:.2f}): {full_url}")
                    continue
                
                # Reject if category is clearly non-beauty (category_score == 0.0)
                if category_score == 0.0:
                    write_log(f"↪ Пропуск: неверная категория (category_score={category_score:.2f}): {full_url}")
                    continue

                # Acceptance rules
                # Accept if final_score >= 0.65 AND name_score >= 0.65 AND city_score >= 0.8
                if final_score >= 0.65 and name_score >= 0.65 and city_score >= 0.8:
                    write_log(
                        f"🧪 Кандидат: name={name_score:.2f} city={city_score:.2f} addr={address_score:.2f} cat={category_score:.2f} total={final_score:.2f} | {full_url}"
                    )
                    
                    if final_score > best_score:
                        best_score = final_score
                        best_url = full_url
                else:
                    write_log(
                        f"↪ Пропуск: score too low (name={name_score:.2f} city={city_score:.2f} addr={address_score:.2f} cat={category_score:.2f} total={final_score:.2f}): {full_url}"
                    )

            if best_url and best_score >= 0.65:
                write_log(f"✅ Найдена карточка: {best_url} (score={best_score:.2f})")
                return best_url

            if lead_id is not None:
                write_log(f"❌ Совпадение не найдено для lead_id={lead_id}")
            else:
                write_log("❌ Совпадение не найдено")
            return None
        finally:
            await context.close()
            await browser.close()


async def scrape_yandex_card(url: str, lead_id: Optional[int] = None, mode: Literal["light", "full"] = "light", timeout_sec: int = 60) -> Dict[str, Any]:
    write_log(f"🧭 Открываю карточку: {url} mode={mode}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        user_agent = choose_user_agent()
        write_log(f"🕵️ UA (карточка): {user_agent.split(' Chrome/')[1].split(' ')[0] if ' Chrome/' in user_agent else user_agent}")
        context = await browser.new_context(**get_context_kwargs(user_agent))
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
            await page.wait_for_timeout(2000 if mode == "light" else 3500)

            if await has_captcha_signals(page):
                await save_session_state(context)
                write_captcha_log(lead_id, page.url)
                raise RuntimeError("Обнаружена капча на карточке")

            try:
                await page.get_by_text("Отзывы", exact=False).first.click(timeout=3000)
                await page.wait_for_timeout(1500)
            except Exception:
                pass

            for btn_label in ("Показать номер", "Показать"):
                try:
                    await page.get_by_text(btn_label, exact=False).first.click(timeout=2000)
                    await page.wait_for_timeout(1000)
                    break
                except Exception:
                    continue

            # Only scroll and load more reviews in full mode
            if mode == "full":
                for _ in range(12):
                    try:
                        await page.get_by_text("Показать ещё", exact=False).first.click(timeout=700)
                    except Exception:
                        pass
                    await page.mouse.wheel(0, random.randint(1300, 2200))
                    await page.wait_for_timeout(random.randint(900, 1700))

            raw = await page.evaluate(
                r"""
                () => {
                  const pickText = (selectors) => {
                    for (const sel of selectors) {
                      const el = document.querySelector(sel);
                      if (el && el.textContent && el.textContent.trim()) return el.textContent.trim();
                    }
                    return "";
                  };

                  const uniq = (arr) => Array.from(new Set(arr.filter(Boolean)));

                  const ratingText = pickText([
                    "span[aria-label*='Рейтинг']",
                    "div[aria-label*='Рейтинг']",
                    "[class*='business-rating-badge-view__rating-text']",
                    "[class*='card-rating-view__rating-text']",
                    "[class*='business-summary-rating-badge-view__rating']"
                  ]);

                  const reviewsCountText = pickText([
                    "[class*='business-reviews-card-view__review-count']",
                    "[class*='business-header-rating-view__text']",
                    "[class*='tabs-select-view__counter']",
                    "[class*='business-reviews-card-view__summary']"
                  ]);

                  const address = pickText([
                    "[class*='business-contacts-view__address-link']",
                    "[class*='business-contacts-view__address']",
                    "[class*='toponym-card-title-view__container']"
                  ]);

                  const additionalAddresses = uniq(
                    Array.from(document.querySelectorAll(
                      "[class*='business-branches'] [class*='address'], [class*='chain-branches'] [class*='address'], [class*='branch'] [class*='address'], [class*='business-contacts-view__address-link']"
                    ))
                      .map(el => (el.textContent || '').trim())
                      .filter(Boolean)
                      .filter(v => !/^(маршрут|как добраться|на карте)$/i.test(v))
                      .filter(v => v !== address)
                  );

                  const phones = uniq(
                    Array.from(document.querySelectorAll("a[href^='tel:']"))
                      .map(a => (a.textContent || '').trim())
                  );

                  if (!phones.length) {
                    const bodyText = (document.body?.textContent || '');
                    const found = bodyText.match(/(?:\+7|8)\s*\(?\d{3}\)?\s*\d{3}[\s-]*\d{2}[\s-]*\d{2}/g) || [];
                    for (const p of found) {
                      const val = (p || '').replace(/\s+/g, ' ').trim();
                      if (val) phones.push(val);
                      if (phones.length >= 3) break;
                    }
                  }

                  const workingHours = pickText([
                    "[class*='business-working-status-view']",
                    "[class*='business-working-status-view__text']",
                    "[class*='business-hours-view']"
                  ]);

                  const website = (() => {
                    const links = Array.from(document.querySelectorAll("a[href]"));
                    for (const a of links) {
                      const href = a.getAttribute('href') || '';
                      if (!href) continue;
                      if (/^https?:\/\//i.test(href) && !href.includes('yandex.ru/maps')) {
                        const txt = (a.textContent || '').toLowerCase();
                        if (txt.includes('сайт') || txt.includes('перейти') || txt.includes('www') || txt.includes('.ru') || txt.includes('.com')) {
                          return href;
                        }
                      }
                    }
                    return "";
                  })();

                  // Extract coordinates from URL or page
                  const coordinates = (() => {
                    // Try to extract from URL (ll=lat,lng format in Yandex Maps)
                    const urlParams = window.location.href.split(/[?&]/);
                    for (const param of urlParams) {
                      if (param.startsWith('ll=')) {
                        const parts = param.substring(3).split(',');
                        if (parts.length === 2) {
                          const lat = parseFloat(parts[1]);
                          const lng = parseFloat(parts[0]);
                          if (!isNaN(lat) && !isNaN(lng)) {
                            return { lat, lng };
                          }
                        }
                      }
                    }
                    
                    // Try to extract from meta tags or JSON-LD
                    const metaGeo = document.querySelector('meta[name="geo.position"]')?.content;
                    if (metaGeo) {
                      const parts = metaGeo.split(';');
                      if (parts.length === 2) {
                        const lat = parseFloat(parts[0]);
                        const lng = parseFloat(parts[1]);
                        if (!isNaN(lat) && !isNaN(lng)) {
                          return { lat, lng };
                        }
                      }
                    }
                    
                    // Try JSON-LD
                    try {
                      const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
                      for (const script of scripts) {
                        const data = JSON.parse(script.textContent);
                        if (data.geo && data.geo.latitude && data.geo.longitude) {
                          return { lat: data.geo.latitude, lng: data.geo.longitude };
                        }
                      }
                    } catch (e) {
                      // Ignore JSON parsing errors
                    }
                    
                    return null;
                  })();

                  const reviewsRaw = [];
                  const reviewBlocks = document.querySelectorAll(
                    "[class*='business-review-view'], [class*='review-snippet-view'], [itemprop='review'], [class*='review-view']"
                  );

                  for (const block of reviewBlocks) {
                    const authorEl = block.querySelector("[class*='author'], [class*='name']");
                    const textEl = block.querySelector("[itemprop='reviewBody'], [class*='review-text'], [class*='business-review-view__body'], [class*='business-review-view__body-text'], [class*='comment-text']");
                    const dateEl = block.querySelector("[class*='date']");
                    const ratingEl = block.querySelector("[aria-label*='из 5'], [class*='rating']");

                    const author = (authorEl?.textContent || '').trim();
                    const text = (textEl?.textContent || block.textContent || '').trim();
                    const date = (dateEl?.textContent || '').trim();
                    const rating = (ratingEl?.getAttribute('aria-label') || ratingEl?.textContent || '').trim();

                    if (text && text.length > 6 && text.toLowerCase() !== author.toLowerCase()) {
                      reviewsRaw.push({ author, text, date, rating });
                    }
                    if (reviewsRaw.length >= 30) break;
                  }

                  const images = uniq(
                    Array.from(document.querySelectorAll('img[src]'))
                      .map(i => i.getAttribute('src') || '')
                      .filter(src => /avatars\.mds\.yandex\.net/.test(src))
                  ).slice(0, 20);

                  const services = [];
                  const priceRegex = /(\d[\d\s]{1,10}\s?(?:₽|р\.?|руб\.?))/i;
                  const durationRegex = /(\d+)\s*(?:мин|м|минут)/i;
                  const serviceCards = Array.from(document.querySelectorAll(
                    "[class*='business-prices-list-item'], [class*='business-prices-card-view__item'], [class*='business-prices-view__item'], [class*='business-prices-list-item-view'], [class*='business-prices'] div[role='listitem']"
                  ));

                  const getParts = (node) => {
                    const txt = (node.textContent || '').replace(/\s+/g, ' ').trim();
                    if (!txt) return [];
                    return txt.split(/\n|•|\||—|-/).map(s => s.trim()).filter(Boolean);
                  };

                  for (const card of serviceCards) {
                    const parts = getParts(card);
                    if (!parts.length) continue;

                    const pricePart = [...parts].reverse().find(p => priceRegex.test(p)) || "";
                    const titleEl = card.querySelector("[class*='title'], [class*='name'], [class*='service']");
                    const namePart = (titleEl?.textContent || parts[0] || '').replace(/\s+/g, ' ').trim();
                    if (!namePart || !pricePart) continue;

                    // Extract description (text between name and price)
                    let description = "";
                    const nameIndex = parts.findIndex(p => p.includes(namePart.substring(0, 10)));
                    const priceIndex = parts.findIndex(p => priceRegex.test(p));
                    if (nameIndex >= 0 && priceIndex > nameIndex + 1) {
                      description = parts.slice(nameIndex + 1, priceIndex).join(' ').trim();
                    } else if (parts.length > 2) {
                      // Fallback: use middle parts as description
                      const middleParts = parts.filter(p => p !== namePart && !priceRegex.test(p));
                      description = middleParts.slice(0, 2).join(' ').trim();
                    }

                    // Extract duration
                    const fullText = card.textContent || '';
                    const durationMatch = fullText.match(durationRegex);
                    const durationMin = durationMatch ? parseInt(durationMatch[1]) : null;

                    services.push({ name: namePart, price: pricePart, description: description, duration_min: durationMin });
                    if (services.length >= 40) break;
                  }

                  if (!services.length) {
                    const priceContainers = Array.from(document.querySelectorAll("[class*='business-prices'], [aria-label*='цены'], [aria-label*='прайс']"));
                    for (const root of priceContainers) {
                      const nodes = Array.from(root.querySelectorAll("li, div"));
                      for (const node of nodes) {
                        const txt = (node.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!txt || txt.length < 8 || txt.length > 180) continue;
                        if (!priceRegex.test(txt)) continue;
                        if (/как добраться|маршрут|такси|парковк/i.test(txt)) continue;
                        const pricePart = (txt.match(priceRegex) || [""])[0].trim();
                        const namePart = txt.replace(pricePart, '').replace(/\s+/g, ' ').trim();
                        if (!namePart || !pricePart || namePart === pricePart) continue;
                        services.push({ name: namePart, price: pricePart });
                        if (services.length >= 40) break;
                      }
                      if (services.length >= 40) break;
                    }
                  }

                  if (!services.length) {
                    const noise = /как добраться|маршрут|такси|парковк|улица|школа|универсам/i;
                    const nodes = Array.from(document.querySelectorAll("li, div"));
                    for (const node of nodes) {
                      const txt = (node.textContent || '').replace(/\s+/g, ' ').trim();
                      if (!txt || txt.length < 8 || txt.length > 180) continue;
                      if (!priceRegex.test(txt) || noise.test(txt)) continue;
                      const pricePart = (txt.match(priceRegex) || [""])[0].trim();
                      const namePart = txt.replace(pricePart, '').replace(/\s+/g, ' ').trim();
                      if (!namePart || !pricePart || namePart === pricePart) continue;
                      services.push({ name: namePart, price: pricePart });
                      if (services.length >= 40) break;
                    }
                  }
                  
                  const seen = new Set();
                  const cleanServices = [];
                  for (const s of services) {
                    const key = (s.name + s.price).toLowerCase();
                    if (seen.has(key)) continue;
                    seen.add(key);
                    cleanServices.push(s);
                  }

                  return {
                    rating_text: ratingText,
                    reviews_count_text: reviewsCountText,
                    reviews_list: reviewsRaw,
                    photos: images,
                    services: cleanServices,
                    address,
                    phones,
                    working_hours: workingHours,
                    website,
                    additional_addresses: additionalAddresses,
                    coordinates: coordinates,
                  };
                }
                """
            )

            if await has_captcha_signals(page):
                await save_session_state(context)
                write_captcha_log(lead_id, page.url)
                raise RuntimeError("Обнаружена капча после скролла")

            reviews = []
            if mode == "full":
                for item in raw.get("reviews_list", [])[:30]:
                    author = clean_author(str(item.get("author") or "Клиент"))
                    text = clean_review_text(re.sub(r"\s+", " ", str(item.get("text", "")).strip()))
                    raw_date = str(item.get("date") or "").strip()

                    # T08 requirements: min 30 chars, require date
                    if len(text) < 30 or is_noise_review_text(text):
                        continue
                    if text.lower() == author.lower():
                        continue
                    if not raw_date:
                        continue

                    # Parse date to YYYY-MM-DD
                    parsed_date = parse_russian_date(raw_date)
                    if not parsed_date:
                        continue

                    reviews.append(
                        {
                            "author": author,
                            "text": text,
                            "rating": parse_float(str(item.get("rating", ""))),
                            "date": parsed_date,
                            "source": "yandex",
                        }
                    )

                reviews = dedupe_reviews(reviews)

            # T08: enriched extracted structure
            yandex_reviews = reviews[:15] if mode == "full" else []
            service_carousel = normalize_services(list(raw.get("services", [])))
            photos_list = list(raw.get("photos", []))[:20] if mode == "full" else []
            reviews_list = reviews[:30] if mode == "full" else []
            photos_count = len(raw.get("photos", [])) if mode == "light" else len(photos_list)

            result = {
                "source_url": url,
                "rating": parse_float(str(raw.get("rating_text", ""))),
                "reviews_count": parse_int(str(raw.get("reviews_count_text", ""))),
                "photos_count": photos_count,
                "reviews_list": reviews_list,
                "photos": photos_list,
                "services": normalize_services(list(raw.get("services", []))),
                "address": str(raw.get("address") or "").strip(),
                "phones": list(raw.get("phones", [])),
                "working_hours": str(raw.get("working_hours") or "").strip(),
                "website": str(raw.get("website") or "").strip(),
                "additional_addresses": list(raw.get("additional_addresses", []))[:20],
                "coordinates": raw.get("coordinates"),
                "scraped_at": timestamp(),
                # Enriched extracted fields for T08
                "yandexReviews": yandex_reviews,
                "serviceCarousel": service_carousel,
            }
            
            # Fallback geocoding if coordinates are missing
            if not result.get("coordinates") and result.get("address"):
                # Try to extract coordinates from the page URL after navigation
                # The browser URL might have ll=lat,lng after the page loads
                try:
                    page_url = page.url
                    coords = await geocode_address(result["address"], page_url)
                    if coords:
                        result["coordinates"] = coords
                        write_log(f"📍 Fallback geocoding: {result['address']} → {coords}")
                except Exception as e:
                    # Fallback to original URL
                    coords = await geocode_address(result["address"], url)
                    if coords:
                        result["coordinates"] = coords
                        write_log(f"📍 Fallback geocoding: {result['address']} → {coords}")

            write_log(
                f"✅ Карточка собрана mode={mode}: rating={result['rating']} reviews_count={result['reviews_count']} photos_count={result['photos_count']}"
            )
            return result
        finally:
            await context.close()
            await browser.close()


async def enrich_lead(lead_id: int, force: bool = False, mode: Literal["light", "full"] = "light") -> Optional[Dict[str, Any]]:
    ensure_data_dirs()
    write_log(f"🚀 Старт enrich для lead_id={lead_id} mode={mode}")

    async with get_async_session() as session:
        res = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = res.scalar_one_or_none()

        if not lead:
            write_log(f"❌ Лид ID={lead_id} не найден")
            return None

        lead_name = lead.name or f"Lead {lead_id}"
        city = detect_city(lead.address)

        slug = slugify_name(lead_name, lead_id)
        out_path = YANDEX_DATA_DIR / f"{slug}-{lead_id}.json"

        # Check if file exists and its _meta
        existing_payload = read_cached_payload(out_path)
        has_full = False
        if existing_payload:
            _meta = existing_payload.get("yandex", {}).get("_meta", {})
            has_full = _meta.get("has_full", True)  # Legacy files without _meta are considered full
            if mode == "light" and not force and has_full:
                write_log(f"♻️ Файл уже в full режиме, skip light: {out_path}")
                return summarize_result_from_payload(existing_payload, out_path, cached=True)
            if mode == "full" and has_full and not force:
                write_log(f"♻️ Файл уже в full режиме: {out_path}")
                return summarize_result_from_payload(existing_payload, out_path, cached=True)

        # FULL mode requires light mode first
        if mode == "full" and not existing_payload:
            write_log(f"🔄 FULL mode требует light mode, запускаю light сначала")
            light_result = await enrich_lead(lead_id, force=force, mode="light")
            if not light_result:
                write_log(f"❌ Light mode failed, cannot proceed to full")
                return None
            existing_payload = read_cached_payload(out_path)

        # Cache check for light mode (skip for full mode as it's intentional re-run)
        if mode == "light":
            cache_age = get_cache_age_seconds(out_path)
            if cache_age is not None and cache_age <= 24 * 60 * 60 and not force:
                if existing_payload:
                    write_log(f"♻️ Использую кеш (<24ч): {out_path}")
                    return summarize_result_from_payload(existing_payload, out_path, cached=True)

        minutes_since_last = get_minutes_since_last_live_run(lead_id)
        if minutes_since_last is not None and minutes_since_last < 60 and not force:
            wait_minutes = max(1, 60 - minutes_since_last)
            write_log(
                f"⏸ лид {lead_id} обработан {minutes_since_last} минут назад, подождите {wait_minutes} минут до следующего прогона"
            )
            return None

        if minutes_since_last is not None and minutes_since_last < 60 and force:
            write_log(f"⚠️ --force: игнорирую cooldown для лида {lead_id} ({minutes_since_last} минут с прошлого live-прогона)")

        mark_live_run(lead_id)

        yandex_url = await search_lead_on_yandex(lead_name, city, str(lead.address or ""), lead_id=lead_id)
        if not yandex_url:
            write_log(f"⚠️ Не нашли карточку Яндекс для '{lead_name}'")
            return None

        random_pause(8, 15, "между поиском и скрейпингом")

        # Determine timeout based on mode
        timeout_sec = 10 if mode == "light" else 60
        scraped = await scrape_yandex_card(yandex_url, lead_id=lead_id, mode=mode, timeout_sec=timeout_sec)

        # Sanity check for light mode: address OR source_url required
        if mode == "light":
            if not scraped.get("address") and not scraped.get("source_url"):
                write_log(f"❌ Light mode sanity check failed: нет address и source_url")
                raise RuntimeError("Light mode требует address или source_url")

        # Download photos in full mode
        if mode == "full" and scraped.get("photos"):
            photo_urls = [p for p in scraped["photos"] if isinstance(p, str) and p.startswith("http")]
            if photo_urls:
                write_log(f"📸 Скачиваю {len(photo_urls)} фото...")
                downloaded_photos = await download_photos(photo_urls, slug, lead_id)
                scraped["photos"] = downloaded_photos
                scraped["photos_count"] = len(downloaded_photos)
            else:
                scraped["photos"] = []
                scraped["photos_count"] = 0

        # Build _meta
        _meta = {
            "mode": mode,
            "has_full": mode == "full",
            "scraped_at": timestamp(),
            "parser_version": "2.0"
        }
        if mode == "full":
            _meta["full_scraped_at"] = timestamp()
        elif existing_payload and existing_payload.get("yandex", {}).get("_meta"):
            # Preserve full_scraped_at from existing file if upgrading from light to full
            existing_meta = existing_payload["yandex"]["_meta"]
            if existing_meta.get("full_scraped_at"):
                _meta["full_scraped_at"] = existing_meta["full_scraped_at"]

        scraped["_meta"] = _meta

        existing_reviews = parse_existing_reviews(lead.raw_reviews)
        merged_reviews = dedupe_reviews(existing_reviews + scraped.get("reviews_list", []))

        yandex_reviews_count = scraped.get("reviews_count") or 0
        existing_count = int(lead.reviews_count or 0)

        if yandex_reviews_count > existing_count:
            lead.reviews_count = yandex_reviews_count

        if not lead.address and scraped.get("address"):
            lead.address = str(scraped["address"])

        phones = scraped.get("phones") or []
        if not lead.phone and phones:
            lead.phone = str(phones[0])

        if not lead.website and scraped.get("website"):
            # Фильтруем соцсети — не сохраняем их как website
            website = str(scraped["website"])
            SKIP_DOMAINS = ("instagram.com", "facebook.com", "t.me", "ok.ru", "youtube.com")
            if not any(d in website for d in SKIP_DOMAINS):
                lead.website = website

        lead.raw_reviews = json.dumps(merged_reviews, ensure_ascii=False)

        await session.commit()

    payload = {
        "lead_id": lead_id,
        "lead_name": lead_name,
        "city": city,
        "yandex": scraped,
        "merged_reviews_count": len(merged_reviews),
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    write_log(f"✅ Сохранён JSON: {out_path} mode={mode}")
    return summarize_result_from_payload(payload, out_path, cached=False)


def run_batch(lead_ids: List[int], force: bool = False) -> int:
    if len(lead_ids) > MAX_LEADS_PER_RUN:
        safe_print(f"❌ Превышен лимит: максимум {MAX_LEADS_PER_RUN} лидов за запуск")
        return 1

    started_at = time.perf_counter()
    errors = 0

    for idx, lead_id in enumerate(lead_ids, start=1):
        safe_print("=" * 60)
        safe_print(f"🗺 Yandex Enricher | лид {idx}/{len(lead_ids)} | ID={lead_id}")
        safe_print("=" * 60)

        lead_start = time.perf_counter()
        try:
            result = asyncio.run(enrich_lead(lead_id, force=force))
            if result:
                lead_secs = time.perf_counter() - lead_start
                cache_label = " (cache)" if result.get("cached") else ""
                safe_print(
                    f"✅ ID={lead_id}{cache_label} | rating={result['rating']} | "
                    f"reviews={result['yandex_reviews']} | photos={result['photos']} | "
                    f"time={lead_secs:.1f}s"
                )
                safe_print(f"📄 {result['output_path']}")
            else:
                errors += 1
                safe_print(f"⚠️ ID={lead_id} не обогащён")
        except Exception as exc:
            errors += 1
            msg = str(exc)
            if "капча" in msg.lower() or "captcha" in msg.lower():
                write_log(f"🛑 Капча на лиде {lead_id}. Завершаем запуск.")
                safe_print(f"❌ Капча на лиде {lead_id}. Остановлено.")
                return 2
            write_log(f"❌ Ошибка enrich ID={lead_id}: {exc}")
            safe_print(f"❌ Ошибка ID={lead_id}: {exc}")

        if idx < len(lead_ids):
            random_pause(30, 60, "между лидами")

    total_secs = time.perf_counter() - started_at
    safe_print(f"🏁 Готово за {total_secs:.1f} сек. Ошибок: {errors}")
    return 0 if errors == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Yandex Maps Enricher (scraping, no API)")
    parser.add_argument("lead_ids", nargs="+", type=int, help="Lead ID(s) из БД")
    parser.add_argument("--force", action="store_true", help="Игнорировать кеш 24ч и выполнить свежий прогон")
    args = parser.parse_args()

    exit_code = run_batch(args.lead_ids, force=args.force)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
