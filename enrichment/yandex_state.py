"""
Парсер v2: state-view JSON Яндекс Карт.

Заменяет DOM-скрейпинг в yandex.py для основной выгрузки данных.
"""
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Импорты от yandex.py — переиспользуем готовое
from enrichment.yandex import (
    BASE_DIR,
    USER_AGENTS,
    SESSION_STATE_PATH,
    choose_user_agent,
    get_context_kwargs,
    has_captcha_signals,
    save_session_state,
    write_log,
    write_captcha_log,
    timestamp,
    slugify_name,
    detect_city,
    search_lead_on_yandex,  # переиспользуем поиск
)

YANDEX_DATA_DIR = BASE_DIR / "data" / "yandex"

QUALIFY_THRESHOLDS = {
    "min_rating": 4.0,
    "min_review_count": 20,
    "min_unique_aspect_photos": 8,
    "min_services_with_photos": 3,
}

CONTACT_CHANNELS = ("taplink", "vk", "website", "whatsapp", "booking", "instagram", "telegram")


def _strip_size_suffix(url: str) -> str:
    """Remove size suffix from Yandex photo URL for deduplication."""
    # Remove /%, /S, /M, /L, /XL, /orig, etc.
    for suffix in ["/%", "/S", "/M", "/L", "/XL", "/XXL", "/orig"]:
        if url.endswith(suffix):
            return url[:-len(suffix)]
    return url


def qualify_lead(card: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns qualification status for a lead based on card.json data.
    """
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


async def scrape_state_view(url: str, lead_id: Optional[int] = None, timeout_sec: int = 30) -> Dict[str, Any]:
    """
    Открывает страницу карточки и выдирает <script class="state-view">.
    Возвращает распарсенный JSON стейта.
    """
    write_log(f"🧭 [v2] Открываю карточку: {url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        user_agent = choose_user_agent()
        context = await browser.new_context(**get_context_kwargs(user_agent))
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
            # state-view рендерится при первичной гидратации, ждём чуть-чуть
            await page.wait_for_timeout(3500)

            if await has_captcha_signals(page):
                await save_session_state(context)
                write_captcha_log(lead_id, page.url)
                raise RuntimeError("Капча на этапе state-view")

            # Выдираем content of <script class="state-view">
            raw_json = await page.evaluate(
                """() => {
                    const s = document.querySelector('script.state-view');
                    return s ? s.textContent : null;
                }"""
            )

            if not raw_json:
                raise RuntimeError("script.state-view не найден на странице")

            try:
                state = json.loads(raw_json)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Не удалось распарсить state-view JSON: {e}")

            return state

        finally:
            await context.close()
            await browser.close()


def normalize_state(state: Dict[str, Any], lead_id: int, slug: str, source_url: str) -> Dict[str, Any]:
    """
    Преобразует сырой state-view JSON в card.json по контракту schema_version=2.

    Стейт имеет структуру:
        state.stack[0].results.items[0]  -- основной объект бизнеса
        ИЛИ
        state.stack[0].response.items[0]  -- альтернативная структура (mobile)

    Внутри одного из вариантов лежит вся карточка.
    """
    # Найти items в любой из веток
    stack = state.get("stack") or []
    if not stack:
        raise ValueError("state.stack пуст")

    first = stack[0]
    items = (
        (first.get("results") or {}).get("items")
        or (first.get("response") or {}).get("items")
        or []
    )
    if not items:
        raise ValueError("Не найдены items в state.stack[0]")

    item = items[0]  # карточка организации

    # ===== Базовые поля =====
    card: Dict[str, Any] = {
        "schema_version": 2,
        "lead_id": lead_id,
        "slug": slug,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "source_url": source_url,
        "org_id": str(item.get("id") or ""),
        "title": (item.get("title") or "").strip(),
        "short_title": (item.get("shortTitle") or item.get("title") or "").strip(),
    }

    # ===== Рейтинг =====
    rating_data = item.get("ratingData") or {}
    card["rating"] = float(rating_data.get("ratingValue") or 0) or None
    card["rating_count"] = int(rating_data.get("ratingCount") or 0) or None
    card["review_count"] = int(rating_data.get("reviewCount") or 0) or None

    # ===== Верификация и награды =====
    bp = item.get("businessProperties") or {}
    card["verified_owner"] = bool(bp.get("has_verified_owner", False))
    awards = item.get("awards") or {}
    card["good_place_year"] = awards.get("goodPlaceYear")

    # ===== Адрес =====
    composite = item.get("compositeAddress") or {}
    card["address"] = {
        "full": item.get("fullAddress") or item.get("address") or "",
        "country": composite.get("country"),
        "locality": composite.get("locality"),
        "street": composite.get("street"),
        "house": composite.get("house"),
        "additional": item.get("additionalAddress"),
        "postal_code": item.get("postalCode"),
    }

    # ===== Координаты =====
    coords = item.get("coordinates") or item.get("displayCoordinates")
    if isinstance(coords, list) and len(coords) == 2:
        card["coordinates"] = {"lng": float(coords[0]), "lat": float(coords[1])}
    else:
        card["coordinates"] = None

    # ===== Телефоны =====
    phones_raw = item.get("phones") or []
    card["phones"] = [p.get("number") for p in phones_raw if isinstance(p, dict) and p.get("number")]

    # ===== Часы работы =====
    working_time = item.get("workingTime") or []
    by_day = []
    for day in working_time:
        if isinstance(day, list):
            slots = []
            for slot in day:
                f = slot.get("from") or {}
                t = slot.get("to") or {}
                slots.append({
                    "from": f"{f.get('hours', 0):02d}:{f.get('minutes', 0):02d}",
                    "to":   f"{t.get('hours', 0):02d}:{t.get('minutes', 0):02d}",
                })
            by_day.append(slots)
    current_status = item.get("currentWorkingStatus") or {}
    card["working_hours"] = {
        "text": item.get("workingTimeText"),
        "by_day": by_day,
        "is_open_now": bool(current_status.get("isOpenNow", False)),
        "unusual_hours": bp.get("unusual_hours") or [],
    }

    # ===== URLs (TapLink, сайт, соцсети) =====
    urls_raw = item.get("urls") or []
    social_links = item.get("socialLinks") or []
    business_links = item.get("businessLinks") or []

    taplink = next((u for u in urls_raw if isinstance(u, str) and "taplink" in u.lower()), None)
    website_candidates = [
        u for u in urls_raw
        if isinstance(u, str)
        and "taplink" not in u.lower()
        and "yandex" not in u.lower()
        and "instagram" not in u.lower()
    ]
    website = website_candidates[0] if website_candidates else None

    vk = next(
        (s.get("href") for s in social_links if isinstance(s, dict) and s.get("type") == "vkontakte"),
        None,
    )
    whatsapp = next(
        (s.get("href") for s in social_links if isinstance(s, dict) and s.get("type") == "whatsapp"),
        None,
    )
    booking = next(
        (b.get("href") for b in business_links if isinstance(b, dict) and b.get("type") == "booking"),
        None,
    )

    card["urls"] = {
        "website": website,
        "taplink": taplink,
        "vk": vk,
        "whatsapp": whatsapp,
        "booking": booking,
        "all": urls_raw,
    }

    # ===== Логотип =====
    business_images = item.get("businessImages") or {}
    logo = business_images.get("logo")
    if isinstance(logo, dict):
        url_template = logo.get("urlTemplate") or ""
        card["logo_url"] = url_template.replace("%s", "orig") if "%s" in url_template else url_template
    else:
        card["logo_url"] = None

    # ===== Услуги (главное!) =====
    top_objects = item.get("topObjects") or {}
    categories = top_objects.get("categories") or []
    services: List[Dict[str, Any]] = []
    for cat in categories:
        for service_item in (cat.get("categoryItems") or []):
            photo_link = service_item.get("photoLink") or ""
            services.append({
                "title": (service_item.get("title") or "").strip(),
                "description": (service_item.get("description") or "").strip(),
                "price": _parse_int(service_item.get("price")),
                "price_text": f"{service_item.get('price', '')} {service_item.get('currency', '')}".strip(),
                "currency": service_item.get("currency"),
                "photo_url": photo_link.replace("{size}", "orig") if "{size}" in photo_link else photo_link,
            })
    card["services"] = services
    card["services_total_count"] = top_objects.get("fullObjectsCount") or len(services)

    # ===== Фичи =====
    features = item.get("features") or []
    feature_groups = item.get("featureGroups") or []
    by_group: Dict[str, List[str]] = {}
    feature_by_id = {f.get("id"): f for f in features if isinstance(f, dict)}
    for grp in feature_groups:
        grp_name = grp.get("name") or "Прочее"
        feature_ids = grp.get("featureIds") or []
        items_text = []
        for fid in feature_ids:
            f = feature_by_id.get(fid)
            if not f:
                continue
            items_text.append(_render_feature(f))
        if items_text:
            # Транслитерируем русские названия групп в ASCII-ключи
            key = _slugify_group_name(grp_name)
            by_group[key] = items_text
    card["features_categorized"] = by_group

    # ===== Аспекты (фото с готовой семантикой) =====
    aspects_raw = item.get("aspects") or []
    aspects: List[Dict[str, Any]] = []
    for asp in aspects_raw:
        if not isinstance(asp, dict):
            continue
        photos_raw = asp.get("photos") or []
        photos_normalized = []
        for ph in photos_raw:
            if isinstance(ph, dict) and ph.get("link"):
                # link имеет формат "https://avatars.mds.yandex.net/get-altay/.../%"
                # %= placeholder для размера. Меняем на orig.
                url = ph["link"].rstrip("%") + "orig" if ph["link"].endswith("/%") else ph["link"].replace("/%", "/orig")
                photos_normalized.append(url)
        aspects.append({
            "id": asp.get("id"),
            "text": asp.get("text"),
            "count": asp.get("count"),
            "positive": asp.get("positive"),
            "neutral": asp.get("neutral"),
            "negative": asp.get("negative"),
            "photos": photos_normalized,
        })
    card["aspects"] = aspects

    # ===== Видео =====
    videos_raw = (item.get("videos") or {}).get("items") or []
    card["videos"] = [
        {
            "id": v.get("id"),
            "thumbnail_url": v.get("thumbnailUrl"),
            "video_url": v.get("videoUrl"),
            "width": v.get("width"),
            "height": v.get("height"),
        }
        for v in videos_raw if isinstance(v, dict)
    ]

    # ===== Отзывы превью =====
    review_results = item.get("reviewResults") or {}
    reviews_raw = review_results.get("reviews") or []
    reviews_preview = []
    for r in reviews_raw[:10]:
        if not isinstance(r, dict):
            continue
        reviews_preview.append({
            "review_id": r.get("reviewId"),
            "author": (r.get("author") or {}).get("name"),
            "text": (r.get("text") or "").strip(),
            "rating": r.get("rating"),
            "date": (r.get("updatedTime") or "")[:10],
            "photos": [
                p.get("urlTemplate", "").replace("{size}", "orig")
                for p in (r.get("photos") or []) if isinstance(p, dict)
            ],
            "business_reply": (r.get("businessComment") or {}).get("text"),
        })
    card["reviews_preview"] = reviews_preview
    card["reviews_total_count"] = (review_results.get("params") or {}).get("count")

    # ===== Подборки (discoveries) =====
    discoveries_raw = item.get("discoveries") or []
    card["discoveries"] = [
        {
            "alias": d.get("alias"),
            "title": d.get("title"),
            "place_number": d.get("placeNumber"),
            "partner": (d.get("partner") or {}).get("name"),
        }
        for d in discoveries_raw if isinstance(d, dict)
    ]

    # ===== Гистограмма =====
    histogram = item.get("histogramData") or {}
    card["histogram"] = histogram if isinstance(histogram, dict) else {}

    # ===== Панорама =====
    panorama = item.get("panorama")
    if isinstance(panorama, dict):
        point = (panorama.get("point") or {}).get("coordinates") or []
        card["panorama"] = {
            "preview_url": panorama.get("preview"),
            "lat": point[1] if len(point) == 2 else None,
            "lng": point[0] if len(point) == 2 else None,
        }
    else:
        card["panorama"] = None

    # ===== Meta =====
    card["_meta"] = {
        "parser_version": "2.0",
        "method": "state_view",
        "fallback_used": False,
        "fields_extracted": [k for k, v in card.items() if v not in (None, [], {}, "")],
    }

    return card


def _parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"\d+", value.replace(" ", ""))
        return int(m.group(0)) if m else None
    return None


def _render_feature(f: Dict[str, Any]) -> str:
    name = f.get("name") or f.get("id") or ""
    value = f.get("value")
    if isinstance(value, bool):
        return name
    if isinstance(value, (str, int, float)):
        return f"{name}: {value}" if name else str(value)
    if isinstance(value, list):
        # enum-список вариантов
        labels = [v.get("name") for v in value if isinstance(v, dict) and v.get("name")]
        return f"{name}: {', '.join(labels)}" if labels else name
    return name


def _slugify_group_name(name: str) -> str:
    """Транслит русского названия группы в ASCII-ключ."""
    translit_map = {
        "услуги": "services",
        "цены": "prices",
        "общая информация": "general",
        "доступность": "accessibility",
        "достижения": "achievements",
    }
    return translit_map.get(name.lower().strip(), "other")


async def enrich_lead_v2(
    lead_id: int,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Главная функция: загрузить лид из БД, найти карточку Яндекса,
    выкачать state-view, нормализовать в card.json, сохранить.
    """
    from sqlalchemy import select
    from db.database import get_async_session
    from db.models import Lead

    YANDEX_DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_log(f"🚀 [v2] Старт enrich_lead_v2 для lead_id={lead_id}")

    async with get_async_session() as session:
        res = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = res.scalar_one_or_none()
        if not lead:
            write_log(f"❌ Лид {lead_id} не найден")
            return None

        lead_name = lead.name or f"Lead {lead_id}"
        city = detect_city(lead.address)
        slug = slugify_name(lead_name, lead_id)

    # Папка лида: ASCII-only имя
    lead_dir = YANDEX_DATA_DIR / f"{slug}-{lead_id}"
    lead_dir.mkdir(parents=True, exist_ok=True)
    card_path = lead_dir / "card.json"
    raw_path = lead_dir / "raw_state.json"

    # Кеш: если есть card.json новее 24h и не force — возвращаем кеш
    if card_path.exists() and not force:
        import time
        age_sec = time.time() - card_path.stat().st_mtime
        if age_sec < 24 * 3600:
            write_log(f"♻️ [v2] Использую кеш (age={age_sec/60:.0f} min): {card_path}")
            with open(card_path, "r", encoding="utf-8") as f:
                return json.load(f)

    # Поиск карточки на Яндексе (переиспользуем существующий поиск)
    yandex_url = await search_lead_on_yandex(lead_name, city, str(lead.address or ""), lead_id=lead_id)
    if not yandex_url:
        write_log(f"⚠️ [v2] Не нашли карточку Яндекс для '{lead_name}'")
        return None

    # Добавим ?lang=ru для гарантии русской версии
    if "?" in yandex_url:
        full_url = f"{yandex_url}&lang=ru"
    else:
        full_url = f"{yandex_url}?lang=ru"

    # Скрейп state-view
    try:
        state = await scrape_state_view(full_url, lead_id=lead_id)
    except Exception as exc:
        write_log(f"❌ [v2] Ошибка скрейпа state-view: {exc}")
        return None

    # Сохраним сырой стейт для отладки
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    write_log(f"💾 [v2] Сырой state сохранён: {raw_path}")

    # Нормализация
    try:
        card = normalize_state(state, lead_id=lead_id, slug=slug, source_url=full_url)
    except Exception as exc:
        write_log(f"❌ [v2] Ошибка нормализации: {exc}")
        return None

    # Квалификация лида
    qualification = qualify_lead(card)
    card["qualification"] = qualification

    # Сохраним card.json
    with open(card_path, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=2)
    write_log(f"✅ [v2] card.json сохранён: {card_path}")
    write_log(
        f"   services={len(card.get('services', []))}, "
        f"aspects={len(card.get('aspects', []))}, "
        f"videos={len(card.get('videos', []))}, "
        f"reviews_preview={len(card.get('reviews_preview', []))}"
    )

    # Логирование квалификации
    if qualification["qualified"]:
        write_log(f"   🎯 Qualification: QUALIFIED (score={qualification['score']}/100) — {qualification['reason']}")
    else:
        write_log(f"   🚫 Qualification: NOT QUALIFIED (score={qualification['score']}/100) — {qualification['reason']}")

    # Запись в БД
    from sqlalchemy import update
    async with get_async_session() as session:
        try:
            await session.execute(
                update(Lead).where(Lead.id == lead_id).values(
                    qualification_status="qualified" if qualification["qualified"] else "not_qualified"
                )
            )
            await session.commit()
        except Exception as exc:
            # Если колонка не существует, игнорируем (будет добавлена миграцией)
            write_log(f"⚠️ [v2] Не удалось записать qualification_status в БД: {exc}")

    return card


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Yandex parser v2 (state-view)")
    parser.add_argument("lead_ids", nargs="+", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    for lead_id in args.lead_ids:
        try:
            result = asyncio.run(enrich_lead_v2(lead_id, force=args.force))
            if result:
                print(f"OK: lead_id={lead_id}, services={len(result.get('services', []))}")
            else:
                print(f"FAIL: lead_id={lead_id}")
        except Exception as exc:
            print(f"ERROR lead_id={lead_id}: {exc}")


if __name__ == "__main__":
    main()
