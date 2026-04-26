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
from typing import Optional, List
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
REQUEST_TIMEOUT = 20  # таймаут запросов в секундах


def safe_print(text: str) -> None:
    """Безопасный print с защитой от charmap ошибок на Windows."""
    try:
        print(text, flush=True)
    except (UnicodeEncodeError, UnicodeError):
        print(text.encode('cp1251', errors='replace').decode('cp1251'), flush=True)


async def fetch_place_details(session, place_id, api_key):
    """Получить детальную информацию о месте через Place Details API"""
    params = {
        "place_id": place_id,
        "key": api_key,
        "fields": "website,formatted_phone_number,reviews,url",
        "language": "ru",
    }
    try:
        async with session.get(PLACE_DETAILS_URL, params=params, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as response:
            data = await response.json()
            if data.get("status") != "OK":
                safe_print(f"[RADAR] [DETAILS ERROR] place_id={place_id}, status={data.get('status')}")
                return {}
            result = data.get("result", {})
            return {
                "website": result.get("website"),
                "phone": result.get("formatted_phone_number"),
                "reviews": result.get("reviews", []),
                "google_maps_url": result.get("url"),
            }
    except asyncio.TimeoutError:
        safe_print(f"[RADAR] [DETAILS TIMEOUT] Таймаут запроса деталей place_id={place_id} ({REQUEST_TIMEOUT}с)")
        return {}
    except aiohttp.ClientError as e:
        safe_print(f"[RADAR] [DETAILS ERROR] Ошибка запроса: {e}")
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


async def search_and_save(query, api_key, max_pages=MAX_PAGES, page_delay=PAGE_DELAY, log_callback=None, category="other"):
    """
    Снайперский поиск компаний с сохранением в БД.
    Сохраняем ВСЕ компании: с сайтом (new) и без сайта (no_website).
    
    log_callback — optional async function для передачи логов в UI (Streamlit).
    category — категория/ниша для сохранения лидов (по умолчанию 'other').
    """
    async def log(message: str):
        safe_print(message)
        if log_callback:
            await log_callback(message)

    await log("=" * 60)
    await log("[RADAR] 🎯 RADAR: Снайперский поиск")
    await log(f"[RADAR] 📍 Запрос: '{query}'")
    await log(f"[RADAR] 📄 Макс. страниц: {max_pages}, задержка: {page_delay}с")
    await log("=" * 60)

    saved_leads = []

    try:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            params = {"query": query, "key": api_key, "language": "ru"}
            page_count = 0
            next_page_token = None

            while page_count < max_pages:
                page_count += 1
                await log(f"\n[RADAR] [PAGE {page_count}] Запрос страницы {page_count} по URL: {TEXT_SEARCH_URL}...")

                if next_page_token:
                    params["pagetoken"] = next_page_token
                    await log(f"[RADAR] [DELAY] Пауза {page_delay} сек...")
                    await asyncio.sleep(page_delay)

                try:
                    async with session.get(TEXT_SEARCH_URL, params=params, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as response:
                        data = await response.json()
                except asyncio.TimeoutError:
                    await log(f"[RADAR] [ERROR] Таймаут запроса к Google API (страница {page_count}, {REQUEST_TIMEOUT}с)")
                    break
                except aiohttp.ClientError as e:
                    await log(f"[RADAR] [ERROR] Ошибка запроса к Google API: {e}")
                    break

                status = data.get("status")
                if status not in ("OK", "ZERO_RESULTS"):
                    await log(f"[RADAR] [ERROR] API status: {status}")
                    if data.get("error_message"):
                        await log(f"[RADAR] [ERROR] {data.get('error_message')}")
                    break

                if status == "ZERO_RESULTS":
                    await log("[RADAR] [INFO] Ничего не найдено (ZERO_RESULTS)")
                    break

                results = data.get("results", [])
                await log(f"[RADAR] [FOUND] {len(results)} результатов на странице")

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
                        await log(f"[RADAR] [SKIP] {name} — нет ссылки на Google Maps, игнорируем")
                        continue

                    if negative_reviews:
                        await log(f"[RADAR] [REVIEWS] {name} — {len(negative_reviews)} негативных отзывов")

                    if not has_website:
                        await log(f"[RADAR] [NO_SITE] {name} — нет сайта (no_website)")
                    else:
                        await log(f"[RADAR] [SAVE] {name} — {website}")

                    # Сохраняем ВСЕ компании в БД (и с сайтом, и без)
                    lead = await upsert_lead(
                        name=name, website=website, google_rating=rating,
                        reviews_count=reviews, address=address, phone=phone,
                        google_maps_url=google_maps_url,
                        status=lead_status, raw_reviews=raw_reviews_json,
                        category=category,
                    )
                    saved_leads.append(lead)

                next_page_token = data.get("next_page_token")
                if not next_page_token:
                    await log("[RADAR] [INFO] Больше нет страниц")
                    break

    except Exception as e:
        error_msg = f"[ERROR] Критическая ошибка парсера: {str(e)}"
        safe_print(error_msg)
        import traceback
        safe_print(f"[ERROR] Traceback: {traceback.format_exc()}")
        # Пробрасываем ошибку дальше для обработки в UI
        raise

    with_site = sum(1 for l in saved_leads if l.website)
    without_site = sum(1 for l in saved_leads if not l.website)

    summary = f"\n{'=' * 60}\n✅ ЗАВЕРШЕНО: сохранено {len(saved_leads)} лидов\n   🌐 С сайтом (new): {with_site}\n   🚫 Без сайта (no_website): {without_site}\n{'=' * 60}"
    await log(summary)

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