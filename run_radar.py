#!/usr/bin/env python3
"""Поиск обслуживания загородных домов (МО) + сохранение лидов"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from db.database import init_db, get_all_leads
from services.google_radar import search_and_save

NICHE = "cottage_service"

CITIES = [
    "Одинцово", "Истра", "Дмитров", "Пушкино", "Наро-Фоминск",
    "Домодедово", "Клин", "Солнечногорск", "Ногинск",
    "Сергиев Посад", "Волоколамск", "Можайск",
]

QUERIES = [
    "обслуживание загородных домов",
    "сервисная компания коттедж",
    "обслуживание коттеджных посёлков",
    "обслуживание септиков",
    "ассенизатор",
    "обслуживание скважин",
    "обслуживание котлов отопления",
    "клининг коттеджей",
]

async def main():
    await init_db()
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        print("GOOGLE_PLACES_API_KEY не найден в .env")
        return

    total = 0
    for city in CITIES:
        for query in QUERIES:
            full_query = f"{query} {city}"
            print(f"\nПоиск: {full_query}")
            leads = await search_and_save(full_query, api_key, max_pages=2, category=NICHE)
            total += len(leads)
            print(f"   Найдено: {len(leads)}")
            await asyncio.sleep(3)

    print(f"\nВСЕГО СОБРАНО: {total} лидов")
    all_leads = await get_all_leads()
    cottage = sum(1 for l in all_leads if l.category == NICHE)
    with_site = sum(1 for l in all_leads if l.website)
    print(f"   cottage_service: ~{cottage}")
    print(f"   С сайтом: {with_site}")
    print(f"   Без сайта: {len(all_leads) - with_site}")

asyncio.run(main())
