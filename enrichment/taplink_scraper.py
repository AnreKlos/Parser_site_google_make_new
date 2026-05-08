"""
TapLink photo scraper.

Extracts photos from TapLink pages as fallback when Yandex.aspects is empty/insufficient.
Only photos - services, reviews and other content are not parsed at this stage.
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Shared paths
BASE_DIR = Path(__file__).parent.parent
YANDEX_DATA_DIR = BASE_DIR / "data" / "yandex"
TAPLINK_DATA_DIR = BASE_DIR / "data" / "taplink"

# Image extensions to include
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# TapLink asset patterns to exclude
TAPLINK_ASSET_PATTERNS = [
    r"taplink\.(ru|cc|net)/.*\.(png|svg|ico)",  # TapLink platform assets
    r"/favicon",  # Favicons
    r"/logo",  # Logos
    r"/icon",  # Icons
]


def _is_taplink_asset(url: str) -> bool:
    """Check if URL is a TapLink platform asset (favicon, logo, etc)."""
    url_lower = url.lower()
    for pattern in TAPLINK_ASSET_PATTERNS:
        if re.search(pattern, url_lower):
            return True
    return False


def _is_valid_image_url(url: str) -> bool:
    """Check if URL is a valid image URL."""
    # Convert protocol-relative URLs to https
    if url.startswith("//"):
        url = "https:" + url
    
    if not url.startswith("https://"):
        return False
    
    # Check extension
    url_lower = url.lower()
    if not any(ext in url_lower for ext in IMAGE_EXTENSIONS):
        return False
    
    # Exclude TapLink assets
    if _is_taplink_asset(url):
        return False
    
    return True


async def _extract_photos_playwright(url: str) -> List[Dict[str, str]]:
    """Extract photos from TapLink page using Playwright for dynamic rendering."""
    photos = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            
            # Wait for Vue app to mount and window.data to be available
            await page.wait_for_timeout(5000)
            
            # Get the HTML content
            html_content = await page.content()
            
            # Try to extract window.data from script tag using regex
            # Match the entire window.data assignment
            import re
            data_match = re.search(r'window\.data\s*=\s*(\{.*?\});', html_content, re.DOTALL)
            if data_match:
                try:
                    data_json_str = data_match.group(1)
                    # Use a simple approach: try to eval it as JavaScript
                    # Since we can't use execjs, let's try to convert to JSON manually
                    
                    # Simple approach: replace common JS patterns with JSON
                    # This is not perfect but should work for basic structures
                    data_json_str = data_json_str.replace("'", '"')
                    data_json_str = re.sub(r'(\w+):', r'"\1":', data_json_str)
                    data_json_str = re.sub(r':\s*([a-zA-Z_][a-zA-Z0-9_]*)', r': "\1"', data_json_str)
                    
                    data = json.loads(data_json_str)
                    print(f"📊 Parsed window.data with {len(data.get('fields', []))} fields")
                    
                    fields = data.get("fields", [])
                    for field in fields:
                        items = field.get("items", [])
                        for item in items:
                            options = item.get("options", {})
                            
                            # Check for picture in options
                            if "picture" in options:
                                picture = options["picture"]
                                if isinstance(picture, dict):
                                    pic_url = picture.get("picture_url") or picture.get("url")
                                    if pic_url and _is_valid_image_url(pic_url):
                                        if not any(p["url"] == pic_url for p in photos):
                                            photos.append({"url": pic_url, "alt": picture.get("title", "")})
                            
                            # Check for pictures array (gallery)
                            if "pictures" in options:
                                pics = options["pictures"]
                                if isinstance(pics, list):
                                    print(f"📸 Found pictures block with {len(pics)} photos")
                                    for pic in pics:
                                        if isinstance(pic, dict):
                                            pic_url = pic.get("picture_url") or pic.get("url")
                                            if pic_url and _is_valid_image_url(pic_url):
                                                if not any(p["url"] == pic_url for p in photos):
                                                    photos.append({"url": pic_url, "alt": pic.get("title", "")})
                except Exception as e:
                    print(f"⚠️ Failed to parse window.data: {e}")
                    # Save HTML for debugging
                    output_dir = TAPLINK_DATA_DIR / "debug"
                    output_dir.mkdir(parents=True, exist_ok=True)
                    debug_file = output_dir / "taplink_debug.html"
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(html_content)
                    print(f"💾 Saved HTML to {debug_file} for debugging")
            else:
                print(f"⚠️ window.data not found in HTML")
            
            # Scroll down the page to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            
            # Scroll back up
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(1000)
            
            # Fallback: get all image elements and data-src attributes
            img_elements = await page.query_selector_all("img")
            print(f"🔍 Found {len(img_elements)} img elements")
            
            for img in img_elements:
                src = await img.get_attribute("src")
                data_src = await img.get_attribute("data-src")
                data_original = await img.get_attribute("data-original")
                
                image_url = src or data_src or data_original
                
                if not image_url:
                    continue
                
                if not _is_valid_image_url(image_url):
                    continue
                
                # Get alt text
                alt = await img.get_attribute("alt") or ""
                
                # Avoid duplicates
                if not any(p["url"] == image_url for p in photos):
                    photos.append({
                        "url": image_url,
                        "alt": alt,
                    })
            
            # Also look for data-src on any element (lazy-loaded images)
            all_elements = await page.query_selector_all("[data-src]")
            print(f"🔍 Found {len(all_elements)} elements with data-src")
            
            for elem in all_elements:
                data_src = await elem.get_attribute("data-src")
                if not data_src:
                    continue
                
                # Convert protocol-relative URLs to https
                if data_src.startswith("//"):
                    data_src = "https:" + data_src
                
                if not _is_valid_image_url(data_src):
                    continue
                
                # Avoid duplicates
                if not any(p["url"] == data_src for p in photos):
                    photos.append({
                        "url": data_src,
                        "alt": "",
                    })
                    
        except PlaywrightTimeoutError:
            print(f"⚠️ Timeout loading page")
        except Exception as e:
            print(f"⚠️ Error extracting photos with Playwright: {e}")
        finally:
            await browser.close()
    
    return photos


def _load_card_json(lead_id: int) -> Dict[str, Any] | None:
    """Load card.json from Yandex data directory."""
    # Find the card.json file for this lead_id
    for card_file in YANDEX_DATA_DIR.glob(f"*/card.json"):
        with open(card_file, "r", encoding="utf-8") as f:
            card = json.load(f)
            # Check if this is the right lead
            if card.get("lead_id") == lead_id:
                return card
    return None


async def scrape_taplink_photos(lead_id: int) -> Dict[str, Any]:
    """Scrape photos from TapLink page for a given lead."""
    # Load card.json to get taplink URL
    card = _load_card_json(lead_id)
    if not card:
        print(f"❌ card.json not found for lead_id={lead_id}")
        return {"error": "card.json not found"}
    
    urls = card.get("urls") or {}
    taplink_url = (urls.get("taplink") or "").strip()
    
    if not taplink_url:
        print(f"ℹ️  lead_id={lead_id}: no taplink URL, skip")
        return {"status": "skipped", "reason": "no taplink URL"}
    
    print(f"🔗 lead_id={lead_id}: taplink URL found: {taplink_url}")
    
    # Get slug from card
    slug = card.get("slug") or f"lead-{lead_id}"
    
    # Create output directory
    output_dir = TAPLINK_DATA_DIR / f"{slug}-{lead_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract photos using Playwright
    photos = await _extract_photos_playwright(taplink_url)
    
    if not photos:
        print(f"⚠️  No photos found on taplink page")
        return {"status": "no_photos", "photos_count": 0}
    
    print(f"✅ Found {len(photos)} photos on taplink page")

    # Save photos.json
    output_file = output_dir / "photos.json"
    result = {
        "lead_id": lead_id,
        "scraped_at": datetime.now().isoformat(),
        "source_url": taplink_url,
        "photos": photos,
    }
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"💾 Saved to {output_file}")
    
    return result


def main() -> None:
    import asyncio
    import argparse
    
    parser = argparse.ArgumentParser(description="TapLink photo scraper")
    parser.add_argument("lead_ids", nargs="+", type=int, help="Lead ID(s) to scrape")
    parser.add_argument("--force", action="store_true", help="Force re-scrape even if photos.json exists")
    
    args = parser.parse_args()
    
    async def run_all():
        for lead_id in args.lead_ids:
            print(f"\n{'='*60}")
            print(f"Processing lead_id={lead_id}")
            print(f"{'='*60}")
            
            # Check if photos.json already exists
            slug = None
            for card_file in YANDEX_DATA_DIR.glob(f"*/card.json"):
                with open(card_file, "r", encoding="utf-8") as f:
                    card = json.load(f)
                    if card.get("lead_id") == lead_id:
                        slug = card.get("slug")
                        break
            
            if slug:
                existing_file = TAPLINK_DATA_DIR / f"{slug}-{lead_id}" / "photos.json"
                if existing_file.exists() and not args.force:
                    print(f"ℹ️  photos.json already exists, use --force to re-scrape")
                    continue
            
            result = await scrape_taplink_photos(lead_id)
            
            if result.get("status") == "skipped":
                continue
            
            # Sleep between requests for anti-ban
            if len(args.lead_ids) > 1:
                sleep_time = 1.0
                print(f"⏱️  Sleeping {sleep_time}s before next request...")
                time.sleep(sleep_time)
    
    asyncio.run(run_all())


if __name__ == "__main__":
    main()
