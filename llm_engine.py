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

from dotenv import load_dotenv
load_dotenv()

# Prompt template for services niche (current)
PROMPT_SERVICES = """
Ты — парсер сайтов услуг (клиники, салоны, сервисные компании и любые сайты, где есть услуги/товары, цены и контакты).

Тебе даётся HTML страницы (обрезанный фрагмент):

[HTML НАЧАЛО]
{html}
[HTML КОНЕЦ]

Твоя задача — проанализировать структуру и извлечь данные. Найди и верни данные в следующем JSON-формате:

{
  "domain": "",  // домен из URL (не нужно извлекать, будет заполнен автоматически)
  "business_name": "",  // название компании/организации
  "tagline": "",  // краткий слоган или описание
  "about": "",  // информация о компании (о нас)
  "services": [
    {
      "name": "",  // название услуги
      "desc": "",  // описание услуги
      "price_from": ""  // минимальная цена или диапазон, если указано
    }
  ],
  "contacts": {
    "phones": [],  // список телефонов
    "emails": [],  // список email-адресов
    "address": "",  // почтовый адрес
    "work_time": "",  // время работы
    "social": []  // ссылки на соцсети/мессенджеры
  },
  "benefits": [],  // преимущества/выгоды (если есть)
  "testimonials": []  // отзывы клиентов (если есть)
}

Очень важно:
- Ответ должен быть ТОЛЬКО в формате JSON, без пояснений, комментариев, текстов до или после.
- Если какое-то поле не найдено — оставь пустым (строку пустой, список пустым), но не убирай ключ.
- Старайся извлечь как можно больше информации, особенно из видимых текстов.
"""

# Prompt template for auto_dealer niche
PROMPT_AUTO_DEALER = """
Ты — структурировщик данных для сайта автодилера.

ВХОД:
Тебе даётся НЕ весь сайт, а HTML-ФРАГМЕНТЫ КАРТОЧЕК МОДЕЛЕЙ.
Каждый фрагмент — это, как правило, тег <li class="menu-models__item"> или похожий контейнер, внутри которого есть:
- название модели (внутри тега a, в атрибуте title или в div с классом, содержащим 'title' или 'name'),
- цена (div с классом, содержащим 'price', текст вида 'от 3 299 000 ₽'),
- иногда бейджи ('НОВЫЙ', 'СПЕЦПРЕДЛОЖЕНИЕ', 'ВЫГОДА' и т.п.).

Твоя задача — ПО КАЖДОМУ ТАКОМУ КОНТЕЙНЕРУ сформировать ОДНУ запись в массиве models[].

ИТОГОВАЯ СТРУКТУРА JSON (строго такая):

{
  "domain": "строка, домен сайта без протокола",
  "business_name": "короткое название бренда/дилера (например, 'HAVAL Авторитет-Авто+')",
  "tagline": "одна фраза-оффер для главной (например, 'Новый Haval с выгодой до 450 000 ₽')",
  "dealership_info": {
    "address": "одна строка, основной адрес салона (город + улица + дом)",
    "phones": ["список телефонов как на сайте"],
    "work_time": "часы работы (если найдёшь, иначе пустая строка)"
  },
  "models": [
    {
      "name": "название модели, например 'Haval DARGO X'",
      "price_from": "строка вида 'от 3 299 000 ₽' или '3 299 000 ₽'",
      "short_specs": "1–2 коротких тезиса: тип кузова, привод, коробка, если есть (иначе пустая строка)",
      "badge": "короткая метка: 'новинка', 'выгода', 'спецпредложение', 'хит'; если ничего нет — пустая строка"
    }
  ],
  "special_offers": [
    "список коротких текстов про акции: 'Выгода до 450 000 ₽ по программе трейд-ин', 'Второй автомобиль в семью' и т.п."
  ],
  "niche": "auto_dealer"
}

ОСОБЫЕ УКАЗАНИЯ ДЛЯ КАТАЛОГОВ ТИПА HAVAL / GAC:

1. Считай, что КАЖДЫЙ <li class="menu-models__item"> или похожий блок = ОДНА модель в массиве models[].
   - Из этого блока:
     - name: бери из:
       - атрибута title у <a> (например, title="DARGO X"),
       - или текста в div с классом, содержащим 'item-title', 'model', 'name'.
       - Если есть и бренд, и модель (например, логотип HAVAL и текст 'DARGO X'), собирай это как 'Haval DARGO X'.
     - price_from: ищи текст внутри элементов, где класс содержит 'price' (например, 'menu-models__item-price').
       - Приводи к виду 'от NNNN ₽' или 'NNNN ₽', убирая лишние пробелы и мусор.
     - short_specs: если в этой же карточке есть краткое описание комплектации (4x4, AT, Comfort и т.п.), собери это в одну короткую строку. Если нет — оставь пустую строку.
     - badge: если внутри карточки есть слова 'НОВЫЙ', 'СПЕЦПРЕДЛОЖЕНИЕ', 'ВЫГОДА', 'АКЦИЯ', 'LIMITED' — ставь 'новинка' или 'выгода'. Если ничего такого нет — пустая строка.

2. НЕ создавай models из кредитных disclaimers или списков условий.
   - Если в блоке нет признаков модели (нет названия машины, только текст про кредит/банк) — игнорируй его.

3. business_name и dealership_info:
   - По всему фрагменту HTML постарайся найти:
     - указание официального дилера ('Авторитет-Авто+', 'официальный дилер HAVAL'),
     - основной адрес (город + улица + дом),
     - телефоны.
   - Если информации мало — лучше оставить часть полей пустыми, чем придумывать.

4. special_offers:
   - Ищи короткие фразы про выгоды и программы:
     - 'Выгода до 450 000 ₽',
     - 'Специальные условия трейд-ин',
     - 'Кредит от 0,01%'.
   - В массив special_offers пиши короткие человеческие формулировки, без длинных юридических абзацев.

ОГРАНИЧЕНИЯ:
- ВСЕГДА возвращай КОРРЕКТНЫЙ JSON строго по указанной схеме.
- НЕ добавляй другие поля.
- В models должно быть от 3 до 12 элементов, если столько карточек есть во входном HTML.
- Если для отдельной модели не удалось найти цену — оставь "price_from": "" (но модель всё равно добавь, если есть название).
- Если не удалось найти ни одной модели — верни пустой массив "models": [].

ВХОДНОЙ HTML для анализа:
{html}

Верни ТОЛЬКО JSON, без пояснений, комментариев и Markdown.
"""

# OpenRouter configuration
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "gpt-4o-mini"  # or another model

def call_openrouter(html: str, prompt: str) -> Optional[Dict[str, Any]]:
    """Call OpenRouter API with the prompt and HTML."""
    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set, skipping API call")
        return None
    
    # Truncate HTML to avoid token limits (keep within ~20k chars)
    html_truncated = html[:20000]
    full_prompt = prompt.replace("{html}", html_truncated)
    
    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": full_prompt}],
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

def detect_niche(html: str) -> str:
    """
    Detect the niche of a website using heuristics and/or LLM.
    Returns either "services" or "auto_dealer".
    """
    # First, try heuristic detection based on keywords
    html_lower = html.lower()
    
    # Auto dealer keywords - expanded set
    auto_keywords = [
        # Core auto terms
        'автосалон', 'автомобиль', 'автоцентр', 'дилер', 'официальный дилер',
        'продажа автомобилей', 'продажа машин', 'купить авто', 'купить машину',
        'новый автомобиль', 'новый авто', 'б/у автомобиль', 'б/у авто', 'с пробегом',
        'автокредит', 'автокредит', 'trade-in', 'трейд-ин',
        'test drive', 'тест-драйв', 'каталог автомобилей', 'каталог машин',
        'комплектации', 'модельный ряд', 'год выпуска', 'пробег',
        # Car brands
        'toyota', 'bmw', 'mercedes', 'audi', 'lexus', 'nissan', 'honda',
        'mitsubishi', 'ford', 'chevrolet', 'volkswagen', 'skoda', 'renault',
        'kia', 'hyundai', 'geely', 'changan', 'haval', 'lada', 'uaz', 'gaz',
        # Auto services
        'сервис', 'сервисный центр', 'запчасти', 'шиномонтаж', 'шины',
        'диагностика', 'кузовной ремонт', 'сто', 'автосервис',
        # Locations and addresses
        'мск', 'спб', 'москва', 'санкт-петербург', 'адрес:', 'ул. ', 'пр. ',
        'просп.', 'город', 'городской',
        # Price indicators specific to auto
        'от', 'руб.', 'руб', '₽', 'миллионов', 'млн', 'тыс', 'лakh'
    ]
    
    # Count auto keywords
    auto_score = sum(1 for kw in auto_keywords if kw in html_lower)
    
    # Additional heuristic: look for price patterns typical for auto (e.g., "от 1.5 млн")
    price_pattern = r'от\s+\d+[\.,]\d+\s*(млн|миллион|lakh|тыс)'
    if re.search(price_pattern, html_lower):
        auto_score += 2
        logger.debug(f"Found auto-style price pattern, bonus +2")
    
    # If moderate auto signal, return auto_dealer (lowered from 3 to 2)
    if auto_score >= 2:
        logger.info(f"Auto dealer detected by heuristics (score: {auto_score})")
        return "auto_dealer"
    
    # If we have OpenRouter API key, use LLM for more accurate detection
    if OPENROUTER_API_KEY:
        prompt = """
Проанализируй HTML страницу и определи, к какой нише она относится.
Верни ТОЛЬКО одно слово: "services" или "auto_dealer".

Критерии:
- "auto_dealer": сайты автосалонов, продажа автомобилей (новых/б/у), сервисы, запчасти. Признаки: 
  * Названия машин (Toyota Camry, BMW X5 и т.п.)
  * Цены в миллионах/лакх (1.5 млн руб, 25 лакх)
  * Слова: "каталог", "модель", "комплектация", "год выпуска", "пробег", "дилер", "официальный"
  * Кнопки "Купить", "Заказать", "Рассчитать кредит", "Trade-in"
  * Списки автомобилей с фото и характеристиками
- "services": все остальные сайты услуг (клиники, салоны красоты, сервисные компании, ремонт, образование и т.д.)

Примеры:
- Страница с таблицей "Toyota Camry 2024 - 3.5 млн руб" -> auto_dealer
- Страница "Услуги: маникюр, стрижка, массаж" -> services
- Страница "Ремонт холодильников на дому" -> services
- Страница "Шиномонтаж и развал-схождение" -> services (даже если есть слово "шины", это не продажа машин)

HTML:
{html}
        """.strip()
        
        try:
            result = call_openrouter(html[:8000], prompt)
            if result and isinstance(result, dict):
                # Try to extract the niche from the response
                content = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip().lower()
                if 'auto_dealer' in content:
                    logger.info("Auto dealer detected by LLM")
                    return "auto_dealer"
                elif 'services' in content:
                    logger.info("Services detected by LLM")
                    return "services"
        except Exception as e:
            logger.error(f"LLM niche detection failed: {e}")
    
    # Default to services
    logger.info("Defaulting to services niche")
    return "services"

def build_models_html_fragment(soup: BeautifulSoup, selectors: Dict[str, Any]) -> str:
    """
    For auto_dealer niche, extract HTML fragments around model cards.
    Uses price selector to locate card elements and climbs up DOM to capture full cards.
    Returns concatenated HTML of all cards, or full page if not found/not specified.
    """
    price_sel = selectors.get("price")
    if not price_sel:
        return str(soup)
    
    elements = soup.select(price_sel)
    if not elements:
        return str(soup)
    
    cards_html = []
    for el in elements:
        current = el
        found_container = False
        
        # Smart container detection: climb up looking for semantic containers
        while current.parent and current.parent.name not in ['body', 'html']:
            parent = current.parent
            classes = parent.get('class', [])
            class_str = ' '.join(classes).lower()
            
            # Check if parent is a semantic container (list item or card/model container)
            if parent.name == 'li' or 'item' in class_str or 'card' in class_str or 'model' in class_str:
                cards_html.append(str(parent))
                found_container = True
                break
            
            current = parent
        
        # Fallback: if no semantic container found, climb 3 levels as before
        if not found_container:
            card = el
            for _ in range(3):
                if card.parent:
                    card = card.parent
            cards_html.append(str(card))
    
    fragment = "\n\n".join(cards_html)
    return fragment if fragment.strip() else str(soup)

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

def unified_to_old(unified: dict) -> dict:
    """Convert unified structure to old format for markdown compatibility."""
    old = {
        "prices": [],
        "contacts": {"phones": [], "emails": [], "address": [], "social": []},
        "services": []
    }
    # Prices: collect price_from from services
    for s in unified.get("services", []):
        price = s.get("price_from", "").strip()
        if price and price not in old["prices"]:
            old["prices"].append(price)
    # Services
    for s in unified.get("services", []):
        name = s.get("name", "").strip()
        desc = s.get("desc", "").strip()
        # Build text field
        text_parts = []
        if name:
            text_parts.append(name)
        if desc:
            text_parts.append(desc)
        price = s.get("price_from", "").strip()
        if price:
            text_parts.append(f"Цена: {price}")
        text = "\n".join(text_parts)[:200]
        old["services"].append({
            "name": name,
            "description": desc,
            "text": text
        })
    # Contacts
    uc = unified.get("contacts", {})
    old["contacts"]["phones"] = uc.get("phones", [])[:]
    old["contacts"]["emails"] = uc.get("emails", [])[:]
    # address: unified is string, old expects list
    addr = uc.get("address", "")
    if addr:
        if isinstance(addr, list):
            old["contacts"]["address"] = addr[:]
        else:
            old["contacts"]["address"] = [addr]
    else:
        old["contacts"]["address"] = []
    old["contacts"]["social"] = uc.get("social", [])[:]
    return old

def llm_extract(soup: BeautifulSoup, html: str, selectors: Dict[str, Any], score: float) -> dict:
    """
    Extract data using OpenRouter API with fallback to heuristics.
    Detects niche first, then uses appropriate prompt.
    Returns a dict with keys 'data' (old format) and 'normalized' (unified or None).
    """
    # Detect niche
    niche = detect_niche(html)
    logger.info(f"Detected niche: {niche}")
    
    # Choose appropriate prompt and HTML for LLM
    if niche == "auto_dealer":
        prompt = PROMPT_AUTO_DEALER
        html_for_llm = build_models_html_fragment(soup, selectors)
        
        # Extract header and footer for additional context
        header = soup.find('header')
        footer = soup.find('footer')
        
        if header:
            header_text = str(header)[:3000]  # limit length
            html_for_llm += f"\n\n<!-- HEADER INFO -->\n\n{header_text}"
        
        if footer:
            footer_text = str(footer)[:3000]  # limit length
            html_for_llm += f"\n\n<!-- FOOTER INFO -->\n\n{footer_text}"
        
        # Debug: save HTML fragment for auto_dealer
        with open("debug_llm_input.html", "w", encoding="utf-8") as f:
            f.write(html_for_llm)
    else:
        prompt = PROMPT_SERVICES
        html_for_llm = html
    
    # Try OpenRouter API if key is set
    if OPENROUTER_API_KEY:
        api_result = call_openrouter(html_for_llm, prompt)
        if api_result:
            # Add niche to the result
            api_result["niche"] = niche
            
            # For auto_dealer, return normalized as-is (no conversion to old format)
            if niche == "auto_dealer":
                return {
                    "data": _heuristic_extract(soup, selectors, score),  # fallback old format
                    "normalized": api_result
                }
            else:
                # For services, convert to old format
                old_data = unified_to_old(api_result)
                return {
                    "data": old_data,
                    "normalized": api_result
                }
    
    # Fallback to heuristic extraction
    logger.info("Falling back to heuristic extraction")
    heuristic_data = _heuristic_extract(soup, selectors, score)
    return {
        "data": heuristic_data,
        "normalized": None,
        "niche": niche
    }

def _find_common_selector_for_strings(soup: BeautifulSoup, strings: List[str]) -> Optional[str]:
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