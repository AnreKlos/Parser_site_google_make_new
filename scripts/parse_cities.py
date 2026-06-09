#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для извлечения города из поля address у лидов, у которых city IS NULL.

Читает лиды с address, парсит город по шаблонам:
- "г. Брянск, ул. Димитрова, 60" -> "Брянск"
- "Москва, Ленинский пр-т, 10" -> "Москва"
- "ул. Ленина, д.1, Москва, Россия, 101000" -> "Москва"
- "Московский пр., 1, Брянск, Брянская обл., Россия" -> "Брянск"

Обновляет city в БД и выводит статистику.
"""

import sqlite3
import re
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "leads.db"

# Список известных городов России для проверки
KNOWN_RUSSIAN_CITIES: set[str] = {
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Красноярск", "Нижний Новгород", "Челябинск", "Уфа", "Самара",
    "Ростов-на-Дону", "Краснодар", "Омск", "Воронеж", "Пермь",
    "Волгоград", "Саратов", "Тюмень", "Тольятти", "Ижевск",
    "Барнаул", "Ульяновск", "Иркутск", "Хабаровск", "Ярославль",
    "Владивосток", "Махачкала", "Томск", "Оренбург", "Кемерово",
    "Новокузнецк", "Рязань", "Астрахань", "Набережные Челны", "Пенза",
    "Липецк", "Тула", "Киров", "Чебоксары", "Калининград",
    "Брянск", "Курск", "Иваново", "Магнитогорск", "Тверь",
    "Симферополь", "Севастополь", "Владимир", "Ставрополь", "Сочи",
    "Архангельск", "Белгород", "Смоленск", "Мурманск", "Великий Новгород",
    "Якутск", "Чита", "Грозный", "Волжский", "Петрозаводск",
    "Кострома", "Новороссийск", "Тамбов", "Орёл", "Шахты",
    "Дзержинск", "Братск", "Ангарск", "Энгельс", "Благовещенск",
    "Королёв", "Подольск", "Мытищи", "Люберцы", "Коломна",
    "Серпухов", "Химки", "Балашиха", "Домодедово", "Одинцово",
    "Красногорск", "Железнодорожный", "Щёлково", "Пушкино", "Видное",
    "Лобня", "Дмитров", "Ступино", "Раменское", "Ногинск",
    "Электросталь", "Реутов", "Долгопрудный", "Фрязино", "Клин",
    "Сергиев Посад", "Воскресенск", "Истра", "Можайск", "Волоколамск",
    "Зеленоград", "Калуга", "Обнинск", "Дубна", "Черноголовка",
    "Пущино", "Протвино", "Троицк", "Московский", "Щербинка",
}


def extract_city_from_address(address: str) -> str | None:
    """
    Извлекает название города из адреса.
    """
    if not address or not isinstance(address, str):
        return None

    address = address.strip()

    # Паттерн 1: "г. Город" или "г Город" — самый надёжный
    m = re.search(r'\bг\.?\s*([А-ЯЁ][а-яё\-]+(?:-[А-ЯЁ][а-яё]+)?)', address)
    if m:
        return m.group(1)

    # Паттерн 2: "город Город"
    m = re.search(r'\bгород\s+([А-ЯЁ][а-яё\-]+(?:-[А-ЯЁ][а-яё]+)?)', address)
    if m:
        return m.group(1)

    # Паттерн 3: Разбиваем по запятым и ищем город
    parts = [p.strip() for p in address.split(",")]

    # Сначала ищем известный город среди частей
    for part in parts:
        part_clean = part.strip().rstrip(".")
        if part_clean in KNOWN_RUSSIAN_CITIES:
            return part_clean

    # Ищем часть перед "Россия"
    for i, part in enumerate(parts):
        if "россия" in part.lower():
            if i > 0:
                prev = parts[i - 1].strip()
                obls = ["область", "край", "республика", "ао", "обл."]
                if not any(prev.lower().startswith(o) for o in obls):
                    prev_clean = re.sub(r'\d+', '', prev).strip().rstrip(",").strip()
                    if prev_clean and re.match(r'^[А-ЯЁ][а-яё\s\-]+$', prev_clean):
                        return prev_clean
            if i >= 2:
                prev2 = parts[i - 2].strip()
                prev2_clean = re.sub(r'\d+', '', prev2).strip().rstrip(",").strip()
                if prev2_clean and re.match(r'^[А-ЯЁ][а-яё\s\-]+$', prev2_clean):
                    return prev2_clean

    # Паттерн 4: "г. Город" в любой части
    for part in parts:
        m = re.search(r'\bг\.?\s*([А-ЯЁ][а-яё\-]+(?:-[А-ЯЁ][а-яё]+)?)', part)
        if m:
            return m.group(1)

    # Паттерн 5: первая часть, если не похожа на улицу
    if parts:
        first = parts[0].strip()
        if re.match(r'^[А-ЯЁ][а-яё\s\-]+$', first):
            lower = first.lower()
            not_city_keywords = [
                "улица", "ул", "проспект", "пр", "проезд", "переулок",
                "бульвар", "шоссе", "набережная", "площадь", "строение",
            ]
            if not any(lower.startswith(kw) for kw in not_city_keywords):
                return first

    return None


def main() -> None:
    db_path = DB_PATH
    if not db_path.exists():
        print(f"[ERROR] База данных не найдена: {db_path}")
        return

    print(f"[PARSE] База данных: {db_path}")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Читаем лиды с address, у которых city IS NULL
    cursor.execute(
        "SELECT id, name, address FROM leads WHERE address IS NOT NULL AND city IS NULL"
    )
    rows = cursor.fetchall()
    total = len(rows)
    print(f"[PARSE] Лидов с address и city=NULL: {total}")

    if total == 0:
        print("[PARSE] Нечего обновлять — все города уже заполнены.")
        conn.close()
        return

    # Извлекаем города
    city_counts: dict[str, int] = {}
    updated = 0
    no_city = 0

    for lead_id, name, address in rows:
        city = extract_city_from_address(address)
        if city:
            city_counts[city] = city_counts.get(city, 0) + 1
            cursor.execute(
                "UPDATE leads SET city = ? WHERE id = ?",
                (city, lead_id),
            )
            updated += 1
        else:
            no_city += 1
            if no_city <= 5:
                print(f"[PARSE] Не удалось извлечь город: ID={lead_id} name={name!r} address={address!r}")

    conn.commit()

    # Выводим статистику
    print(f"\n{'='*50}")
    print(f"СТАТИСТИКА ПАРСИНГА ГОРОДОВ")
    print(f"{'='*50}")
    print(f"Лидов с address и city=NULL: {total}")
    print(f"Город найден:              {updated}")
    print(f"Город не найден:           {no_city}")
    print(f"\nНайденные города ({len(city_counts)} шт.):")
    print(f"{'-'*40}")
    for city, count in sorted(city_counts.items(), key=lambda x: -x[1]):
        print(f"  {city}: {count} лидов")

    conn.close()
    print(f"\n[PARSE] Готово! Обновлено {updated} лидов.")


if __name__ == "__main__":
    main()
