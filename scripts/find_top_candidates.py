#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Find top-5 candidates for first deployment.
Loads yandex JSON files, filters by criteria, and outputs top candidates.
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import async_session, get_async_session
from db.models import Lead


def extract_yandex_data(json_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract lead_id, slug, rating, reviews_count, photos_count from yandex JSON.
    Handles both light and legacy formats.
    
    Returns None if the file doesn't have required fields (not a light/legacy file).
    """
    yandex = json_data.get("yandex", {})
    
    # Check if it's a light/legacy file (has rating/reviews_count/photos_count)
    rating = yandex.get("rating")
    reviews_count = yandex.get("reviews_count")
    
    if rating is None or reviews_count is None:
        return None
    
    # Extract photos_count (light format) or count photos array (legacy format)
    photos_count = yandex.get("photos_count")
    if photos_count is None:
        photos = yandex.get("photos", [])
        photos_count = len(photos)
    
    lead_id = json_data.get("lead_id")
    if lead_id is None:
        return None
    
    return {
        "lead_id": lead_id,
        "rating": rating,
        "reviews_count": reviews_count,
        "photos_count": photos_count,
    }


def is_beauty_category(category: Optional[str]) -> bool:
    """Check if category is beauty-related."""
    if not category:
        return False
    category_low = category.lower()
    return "salon" in category_low or "крас" in category_low or "beauty" in category_low


def is_bryansk_city(address: Optional[str], yandex_city: Optional[str]) -> bool:
    """Check if city is Bryansk."""
    # Check yandex city field first
    if yandex_city and "брянск" in yandex_city.lower():
        return True
    # Fallback to address field
    if address and "брянск" in address.lower():
        return True
    return False


def has_no_website_or_social(website: Optional[str]) -> bool:
    """Check if website is empty or contains instagram/vk."""
    if not website:
        return True
    website_low = website.lower()
    return "instagram" in website_low or "vk" in website_low


async def get_lead_data(lead_id: int) -> Optional[Dict[str, Any]]:
    """Get lead data from DB by lead_id."""
    async with async_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.id == lead_id)
        )
        lead = result.scalar_one_or_none()
        if lead:
            return {
                "name": lead.name,
                "city": lead.address,  # Use address for city detection
                "category": lead.category,
                "website": lead.website,
            }
    return None


async def main():
    # Load all yandex JSON files
    yandex_dir = Path(__file__).parent.parent / "data" / "yandex"
    json_files = list(yandex_dir.glob("*.json"))
    
    candidates_dict = {}  # Deduplicate by lead_id, keep best version
    
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error reading {json_file}: {e}", file=sys.stderr)
            continue
        
        # Extract yandex data
        yandex_data = extract_yandex_data(data)
        if not yandex_data:
            continue  # Skip non-light/legacy files
        
        lead_id = yandex_data["lead_id"]
        
        # Get lead data from DB
        lead_data = await get_lead_data(lead_id)
        if not lead_data:
            print(f"Lead {lead_id} not found in DB", file=sys.stderr)
            continue
        
        # Get yandex city for filtering
        yandex_city = data.get("city")
        
        # Apply filters
        # photos_count >= 5
        if yandex_data["photos_count"] < 5:
            continue
        
        # reviews_count >= 10
        if yandex_data["reviews_count"] < 10:
            continue
        
        # rating >= 4.0
        if yandex_data["rating"] < 4.0:
            continue
        
        # city = Брянск
        if not is_bryansk_city(lead_data["city"], yandex_city):
            continue
        
        # category = beauty
        if not is_beauty_category(lead_data["category"]):
            continue
        
        # website empty OR contains instagram/vk
        if not has_no_website_or_social(lead_data["website"]):
            continue
        
        # All filters passed, add to candidates_dict (deduplicate by lead_id)
        candidate = {
            "lead_id": lead_id,
            "name": lead_data["name"],
            "rating": yandex_data["rating"],
            "reviews_count": yandex_data["reviews_count"],
            "photos_count": yandex_data["photos_count"],
            "website": lead_data["website"] or "",
        }
        
        # Keep the version with higher photos_count (prefer more data)
        if lead_id not in candidates_dict or candidate["photos_count"] > candidates_dict[lead_id]["photos_count"]:
            candidates_dict[lead_id] = candidate
    
    # Convert dict to list and sort
    candidates = list(candidates_dict.values())
    candidates.sort(
        key=lambda x: (-x["rating"], -x["reviews_count"], -x["photos_count"])
    )
    
    # Top-5
    top_candidates = candidates[:5]
    
    # Output table to stdout
    print("| # | Lead | Название | Rating | Reviews | Photos | Site |")
    print("|---|------|----------|--------|---------|--------|------|")
    for i, cand in enumerate(top_candidates, 1):
        site_display = cand["website"]
        if not site_display:
            site_display = "-"
        elif "instagram" in site_display.lower():
            site_display = "instagram"
        elif "vk" in site_display.lower():
            site_display = "vk"
        print(f"| {i} | {cand['lead_id']} | {cand['name']} | {cand['rating']} | {cand['reviews_count']} | {cand['photos_count']} | {site_display} |")
    
    # Save to data/top_candidates.json
    output_file = Path(__file__).parent.parent / "data" / "top_candidates.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(top_candidates, f, ensure_ascii=False, indent=2)
    
    print(f"\nSaved to {output_file}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
