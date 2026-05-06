#!/usr/bin/env python3
import json
import sys

def show_lead_stats(lead_id, slug):
    path = f"data/yandex/{slug}-{lead_id}/photo_map.json"
    with open(path, "r", encoding="utf-8") as f:
        m = json.load(f)
    
    print(f"\n{'='*60}")
    print(f"Lead {lead_id} ({slug})")
    print(f"{'='*60}")
    print("\nmarketing_grade_distribution:")
    for tier, count in m["stats"]["marketing_grade_distribution"].items():
        print(f"  {tier}: {count}")
    
    print("\nBlocks with marketing_grade per photo:")
    for block_name, block_data in m["blocks"].items():
        if not block_data.get("photos"):
            continue
        print(f"\n  {block_name} ({len(block_data['photos'])} photos):")
        for p in block_data["photos"]:
            grade = p.get("marketing_grade", "N/A")
            tier = p.get("marketing_tier", "N/A")
            alt = p.get("alt", "")[:50]
            print(f"    grade={grade} tier={tier} | {alt}")

if __name__ == "__main__":
    # Lead 16
    show_lead_stats(16, "studiya-krasoty-aleny-rogovtsevoy")
    # Lead 26
    show_lead_stats(26, "mood")
