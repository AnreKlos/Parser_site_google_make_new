#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KURSOR X-Ray Auditor - Модуль технического аудита сайтов
"""

import sys
from pathlib import Path

# Добавляем корень проекта в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import time
import re
import json
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

from db.models import Lead
from db.database import get_async_session


# --- Константы ---
DEFAULT_TIMEOUT = 15  # секунд
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# --- Регулярные выражения для контактов ---
EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
TELEGRAM_PATTERNS = [
    re.compile(r'https?://(?:t\.me|telegram\.me)/[\w]+'),
    re.compile(r'tg://resolve\?domain=[\w]+'),
]
WHATSAPP_PATTERNS = [
    re.compile(r'https?://(?:wa\.me|chat\.whatsapp\.com)/[\w]+'),
    re.compile(r'whatsapp://[^\s"\'<>]+'),
]
SOCIAL_PATTERNS = {
    'telegram': TELEGRAM_PATTERNS,
    'whatsapp': WHATSAPP_PATTERNS,
}

# --- Признаки мёртвого/заблокированного сайта ---
SITE_DEAD_PATTERNS = [
    "доступ ограничен",
    "доступ к информационному ресурсу ограничен",
    "403 forbidden",
    "срок регистрации домена истек",
    "domain has expired",
    "страница заблокирована",
    "сайт заблокирован",
    "доступ закрыт",
    "website blocked",
    "access denied",
    "access forbidden",
    "this domain has expired",
    "domain expired",
    "регистрация домена истекла",
    "домен не продлён",
    "домен припаркован",
    "parked domain",
    "this page is parked",
]


def is_site_dead(html: str) -> bool:
    """
    Проверяет, является ли страница заглушкой/блокировкой.
    Ищет типичные признаки в тексте страницы.
    """
    if not html:
        return False
    text = html.lower()
    for pattern in SITE_DEAD_PATTERNS:
        if pattern in text:
            return True
    return False


def extract_contacts(html: str) -> dict:
    """
    Извлекает email-адреса и ссылки на мессенджеры из HTML.
    Возвращает dict с ключами 'emails' (список) и 'social_links' (dict: platform -> url).
    """
    emails = set()
    social_links = {}

    try:
        soup = BeautifulSoup(html, 'html.parser')

        # Извлекаем все email из текста и атрибутов href="mailto:"
        # 1) mailto: ссылки
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.startswith('mailto:'):
                email = href[7:].split('?')[0].strip()
                if EMAIL_PATTERN.match(email):
                    emails.add(email.lower())

        # 2) Поиск email в тексте страницы
        text_content = soup.get_text()
        found_emails = EMAIL_PATTERN.findall(text_content)
        for email in found_emails:
            # Фильтруем ложные срабатывания
            if len(email) > 5 and '.' in email.split('@')[1]:
                emails.add(email.lower())

        # 3) Поиск в meta тегах
        for meta in soup.find_all('meta'):
            content = meta.get('content', '')
            if content:
                found = EMAIL_PATTERN.findall(content)
                for email in found:
                    emails.add(email.lower())

        # Извлекаем ссылки на мессенджеры
        # Ищем все href и src атрибуты
        all_urls = set()
        for tag in soup.find_all(True):
            for attr in ['href', 'src', 'data-href']:
                val = tag.get(attr, '')
                if val:
                    all_urls.add(val)

        # Также ищем URL в тексте
        url_in_text_pattern = re.compile(r'https?://[^\s<>"\']+')
        for url_match in url_in_text_pattern.finditer(text_content):
            all_urls.add(url_match.group(0))

        # Проверяем каждую ссылку на паттерны соцсетей
        for platform, patterns in SOCIAL_PATTERNS.items():
            for url in all_urls:
                for pattern in patterns:
                    if pattern.search(url):
                        if platform not in social_links:
                            social_links[platform] = []
                        # Нормализуем URL
                        clean_url = url.strip()
                        if clean_url not in social_links[platform]:
                            social_links[platform].append(clean_url)

    except Exception as e:
        pass  # Тихо игнорируем ошибки парсинга контактов

    return {
        'emails': sorted(list(emails)),
        'social_links': social_links
    }


# --- Функции аудита ---
async def fetch_page(session: aiohttp.ClientSession, url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[Optional[str], float, Optional[str]]:
    """
    Загружает HTML страницы и возвращает (html, load_time, error_message)
    """
    start_time = time.time()
    
    # Нормализация URL
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=False,  # Игнорируем SSL ошибки
            allow_redirects=True
        ) as response:
            load_time = time.time() - start_time
            
            if response.status >= 400:
                return None, load_time, f"HTTP ошибка: {response.status}"
            
            html = await response.text()
            return html, load_time, None
            
    except asyncio.TimeoutError:
        load_time = time.time() - start_time
        return None, load_time, "Таймаут при загрузке"
    
    except aiohttp.ClientError as e:
        load_time = time.time() - start_time
        return None, load_time, f"Ошибка соединения: {str(e)[:100]}"
    
    except Exception as e:
        load_time = time.time() - start_time
        return None, load_time, f"Неизвестная ошибка: {str(e)[:100]}"


def analyze_html(html: str, url: str, load_time: float) -> dict:
    """
    Анализирует HTML и возвращает результаты аудита
    """
    result = {
        "has_https": url.startswith("https://"),
        "load_time_sec": round(load_time, 2),
        "has_title": False,
        "has_h1": False,
        "has_meta_description": False,
        "has_viewport": False,
        "has_phone_link": False,
        "problems": [],
        "is_dead": False,
    }
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Проверка title
        title = soup.find('title')
        result["has_title"] = title is not None and len(title.get_text(strip=True)) > 0
        
        # Проверка H1
        h1 = soup.find('h1')
        result["has_h1"] = h1 is not None and len(h1.get_text(strip=True)) > 0
        
        # Проверка meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        result["has_meta_description"] = meta_desc is not None and meta_desc.get('content', '').strip() != ''
        
        # Проверка viewport
        viewport = soup.find('meta', attrs={'name': 'viewport'})
        result["has_viewport"] = viewport is not None
        
        # Проверка ссылки на телефон
        phone_link = soup.find('a', href=re.compile(r'^tel:'))
        result["has_phone_link"] = phone_link is not None
        
        # Проверка на мёртвый/заблокированный сайт
        if is_site_dead(html):
            result["is_dead"] = True
            result["problems"].append("САЙТ МЁРТВ: заблокирован или заглушка")
        
    except Exception as e:
        result["problems"].append(f"Ошибка парсинга HTML: {str(e)[:50]}")
    
    return result


def compute_tech_score(analysis: dict) -> tuple[int, str]:
    """
    Вычисляет технический балл (0-100) и формирует заметки
    """
    # Если сайт мёртв — ЖЁСТКО ставим 0 и [SITE_DEAD]
    if analysis.get("is_dead"):
        return 0, "[SITE_DEAD] Сайт заблокирован или недоступен (заглушка/блокировка провайдера)"
    
    score = 100
    problems = []
    
    # HTTPS (критично)
    if not analysis["has_https"]:
        score -= 20
        problems.append("нет HTTPS")
    
    # Время загрузки
    load_time = analysis["load_time_sec"]
    if load_time > 5:
        score -= 15
        problems.append(f"медленная загрузка ({load_time:.1f}с)")
    elif load_time > 3:
        score -= 5
    
    # Title (критично)
    if not analysis["has_title"]:
        score -= 15
        problems.append("нет заголовка title")
    
    # H1 (важно)
    if not analysis["has_h1"]:
        score -= 10
        problems.append("нет заголовка H1")
    
    # Meta description (важно для SEO)
    if not analysis["has_meta_description"]:
        score -= 10
        problems.append("нет meta description")
    
    # Viewport (критично для мобильных)
    if not analysis["has_viewport"]:
        score -= 15
        problems.append("нет viewport (плохо на мобильных)")
    
    # Телефон кликабельный
    if not analysis["has_phone_link"]:
        score -= 5
        problems.append("телефон не кликабелен")
    
    # Гарантируем минимум 0
    score = max(0, min(100, score))
    
    # Формируем заметки
    if problems:
        notes = "Проблемы: " + "; ".join(problems)
    else:
        notes = "Сайт в порядке, критичных проблем нет"
    
    return score, notes


async def audit_single_site(session: aiohttp.ClientSession, lead: Lead) -> dict:
    """
    Проводит аудит одного сайта
    """
    url = lead.website

    # Загружаем страницу
    html, load_time, error = await fetch_page(session, url)

    if error or html is None:
        # Сайт недоступен (таймаут, ошибка соединения, HTTP 4xx/5xx)
        return {
            "lead_id": lead.id,
            "tech_score": 0,
            "load_time_sec": None,
            "status": "audited",
            "audit_notes": f"[SITE_DEAD] {error or 'Сайт недоступен'}",
            "emails": None,
            "social_links": None,
        }

    # Извлекаем контакты (email, соцсети) ДО проверки is_dead
    contacts = extract_contacts(html)
    emails_json = json.dumps(contacts['emails'], ensure_ascii=False) if contacts['emails'] else None
    social_links_json = json.dumps(contacts['social_links'], ensure_ascii=False) if contacts['social_links'] else None

    # Анализируем HTML
    analysis = analyze_html(html, url, load_time)

    # Если сайт мёртв (заглушка/блокировка) — ЖЁСТКО ставим load_time_sec = None
    if analysis.get("is_dead"):
        return {
            "lead_id": lead.id,
            "tech_score": 0,
            "load_time_sec": None,
            "status": "audited",
            "audit_notes": "[SITE_DEAD] Сайт заблокирован или недоступен (заглушка/блокировка провайдера)",
            "emails": emails_json,
            "social_links": social_links_json,
        }

    # Вычисляем балл
    tech_score, notes = compute_tech_score(analysis)

    return {
        "lead_id": lead.id,
        "tech_score": tech_score,
        "load_time_sec": analysis["load_time_sec"],
        "status": "audited",
        "audit_notes": notes,
        "emails": emails_json,
        "social_links": social_links_json,
    }


async def run_xray_audit(batch_size: int = 5) -> dict:
    """
    Запускает аудит всех новых лидов
    
    Args:
        batch_size: Количество параллельных запросов
    
    Returns:
        Статистика аудита
    """
    from sqlalchemy import select, update
    
    stats = {
        "total": 0,
        "audited": 0,
        "errors": 0,
        "results": []
    }
    
    async with get_async_session() as session:
        # Получаем только лиды со статусом "new" (есть сайт, нужен аудит)
        # Лиды со статусом "no_website" пропускаются — им не нужен аудит сайта
        result = await session.execute(
            select(Lead).where(Lead.status == "new")
        )
        leads = result.scalars().all()
        
        stats["total"] = len(leads)
        
        if not leads:
            return stats
        
        # Создаем HTTP сессию
        connector = aiohttp.TCPConnector(limit=batch_size, ssl=False)
        async with aiohttp.ClientSession(
            connector=connector,
            headers={"User-Agent": USER_AGENT}
        ) as http_session:
            
            # Аудитим пачками
            for i in range(0, len(leads), batch_size):
                batch = leads[i:i + batch_size]
                
                # Запускаем аудит параллельно
                tasks = [audit_single_site(http_session, lead) for lead in batch]
                audit_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Обрабатываем результаты
                for lead, audit_result in zip(batch, audit_results):
                    if isinstance(audit_result, Exception):
                        # Ошибка при аудите
                        audit_result = {
                            "lead_id": lead.id,
                            "tech_score": 0,
                            "load_time_sec": None,
                            "status": "audited",
                            "audit_notes": f"[SITE_DEAD] Ошибка: {str(audit_result)[:100]}",
                            "emails": None,
                            "social_links": None,
                        }
                    
                    # Обновляем запись в БД
                    await session.execute(
                        update(Lead)
                        .where(Lead.id == lead.id)
                        .values(
                            tech_score=audit_result["tech_score"],
                            load_time_sec=audit_result["load_time_sec"],
                            status=audit_result["status"],
                            audit_notes=audit_result["audit_notes"],
                            emails=audit_result.get("emails"),
                            social_links=audit_result.get("social_links"),
                        )
                    )
                    
                    stats["results"].append(audit_result)
                    stats["audited"] += 1
                
                # Коммитим пачку
                await session.commit()
    
    return stats


# --- Синхронная обертка для вызова из дашборда ---
def run_xray_audit_sync() -> dict:
    """
    Синхронная обертка для запуска аудита
    """
    return asyncio.run(run_xray_audit())


# --- CLI запуск ---
if __name__ == "__main__":
    print("🔬 KURSOR X-Ray Auditor")
    print("=" * 40)
    
    result = run_xray_audit_sync()
    
    print(f"\n📊 Результаты аудита:")
    print(f"   Всего лидов: {result['total']}")
    print(f"   Проверено: {result['audited']}")
    
    if result['results']:
        print(f"\n📋 Детали:")
        for r in result['results'][:10]:  # Показываем первые 10
            dead_icon = "💀" if "[SITE_DEAD]" in r.get('audit_notes', '') else ""
            status_icon = "✅" if r['tech_score'] > 0 else "❌"
            print(f"   {status_icon}{dead_icon} ID={r['lead_id']}: score={r['tech_score']}, {r['audit_notes'][:60]}")