#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Миграция БД - добавление полей для аудита
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("data/leads.db")

def migrate():
    if not DB_PATH.exists():
        print("❌ База данных не найдена!")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Проверяем, есть ли уже колонки
    cursor.execute("PRAGMA table_info(leads)")
    columns = [col[1] for col in cursor.fetchall()]
    
    new_columns = []
    
    if "tech_score" not in columns:
        new_columns.append("ALTER TABLE leads ADD COLUMN tech_score INTEGER")
    
    if "load_time_sec" not in columns:
        new_columns.append("ALTER TABLE leads ADD COLUMN load_time_sec REAL")
    
    if "audit_notes" not in columns:
        new_columns.append("ALTER TABLE leads ADD COLUMN audit_notes TEXT")
    
    if "pitch_text" not in columns:
        new_columns.append("ALTER TABLE leads ADD COLUMN pitch_text TEXT")
    
    if "raw_reviews" not in columns:
        new_columns.append("ALTER TABLE leads ADD COLUMN raw_reviews TEXT")
    
    if new_columns:
        for sql in new_columns:
            cursor.execute(sql)
            print(f"✅ Добавлена колонка: {sql.split('ADD COLUMN')[1].strip().split()[0]}")
        
        conn.commit()
        print(f"\n🎉 Миграция завершена! Добавлено {len(new_columns)} колонок.")
    else:
        print("✅ База данных уже актуальна, миграция не требуется.")
    
    conn.close()

if __name__ == "__main__":
    migrate()