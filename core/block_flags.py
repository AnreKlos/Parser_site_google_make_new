from config import settings
from typing import Any, Dict


def compute_block_flags(extracted: dict, photos: dict, yandex: dict, sections: dict = None) -> dict:
    """
    extracted — данные из data/extracted/{slug}.json
    photos    — photos_by_block или photo_map (hero/gallery/team/...)
    yandex    — данные из data/yandex/{slug}.json (рейтинг, отзывы и т.п.)
    sections  — конфигурация секций (sections.*.enabled) для согласованности

    Возвращает флаги, какие секции вообще должны рисоваться на сайте.
    """
    team_raw = extracted.get("team") or []
    services_raw = extracted.get("services") or extracted.get("serviceCarousel") or []
    faq_raw = extracted.get("faq") or extracted.get("faq_accordion") or []
    about_text = (extracted.get("about_text") or extracted.get("about") or "").strip()
    promotions_raw = extracted.get("promotions") or []
    hero_photos = photos.get("hero") or []
    gallery_photos = photos.get("gallery") or []
    team_photos = photos.get("team") or []

    yandex_data = yandex.get("yandex") if isinstance(yandex.get("yandex"), dict) else yandex
    rating = yandex_data.get("rating") if isinstance(yandex_data, dict) else None
    reviews = yandex_data.get("reviews") if isinstance(yandex_data, dict) else []
    reviews_count = len(reviews or [])

    phone = str(extracted.get("phone") or (yandex_data.get("phone") if isinstance(yandex_data, dict) else "")).strip()
    address = str(extracted.get("address") or (yandex_data.get("address") if isinstance(yandex_data, dict) else "")).strip()

    # TEAM: только если есть блок команды на сайте
    # Условие: в extracted["team"] есть записи с именем (name/first_name/last_name)
    real_team_items = []
    for item in team_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("first_name") or "").strip()
        if name:
            real_team_items.append(item)

    has_team_block = len(real_team_items) >= settings.min_team_items  # можно сделать >=2, если хотим "команду", а не одиночку

    block_flags = {
        # Hero нужен, если есть хотя бы одно hero-фото ИЛИ если включено в sections
        "hero": len(hero_photos) >= settings.min_hero_photos,

        # Галерея — если есть >= N нормальных фото
        "gallery": len(gallery_photos) >= settings.min_gallery_photos,

        # Команда — ТОЛЬКО если в extracted есть реальные мастера с именами.
        # Фото берем только из site_team / photos["team"], а не из Яндекса.
        "team": has_team_block and len(team_photos) >= settings.min_team_items,

        # Услуги — если реально есть услуги/категории
        "services": len(services_raw) >= 1,

        # FAQ — только если на сайте/в extracted есть вопросы-ответы
        "faq": len(faq_raw) >= 1,

        # Отзывы — только если есть рейтинг/отзывы от Яндекса или сайта
        "reviews": bool(rating) or reviews_count > 0,

        # About — если есть осмысленный текст "о салоне"
        "about": len(about_text) > 0,

        # Контакты — если есть телефон или адрес
        "contacts": bool(phone or address),

        # Акции — включаем по умолчанию, даже если нет промо (для будущего использования)
        "promotions": True,
    }

    # Согласованность с sections.enabled
    if sections and isinstance(sections, dict):
        # Если sections.hero.enabled = True, то block_flags.hero тоже должен быть True
        if sections.get("hero", {}).get("enabled"):
            block_flags["hero"] = True
        
        # Если sections.services.enabled = True и есть >= 1 услуга, то block_flags.services = True
        if sections.get("services", {}).get("enabled"):
            services_items = sections.get("services", {}).get("items", [])
            if isinstance(services_items, list) and len(services_items) >= 1:
                block_flags["services"] = True
        
        # Если sections.about.enabled = True и about.text не пустой, то block_flags.about = True
        if sections.get("about", {}).get("enabled"):
            if sections.get("about", {}).get("text"):
                block_flags["about"] = True
        
        # Если sections.faq.enabled = True и faq.items не пустой, то block_flags.faq = True
        if sections.get("faq", {}).get("enabled"):
            faq_items = sections.get("faq", {}).get("items", [])
            if isinstance(faq_items, list) and len(faq_items) >= 1:
                block_flags["faq"] = True
        
        # Если sections.price.enabled = True, то block_flags.price = True
        if sections.get("price", {}).get("enabled"):
            block_flags["price"] = True

    return block_flags
