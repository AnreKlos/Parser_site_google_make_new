#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from typing import Any, Dict


def compute_block_flags(extracted: dict, photos: dict, yandex: dict) -> dict:
    """
    extracted — данные из data/extracted/{slug}.json
    photos    — photos_by_block или photo_map (hero/gallery/team/...)
    yandex    — данные из data/yandex/{slug}.json (рейтинг, отзывы и т.п.)

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

    has_team_block = len(real_team_items) >= 1  # можно сделать >=2, если хотим "команду", а не одиночку

    block_flags = {
        # Hero нужен, если есть хотя бы одно hero-фото
        "hero": len(hero_photos) >= 1,

        # Галерея — если есть >=3 нормальных фото
        "gallery": len(gallery_photos) >= 3,

        # Команда — ТОЛЬКО если в extracted есть реальные мастера с именами.
        # Фото берем только из site_team / photos["team"], а не из Яндекса.
        "team": has_team_block and len(team_photos) >= 1,

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

        # Акции — только если реально есть промо
        "promotions": len(promotions_raw) >= 1,
    }

    return block_flags
