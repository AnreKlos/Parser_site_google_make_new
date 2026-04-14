#!/usr/bin/env python3
"""Сброс питчей для повторной генерации"""
import sqlite3

conn = sqlite3.connect('data/leads.db')
c = conn.cursor()

# Сбрасываем pitch_text и status для лидов с score <= 85
c.execute("""
    UPDATE leads 
    SET pitch_text = NULL, status = 'audited' 
    WHERE tech_score <= 85 AND pitch_text IS NOT NULL
""")
conn.commit()
print(f"Сброшено питчей: {c.rowcount}")

# Показываем результат
c.execute("""
    SELECT id, name, tech_score, status 
    FROM leads 
    WHERE tech_score <= 85 AND pitch_text IS NULL
""")
rows = c.fetchall()
print(f"\nЛиды готовые к генерации ({len(rows)}):")
for r in rows:
    print(f"  {r[0]}: {r[1][:40]} | score={r[2]} | {r[3]}")

conn.close()