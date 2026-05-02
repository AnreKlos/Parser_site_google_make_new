#!/usr/bin/env python3
"""One-time migration: make website column nullable and rebuild table"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'leads.db')
print(f"DB path: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check current schema
cursor.execute("PRAGMA table_info(leads)")
cols = cursor.fetchall()
print("Current columns:")
for c in cols:
    print(f"  {c}")

# SQLite doesn't support ALTER COLUMN, so we recreate the table
cursor.execute("""
CREATE TABLE IF NOT EXISTS leads_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(255) NOT NULL,
    google_rating FLOAT,
    reviews_count INTEGER DEFAULT 0,
    address VARCHAR(500),
    phone VARCHAR(50),
    website VARCHAR(500),
    status VARCHAR(50) NOT NULL DEFAULT 'new',
    tech_score INTEGER,
    load_time_sec FLOAT,
    audit_notes TEXT,
    pitch_text TEXT,
    raw_reviews TEXT
)
""")

cursor.execute("""
INSERT INTO leads_new (id, name, google_rating, reviews_count, address, phone, website, status, tech_score, load_time_sec, audit_notes, pitch_text, raw_reviews)
SELECT id, name, google_rating, reviews_count, address, phone, website, status, tech_score, load_time_sec, audit_notes, pitch_text, raw_reviews FROM leads
""")
print(f"Copied {cursor.rowcount} rows")

cursor.execute("DROP TABLE leads")
cursor.execute("ALTER TABLE leads_new RENAME TO leads")
cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_leads_website ON leads(website) WHERE website IS NOT NULL")

conn.commit()

cursor.execute("PRAGMA table_info(leads)")
cols = cursor.fetchall()
print("\nNew columns:")
for c in cols:
    print(f"  {c}")

cursor.execute("SELECT COUNT(*) FROM leads")
count = cursor.fetchone()[0]
print(f"\nTotal leads: {count}")

conn.close()
print("\n✅ Migration complete! website is now nullable")