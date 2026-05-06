#!/usr/bin/env python3
"""
Check which filters are killing reviews from diagnostic data.
"""

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"

# Copy filter functions from enrichment/yandex.py
def is_noise_review_text(text: str) -> bool:
    value = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not value:
        return True
    noise_phrases = {
        "подписаться",
        "посмотреть ответ организации",
        "знаток города",
        "ответ организации",
    }
    if value in noise_phrases:
        return True
    if len(value) < 20 and "подпис" in value:
        return True
    if re.fullmatch(r"\d{1,2}\s+[а-яё]+\s+\d{4}", value):
        return True
    if len(value) < 15 and len(value.split()) <= 2:
        return True
    return False


def parse_russian_date(text: str):
    """Парсит русскую дату в YYYY-MM-DD."""
    if not text:
        return None
    text = text.strip()
    
    months = {
        "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
        "мая": "05", "июня": "06", "июля": "07", "августа": "08",
        "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12"
    }
    
    match = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", text, flags=re.IGNORECASE)
    if match:
        day, month, year = match.groups()
        month_num = months.get(month.lower())
        if month_num:
            return f"{year}-{month_num}-{day.zfill(2)}"
    
    match = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        return match.group(0)
    
    return None


def main():
    with open(LOGS_DIR / "diag_mood26_reviews_raw.json", encoding="utf-8") as f:
        reviews = json.load(f)
    
    print(f"📊 Total reviews from diagnostic: {len(reviews)}")
    print("=" * 60)
    
    filters = {
        "len(text) < 30": 0,
        "is_noise_review_text": 0,
        "text.lower() == author.lower()": 0,
        "not raw_date": 0,
        "parse_russian_date is None": 0,
        "passed": 0
    }
    
    for idx, review in enumerate(reviews):
        author = str(review.get("author") or "").strip()
        text = str(review.get("text") or "").strip()
        raw_date = str(review.get("date") or "").strip()
        
        # Apply filters in same order as enrichment/yandex.py
        if len(text) < 30:
            filters["len(text) < 30"] += 1
            print(f"[{idx}] ❌ len(text) < 30: {len(text)} chars | text: {text[:50]}")
            continue
        
        if is_noise_review_text(text):
            filters["is_noise_review_text"] += 1
            print(f"[{idx}] ❌ is_noise_review_text: {text[:50]}")
            continue
        
        if text.lower() == author.lower():
            filters["text.lower() == author.lower()"] += 1
            print(f"[{idx}] ❌ text == author: {author}")
            continue
        
        if not raw_date:
            filters["not raw_date"] += 1
            print(f"[{idx}] ❌ no raw_date")
            continue
        
        parsed_date = parse_russian_date(raw_date)
        if not parsed_date:
            filters["parse_russian_date is None"] += 1
            print(f"[{idx}] ❌ parse_russian_date failed: {raw_date}")
            continue
        
        filters["passed"] += 1
        print(f"[{idx}] ✅ PASSED | author: {author[:30]} | date: {parsed_date}")
    
    print("=" * 60)
    print("📊 FILTER SUMMARY:")
    for filter_name, count in filters.items():
        print(f"  {filter_name}: {count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
