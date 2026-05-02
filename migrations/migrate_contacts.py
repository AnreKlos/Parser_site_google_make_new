#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Миграция: добавление колонок emails и social_links в таблицу leads
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "leads.db"


def migrate():
    if not DB_PATH.exists():
        print("[MIGRATE] База данных не найдена — будет создана автоматически при запуске")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Проверяем, есть ли уже колонки
    cursor.execute("PRAGMA table_info(leads)")
    columns = [row[1] for row in cursor.fetchall()]

    if "emails" in columns:
        print("[MIGRATE] Колонка emails уже существует")
    else:
        cursor.execute("ALTER TABLE leads ADD COLUMN emails TEXT")
        conn.commit()
        print("[MIGRATE] ✅ Колонка emails добавлена")

    if "social_links" in columns:
        print("[MIGRATE] Колонка social_links уже существует")
    else:
        cursor.execute("ALTER TABLE leads ADD COLUMN social_links TEXT")
        conn.commit()
        print("[MIGRATE] ✅ Колонка social_links добавлена")

    conn.close()
    print("[MIGRATE] Миграция завершена")


if __name__ == "__main__":
    migrate()
