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

from auto_detector import auto_detect
from config_manager import load_config, save_config
from llm_engine import llm_extract, generate_selectors_from_data

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    config: Optional[Dict] = None
) -> dict:
    """
    Main parsing function.
    Pipeline:
    1. Load HTML from URL
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
    
    Returns:
        dict with result info (output_path, domain, score, data, used_llm, selectors, data_preview)
    """
    # Extract domain from URL
    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain = parsed.netloc
    
    logger.info(f"Starting parse for {url} (domain: {domain})")
    
    # Step 1: Load HTML
    try:
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
            data = llm_extract(soup, html, selectors, score if score is not None else 0.0)
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
                "used_llm": used_llm
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
                "used_llm": used_llm
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
        "data_preview": data_preview
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