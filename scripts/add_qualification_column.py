# scripts/add_qualification_column.py
"""
Migration script to add qualification_status column to leads table.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "leads.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if column already exists
    cursor.execute("PRAGMA table_info(leads)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "qualification_status" in columns:
        print("Column qualification_status already exists.")
        return

    # Add column
    cursor.execute(
        "ALTER TABLE leads ADD COLUMN qualification_status TEXT DEFAULT 'pending'"
    )
    conn.commit()
    print("Added qualification_status column to leads table.")
    
    conn.close()


if __name__ == "__main__":
    migrate()
