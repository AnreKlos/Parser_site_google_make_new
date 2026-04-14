from __future__ import annotations

import re
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import stealth_sync

from src.models import BusinessRecord
from config.settings import settings


class TwoGISParser:
    BASE_SEARCH_URL = "https://2gis.ru/{city_slug}/search/{query}"

    def __init__(self, pause_sec: Optional[float] = None, headless: Optional[bool] = None):
        self.pause_sec = pause_sec if pause_sec is not None else settings.MIN_PAUSE_SEC
        self.headless = headless if headless is not None else settings.HEADLESS

    def search(self, query: str, city: str, limit: int = 20) -> List[BusinessRecord]:
        city_slug = self._slugify_city(city)
        query_slug = quote(query.strip())
        search_url = self.BASE_SEARCH_URL.format(city_slug=city_slug, query=query_slug)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                slow_mo=250 if not self.headless else 0,
            )
            context = browser.new_context(
                user_agent=settings.USER_AGENT,
                viewport={"width": 1440, "height": 960},
                locale="ru-RU",
            )
            page = context.new_page()
            stealth_sync(page)

            try:
                print(f"[2GIS] Открываю: {search_url}")
                page.goto(search_url, wait_until="domcontentloaded", timeout=max(60000, settings.TIMEOUT_MS * 2))
                self._handle_overlays(page)
                self._save_debug_artifacts(page)
                page.wait_for_timeout(4000)

                self._print_debug_meta(page)
                self._save_debug_artifacts(page)

                self._handle_possible_overlays(page)

                page.wait_for_timeout(3000)
                self._print_debug_meta(page)
                self._save_debug_artifacts(page, suffix="_after_clicks")

                firm_count = page.locator('a[href*="/firm/"]').count()
                geo_count = page.locator('a[href*="/geo/"]').count()

                print(f"[2GIS] Найдено ссылок /firm/: {firm_count}")
                print(f"[2GIS] Найдено ссылок /geo/: {geo_count}")

                if firm_count == 0:
                    raise RuntimeError(
                        "2GIS не показал карточки организаций. "
                        "Смотри debug_2gis_page*.html и debug_2gis_body*.txt"
                    )

                self._scroll_results(page, target_count=limit)
                card_urls = self._collect_card_urls(page, limit=limit)

                print(f"[2GIS] Собрано карточек: {len(card_urls)}")

                results: List[BusinessRecord] = []
                for card_url in card_urls:
                    try:
                        record = self._parse_card(context=context, card_url=card_url, city=city)
                        if record:
                            results.append(record)
                            print(f"[2GIS] OK: {record.business_name}")
                    except Exception as e:
                        print(f"[2GIS] Ошибка карточки {card_url}: {e}")

                    if self.pause_sec > 0:
                        time.sleep(self.pause_sec)

                    if len(results) >= limit:
                        break

                return results

            finally:
                context.close()
                browser.close()

    def _handle_overlays(self, page) -> None:
        """
        Обработка оверлеев на основе текстов из debug_2gis_body.txt
        """
        button_texts = [
            "Принять",
            "Согласен",
            "подробнее",
        ]

        for text in button_texts:
            try:
                locator = page.get_by_text(text, exact=False)
                if locator.count() > 0:
                    locator.first.click(timeout=2000)
                    print(f"[2GIS] Нажал кнопку (overlays): {text}")
                    page.wait_for_timeout(1500)
            except Exception:
                continue

    def _handle_possible_overlays(self, page) -> None:
        """
        Пытаемся прожать типовые кнопки:
        - принять cookies
        - понятно
        - согласен
        - разрешить/продолжить
        """
        button_texts = [
            "Принять",
            "Принять все",
            "Согласен",
            "Понятно",
            "Хорошо",
            "Продолжить",
            "Разрешить",
            "Ок",
            "OK",
        ]

        for text in button_texts:
            try:
                locator = page.get_by_text(text, exact=False)
                if locator.count() > 0:
                    locator.first.click(timeout=2000)
                    print(f"[2GIS] Нажал кнопку: {text}")
                    page.wait_for_timeout(1500)
            except Exception:
                continue

    def _scroll_results(self, page, target_count: int = 20) -> None:
        previous_count = 0

        for i in range(12):
            page.keyboard.press("PageDown")
            page.wait_for_timeout(1200)

            current_count = page.locator('a[href*="/firm/"]').count()
            print(f"[2GIS] Скролл {i + 1}: карточек {current_count}")

            if current_count >= target_count:
                break

            if current_count == previous_count:
                # если не растет — ещё чуть дожидаемся
                page.wait_for_timeout(1500)

            previous_count = current_count

    def _collect_card_urls(self, page, limit: int) -> List[str]:
        links = page.locator('a[href*="/firm/"]')
        urls: List[str] = []
        seen = set()

        count = links.count()
        for i in range(count):
            try:
                href = links.nth(i).get_attribute("href")
            except Exception:
                continue

            if not href:
                continue

            href = href.split("?")[0].strip()

            if "/firm/" not in href:
                continue

            full_url = self._make_absolute_2gis_url(href)

            if full_url in seen:
                continue

            seen.add(full_url)
            urls.append(full_url)

            if len(urls) >= limit:
                break

        return urls

    def _parse_card(self, context, card_url: str, city: str) -> Optional[BusinessRecord]:
        page = context.new_page()
        try:
            page.goto(card_url, wait_until="domcontentloaded", timeout=max(30000, settings.TIMEOUT_MS * 2))
            page.wait_for_timeout(2000)

            name = self._extract_text(page, ["h1"])
            if not name:
                return None

            body_text = self._clean_text(page.locator("body").inner_text())

            address = self._extract_text(page, [
                'a[href*="/geo/"]',
                '[class*="address"]',
            ])

            phone = self._extract_phone_from_text(body_text)
            website = self._extract_website_from_page(page, body_text)

            return BusinessRecord(
                business_name=name,
                category=None,
                city=city,
                address=address,
                phone=phone,
                website=website,
                maps_url=card_url,
                source="2gis",
            )
        finally:
            page.close()

    def _extract_text(self, page, selectors: List[str]) -> Optional[str]:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if locator.count() > 0:
                    text = locator.first.inner_text().strip()
                    text = self._clean_text(text)
                    if text:
                        return text
            except Exception:
                continue
        return None

    def _extract_phone_from_text(self, text: str) -> Optional[str]:
        patterns = [
            r"\+7\s?\(?\d{3}\)?\s?\d{3}[-\s]?\d{2}[-\s]?\d{2}",
            r"8\s?\(?\d{3}\)?\s?\d{3}[-\s]?\d{2}[-\s]?\d{2}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return self._clean_text(match.group(0))
        return None

    def _extract_website_from_page(self, page, body_text: str) -> Optional[str]:
        try:
            links = page.locator('a[href^="http"]')
            for i in range(links.count()):
                href = links.nth(i).get_attribute("href")
                if not href:
                    continue

                href = href.strip()
                ignored = ["2gis", "vk.com", "instagram.com", "facebook.com", "t.me", "wa.me"]
                if any(x in href for x in ignored):
                    continue

                return href
        except Exception:
            pass

        match = re.search(
            r"(https?://[^\s,]+|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s,]*)?)",
            body_text,
        )
        if match:
            website = match.group(1).strip().rstrip(".,);]")
            if "2gis" not in website:
                if not website.startswith(("http://", "https://")):
                    website = f"https://{website}"
                return website

        return None

    def _print_debug_meta(self, page) -> None:
        try:
            print(f"[2GIS] TITLE: {page.title()}")
        except Exception:
            print("[2GIS] TITLE: <error>")

        try:
            print(f"[2GIS] URL: {page.url}")
        except Exception:
            print("[2GIS] URL: <error>")

        try:
            body_text = self._clean_text(page.locator("body").inner_text())
            print(f"[2GIS] BODY PREVIEW: {body_text[:1000]}")
        except Exception:
            print("[2GIS] BODY PREVIEW: <error>")

    def _save_debug_artifacts(self, page, suffix: str = "") -> None:
        debug_dir = Path("data/out")
        debug_dir.mkdir(parents=True, exist_ok=True)

        html_path = debug_dir / f"debug_2gis_page{suffix}.html"
        text_path = debug_dir / f"debug_2gis_body{suffix}.txt"

        try:
            html = page.content()
            html_path.write_text(html, encoding="utf-8")
            print(f"[2GIS] Сохранил HTML: {html_path}")
        except Exception as e:
            print(f"[2GIS] Не смог сохранить HTML: {e}")

        try:
            body_text = page.locator("body").inner_text()
            text_path.write_text(body_text, encoding="utf-8")
            print(f"[2GIS] Сохранил BODY text: {text_path}")
        except Exception as e:
            print(f"[2GIS] Не смог сохранить BODY text: {e}")

    def _make_absolute_2gis_url(self, href: str) -> str:
        if href.startswith(("http://", "https://")):
            return href
        return f"https://2gis.ru{href}"

    def _slugify_city(self, city: str) -> str:
        city = city.strip().lower()
        mapping = {
            "москва": "moscow",
            "санкт-петербург": "spb",
            "спб": "spb",
            "казань": "kazan",
            "екатеринбург": "ekaterinburg",
            "новосибирск": "novosibirsk",
            "краснодар": "krasnodar",
            "нижний новгород": "n_novgorod",
            "ростов-на-дону": "rostov",
            "самара": "samara",
        }
        return mapping.get(city, quote(city))

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()