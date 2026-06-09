#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт нормализации статусов лидов.

Обновляет старые статусы:
  contacted  → pitched
  converted  → pitched
  rejected   → agents_rejected
  error      → agents_rejected

Статусы no_website, audited, new, pitched — остаются как есть.

Выводит статистику до и после.
"""

import sqlite3
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "leads.db"

# Маппинг: старый статус → новый статус
STATUS_MAP = {
    "contacted": "pitched",
    "converted": "pitched",
    "rejected": "agents_rejected",
    "error": "agents_rejected",
}

# Статусы, которые не трогаем
KEPT_STATUSES = {"new", "no_website", "audited", "pitched", "agents_rejected"}


def main():
    db_path = DB_PATH
    if not db_path.exists():
        print(f"[ERROR] База данных не найдена: {db_path}")
        return

    print(f"[NORMALIZE] База данных: {db_path}")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Статистика ДО
    cursor.execute("SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY status")
    before_rows = cursor.fetchall()
    before_counts = dict(before_rows)
    total_before = sum(before_counts.values())

    print(f"\n{'='*50}")
    print("СТАТИСТИКА ДО НОРМАЛИЗАЦИИ")
    print(f"{'='*50}")
    for status, count in before_rows:
        print(f"  {status}: {count}")

    # Проверяем, есть ли что обновлять
    old_statuses = list(STATUS_MAP.keys())
    placeholders = ",".join("?" for _ in old_statuses)
    cursor.execute(
        f"SELECT id, name, status FROM leads WHERE status IN ({placeholders})",
        old_statuses,
    )
    to_update = cursor.fetchall()

    if not to_update:
        print(f"\n[NORMALIZE] Нет лидов со старыми статусами. Всё уже нормализовано.")
    else:
        print(f"\n[NORMALIZE] Найдено {len(to_update)} лидов для обновления:")
        for lead_id, name, old_status in to_update:
            new_status = STATUS_MAP[old_status]
            print(f"  ID={lead_id}: {name!r}: {old_status} → {new_status}")
            cursor.execute(
                "UPDATE leads SET status = ? WHERE id = ?",
                (new_status, lead_id),
            )

        conn.commit()
        print(f"\n[NORMALIZE] Обновлено {len(to_update)} лидов.")

    # Статистика ПОСЛЕ
    cursor.execute("SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY status")
    after_rows = cursor.fetchall()
    after_counts = dict(after_rows)
    total_after = sum(after_counts.values())

    print(f"\n{'='*50}")
    print("СТАТИСТИКА ПОСЛЕ НОРМАЛИЗАЦИИ")
    print(f"{'='*50}")
    for status, count in after_rows:
        arrow = ""
        if status in STATUS_MAP.values():
            arrow = " (нормализован)"
        elif status in STATUS_MAP:
            arrow = " (не должен остаться!)"
        print(f"  {status}: {count}{arrow}")

    # Проверка: нет ли остатков старых статусов
    cursor.execute(
        f"SELECT COUNT(*) FROM leads WHERE status IN ({placeholders})",
        old_statuses,
    )
    remaining = cursor.fetchone()[0]
    if remaining == 0:
        print(f"\n[NORMALIZE] ✅ Все старые статусы успешно нормализованы!")
    else:
        print(f"\n[NORMALIZE] ⚠️ Осталось {remaining} лидов со старыми статусами!")

    # Разбивка по городам (если есть колонка city)
    cursor.execute("PRAGMA table_info(leads)")
    columns = [r[1] for r in cursor.fetchall()]
    if "city" in columns:
        print(f"\n{'='*50}")
        print("СТАТИСТИКА ПО ГОРОДАМ")
        print(f"{'='*50}")
        cursor.execute(
            "SELECT city, status, COUNT(*) FROM leads GROUP BY city, status ORDER BY city, status"
        )
        for city, status, count in cursor.fetchall():
            print(f"  {city or '(без города)'}: {status} = {count}")

    conn.close()
    print(f"\n[NORMALIZE] Готово! {total_after} лидов в БД.")


if __name__ == "__main__":
    main()
