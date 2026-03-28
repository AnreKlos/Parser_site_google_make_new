"""
Core scraping functions.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from auto_detector import auto_detect
from config_manager import load_config, save_config
from llm_engine import llm_extract, generate_selectors_from_data

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fetch_html_with_playwright(url: str, timeout_ms: int = 30000) -> str:
    """
    Fetch HTML using Playwright to handle JavaScript-rendered pages.
    
    Args:
        url: Target URL
        timeout_ms: Timeout in milliseconds (default: 30000)
    
    Returns:
        HTML content as string
    
    Raises:
        Exception: If fetching fails or times out
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()
            
            # Navigate to URL with timeout
            page.goto(url, timeout=timeout_ms, wait_until='domcontentloaded')
            
            # Wait for JS to render
            page.wait_for_timeout(3000)
            
            # Get final HTML
            html = page.content()
            
            browser.close()
            return html
    except PlaywrightTimeoutError:
        logger.error(f"Playwright timeout for {url}")
        raise
    except Exception as e:
        logger.error(f"Playwright error for {url}: {e}")
        raise

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# Regex patterns for extracting specific data
PRICE_PATTERN = re.compile(r'(\d{3,}[\s,]*[р₽]?|\d{1,3}[.,]\d{2}[р₽]?|\d{1,3}\s+\d{3})')
PHONE_PATTERN = re.compile(r'(\+7|8)[\s\-]?\(?[\d]{3}\)?[\s\-]?[\d]{2,3}[\s\-]?[\d]{2}[\s\-]?[\d]{2}|[\d]{3,4}[\s\-]?[\d]{3}[\s\-]?[\d]{2}[\s\-]?[\d]{2}')
EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')


def run_parse(
    url: str,
    force_llm: bool = False,
    auto_detect_only: bool = False,
    output_path: Optional[str] = None,
    save_json: bool = True,
    config: Optional[Dict] = None,
    use_js: bool = False
) -> dict:
    """
    Main parsing function.
    Pipeline:
    1. Load HTML from URL (using requests or Playwright based on use_js)
    2. Try to load config by domain (unless provided via config param)
    3. If no config: auto-detect selectors, check score
    4. If score >= 0.75 and not force_llm: use selector-based extraction
    5. If score < 0.75 or force_llm: use LLM extraction, then generate selectors from LLM data
    6. Save config with selectors and score (from auto-detect or from LLM-generated)
    7. Build markdown and save to data/ or custom output_path
    
    Args:
        url: Target URL to parse
        force_llm: Force using LLM extraction even if auto-detect score is high
        auto_detect_only: Only run auto-detection, return selectors/preview without saving files
        output_path: Custom path to save output files (if None, uses DATA_DIR/domain.md)
        save_json: Whether to also save JSON file alongside markdown
        config: Pre-loaded config dict to use (if None, will load/create for domain)
        use_js: Use Playwright to render JavaScript (default: False)
    
    Returns:
        dict with result info (output_path, domain, score, data, used_llm, selectors, data_preview)
    """
    # Extract domain from URL
    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain = parsed.netloc
    
    logger.info(f"Starting parse for {url} (domain: {domain})")
    if use_js:
        logger.info("Using Playwright for JavaScript rendering")
    
    # Step 1: Load HTML
    try:
        if use_js:
            html = fetch_html_with_playwright(url, timeout_ms=30000)
        else:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            html = response.text
    except Exception as e:
        logger.error(f"Failed to fetch URL: {e}")
        raise
    
    soup = BeautifulSoup(html, 'html.parser')
    
    # Step 2: Determine config source
    if config is None:
        # Try load existing config
        config = load_config(domain)
    else:
        logger.info(f"Using provided config for {domain}")
    
    score = None
    selectors = None
    used_llm = False
    llm_result = None  # To store LLM extraction result if used
    data = {"prices": [], "contacts": {}, "services": []}  # Initialize to avoid unbound error
    data_preview = {}
    
    if not config:
        logger.info(f"No config found for {domain}, running auto-detection")
        # Step 3: Auto-detect
        detection_result = auto_detect(soup)
        selectors = detection_result["selectors"]
        score = detection_result["score"]
        data_preview = detection_result["data_preview"]
        
        logger.info(f"Auto-detection score: {score:.2f}")
        
        # Check if only auto-detection is requested
        if auto_detect_only:
            logger.info("Auto-detect only mode: returning selectors and preview without saving")
            return {
                "selectors": selectors,
                "score": score,
                "data_preview": data_preview,
                "domain": domain,
                "used_llm": False
            }
        
        # Step 4: Decide whether to use LLM
        # FORCED LLM FOR AUTO DEALERS TEST
        if domain in ["gac.ru", "32status.ru"] or use_js:
            force_llm = True
            logger.info(f"Forcing LLM extraction for domain {domain} (or use_js is True)")

        if score >= 0.75 and not force_llm:
            logger.info(f"Config will be saved (score: {score:.2f}) and selector-based extraction will be used")
            config_to_save = {
                "selectors": selectors,
                "score": score,
                "data_preview": data_preview
            }
            save_config(domain, config_to_save)
            config = config_to_save
        else:
            # Use LLM extraction
            logger.warning(f"Using LLM extraction (score: {score:.2f}, force_llm: {force_llm})")
            llm_result = llm_extract(soup, html, selectors, score if score is not None else 0.0)
            data = llm_result["data"]
            used_llm = True
            
            # Generate selectors from LLM-extracted data
            selectors = generate_selectors_from_data(soup, data)
            score = 1.0  # LLM extraction is considered high confidence
            
            # Save config with LLM-generated selectors
            config_to_save = {
                "selectors": selectors,
                "score": score,
                "data_preview": {}  # Could add a preview if needed
            }
            save_config(domain, config_to_save)
            logger.info(f"Config saved for {domain} (from LLM extraction)")
            config = config_to_save
    else:
        # Config exists, use its selectors
        selectors = config.get("selectors", {})
        score = config.get("score")
        logger.info(f"Using existing config for {domain} (score: {score})")
    
    # Step 5/6: Extract data if not already extracted (LLM case)
    if not used_llm:
        # Use selector-based extraction
        logger.info("Extracting data using detected/loaded selectors")
        data = extract_data(soup, selectors)
        # Normalize data to unified structure (for non-LLM extraction)
        normalized = normalize_data(data, domain)
    else:
        # LLM extraction: use normalized data from LLM if available
        if llm_result and llm_result.get("normalized"):
            normalized = llm_result["normalized"]

            # Ensure niche is set correctly
            niche = normalized.get("niche") or llm_result.get("niche") or "services"
            normalized["niche"] = niche

            # If this is an auto_dealer, make sure minimal required fields exist
            if niche == "auto_dealer":
                normalized.setdefault("domain", domain)
                normalized.setdefault("business_name", "")
                normalized.setdefault("tagline", "")
                normalized.setdefault("dealership_info", {
                    "address": "",
                    "phones": [],
                    "work_time": ""
                })
                normalized.setdefault("models", [])
                normalized.setdefault("special_offers", [])
        else:
            # LLM fallback (heuristic) - normalize the heuristic data with detected niche
            niche = llm_result.get("niche", "services") if llm_result else "services"
            normalized = normalize_data(data, domain, niche=niche)
    
    # Step 7: Build markdown and save
    markdown = build_markdown(data, domain, score)
    
    # Determine output path
    if output_path:
        # Use provided path
        out_path = Path(output_path)
        # Ensure parent directory exists
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Save markdown
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(markdown)
        logger.info(f"Markdown saved to {out_path}")
        
        # Optionally save JSON
        if save_json:
            json_path = out_path.with_suffix('.json')
            json_data = {
                "domain": domain,
                "score": score,
                "data": data,
                "selectors": selectors,
                "used_llm": used_llm,
                "normalized": normalized
            }
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)
            logger.info(f"JSON saved to {json_path}")
    else:
        # Use default DATA_DIR
        out_path = DATA_DIR / f"{domain}.md"
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(markdown)
        logger.info(f"Markdown saved to {out_path}")
        
        if save_json:
            json_path = DATA_DIR / f"{domain}.json"
            json_data = {
                "domain": domain,
                "score": score,
                "data": data,
                "selectors": selectors,
                "used_llm": used_llm,
                "normalized": normalized
            }
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)
            logger.info(f"JSON saved to {json_path}")
    
    return {
        "output_path": str(out_path),
        "domain": domain,
        "score": score,
        "data": data,
        "used_llm": used_llm,
        "selectors": selectors,
        "data_preview": data_preview,
        "normalized": normalized
    }


def extract_data(soup: BeautifulSoup, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract structured data from soup using provided selectors.
    
    Args:
        soup: BeautifulSoup object
        selectors: Dict with selector definitions (price, contact, service)
    
    Returns:
        dict with extracted data
    """
    data = {
        "prices": [],
        "contacts": {},
        "services": []
    }
    
    # Extract prices
    price_selector = selectors.get("price")
    if price_selector:
        # If selector points to a table, extract cells; otherwise extract text and filter with regex
        elements = soup.select(price_selector)
        for elem in elements:
            # Strategy 1: If it's a table, extract cell texts
            if elem.name == 'table':
                cell_texts = [cell.get_text(strip=True) for cell in elem.find_all(['td', 'th'])]
                for text in cell_texts:
                    # Extract price-like substrings
                    matches = PRICE_PATTERN.findall(text)
                    data["prices"].extend(matches)
            else:
                # Strategy 2: Get element text and extract price patterns
                text = elem.get_text(strip=True)
                if text:
                    matches = PRICE_PATTERN.findall(text)
                    if matches:
                        data["prices"].extend(matches)
                    else:
                        data["prices"].append(text)
    
    # Extract contacts - handle lists of selectors
    contact_selectors = selectors.get("contact", {})
    for contact_type, selector_val in contact_selectors.items():
        if selector_val:
            # Normalize to list
            if isinstance(selector_val, str):
                selector_list = [selector_val]
            elif isinstance(selector_val, list):
                selector_list = selector_val
            else:
                continue
            
            all_values = []
            for selector in selector_list:
                try:
                    elements = soup.select(selector)
                    for elem in elements:
                        text = elem.get_text(strip=True)
                        if not text:
                            continue
                        
                        # Extract specific data based on contact type
                        if contact_type == "phones":
                            matches = PHONE_PATTERN.findall(text)
                            if matches:
                                all_values.extend(matches)
                            else:
                                # Fallback: keep full text if no phone pattern matched
                                all_values.append(text)
                        elif contact_type == "emails":
                            matches = EMAIL_PATTERN.findall(text)
                            if matches:
                                all_values.extend(matches)
                            else:
                                all_values.append(text)
                        elif contact_type in ["address", "social"]:
                            # For address and social, keep full text or try to extract
                            all_values.append(text)
                except Exception as e:
                    logger.warning(f"Failed to extract {contact_type} with selector '{selector}': {e}")
            
            if all_values:
                data["contacts"][contact_type] = list(set(all_values))  # deduplicate
    
    # Extract services
    service_selectors = selectors.get("service")
    if service_selectors:
        # Could be a single selector or list
        if isinstance(service_selectors, str):
            service_selectors = [service_selectors]
        
        for selector in service_selectors:
            service_blocks = soup.select(selector)
            for block in service_blocks:
                # Try to find individual service items within the block
                # Look for child elements that look like service cards/items
                item_candidates = block.find_all(['div', 'li', 'article'], class_=re.compile(r'service|card|item|product', re.IGNORECASE))
                
                if item_candidates:
                    # Multiple items found within block
                    for item in item_candidates:
                        service_name = ""
                        service_desc = ""
                        h_tag = item.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                        if h_tag:
                            service_name = h_tag.get_text(strip=True)
                        p_tag = item.find('p')
                        if p_tag:
                            service_desc = p_tag.get_text(strip=True)
                        
                        if service_name or service_desc:
                            data["services"].append({
                                "name": service_name,
                                "description": service_desc,
                                "text": item.get_text(strip=True)[:200]
                            })
                else:
                    # Block itself is a single service or container with heading + content
                    service_name = ""
                    service_desc = ""
                    h_tag = block.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                    if h_tag:
                        service_name = h_tag.get_text(strip=True)
                    p_tag = block.find('p')
                    if p_tag:
                        service_desc = p_tag.get_text(strip=True)
                    
                    if service_name or service_desc:
                        data["services"].append({
                            "name": service_name,
                            "description": service_desc,
                            "text": block.get_text(strip=True)[:200]
                        })
    
    return data


def build_markdown(data: Dict[str, Any], domain: str, score: Optional[float] = None) -> str:
    """
    Convert parsed data to Markdown format.
    
    Args:
        data: Parsed data dictionary
        domain: Source domain
        score: Auto-detection confidence score (optional)
    
    Returns:
        Markdown string
    """
    lines = []
    lines.append(f"# Data from {domain}")
    lines.append("")
    
    if score is not None:
        lines.append(f"**Auto-detection score:** {score:.2f}")
        lines.append("")
    
    # Prices section
    if data.get("prices"):
        lines.append("## Prices")
        # Deduplicate and clean
        seen = set()
        for price in data["prices"]:
            price_clean = price.strip()
            if price_clean and price_clean not in seen:
                seen.add(price_clean)
                lines.append(f"- {price_clean}")
        lines.append("")
    
    # Contacts section
    if data.get("contacts"):
        lines.append("## Contacts")
        for contact_type, values in data["contacts"].items():
            lines.append(f"### {contact_type.title()}")
            # Deduplicate
            seen = set()
            for val in values:
                val_clean = val.strip()
                if val_clean and val_clean not in seen:
                    seen.add(val_clean)
                    lines.append(f"- {val_clean}")
            lines.append("")
    
    # Services section
    if data.get("services"):
        lines.append("## Services")
        seen = set()
        for service in data["services"]:
            name = service.get("name", "").strip()
            desc = service.get("description", "").strip()
            if name or desc:
                # Create a unique key for deduplication
                key = f"{name}|{desc}"
                if key not in seen:
                    seen.add(key)
                    if name:
                        lines.append(f"### {name}")
                    if desc:
                        lines.append(desc)
                        lines.append("")
    
    if not any([data.get("prices"), data.get("contacts"), data.get("services")]):
        lines.append("No data extracted.")
    
    return "\n".join(lines)


def normalize_data(raw_result: dict, domain: str, niche: str = "services") -> dict:
    """
    Normalize raw parsing data into unified JSON structure.
    
    Args:
        raw_result: Raw data from extraction (prices, contacts, services)
        domain: Source domain
        niche: Detected niche ("services" or "auto_dealer")
    
    Returns:
        dict in unified structure (appropriate for the niche)
    """
    if niche == "auto_dealer":
        # Normalize auto_dealer data to new schema
        normalized = raw_result.copy() if isinstance(raw_result, dict) else {}
        normalized.setdefault("domain", domain)
        normalized["niche"] = "auto_dealer"
        
        # Ensure dealership_info exists (new format) or convert from old contacts
        if "dealership_info" not in normalized:
            # Convert from old heuristic contacts format
            contacts = normalized.get("contacts", {})
            # Extract address
            addr = contacts.get("address", "")
            if isinstance(addr, list):
                addr = addr[0] if addr else ""
            normalized["dealership_info"] = {
                "address": addr,
                "phones": contacts.get("phones", []),
                "work_time": ""  # not available in heuristic extraction
            }
            # Remove old contacts to avoid confusion
            normalized.pop("contacts", None)
        else:
            # Ensure dealership_info has required fields
            normalized["dealership_info"].setdefault("address", "")
            normalized["dealership_info"].setdefault("phones", [])
            normalized["dealership_info"].setdefault("work_time", "")
        
        # Ensure models exists
        normalized.setdefault("models", [])
        # Ensure special_offers exists
        normalized.setdefault("special_offers", [])
        
        # Remove old fields that shouldn't be present in new format
        for old_field in ["about", "city", "benefits", "testimonials", "services"]:
            if old_field in normalized:
                del normalized[old_field]
        
        # Ensure business_name and tagline exist (may be empty)
        normalized.setdefault("business_name", "")
        normalized.setdefault("tagline", "")
        
        return normalized
    else:
        # Services niche - use the existing normalized structure
        normalized = {
            "domain": domain,
            "business_name": "",
            "tagline": "",
            "about": "",
            "services": [],
            "contacts": {
                "phones": [],
                "emails": [],
                "address": "",
                "work_time": "",
                "social": []
            },
            "benefits": [],
            "testimonials": []
        }
        
        # Map services
        raw_services = raw_result.get("services", [])
        normalized_services = []
        for s in raw_services:
            name = s.get("name", "").strip()
            desc = s.get("description", "").strip()
            # Try to extract price from text if available
            price_from = ""
            text = s.get("text", "").strip()
            if text:
                price_match = PRICE_PATTERN.search(text)
                if price_match:
                    price_from = price_match.group(0)
            
            if name or desc:
                normalized_services.append({
                    "name": name,
                    "desc": desc,
                    "price_from": price_from
                })
        normalized["services"] = normalized_services
        
        # Map contacts
        raw_contacts = raw_result.get("contacts", {})
        normalized["contacts"]["phones"] = raw_contacts.get("phones", [])[:]
        normalized["contacts"]["emails"] = raw_contacts.get("emails", [])[:]
        
        # Address - take first available
        addresses = raw_contacts.get("address", [])
        if isinstance(addresses, list) and addresses:
            normalized["contacts"]["address"] = addresses[0].strip()
        elif isinstance(addresses, str):
            normalized["contacts"]["address"] = addresses.strip()
        
        # Social links
        normalized["contacts"]["social"] = raw_contacts.get("social", [])[:]
        
        # Prices - could be used for tagline or about
        prices = raw_result.get("prices", [])
        if prices and not normalized["tagline"]:
            # Use first price as tagline hint
            normalized["tagline"] = f"Услуги от {prices[0]}"
        
        normalized["niche"] = "services"
        return normalized