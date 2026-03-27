"""Test new LLM integration with OpenRouter and force_llm flag."""

import os
import sys
from pathlib import Path

# Set dummy API key for testing fallback (or set real key in env)
if not os.getenv('OPENROUTER_API_KEY'):
    os.environ['OPENROUTER_API_KEY'] = 'dummy-key-for-testing-fallback'

from scraper_core import run_parse

def test_force_llm():
    """Test force_llm flag on a known site."""
    print("Testing force_llm flag on https://web-c.ru/...")
    result = run_parse("https://web-c.ru/", force_llm=True)
    print(f"Result: used_llm={result.get('used_llm')}, score={result.get('score')}")
    print(f"Data: prices={len(result['data'].get('prices', []))}, contacts={len(result['data'].get('contacts', {}))}, services={len(result['data'].get('services', []))}")
    print(f"Output: {result['output_path']}")
    return result

def test_normal_flow():
    """Test normal flow (without force_llm)."""
    print("\nTesting normal flow on https://web-c.ru/...")
    result = run_parse("https://web-c.ru/", force_llm=False)
    print(f"Result: used_llm={result.get('used_llm')}, score={result.get('score')}")
    print(f"Data: prices={len(result['data'].get('prices', []))}, contacts={len(result['data'].get('contacts', {}))}, services={len(result['data'].get('services', []))}")
    return result

def test_32status():
    """Test on 32status.ru (known low score)."""
    print("\nTesting on http://32status.ru/...")
    result = run_parse("http://32status.ru/", force_llm=False)
    print(f"Result: used_llm={result.get('used_llm')}, score={result.get('score')}")
    print(f"Data: prices={len(result['data'].get('prices', []))}, contacts={len(result['data'].get('contacts', {}))}, services={len(result['data'].get('services', []))}")
    return result

if __name__ == "__main__":
    # Create data directory if not exists
    Path("data").mkdir(exist_ok=True)
    
    try:
        test_force_llm()
        test_normal_flow()
        test_32status()
        print("\nAll tests completed!")
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)