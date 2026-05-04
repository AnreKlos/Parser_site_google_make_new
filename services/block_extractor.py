#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import json
import random
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "leads.db"
EXTRACTED_DIR = BASE_DIR / "data" / "extracted"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
]


# Future selector hints:
# - Tilda: .t-store__card, .t-accordion__wrapper, .t668__col
# - Wix: [data-hook*='product'], [data-testid*='accordion']
# - vsite.biz: .services-carousel, .accordion-item
# - YClients widgets: iframe + embedded service blocks


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{timestamp()}] {message}", flush=True)


def choose_user_agent() -> str:
    return random.choice(USER_AGENTS)


def slugify_name(name: str, lead_id: int) -> str:
    translit_map = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    lower = (name or "").strip().lower()
    translit = "".join(translit_map.get(ch, ch) for ch in lower)
    translit = re.sub(r"[^a-z0-9]+", "-", translit)
    translit = re.sub(r"-+", "-", translit).strip("-")
    return translit or f"lead-{lead_id}"


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def resolve_target_url(lead: Dict[str, Any]) -> str:
    website = compact(str(lead.get("website") or lead.get("website_url") or ""))
    if not website:
        return ""

    parsed = urlparse(website)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")

    social_hosts = {"instagram.com", "www.instagram.com", "vk.com", "m.vk.com", "t.me", "telegram.me"}
    if host in social_hosts:
        handle = ""
        if host.endswith("instagram.com"):
            handle = path.split("/")[0] if path else ""
            handle = re.sub(r"[^a-zA-Z0-9]", "", handle)
        elif host.endswith("vk.com") or host in {"t.me", "telegram.me"}:
            handle = path.split("/")[0] if path else ""
            handle = re.sub(r"[^a-zA-Z0-9]", "", handle)

        if handle:
            return f"https://{handle.lower()}.orgs.biz/"

    return website


def load_lead(lead_id: int) -> Optional[Dict[str, Any]]:
    if not DB_PATH.exists():
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row) if row else None


async def random_pause(min_seconds: float, max_seconds: float, reason: str) -> None:
    seconds = random.uniform(min_seconds, max_seconds)
    log(f"⏳ Пауза {seconds:.1f} сек ({reason})")
    await asyncio.sleep(seconds)


def normalize_price(value: str) -> str:
    text = compact(value)
    if not text:
        return ""
    text = text.replace("руб.", "₽").replace("руб", "₽")
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"(?:от\s*)?\d[\d\s]{1,10}\s*(?:₽|р|р\.)", text, flags=re.IGNORECASE)
    if m:
        price = m.group(0).replace("р.", "₽").replace("р", "₽")
        return compact(price)
    return ""


def clean_service_title(value: str) -> str:
    text = compact(value)
    if not text:
        return ""
    text = re.sub(r"(?:от\s*)?\d[\d\s]{1,10}\s*(?:₽|р|р\.)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)товары\s+и\s+услуги", "", text)
    text = compact(text)
    return text


def clean_description(value: str) -> str:
    text = compact(value)
    if not text:
        return ""
    garbage_markers = [
        "orgs.biz- каталог организаций",
        "Информация размещена в ознакомительных целях",
        "ГлавнаяТовары и услуги",
        "Позвонить",
        "📞",
        "✉",
        "Главная",
        "Контакты",
    ]
    for marker in garbage_markers:
        idx = text.lower().find(marker.lower())
        if idx > 0:
            text = text[:idx].strip()
    text = re.sub(r"ПОДРОБНЕЕ", "", text, flags=re.IGNORECASE)
    
    # Фильтруем строки с императивными конструкциями и телефонами
    imperative_patterns = [
        r"ВЫПОЛНЯЕТСЯ",
        r"ЗАПИСЬ",
        r"ТОЛЬКО",
        r"ВНИМАНИЕ",
        r"📞",
        r"\d{3,4}[-\s]?\d{2,3}[-\s]?\d{2}",
    ]
    for pattern in imperative_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    
    # Фильтруем строки начинающиеся с заглавных букв (императив)
    lines = text.split('. ')
    filtered_lines = []
    for line in lines:
        line = line.strip()
        if line and len(line) > 3:
            # Если строка начинается с заглавной буквы и содержит императив — пропускаем
            if line[0].isupper() and any(x in line.upper() for x in ["ВЫПОЛНЯЕТСЯ", "ЗАПИСЬ", "ТОЛЬКО", "ВНИМАНИЕ", "ПРЕДВАРИТЕЛЬНО"]):
                continue
            filtered_lines.append(line)
    text = '. '.join(filtered_lines)
    
    text = compact(text)
    if len(text) > 260:
        text = text[:257].rstrip() + "..."
    return text


def is_valid_service(service: Dict[str, str]) -> bool:
    title = compact(service.get("title", ""))
    price = compact(service.get("price", ""))
    if not title:
        return False
    if len(title) < 3 or len(title) > 120:
        return False
    if title.lower() in {"подробнее", "заказать", "купить", "читать"}:
        return False
    if re.fullmatch(r"[\d\s₽р.]+", title, flags=re.IGNORECASE):
        return False
    if price and not re.search(r"\d", price):
        return False
    return True


async def _open_page(url: str):
    ua = choose_user_agent()
    log(f"🕵️ UA: {ua.split('Chrome/')[-1].split(' ')[0] if 'Chrome/' in ua else ua[:20]}")

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    context = await browser.new_context(
        user_agent=ua,
        viewport={"width": 1920, "height": 1080},
        locale="ru-RU",
    )
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await random_pause(2.0, 4.0, "после загрузки")
        for _ in range(4):
            await page.mouse.wheel(0, random.randint(700, 1400))
            await asyncio.sleep(random.uniform(0.6, 1.2))
        await random_pause(1.0, 2.0, "после скролла")
        return playwright, browser, context, page
    except Exception:
        await context.close()
        await browser.close()
        await playwright.stop()
        raise


async def _extract_services_from_page(page) -> List[Dict[str, str]]:
    raw_cards = await page.evaluate(
        r"""
        () => {
          const uniq = (arr) => Array.from(new Set(arr.filter(Boolean)));
          const cards = [];
          const priceRegex = /(?:от\s*)?\d[\d\s]{1,10}\s*(?:₽|р\.?)/i;

          const sectionRoots = Array.from(document.querySelectorAll('section, div'))
            .filter(el => {
              const head = (el.querySelector('h1,h2,h3,h4,.title,.heading')?.textContent || '').toLowerCase();
              const txt = (el.textContent || '').toLowerCase();
              return head.includes('товары') || head.includes('услуги') || txt.includes('товары и услуги');
            })
            .slice(0, 8);

          const roots = sectionRoots.length ? sectionRoots : [document.body];

          for (const root of roots) {
            const nodes = root.querySelectorAll('article, li, .card, .item, .product, [class*="card"], [class*="item"]');
            for (const node of nodes) {
              const text = (node.innerText || '').trim();
              if (!text || text.length < 8 || text.length > 500) continue;
              const pm = text.match(priceRegex);
              if (!pm) continue;

              const img = node.querySelector('img');
              const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
              const titleLine = lines.find(s => s && !priceRegex.test(s) && s.length <= 90) || lines[0] || '';
              const descriptionLine = lines.filter(s => s && s !== titleLine && !priceRegex.test(s)).join(' ').trim();

              cards.push({
                title: titleLine,
                price: pm[0],
                image_url: img ? (img.getAttribute('src') || img.getAttribute('data-src') || '') : '',
                description: descriptionLine,
              });
              if (cards.length >= 80) break;
            }
            if (cards.length >= 80) break;
          }

          return cards;
        }
        """
    )

    services: List[Dict[str, str]] = []
    seen = set()
    for item in raw_cards if isinstance(raw_cards, list) else []:
        if not isinstance(item, dict):
            continue
        service = {
            "title": clean_service_title(str(item.get("title") or "")),
            "price": normalize_price(str(item.get("price") or "")),
            "image_url": compact(str(item.get("image_url") or "")),
            "description": clean_description(str(item.get("description") or "")),
        }
        if not is_valid_service(service):
            continue
        key = (service["title"].lower(), service["price"])
        if key in seen:
            continue
        seen.add(key)
        services.append(service)

    # orgs.biz: often has short card + detail page with full description
    detail_candidates = await page.evaluate(
        r"""
        () => {
          const out = [];
          const links = Array.from(document.querySelectorAll('a[href]'));
          for (const a of links) {
            const href = (a.getAttribute('href') || '').trim();
            const txt = (a.textContent || '').trim();
            if (!href) continue;
            if (/\/item\//i.test(href) || /\/service\//i.test(href) || /подроб/i.test(txt)) {
              out.push(href);
            }
          }
          return Array.from(new Set(out)).slice(0, 20);
        }
        """
    )

    detail_texts: List[str] = []
    for href in detail_candidates if isinstance(detail_candidates, list) else []:
        try:
            full_url = await page.evaluate("u => new URL(u, window.location.href).href", str(href))
            await page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(0.6, 1.2))
            detail_text = await page.evaluate(
                r"""
                () => {
                  const blocks = [
                    document.querySelector('article'),
                    document.querySelector('.content'),
                    document.querySelector('.description'),
                    document.querySelector('[class*="product"]'),
                    document.body,
                  ].filter(Boolean);
                  for (const b of blocks) {
                    const txt = (b.innerText || '').trim();
                    if (txt && txt.length > 40) return txt.slice(0, 800);
                  }
                  return '';
                }
                """
            )
            if detail_text:
                detail_texts.append(clean_description(str(detail_text)))
        except Exception:
            continue

    if detail_texts and services:
        for idx, text in enumerate(detail_texts):
            if idx >= len(services):
                break
            if len(text) > len(services[idx].get("description") or ""):
                services[idx]["description"] = text

    return services[:30]


async def _extract_faq_from_page(page) -> List[Dict[str, str]]:
    raw_faq = await page.evaluate(
        r"""
        () => {
          const out = [];
          const pushItem = (q, a) => {
            q = (q || '').trim();
            a = (a || '').trim();
            if (!q || !a) return;
            if (q.length < 5 || a.length < 10) return;
            out.push({ q, a });
          };

          // details/summary
          document.querySelectorAll('details').forEach(d => {
            const q = d.querySelector('summary')?.textContent || '';
            const a = d.textContent?.replace(q, '') || '';
            pushItem(q, a);
          });

          // Common accordion patterns
          document.querySelectorAll('.faq-item, .accordion-item, [class*="faq"], [class*="accordion"], [class*="expand"]').forEach(item => {
            const q = item.querySelector('.question, .title, .header, button, h3, h4')?.textContent || '';
            const a = item.querySelector('.answer, .content, .body, .panel, [class*="answer"], [class*="content"]')?.textContent || '';
            pushItem(q, a);
          });

          return out;
        }
        """
    )

    faq: List[Dict[str, str]] = []
    seen = set()
    for item in raw_faq if isinstance(raw_faq, list) else []:
        if not isinstance(item, dict):
            continue
        q = compact(str(item.get("q") or ""))
        a = compact(str(item.get("a") or ""))
        if not q or not a:
            continue
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        faq.append({"q": q, "a": a})
    return faq[:30]


async def _extract_faq_from_faq_link(page) -> List[Dict[str, str]]:
    faq_links = await page.evaluate(
        r"""
        () => {
          const links = Array.from(document.querySelectorAll('a[href]'));
          const out = [];
          for (const a of links) {
            const txt = (a.textContent || '').toLowerCase();
            const href = (a.getAttribute('href') || '').trim();
            if (!href) continue;
            if (txt.includes('faq') || txt.includes('чаво') || txt.includes('вопрос') || /faq|chavo|vopros/i.test(href)) {
              out.push(href);
            }
          }
          return Array.from(new Set(out)).slice(0, 5);
        }
        """
    )

    for href in faq_links if isinstance(faq_links, list) else []:
        try:
            full_url = await page.evaluate("u => new URL(u, window.location.href).href", str(href))
            await page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(0.8, 1.4))
            faq = await _extract_faq_from_page(page)
            if faq:
                return faq
        except Exception:
            continue
    return []


async def _extract_faq_from_known_paths(page) -> List[Dict[str, str]]:
    base = await page.evaluate("() => window.location.origin")
    candidates = [
        f"{base}/chavo",
        f"{base}/faq",
        f"{base}/voprosy-i-otvety",
        f"{base}/questions",
    ]

    for url in candidates:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(0.8, 1.4))
            faq = await _extract_faq_from_page(page)
            if faq:
                return faq

            raw_pairs = await page.evaluate(
                r"""
                () => {
                  const out = [];
                  const blocks = Array.from(document.querySelectorAll('h2, h3, h4, .question, .title'));
                  for (const b of blocks) {
                    const q = (b.textContent || '').trim();
                    if (!q || q.length < 5) continue;
                    const next = b.nextElementSibling;
                    const a = (next?.textContent || '').trim();
                    if (!a || a.length < 10) continue;
                    out.push({ q, a });
                    if (out.length >= 30) break;
                  }
                  return out;
                }
                """
            )

            faq2: List[Dict[str, str]] = []
            seen = set()
            for item in raw_pairs if isinstance(raw_pairs, list) else []:
                if not isinstance(item, dict):
                    continue
                q = compact(str(item.get("q") or ""))
                a = compact(str(item.get("a") or ""))
                if not q or not a:
                    continue
                k = q.lower()
                if k in seen:
                    continue
                seen.add(k)
                faq2.append({"q": q, "a": a})
            if faq2:
                return faq2[:30]
        except Exception:
            continue

    return []


async def _extract_team_from_page(page) -> List[Dict[str, str]]:
    raw_team = await page.evaluate(
        r"""
        () => {
          const out = [];
          const seen = new Set();
          const keyWords = ['мастер', 'команд', 'специалист', 'сотрудник', 'team', 'staff'];

          const normalize = (v) => (v || '').replace(/\s+/g, ' ').trim();
          const isLikelyName = (v) => {
            const t = normalize(v);
            if (!t || t.length < 3 || t.length > 60) return false;
            if (/https?:\/\//i.test(t)) return false;
            if (/^[\d\s.,:;!?()\-+]+$/.test(t)) return false;
            return true;
          };

          const roots = Array.from(document.querySelectorAll('section, div')).filter(el => {
            const head = (el.querySelector('h1,h2,h3,h4,.title,.heading,[class*="title"],[class*="heading"]')?.textContent || '').toLowerCase();
            return keyWords.some(k => head.includes(k));
          }).slice(0, 20);

          for (const root of roots) {
            const imgs = Array.from(root.querySelectorAll('img'));
            for (const img of imgs) {
              const rawSrc = img.getAttribute('src') || img.getAttribute('data-src') || '';
              const src = normalize(rawSrc);
              if (!src) continue;

              let name = '';
              let node = img;
              for (let depth = 0; depth < 6 && node && node !== document.body; depth++) {
                const picks = node.querySelectorAll('h2,h3,h4,p,.name,.title,[class*="name"],[class*="master"]');
                for (const p of picks) {
                  const txt = normalize(p.textContent || '');
                  if (isLikelyName(txt)) {
                    name = txt;
                    break;
                  }
                }
                if (name || node === root) break;
                node = node.parentElement;
              }

              if (!name) continue;

              let photoUrl = src;
              try {
                photoUrl = new URL(src, window.location.href).href;
              } catch (_) {
                continue;
              }

              const dedupeKey = `${name.toLowerCase()}|${photoUrl}`;
              if (seen.has(dedupeKey)) continue;
              seen.add(dedupeKey);
              out.push({ name, photo_url: photoUrl });
              if (out.length >= 10) break;
            }
            if (out.length >= 10) break;
          }

          return out;
        }
        """
    )

    team: List[Dict[str, str]] = []
    for item in raw_team if isinstance(raw_team, list) else []:
        if not isinstance(item, dict):
            continue
        name = compact(str(item.get("name") or ""))
        photo_url = compact(str(item.get("photo_url") or ""))
        if not name or not photo_url:
            continue
        if len(name) < 3 or len(name) > 60:
            continue
        team.append({"name": name, "photo_url": photo_url})
        if len(team) >= 10:
            break
    return team


async def _extract_faq_accordion(page) -> List[Dict[str, str]]:
    """Извлекает FAQ из div#faq-accordion с div.faq-item."""
    current_url = page.url
    base_url = str(page.url).split('/')[0] + '//' + str(page.url).split('/')[2]
    
    # Если ушли со стартовой страницы — возвращаемся
    if '/product/' in current_url or '/services/' in current_url:
        await page.goto(base_url, wait_until='domcontentloaded')
        await asyncio.sleep(3.0)
        # Скроллим до низа чтобы FAQ загрузился
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2.0)
    
    # Скроллим к FAQ и ждём появления
    try:
        await page.evaluate("""
            () => {
                const el = document.querySelector('#faq-accordion')
                    || document.querySelector('[id*="faq"]')
                    || document.querySelector('[class*="faq"]');
                if (el) el.scrollIntoView({ behavior: 'instant', block: 'center' });
            }
        """)
        await asyncio.sleep(2.0)
        
        # Ждём появления элементов в DOM
        await page.wait_for_selector(
            '#faq-accordion, .faq-item, [itemprop="mainEntity"]',
            timeout=8000
        )
        await asyncio.sleep(1.0)
    except Exception:
        pass  # если не нашли — идём дальше, вернём []

    try:
        # Раскрываем все вопросы гармошки кликом
        faq_triggers = await page.query_selector_all(
            '#faq-accordion .faq-item h3, '
            '#faq-accordion .faq-item [class*="question"], '
            '#faq-accordion .faq-item [class*="title"], '
            '#faq-accordion [itemtype*="Question"] h3'
        )
        for trigger in faq_triggers:
            try:
                await trigger.scroll_into_view_if_needed()
                await trigger.click()
                await asyncio.sleep(0.4)
            except Exception:
                pass
        await asyncio.sleep(1.0)
    except Exception:
        pass

    debug = await page.evaluate("""
        () => {
            // Ищем по всем возможным признакам FAQ
            return {
                faq_accordion: !!document.querySelector('#faq-accordion'),
                faq_items: document.querySelectorAll('.faq-item').length,
                h3_itemprop: document.querySelectorAll('h3[itemprop]').length,
                p_itemprop: document.querySelectorAll('p[itemprop]').length,
                // Ищем любые id/классы со словом faq
                ids_with_faq: Array.from(document.querySelectorAll('[id*="faq"],[id*="FAQ"]')).map(e => e.id).slice(0,5),
                classes_with_faq: Array.from(document.querySelectorAll('[class*="faq"],[class*="FAQ"]')).map(e => e.className).slice(0,5),
                // Schema.org Question
                schema_questions: document.querySelectorAll('[itemtype*="Question"]').length,
                // Просто все h3 на странице
                all_h3_count: document.querySelectorAll('h3').length,
                all_h3_texts: Array.from(document.querySelectorAll('h3')).map(e => e.textContent.trim().slice(0,50)).slice(0,10),
                // Текущий URL
                url: window.location.href
            }
        }
    """)
    log(f"=== FAQ DEBUG EXTENDED ===")
    for k, v in debug.items():
        log(f"  {k}: {v}")

    raw_faq = await page.evaluate(
        r"""
        () => {
          const out = [];
          const accordion = document.querySelector('#faq-accordion');
          if (!accordion) return out;

          const items = accordion.querySelectorAll('.faq-item');
          items.forEach(item => {
            const question = item.querySelector('h3[itemprop="name"]')?.textContent || '';
            const answer = item.querySelector('p[itemprop="text"]')?.textContent || '';
            if (question && answer) {
              out.push({ question: question.trim(), answer: answer.trim() });
            }
          });
          return out;
        }
        """
    )

    faq: List[Dict[str, str]] = []
    for item in raw_faq if isinstance(raw_faq, list) else []:
        if not isinstance(item, dict):
            continue
        q = compact(str(item.get("question") or ""))
        a = compact(str(item.get("answer") or ""))
        if q and a:
            faq.append({"q": q, "a": a})
    return faq[:10]


async def _extract_both_in_one_visit(url: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, str]]]:
    playwright = browser = context = page = None
    try:
        playwright, browser, context, page = await _open_page(url)
        services = await _extract_services_from_page(page)
        # Скроллим до низа страницы чтобы orgs.biz раскрыл lazy-блоки
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2.0)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)
        
        # Кликаем по всем закрытым заголовкам гармошки чтобы раскрыть
        await page.evaluate("""
            () => {
                const headers = document.querySelectorAll(
                    '.accordion-header, .faq-question, [class*="accordion"] button, '
                    + '[class*="faq"] button, summary'
                );
                headers.forEach(h => { try { h.click(); } catch(e) {} });
            }
        """)
        await asyncio.sleep(1.5)
        # Специфичный паттерн div#faq-accordion
        faq = await _extract_faq_accordion(page)
        if not faq:
            faq = await _extract_faq_from_page(page)
        team = await _extract_team_from_page(page)
        if not faq:
            faq = await _extract_faq_from_faq_link(page)
        if not faq:
            faq = await _extract_faq_from_known_paths(page)
        return services, faq, team
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()


async def extract_services_carousel(url: str) -> List[Dict[str, str]]:
    if not url:
        return []
    log(f"🔍 Извлекаю карусель услуг: {url}")
    playwright = browser = context = page = None
    try:
        playwright, browser, context, page = await _open_page(url)
        services = await _extract_services_from_page(page)
        log(f"✅ serviceCarousel: {len(services)}")
        return services
    except Exception as exc:
        log(f"❌ Ошибка extract_services_carousel: {exc}")
        return []
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()


async def extract_faq_accordion(url: str) -> List[Dict[str, str]]:
    if not url:
        return []
    log(f"🔍 Извлекаю FAQ-гармошку: {url}")
    playwright = browser = context = page = None
    try:
        playwright, browser, context, page = await _open_page(url)
        faq = await _extract_faq_from_page(page)
        log(f"✅ faq_accordion: {len(faq)}")
        return faq
    except Exception as exc:
        log(f"❌ Ошибка extract_faq_accordion: {exc}")
        return []
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()


async def extract_all(lead_id: int) -> Optional[Dict[str, Any]]:
    lead = load_lead(lead_id)
    if not lead:
        log(f"❌ Лид ID={lead_id} не найден")
        return None

    lead_name = str(lead.get("name") or f"Lead {lead_id}")
    website = resolve_target_url(lead)
    if not website:
        log(f"❌ У лида ID={lead_id} отсутствует website")
        return None

    slug = slugify_name(lead_name, lead_id)
    log(f"🔍 Парсинг блоков: lead_id={lead_id} | {lead_name}")

    try:
        services, faq, team = await _extract_both_in_one_visit(website)
    except Exception as exc:
        log(f"❌ Ошибка парсинга блоков: {exc}")
        return None

    service_carousel = [
        {
            "name": compact(str(item.get("title") or "")),
            "price": compact(str(item.get("price") or "")),
            "image": compact(str(item.get("image_url") or "")),
            "description": clean_description(str(item.get("description") or "")),
        }
        for item in services
        if isinstance(item, dict) and compact(str(item.get("title") or ""))
    ]

    payload: Dict[str, Any] = {
        "lead_id": lead_id,
        "lead_name": lead_name,
        "source_url": website,
        "extracted_at": timestamp(),
        "serviceCarousel": service_carousel,
        "faq_accordion": faq,
        "team": team,
    }

    log(f"✅ team: {len(team)} мастеров")

    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EXTRACTED_DIR / f"{slug}-{lead_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    log(f"✅ Сохранено: {out_path}")
    log(f"✅ Итого: services={len(services)} faq={len(faq)}")

    return {
        "lead_id": lead_id,
        "slug": slug,
        "output_path": str(out_path),
        "services": len(services),
        "faq": len(faq),
        "data": payload,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract structured site blocks")
    parser.add_argument("lead_id", type=int, help="Lead ID")
    args = parser.parse_args()

    result = asyncio.run(extract_all(args.lead_id))
    if not result:
        raise SystemExit(1)

    print("=" * 60)
    print("🏁 Block Extractor completed")
    print(f"📄 File: {result['output_path']}")
    print(f"📦 serviceCarousel={result['services']} | faq_accordion={result['faq']}")


if __name__ == "__main__":
    main()
