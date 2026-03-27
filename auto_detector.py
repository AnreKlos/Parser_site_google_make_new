"""
Auto-detection of page structure using regex and heuristics.
"""
import re
from typing import Dict, List, Any, Optional
from bs4 import BeautifulSoup, Tag


def auto_detect(soup: BeautifulSoup) -> dict:
    """
    Automatically detect selectors and structure for a given page.
    Args:
        soup: BeautifulSoup object of the page
    Returns:
        dict with detected selectors, data preview, and confidence score
    """
    # Convert soup to string for regex scanning
    html_text = str(soup)
    text_content = soup.get_text()

    # 1. Detect price tables/blocks
    price_selectors = detect_price_selectors(soup, html_text)

    # 2. Detect contact information
    contact_selectors = detect_contact_selectors(soup, html_text)

    # 3. Detect service blocks
    service_selectors = detect_service_selectors(soup, text_content)

    # 4. Calculate score (0.0 to 1.0)
    score = calculate_score(price_selectors, contact_selectors, service_selectors)

    # 5. Build data preview (sample extracted data)
    data_preview = build_data_preview(soup, price_selectors, contact_selectors, service_selectors)

    # Combine price selectors (prefer table, fallback to list)
    price_selector = price_selectors.get("best_selector") or price_selectors.get("list_selector")
    
    return {
        "selectors": {
            "price": price_selector,
            "contact": {
                "phones": contact_selectors.get("phones", []),
                "emails": contact_selectors.get("emails", []),
                "address": contact_selectors.get("address", []),
                "social": contact_selectors.get("social", [])
            },
            "service": service_selectors.get("best_selector")
        },
        "data_preview": data_preview,
        "score": score
    }


def detect_price_selectors(soup: BeautifulSoup, html: str) -> Dict[str, Any]:
    """Detect selectors for price tables/lists."""
    # Keywords indicating price sections (Russian/English)
    price_keywords = ['цена', 'стоимость', 'price', 'прайс', 'цены', 'тариф', 'расценки']

    # Regex patterns for prices: numbers with currency symbols or spaces
    price_pattern = re.compile(r'(\d{3,}[\s,]*[р₽]?|\d{1,3}[.,]\d{2}[р₽]?|\d{1,3}\s+\d{3})', re.IGNORECASE)

    # Find tables with price-like content
    tables = soup.find_all('table')
    best_table = None
    best_score = 0

    for table in tables:
        table_text = table.get_text()
        # Count price keywords and price patterns
        keyword_count = sum(1 for kw in price_keywords if kw.lower() in table_text.lower())
        price_matches = len(price_pattern.findall(table_text))

        # Also check nearby heading
        prev = table.find_previous(['h1', 'h2', 'h3', 'h4', 'p'])
        heading_score = 0
        if prev:
            heading_text = prev.get_text().lower()
            heading_score = sum(1 for kw in price_keywords if kw.lower() in heading_text)

        score = keyword_count * 2 + price_matches + heading_score * 3
        if score > best_score:
            best_score = score
            best_table = table

    # Also look for divs with price patterns
    price_divs = []
    for elem in soup.find_all(['div', 'ul', 'ol', 'section']):
        if elem.get('class') or elem.get('id'):
            class_str = ' '.join(elem.get('class', []) + [elem.get('id', '')]).lower()
            if any(kw in class_str for kw in ['price', 'cost', 'tarif', 'ras', 'прайс']):
                price_divs.append(elem)

    # Determine best selector
    best_selector = None
    list_selector = None

    if best_table and best_table.name == 'table':
        # Generate a unique selector for the table
        best_selector = generate_selector(best_table)
    elif price_divs:
        best_selector = generate_selector(price_divs[0])

    # Find a list-based selector (ul/ol with prices)
    list_candidates = soup.find_all(['ul', 'ol'])
    for lst in list_candidates:
        text = lst.get_text()
        if price_pattern.search(text):
            list_selector = generate_selector(lst)
            break

    return {
        "best_selector": best_selector,
        "list_selector": list_selector,
        "has_prices": best_score > 0 or bool(list_selector)
    }


def detect_contact_selectors(soup: BeautifulSoup, html: str) -> Dict[str, List[str]]:
    """Detect selectors for phones, emails, address, social media."""
    selectors = {
        "phones": [],
        "emails": [],
        "address": [],
        "social": []
    }

    # Phone regex (Russian/international formats)
    phone_regex = re.compile(r'(\+7|8)[\s\-]?\(?[\d]{3}\)?[\s\-]?[\d]{2,3}[\s\-]?[\d]{2}[\s\-]?[\d]{2}|[\d]{3,4}[\s\-]?[\d]{3}[\s\-]?[\d]{2}[\s\-]?[\d]{2}')

    # Email regex
    email_regex = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

    # Social domains
    social_domains = ['vk.com', 'ok.ru', 'telegram.me', 't.me', 'facebook.com', 'instagram.com']

    # Search in footer and contact blocks
    search_areas = soup.find_all(['footer', 'div', 'section', 'aside'], class_=re.compile(r'contact|footer|contacts|social|map', re.IGNORECASE))
    if not search_areas:
        search_areas = [soup]  # fallback to whole page

    for area in search_areas:
        # Phones: from tel: links and text
        tel_links = area.find_all('a', href=re.compile(r'^tel:'))
        for link in tel_links:
            selector = generate_selector(link)
            if selector and selector not in selectors["phones"]:
                selectors["phones"].append(selector)

        # Scan text for phone numbers
        text = area.get_text()
        for match in phone_regex.findall(text):
            # Find the element containing this match
            # (simplified: we'll just note that we found a phone)
            pass
        if phone_regex.search(text) and len(selectors["phones"]) == 0:
            # Add a generic selector for the area if we found phones in text
            selector = generate_selector(area)
            if selector:
                selectors["phones"].append(selector)

        # Emails: from mailto: links and text
        mailto_links = area.find_all('a', href=re.compile(r'^mailto:'))
        for link in mailto_links:
            selector = generate_selector(link)
            if selector and selector not in selectors["emails"]:
                selectors["emails"].append(selector)

        if email_regex.search(text) and len(selectors["emails"]) == 0:
            selector = generate_selector(area)
            if selector:
                selectors["emails"].append(selector)

        # Social links
        for link in area.find_all('a', href=True):
            href = link['href'].lower()
            if any(domain in href for domain in social_domains):
                selector = generate_selector(link)
                if selector and selector not in selectors["social"]:
                    selectors["social"].append(selector)

        # Address: look for patterns like ул., пр., пер., г., etc.
        address_keywords = ['ул\\.', 'пр\\.', 'пер\\.', 'просп\\.', 'г\\.', 'город', 'адрес', 'address']
        if any(re.search(kw, text, re.IGNORECASE) for kw in address_keywords):
            selector = generate_selector(area)
            if selector and selector not in selectors["address"]:
                selectors["address"].append(selector)

    return selectors


def detect_service_selectors(soup: BeautifulSoup, text: str) -> Dict[str, Any]:
    """Detect selectors for service blocks (cards or lists)."""
    # Keywords indicating services
    service_keywords = ['услуга', 'service', 'product', 'товар', 'предложение', 'offer', 'карточка', 'card']

    # Find divs with classes containing service-related keywords
    candidate_divs = soup.find_all('div', class_=re.compile(r'|'.join(service_keywords), re.IGNORECASE))

    # Also look for sections with headings followed by lists or grids
    headings = soup.find_all(['h2', 'h3'])
    for heading in headings:
        heading_text = heading.get_text().lower()
        if any(kw in heading_text for kw in service_keywords):
            # Check next sibling for list or div grid
            next_elem = heading.find_next_sibling(['ul', 'ol', 'div'])
            if next_elem:
                candidate_divs.append(next_elem)

    # Evaluate candidates by number of child items
    best_candidate = None
    best_count = 0
    for div in candidate_divs:
        # Count list items or child divs/cards
        count = len(div.find_all(['li', 'div'], class_=re.compile(r'item|card|product')))
        if count == 0:
            count = len(div.find_all(['li']))
        if count > best_count:
            best_count = count
            best_candidate = div

    best_selector = generate_selector(best_candidate) if best_candidate else None

    return {
        "best_selector": best_selector,
        "candidate_count": len(candidate_divs),
        "has_services": best_candidate is not None
    }


def calculate_score(price: Dict, contact: Dict, service: Dict) -> float:
    """Calculate confidence score (0.0 to 1.0)."""
    score = 0.0

    # Price: up to 0.4
    if price.get("has_prices"):
        score += 0.4

    # Contacts: up to 0.3 (phones 0.15, emails 0.1, address/social 0.05)
    if contact.get("phones"):
        score += 0.15
    if contact.get("emails"):
        score += 0.1
    if contact.get("address") or contact.get("social"):
        score += 0.05

    # Services: up to 0.3
    if service.get("has_services"):
        score += 0.3

    return min(score, 1.0)


def build_data_preview(soup: BeautifulSoup, price: Dict, contact: Dict, service: Dict) -> Dict[str, Any]:
    """Build a small sample of extracted data for preview."""
    preview = {}

    # Sample prices (first few matches)
    price_regex = re.compile(r'(\d{3,}[\s,]*[р₽]?|\d{1,3}[.,]\d{2}[р₽]?)')
    price_matches = price_regex.findall(soup.get_text())[:5]
    preview["sample_prices"] = price_matches

    # Sample phones and emails (from detected selectors)
    preview["phones_count"] = len(contact.get("phones", []))
    preview["emails_count"] = len(contact.get("emails", []))
    preview["social_links_count"] = len(contact.get("social", []))

    # Services count
    preview["services_candidates"] = service.get("candidate_count", 0)

    return preview


def generate_selector(element: Tag) -> Optional[str]:
    """Generate a CSS selector for a given element."""
    if not element:
        return None

    # Prefer ID if exists
    if element.get('id'):
        return f"#{element['id']}"

    # Prefer unique class if only one class on element
    if element.get('class'):
        classes = element['class']
        if len(classes) == 1:
            return f".{classes[0]}"
        # If multiple classes, use the most specific one (non-generic)
        for cls in classes:
            if cls not in ['container', 'wrapper', 'row', 'col', 'block', 'item']:
                return f".{cls}"
        # Fallback to first class
        return f".{classes[0]}"

    # Fallback to tag name with nth-child (not ideal but workable)
    parent = element.parent
    if parent:
        siblings = parent.find_all(element.name, recursive=False)
        if len(siblings) == 1:
            return element.name
        else:
            index = siblings.index(element) + 1
            return f"{element.name}:nth-of-type({index})"

    return element.name