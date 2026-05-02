#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Миграция v2: добавление timestamps (created_at, updated_at) и таблицы audit_logs.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("data/leads.db")


def migrate():
    if not DB_PATH.exists():
        print("[ERR] База данных не найдена!")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(leads)")
    columns = {col[1] for col in cursor.fetchall()}
    added = []

    # --- Timestamps для leads ---
    if "created_at" not in columns:
        cursor.execute("ALTER TABLE leads ADD COLUMN created_at TIMESTAMP")
        # Заполняем существующие записи текущим временем
        cursor.execute("UPDATE leads SET created_at = datetime('now') WHERE created_at IS NULL")
        added.append("created_at")
        print("[OK] Добавлена колонка: created_at")

    if "updated_at" not in columns:
        cursor.execute("ALTER TABLE leads ADD COLUMN updated_at TIMESTAMP")
        cursor.execute("UPDATE leads SET updated_at = datetime('now') WHERE updated_at IS NULL")
        added.append("updated_at")
        print("[OK] Добавлена колонка: updated_at")

    # --- Таблица audit_logs ---
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_logs'")
    if not cursor.fetchone():
        cursor.execute("""
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER,
                action VARCHAR(50) NOT NULL,
                details TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_lead_id ON audit_logs(lead_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at)")
        print("[OK] Создана таблица: audit_logs + индексы")
        added.append("audit_logs table")
    else:
        print("[OK] Таблица audit_logs уже существует")

    conn.commit()
    conn.close()

    if added:
        print(f"\n[DONE] Миграция v2 завершена! Добавлено: {', '.join(added)}")
    else:
        print("[OK] База данных уже актуальна, миграция не требуется.")


if __name__ == "__main__":
    migrate()
