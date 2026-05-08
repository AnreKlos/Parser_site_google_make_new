"""Native config builder v2 — works directly with card.json + photo_map.json."""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from enrichment.card_loader import CardBundle, load_card_bundle, load_curated_optional
from utils.text import detect_city, slugify_name

from config_builder.defaults import (
    DEFAULT_CHAT_WIDGET, DEFAULT_CONTENT, DEFAULT_LEGAL,
    DEFAULT_SECTION_ORDER, DEFAULT_TOKENS,
)
from config_builder.io import load_lead, log
from config_builder.photo_renderer import build_block_photos, get_hero_photo


PUBLIC_DIR = settings.public_dir


# ==============================================================================
# Public entry-point
# ==============================================================================

def build_config_v2(lead_id: int) -> Dict[str, Any]:
    """
    Сборка config-объекта для шаблона neuralsync, на основе card.json+photo_map.json.

    Raises:
        RuntimeError: если лид не найден в БД или card.json отсутствует.
    """
    lead = load_lead(lead_id)
    if not lead:
        raise RuntimeError(f"Лид ID={lead_id} не найден в БД")

    lead_name = str(lead.get("name") or f"Lead {lead_id}")
    bundle = load_card_bundle(lead_id, lead_name)
    if not bundle.available:
        raise RuntimeError(
            f"card.json не найден для lead_id={lead_id}. "
            f"Запусти `python -m enrichment.yandex_state {lead_id} --force` сначала. "
            f"Деталь: {bundle.error}"
        )

    log(f"📖 [v2] Читаю card.json: services={len(bundle.card.get('services') or [])}, "
        f"aspects={len(bundle.card.get('aspects') or [])}, "
        f"reviews={len(bundle.card.get('reviews_preview') or [])}")

    qualification = bundle.qualification
    if not qualification.get("qualified"):
        log(f"⚠️  [v2] Лид НЕ КВАЛИФИЦИРОВАН: {qualification.get('reason')}")
        log(f"⚠️  [v2] Конфиг будет собран, но шаблон может выглядеть пусто")

    # Опциональные curated данные (AI-tagline, AI-FAQ)
    curated = load_curated_optional(bundle.slug, lead_id)
    if curated:
        log(f"📖 [v2] Подключён curated: tagline, faq={len(curated.get('faq') or [])}")

    # === Сборка блоков ===
    meta = _build_meta(bundle, curated)
    contacts = _build_contacts(bundle, lead)
    sections = _build_sections(bundle, lead, curated)
    block_flags = _build_block_flags(sections)

    config: Dict[str, Any] = {
        "meta": meta,
        "contacts": contacts,
        "booking": _build_booking(bundle),
        "social": _build_social(bundle, lead),
        "tokens": DEFAULT_TOKENS,
        "legal": DEFAULT_LEGAL,
        "content": DEFAULT_CONTENT,
        "sectionsOrder": _build_section_order(sections),
        "copyrightYear": datetime.now().year,
        "sections": sections,
        "features": {"chatWidget": DEFAULT_CHAT_WIDGET},
        "block_flags": block_flags,
        "_meta": {
            "builder_version": "2.0",
            "card_schema": bundle.card.get("schema_version"),
            "qualification": qualification,
        },
    }

    return config


# ==============================================================================
# Block builders
# ==============================================================================

def _build_meta(bundle: CardBundle, curated: Dict[str, Any]) -> Dict[str, Any]:
    """meta block: brand, tagline, city, награды."""
    card = bundle.card
    name = (card.get("title") or card.get("short_title") or "").strip()
    short_name = _strip_brand_noise(name)
    address = card.get("address") or {}
    city = address.get("locality") or detect_city(address.get("full") or "")

    # Tagline: curated > Yandex-derived
    curated_tagline = ((curated.get("meta") or {}).get("tagline") or "").strip()
    if curated_tagline:
        tagline = curated_tagline
    else:
        # Простой fallback — соберём из города + услуг
        city_prep = _city_prepositional(city)
        services = card.get("services") or []
        first_two_services = [s.get("title", "") for s in services[:2] if s.get("title")]
        if first_two_services:
            tagline = f"Салон красоты в {city_prep}: {', '.join(first_two_services).lower()}"
        else:
            tagline = f"Салон красоты в {city_prep}" if city_prep else "Салон красоты"

    meta = {
        "slug": bundle.slug,
        "brand": {
            "name": name,
            "shortName": short_name,
            "slug": bundle.slug,
            "tagline": tagline,
        },
        "name": name,
        "fullName": f"{name} — {tagline}" if tagline else name,
        "tagline": tagline,
        "city": city or "",
    }

    # Награда «Хорошее место» — серьёзный trust-signal, добавим в meta
    if card.get("good_place_year"):
        meta["awards"] = [{
            "type": "good_place",
            "year": card["good_place_year"],
            "label": f"Хорошее место {card['good_place_year']}",
        }]

    # Yandex Maps URL для кнопки "Все отзывы на Яндекс.Картах"
    source_url = (card.get("source_url") or "").strip()
    if source_url:
        meta["yandexUrl"] = source_url

    return meta


def _build_contacts(bundle: CardBundle, lead: Dict[str, Any]) -> Dict[str, Any]:
    """contacts block: phones, address, coordinates, hours."""
    card = bundle.card
    address = card.get("address") or {}
    phones = card.get("phones") or []
    main_phone = phones[0] if phones else (lead.get("phone") or "")

    hours_raw = ((card.get("working_hours") or {}).get("text") or "").strip()
    hours_formatted = _format_working_hours(hours_raw)

    contacts: Dict[str, Any] = {
        "phone": _format_phone(main_phone),
        "phoneRaw": _to_phone_raw(main_phone),
        "phones": [_format_phone(p) for p in phones],
        "whatsapp": _to_phone_raw(main_phone) if main_phone else "",
        "address": address.get("full") or (lead.get("address") or ""),
        "addressNote": address.get("additional") or None,
        "additionalAddresses": [],
        "workingHours": hours_formatted,
        "hours": hours_formatted,
    }

    coords = card.get("coordinates")
    if isinstance(coords, dict) and coords.get("lat") and coords.get("lng"):
        contacts["coordinates"] = {"lat": float(coords["lat"]), "lng": float(coords["lng"])}

    # Чистим None
    return {k: v for k, v in contacts.items() if v not in (None, "")}


def _build_booking(bundle: CardBundle) -> Dict[str, Any]:
    """booking block: ссылка на онлайн-запись."""
    booking_url = ((bundle.card.get("urls") or {}).get("booking") or "").strip()
    return {"url": booking_url} if booking_url else {}


def _build_social(bundle: CardBundle, lead: Dict[str, Any]) -> List[Dict[str, str]]:
    """social block: VK, TG, IG из card.urls + lead."""
    urls = bundle.card.get("urls") or {}
    out: List[Dict[str, str]] = []
    name = bundle.card.get("title") or ""

    candidates = [
        ("vk", "ВКонтакте", "VK"),
        ("telegram", "Telegram", "TG"),
        ("instagram", "Instagram", "IG"),
        ("taplink", "TapLink", "TL"),
        ("whatsapp", "WhatsApp", "WA"),
    ]
    for key, label, short in candidates:
        href = (urls.get(key) or "").strip()
        if href:
            out.append({"href": href, "label": f"{label} {name}".strip(), "short": short})

    return out


def _build_sections(
    bundle: CardBundle, lead: Dict[str, Any], curated: Dict[str, Any]
) -> Dict[str, Any]:
    """Все sections.* за один проход."""
    card = bundle.card
    photo_map = bundle.photo_map
    slug = bundle.slug
    lead_id = bundle.lead_id

    # Hero
    hero_photo = get_hero_photo(photo_map, slug, lead_id, PUBLIC_DIR)
    hero_image = hero_photo["src"] if hero_photo else ""

    # Gallery
    gallery_items = build_block_photos(photo_map, "gallery", slug, lead_id, PUBLIC_DIR)

    # About
    about_items = build_block_photos(photo_map, "about", slug, lead_id, PUBLIC_DIR)

    # Team
    team_photos = build_block_photos(photo_map, "team", slug, lead_id, PUBLIC_DIR)
    # Team в шаблоне ожидает {name, role, image}. У нас name/role нет — только photo.
    # Для нативного v2: если photo_map дал team-фото с captioned_text — это и есть имя.
    # Пока: если фото есть, конструируем минимальный объект; если нет — блок выкл.
    team_items = []
    for p in team_photos:
        team_items.append({
            "name": p.get("description") or "Мастер",  # как fallback
            "role": "Мастер",
            "image": p["src"],
            "alt": p.get("alt") or "",
        })

    # Services — из curated.services если есть, иначе из card.services + photo_map.services
    services_items = _build_services_items(card, photo_map, slug, lead_id, curated)

    # Reviews — из card.reviews_preview
    reviews_items = _build_reviews_items(card)

    # About text
    about_text = _build_about_text(card, curated)

    # FAQ
    faq_items = _build_faq_items(curated, card)

    sections = {
        "hero": _build_hero_section(card, lead, hero_image, hero_photo, curated),
        "promotion": {"enabled": True},
        "services": {
            "enabled": bool(services_items),
            "items": services_items,
        },
        "gallery": {
            "enabled": len(gallery_items) >= 3,
            "title": "Наши работы",
            "subtitle": "Каждая деталь имеет значение",
            "items": gallery_items,
        },
        "team": {
            "enabled": bool(team_items),
            "items": team_items,
        },
        "reels": _build_reels(card),
        "reviews": {
            "enabled": bool(reviews_items),
            "items": reviews_items,
        },
        "about": {
            "enabled": bool(about_text),
            "showImages": bool(about_items),
            "text": about_text,
            "images": about_items,
        },
        "faq": {
            "enabled": bool(faq_items),
            "items": faq_items,
        },
        "bookingContacts": {
            "enabled": True,
            "showMap": True,
        },
    }

    # Price section отдельно — детальный прайс из чистых services
    price_section = _build_price_section(card)
    if price_section:
        sections["price"] = price_section

    return sections


def _build_hero_section(
    card: Dict[str, Any], lead: Dict[str, Any],
    hero_image: str, hero_photo: Dict[str, Any] | None,
    curated: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """hero block."""
    name = card.get("title") or ""
    address = card.get("address") or {}
    city = address.get("locality") or ""
    city_prep = _city_prepositional(city)
    cat = (lead.get("category") or "").lower()

    title_line1 = "Студия маникюра" if "nail" in cat else "Студия красоты"
    title_line1_small = "идеального маникюра" if "nail" in cat else "по созданию образа"

    # Lead — используем curated tagline если есть, иначе собираем из реальных данных
    curated_tagline = ((curated.get("meta") or {}).get("tagline") or "").strip()
    if curated_tagline:
        lead_text = curated_tagline
    else:
        rating = card.get("rating")
        review_count = card.get("review_count") or card.get("rating_count") or 0
        good_place = card.get("good_place_year")

        lead_parts = []
        if city_prep:
            lead_parts.append(f"Студия красоты в {city_prep}")
        if rating and review_count and review_count >= 30:
            lead_parts.append(f"рейтинг {rating} на основе {review_count} отзывов")
        if good_place:
            lead_parts.append(f"награда «Хорошее место {good_place}»")

        lead_text = ". ".join(lead_parts) + "." if lead_parts else _short_about_text(card)

    # topLabel: если есть реальный booking.url → "запись онлайн", иначе только город
    booking_url = ((card.get("urls") or {}).get("booking") or "").strip()
    has_online_booking = bool(booking_url and booking_url not in (lead.get("website") or ""))
    if has_online_booking and city_prep:
        top_label = f"{city_prep} · запись онлайн"
    elif city_prep:
        top_label = city_prep
    else:
        top_label = ""

    return {
        "enabled": True,
        "image": hero_image,
        "imageAlt": (hero_photo or {}).get("alt") if hero_photo else "",
        "titleLine1": title_line1,
        "titleLine1Small": title_line1_small,
        "titleLine1SmallSize": "default",
        "titleLine2": _strip_brand_noise(name),
        "topLabel": top_label,
        "lead": lead_text,
        "ctaLabel": "Записаться",
    }


def _build_services_items(
    card: Dict[str, Any], photo_map: Dict[str, Any],
    slug: str, lead_id: int, curated: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """
    services_items для блока Services. Сначала из curated.services если есть,
    иначе из card.services (чистые!), дополняем фото из photo_map.services если есть.
    """
    photo_items = build_block_photos(photo_map, "services", slug, lead_id, PUBLIC_DIR)

    # Индекс фото по service_ref для матчинга
    photos_by_ref = {}
    for p in photo_items:
        ref = (p.get("serviceRef") or "").lower().strip()
        if ref and ref not in photos_by_ref:
            photos_by_ref[ref] = p["src"]

    # Если есть curated services — используем их
    if curated and curated.get("services"):
        raw_services = curated.get("services") or []
        items = []
        for svc in raw_services:
            title = (svc.get("title") or "").strip()
            if not title:
                continue
            description = (svc.get("description") or "").strip()
            short = (svc.get("short") or "").strip()
            price_text = (svc.get("priceFrom") or "").strip()
            if not price_text:
                continue

            # Фото услуги: из photo_map.services по service_ref
            photo_src = photos_by_ref.get(_guess_service_ref(title))

            # Фильтруем шаблонные копипасты описаний
            if _is_template_description(description):
                description = ""

            items.append({
                "title": title,
                "short": short if short else None,
                "description": description,
                "priceFrom": price_text or "по запросу",
                "image": photo_src or None,
            })

        # Очистка None
        items = [{k: v for k, v in i.items() if v is not None} for i in items]
        return items[:12]
    
    # Fallback: из card.services
    raw_services = card.get("services") or []
    items = []
    for svc in raw_services:
        title = (svc.get("title") or "").strip()
        if not title:
            continue
        price_raw = svc.get("price")
        price_text_raw = (svc.get("price_text") or "").strip()
        if not price_raw and not price_text_raw:
            continue
        description = (svc.get("description") or "").strip()
        price_text = price_text_raw
        if not price_text and svc.get("price"):
            price_text = f"{svc['price']} {svc.get('currency') or '₽'}".strip()

        # Фото услуги: 1) из card.services.photo_url напрямую (CDN URL),
        # 2) либо из photo_map.services по service_ref
        photo_src = svc.get("photo_url") or photos_by_ref.get(_guess_service_ref(title))

        # Фильтруем шаблонные копипасты описаний
        if _is_template_description(description):
            description = ""

        items.append({
            "title": title,
            "short": description if description else None,
            "description": description,
            "priceFrom": price_text or "по запросу",
            "image": photo_src or None,
        })

    # Очистка None
    items = [{k: v for k, v in i.items() if v is not None} for i in items]
    return items[:12]  # шаблон обычно показывает 5-9


def _is_template_description(text: str) -> bool:
    """Проверяет, является ли описание шаблонной копипастой от Yandex."""
    if not text:
        return False
    lower = text.lower()
    template_phrases = [
        "грейдирование мастеров",
        "мастер, топ-мастер",
        "мастера-эксперты",
        "все специалисты проходят регулярные обучения",
        "по стоимости вы можете проконсультироваться",
        "все стоимости вы можете проконсультироваться",
    ]
    return any(phrase in lower for phrase in template_phrases)


def _guess_service_ref(title: str) -> str:
    """Грубое отображение русского названия услуги в service_ref enum."""
    t = title.lower()
    if "маникюр" in t: return "manicure"
    if "педикюр" in t: return "pedicure"
    if "окрашивание" in t or "колорист" in t: return "coloring"
    if "стрижк" in t: return "haircut"
    if "ресниц" in t: return "lashes"
    if "бров" in t: return "brows"
    if "макияж" in t: return "makeup"
    return "other"


def _build_reviews_items(card: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reviews из card.reviews_preview."""
    raw = card.get("reviews_preview") or []
    items = []
    for r in raw:
        text = (r.get("text") or "").strip()
        author = (r.get("author") or "").strip()
        if not text or not author or len(text) < 20:
            continue
        item = {
            "author": author,
            "text": text,
            "rating": r.get("rating"),
            "date": r.get("date") or "",
        }
        if r.get("business_reply"):
            item["businessReply"] = r["business_reply"]
        items.append(item)
    return items[:6]


def _build_about_text(card: Dict[str, Any], curated: Dict[str, Any]) -> str:
    """About text: curated > yandex-derived."""
    curated_text = (curated.get("about") or "").strip()
    if curated_text:
        return curated_text

    # Fallback: соберём из данных card
    name = card.get("title") or "Салон"
    address = card.get("address") or {}
    rating = card.get("rating")
    review_count = card.get("review_count")
    parts = [f"{name} в городе {address.get('locality')}." if address.get("locality") else f"{name}."]
    if rating and review_count:
        parts.append(f"Рейтинг {rating} из 5 на основе {review_count} отзывов.")
    if card.get("good_place_year"):
        parts.append(f"Награда «Хорошее место {card['good_place_year']}».")
    return " ".join(parts)


def _build_faq_items(curated: Dict[str, Any], card: Dict[str, Any]) -> List[Dict[str, str]]:
    """FAQ из curated, иначе сгенерим базовый."""
    curated_faq = curated.get("faq") or []
    if curated_faq:
        items = [{"q": x.get("q", "").strip(), "a": x.get("a", "").strip()}
                 for x in curated_faq if x.get("q") and x.get("a")]
        if items:
            return items[:8]

    # Fallback: 2-3 стандартных вопроса из данных
    address = card.get("address") or {}
    phones = card.get("phones") or []
    items = []
    if address.get("full"):
        items.append({"q": "Где находится салон?", "a": f"Салон находится по адресу: {address['full']}."})
    if phones:
        items.append({"q": "Как записаться?", "a": f"Позвоните по телефону {phones[0]} или оставьте заявку на сайте."})
    services = card.get("services") or []
    if services:
        s_titles = [s.get("title") for s in services[:3] if s.get("title")]
        if s_titles:
            items.append({"q": "Какие услуги вы предоставляете?", "a": f"Среди наших основных услуг: {', '.join(s_titles)} и многое другое."})
    return items


def _build_reels(card: Dict[str, Any]) -> Dict[str, Any]:
    """Reels из card.videos. Шаблон может уметь — пока выключим, оставим items."""
    videos = card.get("videos") or []
    items = []
    for v in videos[:9]:
        items.append({
            "id": v.get("id"),
            "thumbnail": v.get("thumbnail_url"),
            "video": v.get("video_url"),
            "width": v.get("width"),
            "height": v.get("height"),
        })
    return {
        "enabled": False,  # включим вручную если шаблон поддержит
        "items": items,
    }


def _categorize_price_item(title: str) -> str:
    """Группировка услуги по категории для прайса."""
    if not title:
        return "Прочее"
    
    lower = title.lower()
    
    # Проверяем по приоритету: ресницы → брови → педикюр → маникюр → макияж → волосы → прочее
    
    # Ресницы
    if any(k in lower for k in ["ресниц", "наращивание ресн", "ламинирован ресн"]):
        return "Ресницы"
    
    # Брови
    if any(k in lower for k in ["бров", "brow"]):
        return "Брови"
    
    # Педикюр
    if any(k in lower for k in ["педикюр", "smart-педикюр", " стоп"]):
        return "Педикюр"
    
    # Маникюр (но не педикюр)
    if "педикюр" not in lower and any(k in lower for k in ["маникюр", "ногт", "гель-лак", " gel "]):
        return "Маникюр"
    
    # Макияж
    if any(k in lower for k in ["макияж", "мэйкап", "makeup", "визаж"]):
        return "Макияж"
    
    # Причёски и волосы (но не брови/ресницы)
    if not any(k in lower for k in ["бров", "ресниц"]):
        hair_keywords = [
            "причёск", "причес", "окраш волос", "стрижк", "укладк", 
            "окрашиван", "мелирован", "тонирован", "ламинирован волос",
            "кератин", "ботокс волос", "восстановлен волос", "airtouch"
        ]
        if any(k in lower for k in hair_keywords):
            return "Причёски и волосы"
    
    return "Прочее"


def _build_price_section(card: Dict[str, Any]) -> Dict[str, Any] | None:
    """Price из чистых card.services. Без is_junk-фильтров — данные уже чистые."""
    services = card.get("services") or []
    if not services:
        return None
    items = []
    for s in services:
        title = (s.get("title") or "").strip()
        if not title or len(title) < 3:
            continue
        if not s.get("price") and not (s.get("price_text") or "").strip():
            continue
        price_text = (s.get("price_text") or "").strip()
        if not price_text and s.get("price"):
            price_text = f"{s['price']} ₽"
        description = (s.get("description") or "").strip()
        # Фильтруем шаблонные копипасты
        if _is_template_description(description):
            description = ""
        items.append({
            "title": title,
            "price": price_text or "по запросу",
            "priceFrom": False,
            "duration_min": None,
            "description": description or None,
        })
    if not items:
        return None
    
    # Группировка по категориям
    category_order = ["Ресницы", "Брови", "Педикюр", "Маникюр", "Макияж", "Причёски и волосы", "Прочее"]
    groups_by_category: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        category = _categorize_price_item(item["title"])
        if category not in groups_by_category:
            groups_by_category[category] = []
        groups_by_category[category].append(item)
    
    # Формируем groups в нужном порядке
    groups = []
    for category in category_order:
        category_items = groups_by_category.get(category, [])
        if category_items:
            # Если всего одна группа — title=None, иначе название категории
            group_title = None if len(groups_by_category) == 1 else category
            groups.append({
                "title": group_title,
                "items": category_items,
            })
    
    return {
        "enabled": True,
        "title": "Прайс",
        "subtitle": None,
        "note": "Окончательная стоимость уточняется при записи.",
        "groups": groups,
    }


def _build_section_order(sections: Dict[str, Any]) -> List[str]:
    """Финальный sectionsOrder из включённых блоков."""
    order = list(DEFAULT_SECTION_ORDER)
    if sections.get("price", {}).get("enabled"):
        # Вставим price после services, или перед gallery
        if "services" in order:
            order.insert(order.index("services") + 1, "price")
        else:
            order.insert(order.index("gallery") if "gallery" in order else 0, "price")
    # Удаляем выключенные
    return [s for s in order if sections.get(s, {}).get("enabled", False) or s in ("hero", "bookingContacts", "promotion")]


def _build_block_flags(sections: Dict[str, Any]) -> Dict[str, bool]:
    """block_flags — простой словарь enabled-флагов."""
    return {
        "hero": sections.get("hero", {}).get("enabled", False),
        "gallery": sections.get("gallery", {}).get("enabled", False),
        "team": sections.get("team", {}).get("enabled", False),
        "services": sections.get("services", {}).get("enabled", False),
        "faq": sections.get("faq", {}).get("enabled", False),
        "reviews": sections.get("reviews", {}).get("enabled", False),
        "about": sections.get("about", {}).get("enabled", False),
        "contacts": True,
        "promotions": True,
        "price": sections.get("price", {}).get("enabled", False),
    }


# ==============================================================================
# Helpers
# ==============================================================================

# ==============================================================================
# City prepositional case (Предложный падеж)
# ==============================================================================

CITY_PREPOSITIONAL = {
    "Брянск": "Брянске",
    "Москва": "Москве",
    "Санкт-Петербург": "Санкт-Петербурге",
    "Новосибирск": "Новосибирске",
    "Екатеринбург": "Екатеринбурге",
    "Казань": "Казани",
    "Нижний Новгород": "Нижнем Новгороде",
    "Челябинск": "Челябинске",
    "Самара": "Самаре",
    "Омск": "Омске",
    "Ростов-на-Дону": "Ростове-на-Дону",
    "Уфа": "Уфе",
    "Красноярск": "Красноярске",
    "Воронеж": "Воронеже",
    "Пермь": "Перми",
    "Волгоград": "Волгограде",
    "Краснодар": "Краснодаре",
    "Саратов": "Саратове",
    "Тюмень": "Тюмени",
    "Тольятти": "Тольятти",
    "Ижевск": "Ижевске",
    "Барнаул": "Барнауле",
    "Ульяновск": "Ульяновске",
    "Иркутск": "Иркутске",
    "Хабаровск": "Хабаровске",
    "Ярославль": "Ярославле",
    "Владивосток": "Владивостоке",
    "Махачкала": "Махачкале",
    "Томск": "Томске",
    "Оренбург": "Оренбурге",
    "Кемерово": "Кемерово",
    "Новокузнецк": "Новокузнецке",
    "Рязань": "Рязани",
    "Астрахань": "Астрахани",
    "Пенза": "Пензе",
    "Липецк": "Липецке",
    "Тула": "Туле",
    "Киров": "Кирове",
    "Чебоксары": "Чебоксарах",
    "Калининград": "Калининграде",
    "Курск": "Курске",
    "Ставрополь": "Ставрополе",
    "Сочи": "Сочи",
    "Орёл": "Орле",
    "Орел": "Орле",
    "Тверь": "Твери",
    "Белгород": "Белгороде",
    "Иваново": "Иваново",
    "Брянска": "Брянске",
}


def _city_prepositional(city: str) -> str:
    """Возвращает предложный падеж города. Если не нашли — возвращаем как есть."""
    if not city:
        return ""
    city = city.strip()
    return CITY_PREPOSITIONAL.get(city, city)


def _format_working_hours(text: str) -> str:
    """
    Конвертирует "10:00 AM–8:00 PM" → "10:00–20:00".
    Обрабатывает: AM, PM, am, pm, A.M., P.M.
    """
    if not text:
        return ""
    
    def to_24h(match):
        hour = int(match.group(1))
        minute = match.group(2) or "00"
        meridiem = match.group(3).upper().replace(".", "")
        if meridiem == "PM" and hour != 12:
            hour += 12
        elif meridiem == "AM" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute}"
    
    pattern = r"(\d{1,2}):?(\d{2})?\s*(A\.?M\.?|P\.?M\.?)"
    return re.sub(pattern, to_24h, text, flags=re.IGNORECASE)


def _strip_brand_noise(name: str) -> str:
    noise = {"студия", "красоты", "салон", "beauty", "studio", "center", "центр", "spa", "спа"}
    words = (name or "").strip().split()
    filtered = [w for w in words if w.lower() not in noise]
    return " ".join(filtered).strip() or (name or "").strip()


def _format_phone(phone: str) -> str:
    """+7 (XXX) XXX-XX-XX."""
    if not phone: return ""
    digits = re.sub(r"[^\d]", "", phone)
    if digits.startswith("8"): digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return phone


def _to_phone_raw(phone: str) -> str:
    return re.sub(r"[^\d+]", "", phone or "")


def _short_about_text(card: Dict[str, Any]) -> str:
    """Короткий текст для hero.lead — первое предложение из about или сгенерим."""
    name = card.get("title") or "Салон"
    address = card.get("address") or {}
    locality = address.get("locality") or ""
    locality_prep = _city_prepositional(locality)
    street_house = " ".join([
        address.get("street") or "",
        address.get("house") or "",
    ]).strip()
    if locality_prep and street_house:
        return f"{name} в {locality_prep}, {street_house}."
    if locality_prep:
        return f"{name} в {locality_prep}."
    return name
