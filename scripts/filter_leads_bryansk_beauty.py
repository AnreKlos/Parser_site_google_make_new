#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import sys
from pathlib import Path
from typing import List, Dict, Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import get_async_session
from db.models import Lead
from sqlalchemy import select

BASE_DIR = Path(__file__).parent.parent
OUTPUT_FILE = BASE_DIR / "data" / "filtered_leads.json"
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

# Beauty keywords (Russian and English)
BEAUTY_KEYWORDS_CATEGORY = {
    "nail", "salon", "beauty", "hair", "spa", "manicure", "pedicure",
    "nails", "salons", "beautysalon", "hairsalon", "barbershop"
}

BEAUTY_KEYWORDS_NAME = {
    "салон", "студия", "парикмахер", "маникюр", "педикюр", "красот", 
    "ногт", "брови", "ресниц", "стриж", "уклад", "макияж", "волос",
    "эпиляц", "депиляц", "наращив", "ламинир", "кератин", "ботокс",
    "филлер", "чистк", "пилинг", "массаж", "соляр", "загар", "барбер"
}

# Medical keywords to exclude
MEDICAL_KEYWORDS_NAME = {
    "стомат", "клиник", "медицин", "врач", "доктор", "зубн", "стом-"
}

MEDICAL_CATEGORIES = {
    "dental", "medical", "clinic", "dentist", "stomatology"
}

# City keywords for Bryansk
BRYANSK_KEYWORDS = {"брянск", "bryansk"}


def is_bryansk(lead: Lead) -> bool:
    """Check if lead is from Bryansk based on address."""
    if not lead.address:
        return False
    
    address_lower = lead.address.lower()
    return any(keyword in address_lower for keyword in BRYANSK_KEYWORDS)


def is_beauty_category(lead: Lead) -> bool:
    """Check if lead is in beauty category."""
    # Check category field
    if lead.category:
        category_lower = lead.category.lower()
        if any(keyword in category_lower for keyword in BEAUTY_KEYWORDS_CATEGORY):
            return True
    
    # Check name field
    if lead.name:
        name_lower = lead.name.lower()
        if any(keyword in name_lower for keyword in BEAUTY_KEYWORDS_NAME):
            return True
    
    return False


def is_medical(lead: Lead) -> bool:
    """Check if lead is medical/dental (to exclude)."""
    # Check category field
    if lead.category:
        category_lower = lead.category.lower()
        if category_lower in MEDICAL_CATEGORIES:
            return True
    
    # Check name field
    if lead.name:
        name_lower = lead.name.lower()
        if any(keyword in name_lower for keyword in MEDICAL_KEYWORDS_NAME):
            return True
    
    return False


async def filter_leads() -> List[Dict[str, Any]]:
    """Filter leads based on criteria."""
    async with get_async_session() as session:
        result = await session.execute(select(Lead))
        all_leads = result.scalars().all()
        
        filtered = []
        stats = {
            "total": len(all_leads),
            "bryansk": 0,
            "beauty": 0,
            "medical_excluded": 0,
        }
        
        for lead in all_leads:
            # Check if Bryansk
            if not is_bryansk(lead):
                continue
            stats["bryansk"] += 1
            
            # Check if beauty category
            if not is_beauty_category(lead):
                continue
            stats["beauty"] += 1
            
            # Check if medical (to exclude)
            if is_medical(lead):
                stats["medical_excluded"] += 1
                continue
            
            # Add to filtered list
            filtered.append({
                "lead_id": lead.id,
                "name": lead.name,
                "address": lead.address,
                "category": lead.category,
                "rating": lead.google_rating,
                "reviews_count": lead.reviews_count,
                "website": lead.website,
                "status": lead.status,
            })
        
        return filtered, stats


def print_statistics(stats: Dict[str, int], filtered_count: int):
    """Print statistics to stdout."""
    print("=" * 60)
    print("ФИЛЬТРАЦИЯ ЛИДОВ: Брянск + Beauty")
    print("=" * 60)
    print(f"Всего лидов в БД: {stats['total']}")
    print(f"Брянск: {stats['bryansk']}")
    print(f"+ beauty: {stats['beauty']}")
    print(f"− медицина: {stats['medical_excluded']}")
    print(f"ИТОГО в filtered_leads.json: {filtered_count}")
    print("=" * 60)


async def main():
    """Main function."""
    filtered_leads, stats = await filter_leads()
    
    # Write to JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(filtered_leads, f, ensure_ascii=False, indent=2)
    
    # Print statistics
    print_statistics(stats, len(filtered_leads))
    print(f"\n✅ Файл сохранён: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
