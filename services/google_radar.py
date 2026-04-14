#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Radar — снайперский модуль поиска потенциальных клиентов
Сохраняет ВСЕ компании: с сайтом (new) и без сайта (no_website)
"""

import aiohttp
import asyncio
import json
import os
import sys
from typing import Optional
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import init_db, upsert_lead, get_all_leads
from db.models import Lead

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
MAX_PAGES = 3
PAGE_DELAY = 2


async def fetch_place_details(session, place_id, api_key):
    """Получить детальную информацию о месте через Place Details API"""
    params = {
        "place_id": place_id,
        "key": api_key,
        "fields": "website,formatted_phone_number,reviews,url",
        "language": "ru",
    }
    try:
        async with session.get(PLACE_DETAILS_URL, params=params) as response:
            data = await response.json()
            if data.get("status") != "OK":
                print(f"[DETAILS ERROR] place_id={place_id}, status={data.get('status')}")
                return {}
            result = data.get("result", {})
            return {
                "website": result.get("website"),
                "phone": result.get("formatted_phone_number"),
                "reviews": result.get("reviews", []),
                "google_maps_url": result.get("url"),
            }
    except aiohttp.ClientError as e:
        print(f"[DETAILS ERROR] Ошибка запроса: {e}")
        return {}


def extract_negative_reviews(all_reviews):
    """Фильтрует только негативные отзывы (рейтинг <= 3)"""
    negative = []
    for rev in all_reviews:
        if rev.get("rating", 5) <= 3:
            negative.append({
                "rating": rev.get("rating"),
                "text": rev.get("text", ""),
                "time": rev.get("time", ""),
            })
    return negative


async def search_and_save(query, api_key, max_pages=MAX_PAGES, page_delay=PAGE_DELAY):
    """
    Снайперский поиск компаний с сохранением в БД.
    Сохраняем ВСЕ компании: с сайтом (new) и без сайта (no_website).
    """
    print("=" * 60)
    print(f"🎯 RADAR: Снайперский поиск")
    print(f"📍 Запрос: '{query}'")
    print("=" * 60)

    saved_leads = []

    async with aiohttp.ClientSession() as session:
        params = {"query": query, "key": api_key, "language": "ru"}
        page_count = 0
        next_page_token = None

        while page_count < max_pages:
            page_count += 1
            print(f"\n[PAGE {page_count}] Загрузка страницы...")

            if next_page_token:
                params["pagetoken"] = next_page_token
                print(f"[DELAY] Пауза {page_delay} сек...")
                await asyncio.sleep(page_delay)

            try:
                async with session.get(TEXT_SEARCH_URL, params=params) as response:
                    data = await response.json()
            except aiohttp.ClientError as e:
                print(f"[ERROR] Ошибка запроса: {e}")
                break

            status = data.get("status")
            if status not in ("OK", "ZERO_RESULTS"):
                print(f"[ERROR] API status: {status}")
                if data.get("error_message"):
                    print(f"[ERROR] {data.get('error_message')}")
                break

            if status == "ZERO_RESULTS":
                print("[INFO] Ничего не найдено")
                break

            results = data.get("results", [])
            print(f"[FOUND] {len(results)} результатов на странице")

            for place in results:
                place_id = place.get("place_id", "")
                name = place.get("name", "Unknown")
                website = place.get("website")
                phone = place.get("formatted_phone_number")
                rating = place.get("rating")
                reviews = place.get("user_ratings_total", 0)
                address = place.get("formatted_address", "")

                # Всегда запрашиваем детали для получения url, reviews и т.д.
                details = await fetch_place_details(session, place_id, api_key)
                if not website:
                    website = details.get("website")
                if not phone:
                    phone = details.get("phone")
                google_maps_url = details.get("google_maps_url")
                await asyncio.sleep(0.5)

                all_reviews = details.get("reviews", []) if details else []

                negative_reviews = extract_negative_reviews(all_reviews)
                raw_reviews_json = json.dumps(negative_reviews, ensure_ascii=False) if negative_reviews else None

                # Определяем статус: с сайтом -> new, без сайта -> no_website
                has_website = bool(website)
                lead_status = "new" if has_website else "no_website"

                # КРИТИЧЕСКОЕ ПРАВИЛО: сохраняем ТОЛЬКО если есть ссылка на Карты
                if not google_maps_url:
                    print(f"[SKIP] {name} — нет ссылки на Google Maps, игнорируем")
                    continue

                if negative_reviews:
                    print(f"[REVIEWS] {name} — {len(negative_reviews)} негативных отзывов")

                if not has_website:
                    print(f"[NO_SITE] {name} — нет сайта (no_website)")
                else:
                    print(f"[SAVE] {name} — {website}")

                # Сохраняем ВСЕ компании в БД (и с сайтом, и без)
                lead = await upsert_lead(
                    name=name, website=website, google_rating=rating,
                    reviews_count=reviews, address=address, phone=phone,
                    google_maps_url=google_maps_url,
                    status=lead_status, raw_reviews=raw_reviews_json,
                )
                saved_leads.append(lead)

            next_page_token = data.get("next_page_token")
            if not next_page_token:
                print("[INFO] Больше нет страниц")
                break

    with_site = sum(1 for l in saved_leads if l.website)
    without_site = sum(1 for l in saved_leads if not l.website)

    print("\n" + "=" * 60)
    print(f"✅ ЗАВЕРШЕНО: сохранено {len(saved_leads)} лидов")
    print(f"   🌐 С сайтом (new): {with_site}")
    print(f"   🚫 Без сайта (no_website): {without_site}")
    print("=" * 60)

    return saved_leads


async def show_saved_leads(status=None):
    """Показать сохраненные лиды из БД"""
    leads = await get_all_leads(status=status)
    print("\n" + "=" * 60)
    print(f"📊 БАЗА ДАННЫХ: {len(leads)} лидов")
    if status:
        print(f"   Фильтр: статус='{status}'")
    print("=" * 60)

    for i, lead in enumerate(leads, 1):
        print(f"\n{i}. {lead.name}")
        if lead.website:
            print(f"   🌐 {lead.website}")
        else:
            print(f"   🚫 Нет сайта")
        if lead.google_rating:
            print(f"   ⭐ {lead.google_rating} ({lead.reviews_count} отзывов)")
        if lead.address:
            print(f"   📍 {lead.address}")
        if lead.phone:
            print(f"   📞 {lead.phone}")
        if lead.raw_reviews:
            neg_count = len(json.loads(lead.raw_reviews))
            print(f"   💬 {neg_count} негативных отзывов")
        print(f"   🏷️ Статус: {lead.status}")


async def main():
    """Тестовый запуск"""
    await init_db()
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        print("[ERROR] GOOGLE_PLACES_API_KEY не найден в .env!")
        return
    print(f"[INFO] API ключ найден: {api_key[:10]}...")
    query = "частная стоматология метро Китай-город Москва"
    leads = await search_and_save(query, api_key)
    await show_saved_leads()


if __name__ == "__main__":
    asyncio.run(main())