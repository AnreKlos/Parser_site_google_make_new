#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт миграции: создаёт таблицу scan_regions и добавляет колонки
city и scan_id в таблицу leads (если ещё не существуют).

Использует raw SQL для SQLite — без зависимостей от SQLAlchemy/Pydantic.
"""

import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).parent.parent / "data" / "leads.db"


def migrate():
    db_path = DB_PATH
    if not db_path.exists():
        print(f"[MIGRATE] База данных не найдена: {db_path}")
        print("[MIGRATE] Сначала запустите приложение для создания БД")
        return

    print(f"[MIGRATE] База данных: {db_path}")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 1. Создаём таблицу scan_regions (если не существует)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city VARCHAR(150) NOT NULL,
            district VARCHAR(150),
            niche VARCHAR(100) NOT NULL DEFAULT 'салоны красоты',
            status VARCHAR(50) NOT NULL DEFAULT 'not_scanned',
            last_scanned_at DATETIME,
            leads_found INTEGER DEFAULT 0,
            notes TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """)
    print("[MIGRATE] Таблица scan_regions: создана/существует")

    # 2. Уникальный индекс на (city, district, niche)
    try:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_scan_region
            ON scan_regions (city, district, niche)
        """)
        print("[MIGRATE] Индекс uq_scan_region: создан/существует")
    except sqlite3.OperationalError as e:
        if "already exists" not in str(e):
            print(f"[MIGRATE] Ошибка индекса: {e}")

    # 3. Добавляем колонку city в leads (если нет)
    try:
        cursor.execute("ALTER TABLE leads ADD COLUMN city VARCHAR(255)")
        print("[MIGRATE] Колонка leads.city: добавлена")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("[MIGRATE] Колонка leads.city: уже существует")
        else:
            print(f"[MIGRATE] Ошибка: {e}")

    # 4. Индекс на leads.city
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_leads_city ON leads (city)")
        print("[MIGRATE] Индекс ix_leads_city: создан/существует")
    except sqlite3.OperationalError as e:
        print(f"[MIGRATE] Ошибка создания индекса: {e}")

    # 5. Добавляем колонку scan_id в leads (если нет)
    try:
        cursor.execute("ALTER TABLE leads ADD COLUMN scan_id INTEGER REFERENCES scan_regions(id)")
        print("[MIGRATE] Колонка leads.scan_id: добавлена")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("[MIGRATE] Колонка leads.scan_id: уже существует")
        else:
            print(f"[MIGRATE] Ошибка: {e}")

    conn.commit()
    conn.close()

    # 5. Проверяем результат
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor.fetchall()]
    print(f"[MIGRATE] Таблицы в БД: {tables}")

    cursor.execute("PRAGMA table_info(leads)")
    cols = [r[1] for r in cursor.fetchall()]
    print(f"[MIGRATE] Колонки leads: {cols}")

    cursor.execute("PRAGMA table_info(scan_regions)")
    cols = [r[1] for r in cursor.fetchall()]
    print(f"[MIGRATE] Колонки scan_regions: {cols}")

    conn.close()
    print("[MIGRATE] Миграция завершена успешно!")


if __name__ == "__main__":
    migrate()
