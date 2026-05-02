"""Pure data-transformation functions — no FS, no network, no subprocess."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from utils import (
    generate_neutral_service_description,
    is_junk_service_description,
    is_junk_service_title,
)


# ======================================================================
# Text / string helpers
# ======================================================================


def short_about_text(text: str, fallback: str) -> str:
    """First sentence of *text*, capped at 220 chars. Returns *fallback* if empty."""
    value = re.sub(r"\s+", " ", (text or "").strip())
    if not value:
        return fallback
    parts = re.split(r"(?<=[.!?])\s+", value)
    candidate = parts[0].strip() if parts else value
    return candidate if len(candidate) <= 220 else candidate[:217].rstrip() + "..."


def to_phone_raw(phone: str) -> str:
    """Strip everything except digits and leading ``+``."""
    return re.sub(r"[^\d+]", "", phone or "")


def prettify_name_from_filename(filename: str) -> str:
    """Convert a filename stem to a human-readable name (max 2 words)."""
    stem = Path(filename).stem
    parts = [p for p in re.split(r"[_\-\s]+", stem) if p]
    if not parts:
        return "Мастер"
    words = [p.capitalize() for p in parts[:2]]
    return " ".join(words)


# ======================================================================
# Social links
# ======================================================================


def pick_social_links(lead: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract social-media links from lead fields and JSON social_links."""
    out: List[Dict[str, str]] = []
    social_candidates = [
        ("vk_url", "ВКонтакте", "VK"),
        ("instagram_url", "Instagram", "IG"),
        ("telegram_url", "Telegram", "TG"),
    ]
    for key, label, short in social_candidates:
        url = str(lead.get(key) or "").strip()
        if url:
            out.append({"href": url, "label": f"{label} {lead.get('name') or ''}".strip(), "short": short})

    social_raw = lead.get("social_links")
    if social_raw and isinstance(social_raw, str):
        try:
            parsed = json.loads(social_raw)
            if isinstance(parsed, dict):
                for label, short, key in (
                    ("ВКонтакте", "VK", "vk"),
                    ("Telegram", "TG", "telegram"),
                    ("Instagram", "IG", "instagram"),
                ):
                    urls = parsed.get(key) or []
                    if isinstance(urls, list):
                        for href in urls:
                            href = str(href or "").strip()
                            if href and not any(item["href"] == href for item in out):
                                out.append(
                                    {
                                        "href": href,
                                        "label": f"{label} {lead.get('name') or ''}".strip(),
                                        "short": short,
                                    }
                                )
        except Exception:
            pass
    return out


# ======================================================================
# Service merging & normalisation
# ======================================================================


def normalize_service_title(value: str) -> str:
    """Lowercase, strip special chars, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\sа-яё]", " ", (value or "").lower())).strip()


def normalize_extracted_services(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise raw services from the extracted-payload."""
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or item.get("title") or "").strip()
        if not title:
            continue
        description = str(item.get("description") or "").strip()
        price = str(item.get("price") or "").strip()
        image = str(item.get("image") or item.get("image_url") or "").strip()
        out.append(
            {
                "title": title,
                "short": title,
                "description": description or title,
                "priceFrom": price or "по запросу",
                "image": image,
            }
        )
    return out[:20]


def normalize_extracted_faq(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter FAQ pairs that have both question and answer."""
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or "").strip()
        a = str(item.get("a") or "").strip()
        if q and a:
            out.append({"q": q, "a": a})
    return out[:20]


def merge_services(
    curated_services: List[Dict[str, Any]],
    yandex_services: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge curated + Yandex services, enrich prices, deduplicate."""
    merged: List[Dict[str, Any]] = []
    yandex_pool = []
    for item in yandex_services:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        price = str(item.get("price") or "").strip()
        if not name:
            continue
        if is_junk_service_title(name):
            continue
        yandex_pool.append({"name": name, "price": price, "norm": normalize_service_title(name)})

    used_yandex = set()

    for item in curated_services:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        short = str(item.get("short") or "").strip()
        description = str(item.get("description") or "").strip()
        price_from = str(item.get("priceFrom") or "").strip()

        if is_junk_service_description(description, title):
            description = short

        if not description or description.lower() == title.lower():
            description = generate_neutral_service_description(title)

        norm_title = normalize_service_title(title)
        best_idx = None
        best_score = 0.0
        for idx, y_item in enumerate(yandex_pool):
            yn = y_item["norm"]
            if not yn:
                continue
            score = 0.0
            if norm_title in yn or yn in norm_title:
                score = 0.9
            else:
                tokens_a = set(norm_title.split())
                tokens_b = set(yn.split())
                if tokens_a and tokens_b:
                    score = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None and best_score >= 0.5:
            y_item = yandex_pool[best_idx]
            used_yandex.add(best_idx)
            if y_item.get("price"):
                price_from = y_item["price"]

        merged.append(
            {
                "title": title,
                "short": short or title,
                "description": description or short or title,
                "priceFrom": price_from or "по запросу",
            }
        )

    for idx, y_item in enumerate(yandex_pool):
        if idx in used_yandex:
            continue
        name = y_item["name"]
        if is_junk_service_title(name):
            continue
        merged.append(
            {
                "title": name,
                "short": name,
                "description": generate_neutral_service_description(name),
                "priceFrom": y_item.get("price") or "по запросу",
            }
        )

    return merged[:20]


# ======================================================================
# Hero helpers
# ======================================================================


def hero_brand_name(lead_name: str) -> str:
    """Short brand name for titleLine2 — strips noise words."""
    noise = {
        "студия",
        "красоты",
        "салон",
        "beauty",
        "studio",
        "center",
        "центр",
        "spa",
        "спа",
        "сервис",
        "service",
    }
    words = lead_name.strip().split()
    filtered = [w for w in words if w.lower() not in noise]
    result = " ".join(filtered).strip()
    return result if result else lead_name.strip()


def hero_line1(lead: dict) -> str:
    """Primary tagline based on category."""
    cat = (lead.get("category") or "").lower()
    if "nail" in cat:
        return "Студия маникюра"
    if "brow" in cat:
        return "Студия бровей"
    if "barber" in cat:
        return "Барбершоп"
    return "Моностудия"


def hero_line1_small(lead: dict) -> str:
    """Secondary tagline based on category."""
    cat = (lead.get("category") or "").lower()
    if "nail" in cat:
        return "идеального маникюра"
    if "brow" in cat:
        return "оформления бровей"
    if "barber" in cat:
        return "мужских стрижек"
    return "по созданию образа"
