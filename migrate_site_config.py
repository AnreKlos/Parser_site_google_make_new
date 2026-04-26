#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Миграция: добавление поля site_config_path в таблицу leads
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "leads.db"

def migrate():
    """Добавляет колонку site_config_path если её нет"""
    if not DB_PATH.exists():
        print(f"[ERR] Database not found: {DB_PATH}")
        return False
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Проверяем, существует ли колонка
    cursor.execute("PRAGMA table_info(leads)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "site_config_path" in columns:
        print("[OK] Column 'site_config_path' already exists")
        conn.close()
        return True
    
    try:
        cursor.execute("ALTER TABLE leads ADD COLUMN site_config_path TEXT")
        conn.commit()
        print("[OK] Column 'site_config_path' added successfully")
        return True
    except Exception as e:
        print(f"[ERR] Failed to add column: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
