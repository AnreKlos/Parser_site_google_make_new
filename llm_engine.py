"""
LLM-based extraction and analysis.
Uses OpenRouter API with heuristics fallback.
"""

import os
import re
import json
import logging
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup
import requests

logger = logging.getLogger(__name__)

# Prompt template from user
PROMPT = """
Ты — парсер сайтов услуг (клиники, салоны, сервисные компании и любые сайты, где есть услуги/товары, цены и контакты).

Тебе даётся HTML страницы (обрезанный фрагмент):

[HTML НАЧАЛО]
{html}
[HTML КОНЕЦ]

Твоя задача — проанализировать структуру и извлечь данные. Найди и верни:

1. Прайс-лист (таблицы или блоки с ценами):
   - Список услуг с названиями и ценами.
   - Постарайся понять, где название услуги, а где цена, даже если нет явной таблицы.

2. Контакты:
   - Телефоны.
   - Email.
   - Почтовый адрес (город, улица и т.п., если видно).
   - Социальные сети (VK, Telegram, WhatsApp, Instagram, другие).

3. Услуги:
   - Список услуг/направлений с названием и кратким описанием (если есть).

Очень важно:
- Ответ должен быть ТОЛЬКО в формате JSON, без пояснений, комментариев, текстов до или после.
- Если какое-то поле не найдено — оставь соответствующий список пустым или строку пустой, но не убирай ключ.

Формат ответа строго такой:

{{
  "price": {{
    "items": [
      {{
        "name": "Название услуги",
        "price": "Цена как текст (например, '1500 ₽' или 'от 2000 руб.')"
      }}
    ]
  }},
  "contacts": {{
    "phones": ["строки с телефонами"],
    "emails": ["строки с email"],
    "address": "строка с адресом или пустая строка",
    "social": ["список ссылок на соцсети или мессенджеры"]
  }},
  "services": [
    {{
      "title": "Название услуги или раздела",
      "desc": "Краткое описание, если есть, иначе пустая строка"
    }}
  ]
}}

Требования:
- Всегда возвращай корректный JSON.
- Не используй комментарии.
- Не добавляй лишние поля, только те, что описаны выше.
"""

# OpenRouter configuration
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "gpt-4o-mini"  # or another model

def call_openrouter(html: str) -> Optional[Dict[str, Any]]:
    """Call OpenRouter API with the prompt and HTML."""
    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set, skipping API call")
        return None
    
    # Truncate HTML to avoid token limits (keep within ~20k chars)
    html_truncated = html[:20000]
    prompt = PROMPT.replace("{html}", html_truncated)
    
    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 2000
            },
            timeout=60
        )
        response.raise_for_status()
        result = response.json()
        content = result['choices'][0]['message']['content']
        
        # Clean markdown code blocks if present
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        elif '```' in content:
            content = content.split('```')[1].split('```')[0].strip()
        
        data = json.loads(content)
        logger.info("OpenRouter API call successful")
        return data
    except Exception as e:
        logger.error(f"OpenRouter API call failed: {e}")
        return None

def _heuristic_extract(soup: BeautifulSoup, selectors: Dict[str, Any], score: float) -> Dict[str, Any]:
    """Original heuristic extraction logic (fallback)."""
    data = {
        "prices": [],
        "contacts": {"phones": [], "emails": [], "address": [], "social": []},
        "services": []
    }
    
    # Get full page text
    full_text = soup.get_text(separator='\n', strip=True)
    
    # Enhanced price extraction - look for price patterns anywhere
    price_patterns = [
        r'(\d{1,3}[.,]?\d{3})\s*[р₽]',  # 15,000 ₽ or 15.000 ₽
        r'(\d{3,})\s*[р₽]',  # 15000 ₽
        r'(\d{1,3}\s+\d{3})\s*[р₽]?',  # 15 000
        r'от\s+(\d{3,})\s*[р₽]',  # от 15000 ₽
        r'от\s+(\d{1,3}[.,]?\d{3})',  # от 15,000
        r'(\d{1,3}[.,]\d{2})\s*[р₽]',  # 15.50 ₽
    ]
    
    for pattern in price_patterns:
        matches = re.findall(pattern, full_text, re.IGNORECASE)
        if matches:
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                clean_price = match.replace(' ', '').replace(',', '').replace('.', '')
                if clean_price.isdigit() and len(clean_price) >= 3:
                    data["prices"].append(clean_price + ' ₽')
    
    # Deduplicate prices
    data["prices"] = list(set(data["prices"]))[:20]
    
    # Enhanced phone extraction
    phone_patterns = [
        r'(\+7|8)[\s\-]?\(?[\d]{3}\)?[\s\-]?[\d]{2,3}[\s\-]?[\d]{2}[\s\-]?[\d]{2}',
        r'\+7[\s\-]?\(?[\d]{3}\)?[\s\-]?[\d]{3}[\s\-]?[\d]{2}[\s\-]?[\d]{2}',
        r'8[\s\-]?\(?[\d]{3}\)?[\s\-]?[\d]{3}[\s\-]?[\d]{2}[\s\-]?[\d]{2}',
    ]
    
    for pattern in phone_patterns:
        matches = re.findall(pattern, full_text)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            # Normalize phone format
            phone = re.sub(r'[\s\-\(\)]', '', match)
            if phone.startswith('8') and len(phone) == 11:
                phone = '+7' + phone[1:]
            if phone.startswith('+7') and len(phone) == 12:
                data["contacts"]["phones"].append(phone)
    
    data["contacts"]["phones"] = list(set(data["contacts"]["phones"]))
    
    # Enhanced email extraction
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(email_pattern, full_text)
    data["contacts"]["emails"] = list(set(emails))
    
    # Address extraction - look for Russian address patterns
    address_indicators = [
        r'г\.\s*[А-Яа-я\s]+',  # г. Москва
        r'ул\.\s*[А-Яа-я\s\d-]+',  # ул. Ленина
        r'пр\.\s*[А-Яа-я\s\d-]+',  # пр. Победы
        r'пер\.\s*[А-Яа-я\s\d-]+',  # пер. Тупой
        r'просп\.\s*[А-Яа-я\s\d-]+',  # просп. Мира
        r'р-н\s*[А-Яа-я\s]+',  # р-н Центральный
    ]
    
    addresses = []
    for pattern in address_indicators:
        matches = re.findall(pattern, full_text, re.IGNORECASE)
        addresses.extend([m.strip() for m in matches])
    
    # Also look for multi-line addresses containing street keywords
    lines = full_text.split('\n')
    for i, line in enumerate(lines):
        if any(kw in line.lower() for kw in ['ул.', 'пр.', 'пер.', 'просп.', 'г.', 'адрес:']):
            # Take this line and possibly next line
            addr = line.strip()
            if i + 1 < len(lines) and len(lines[i+1].strip()) < 100:
                addr += ', ' + lines[i+1].strip()
            addresses.append(addr)
    
    data["contacts"]["address"] = list(set(addresses))[:10]
    
    # Social links extraction
    social_domains = ['vk.com', 'ok.ru', 'telegram.me', 't.me', 'facebook.com', 
                      'instagram.com', 'whatsapp.com', 'youtube.com']
    social_links = []
    
    for link in soup.find_all('a', href=True):
        href = link['href'].lower()
        for domain in social_domains:
            if domain in href:
                social_links.append(link.get_text(strip=True) or href)
                break
    
    data["contacts"]["social"] = list(set(social_links))[:10]
    
    # Service extraction - look for service-related content
    service_keywords = ['услуга', 'service', 'product', 'товар', 'предложение', 
                        'offer', 'карточка', 'card', 'пакет', 'тариф']
    
    # Find headings that mention services
    headings = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
    for heading in headings:
        heading_text = heading.get_text(strip=True).lower()
        if any(kw in heading_text for kw in service_keywords):
            # Get the next sibling or parent section content
            next_elem = heading.find_next_sibling(['p', 'div', 'ul', 'ol'])
            if next_elem:
                desc = next_elem.get_text(strip=True)[:200]
                if desc:
                    data["services"].append({
                        "name": heading.get_text(strip=True),
                        "description": desc,
                        "text": heading.get_text(strip=True) + '\n' + desc
                    })
    
    # Also look for list items that might be services
    list_items = soup.find_all('li')
    for item in list_items[:30]:  # Limit to avoid noise
        text = item.get_text(strip=True)
        if len(text) > 10 and len(text) < 200:
            # Check if it contains price-like patterns or service keywords
            if (re.search(r'\d{3,}\s*[р₽]', text) or 
                any(kw in text.lower() for kw in service_keywords)):
                # Try to extract name and price
                data["services"].append({
                    "name": text[:50],
                    "description": "",
                    "text": text[:200]
                })
    
    # Deduplicate services
    seen = set()
    unique_services = []
    for s in data["services"]:
        key = f"{s['name']}|{s['description']}"
        if key not in seen:
            seen.add(key)
            unique_services.append(s)
    data["services"] = unique_services[:50]
    
    return data

def llm_extract(soup: BeautifulSoup, html: str, selectors: Dict[str, Any], score: float) -> Dict[str, Any]:
    """
    Extract data using OpenRouter API with fallback to heuristics.
    Returns data in internal format.
    """
    # Try OpenRouter API if key is set
    if OPENROUTER_API_KEY:
        api_result = call_openrouter(html)
        if api_result:
            # Convert API response to internal format
            internal_data = {
                "prices": [],
                "contacts": {"phones": [], "emails": [], "address": [], "social": []},
                "services": []
            }
            
            # Prices
            price_items = api_result.get("price", {}).get("items", [])
            internal_data["prices"] = [item.get("price", "") for item in price_items if item.get("price")]
            
            # Contacts
            contacts_api = api_result.get("contacts", {})
            internal_data["contacts"]["phones"] = contacts_api.get("phones", [])
            internal_data["contacts"]["emails"] = contacts_api.get("emails", [])
            address = contacts_api.get("address", "")
            internal_data["contacts"]["address"] = [address] if address else []
            internal_data["contacts"]["social"] = contacts_api.get("social", [])
            
            # Services
            services_api = api_result.get("services", [])
            internal_data["services"] = [
                {
                    "name": s.get("title", ""),
                    "description": s.get("desc", ""),
                    "text": f"{s.get('title', '')}\n{s.get('desc', '')}"
                }
                for s in services_api
            ]
            
            logger.info(f"LLM extraction returned: {len(internal_data['prices'])} prices, {len(internal_data['contacts']['phones'])} phones, {len(internal_data['services'])} services")
            return internal_data
    
    # Fallback to heuristic extraction
    logger.info("Falling back to heuristic extraction")
    return _heuristic_extract(soup, selectors, score)

def _find_common_selector_for_strings(soup: BeautifulSoup, strings: List[str]) -> Optional[str]:
    """Find a CSS selector that commonly contains the given strings."""
    if not strings:
        return None
    selector_counts = {}
    for s in strings:
        if not s:
            continue
        pattern = re.escape(s)
        try:
            matches = soup.find_all(string=re.compile(pattern))
        except re.error:
            continue
        for match in matches:
            parent = match.parent
            # Build a simple selector: tag with optional class and id
            selector = parent.name
            if parent.get('class'):
                cls = parent['class'][0]
                selector += '.' + cls
            if parent.get('id'):
                selector += '#' + parent['id']
            selector_counts[selector] = selector_counts.get(selector, 0) + 1
    if not selector_counts:
        return None
    # Sort by count descending
    sorted_selectors = sorted(selector_counts.items(), key=lambda x: x[1], reverse=True)
    # Return selector with count >= 2, or the top one if none have >=2
    for selector, count in sorted_selectors:
        if count >= 2:
            return selector
    return sorted_selectors[0][0] if sorted_selectors else None

def generate_selectors_from_data(soup: BeautifulSoup, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate CSS selectors based on extracted data.
    Returns a dict with keys: price, contact (dict), service.
    """
    selectors = {
        "price": None,
        "contact": {},
        "service": None
    }
    
    # Price selectors from price strings
    price_strings = data.get("prices", [])
    selectors["price"] = _find_common_selector_for_strings(soup, price_strings)
    
    # Contact selectors
    contacts = data.get("contacts", {})
    for key in ["phones", "emails", "social"]:
        if key in contacts:
            selectors["contact"][key] = _find_common_selector_for_strings(soup, contacts[key])
    # Address (single string or list)
    address = contacts.get("address", [])
    if isinstance(address, str):
        address = [address] if address else []
    selectors["contact"]["address"] = _find_common_selector_for_strings(soup, address)
    
    # Service selectors from service names
    services = data.get("services", [])
    service_names = [s.get("name", "") for s in services if s.get("name")]
    selectors["service"] = _find_common_selector_for_strings(soup, service_names)
    
    return selectors

def should_use_llm(score: float) -> bool:
    """Determine if LLM assistance is needed based on score."""
    return score < 0.75