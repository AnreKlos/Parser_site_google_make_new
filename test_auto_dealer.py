#!/usr/bin/env python
"""Test script for auto_dealer niche detection and extraction."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from scraper_core import run_parse

# Test with GAC (auto dealer site)
url = "https://gac.ru/"

print(f"Testing auto_dealer detection with {url}...")
print("=" * 60)

try:
    result = run_parse(
        url,
        use_js=True,
        force_llm=True,
        save_json=True
    )
    
    normalized = result.get('normalized', {})
    print(f"\nNiche detected: {normalized.get('niche')}")
    print(f"Business name: {normalized.get('business_name')}")
    print(f"City: {normalized.get('city')}")
    print(f"Models found: {len(normalized.get('models', []))}")
    
    if normalized.get('models'):
        print("\nFirst few models:")
        for i, model in enumerate(normalized['models'][:5], 1):
            print(f"  {i}. {model.get('name')} - {model.get('category')} - {model.get('price_from')}")
    
    print(f"\nOutput saved to: {result.get('output_path')}")
    print("Test completed successfully!")
    
except Exception as e:
    print(f"Error during test: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)