#!/usr/bin/env python3
"""
Diagnostic script for Yandex full-mode scraping issue.
Tests the same selectors as production code in headed mode.
"""

import asyncio
import json
import random
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

URL = "https://yandex.ru/maps/org/mood/96195832006/"


async def main():
    print(f"🔍 Diagnostic: {URL}")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            # Navigate to page
            print("📄 Loading page...")
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)

            # Initial screenshot
            print("📸 Taking initial screenshot...")
            await page.screenshot(path=str(LOGS_DIR / "diag_mood26_initial.png"), full_page=True)

            # Click on reviews tab
            try:
                await page.get_by_text("Отзывы", exact=False).first.click(timeout=3000)
                await page.wait_for_timeout(1500)
                print("✅ Clicked on reviews tab")
            except Exception as e:
                print(f"⚠️ Could not click reviews tab: {e}")

            # Scroll 12 times
            print("🔄 Scrolling reviews block (12 iterations)...")
            for i in range(12):
                try:
                    await page.get_by_text("Показать ещё", exact=False).first.click(timeout=700)
                    print(f"  [{i+1}/12] Clicked 'Показать ещё'")
                except Exception:
                    print(f"  [{i+1}/12] No 'Показать ещё' button found")
                
                await page.mouse.wheel(0, random.randint(1300, 2200))
                await page.wait_for_timeout(2000)

                # Count reviews and images in DOM
                reviews_count = await page.evaluate("""
                    () => {
                        const reviewBlocks = document.querySelectorAll(
                            "[class*='business-review-view'], [class*='review-snippet-view'], [itemprop='review'], [class*='review-view']"
                        );
                        return reviewBlocks.length;
                    }
                """)
                
                images_count = await page.evaluate("""
                    () => {
                        const images = Array.from(document.querySelectorAll('img[src]'))
                            .filter(i => /avatars\\.mds\\.yandex\\.net/.test(i.getAttribute('src')));
                        return images.length;
                    }
                """)
                
                print(f"  [{i+1}/12] Reviews in DOM: {reviews_count}, Images in DOM: {images_count}")

            # After scroll screenshot
            print("📸 Taking post-scroll screenshot...")
            await page.screenshot(path=str(LOGS_DIR / "diag_mood26_after_scroll.png"), full_page=True)

            # Save HTML
            print("💾 Saving HTML...")
            html_content = await page.content()
            (LOGS_DIR / "diag_mood26.html").write_text(html_content, encoding="utf-8")

            # Extract reviews using same selectors as production
            print("📝 Extracting reviews...")
            reviews_raw = await page.evaluate("""
                () => {
                  const reviewsRaw = [];
                  const reviewBlocks = document.querySelectorAll(
                    "[class*='business-review-view'], [class*='review-snippet-view'], [itemprop='review'], [class*='review-view']"
                  );

                  for (const block of reviewBlocks) {
                    const authorEl = block.querySelector("[class*='author'], [class*='name']");
                    const textEl = block.querySelector("[itemprop='reviewBody'], [class*='review-text'], [class*='business-review-view__body'], [class*='business-review-view__body-text'], [class*='comment-text']");
                    const dateEl = block.querySelector("[class*='date']");
                    const ratingEl = block.querySelector("[aria-label*='из 5'], [class*='rating']");

                    const author = (authorEl?.textContent || '').trim();
                    const text = (textEl?.textContent || block.textContent || '').trim();
                    const date = (dateEl?.textContent || '').trim();
                    const rating = (ratingEl?.getAttribute('aria-label') || ratingEl?.textContent || '').trim();

                    if (text && text.length > 6 && text.toLowerCase() !== author.toLowerCase()) {
                      reviewsRaw.push({ author, text, date, rating });
                    }
                  }
                  return reviewsRaw;
                }
            """)
            
            print(f"✅ Extracted {len(reviews_raw)} reviews from DOM")
            (LOGS_DIR / "diag_mood26_reviews_raw.json").write_text(
                json.dumps(reviews_raw, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            # Extract photos using same selectors as production
            print("📸 Extracting photos...")
            photos_raw = await page.evaluate("""
                () => {
                  const uniq = (arr) => Array.from(new Set(arr.filter(Boolean)));
                  const images = uniq(
                    Array.from(document.querySelectorAll('img[src]'))
                      .map(i => i.getAttribute('src') || '')
                      .filter(src => /avatars\\.mds\\.yandex\\.net/.test(src))
                  ).slice(0, 20);
                  return images;
                }
            """)
            
            print(f"✅ Extracted {len(photos_raw)} photos from DOM")
            (LOGS_DIR / "diag_mood26_photos_raw.json").write_text(
                json.dumps(photos_raw, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            print("\n" + "=" * 60)
            print("📊 DIAGNOSTIC SUMMARY:")
            print(f"  Reviews extracted: {len(reviews_raw)}")
            print(f"  Photos extracted: {len(photos_raw)}")
            print(f"  Files saved to: {LOGS_DIR}")
            print("=" * 60)

        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
