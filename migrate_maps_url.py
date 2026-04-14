#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Миграция: добавление колонки google_maps_url в таблицу leads
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

    # Проверяем, есть ли уже колонка
    cursor.execute("PRAGMA table_info(leads)")
    columns = [row[1] for row in cursor.fetchall()]

    if "google_maps_url" in columns:
        print("[MIGRATE] Колонка google_maps_url уже существует")
    else:
        cursor.execute("ALTER TABLE leads ADD COLUMN google_maps_url TEXT")
        conn.commit()
        print("[MIGRATE] ✅ Колонка google_maps_url добавлена")

    conn.close()
    print("[MIGRATE] Миграция завершена")

if __name__ == "__main__":
    migrate()