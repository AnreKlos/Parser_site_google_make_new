"""Config assembly — build_config orchestrates all data sources into a single JSON config."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from core.block_flags import compute_block_flags
from config import settings
from utils import detect_city, slugify_name

from config_builder.defaults import (
    DEFAULT_CHAT_WIDGET,
    DEFAULT_CONTENT,
    DEFAULT_LEGAL,
    DEFAULT_SECTION_ORDER,
    DEFAULT_TOKENS,
)
from config_builder.io import (
    list_image_urls,
    load_lead,
    log,
    read_json_if_exists,
)
from config_builder.transforms import (
    hero_brand_name,
    hero_line1,
    hero_line1_small,
    merge_services,
    normalize_extracted_faq,
    normalize_extracted_services,
    pick_social_links,
    prettify_name_from_filename,
    short_about_text,
    to_phone_raw,
)

CURATED_DIR = settings.data_dir / "curated"
YANDEX_DIR = settings.data_dir / "yandex"
EXTRACTED_DIR = settings.data_dir / "extracted"


# ======================================================================
# Section builders (private helpers)
# ======================================================================


def _build_meta_block(lead: dict, slug: str, curated: dict, city: str) -> dict:
    """Build the ``meta`` block of the config."""
    curated_meta = curated.get("meta") if isinstance(curated.get("meta"), dict) else {}
    curated_tagline = str(curated_meta.get("tagline") or "").strip()
    lead_name = str(lead.get("name") or f"Lead {slug}")
    fallback_title = (
        "Студия красоты"
        if str(lead.get("category") or "").strip() in {"", "other"}
        else str(lead.get("category") or "").strip()
    )
    return {
        "slug": slug,
        "brand": {
            "name": lead_name,
            "shortName": hero_brand_name(lead_name),
            "slug": slug,
            "tagline": curated_tagline or "",
        },
        "name": lead_name,
        "fullName": f"{lead_name} — {curated_tagline or fallback_title}",
        "tagline": curated_tagline or fallback_title,
        "city": city,
    }


def _build_contacts_block(
    lead: dict,
    yandex: dict,
    yandex_payload: dict,
    phones: List[str],
    phone_main: str,
    phone_raw: str,
    address: str,
    coords: dict | None,
    additional_addresses: list,
    social_links: list,
) -> dict:
    """Build the ``contacts`` block of the config."""
    block: dict = {
        "phone": phone_main,
        "phoneRaw": phone_raw,
        "phones": phones,
        "whatsapp": phone_raw if phone_raw else "",
        "address": address,
        "additionalAddresses": additional_addresses,
        "workingHours": str(yandex.get("working_hours") or "").strip() or "ежедневно 10:00–20:00",
        "vk": str(lead.get("vk_url") or "").strip(),
    }
    if not block["vk"]:
        for item in social_links:
            if item.get("short") == "VK":
                block["vk"] = item.get("href") or ""
                break
    if coords:
        block["coordinates"] = coords
    return block


def _build_hero_section(
    lead: dict,
    hero_image: str,
    curated_about: str,
) -> dict:
    """Build the ``hero`` section."""
    return {
        "enabled": True,
        "image": hero_image,
        "titleLine1": hero_line1(lead),
        "titleLine1Small": hero_line1_small(lead),
        "titleLine1SmallSize": "default",
        "titleLine2": hero_brand_name(str(lead.get("name") or "")),
        "topLabel": "Премиум студия красоты",
        "lead": short_about_text(curated_about, "Подчеркнем вашу индивидуальность и соберем образ под событие и настроение."),
    }


def _build_sections_config(
    lead: dict,
    slug: str,
    *,
    hero_image: str,
    gallery_images: list,
    about_images: list,
    team_images: list,
    curated_about: str,
    curated_reviews: list,
    effective_faq: list,
    merged_services: list,
    extracted_services: list,
    team_items: list,
) -> dict:
    """Build all ``sections.*`` blocks at once."""
    sections = {
        "hero": _build_hero_section(lead, hero_image, curated_about),
        "promotion": {"enabled": True},
        "serviceCarousel": {
            "enabled": bool(extracted_services),
            "items": extracted_services,
        },
        "services": {
            "enabled": bool(merged_services),
            "items": merged_services,
        },
        "gallery": {
            "enabled": len(gallery_images) >= 3,
            "title": "Наши работы",
            "subtitle": "Каждая деталь имеет значение",
            "items": gallery_images,
        },
        "team": {
            "enabled": bool(team_items),
            "items": team_items,
        },
        "reels": {
            "enabled": False,
            "items": [],
        },
        "reviews": {
            "enabled": bool(curated_reviews),
            "items": curated_reviews,
        },
        "about": {
            "enabled": bool(curated_about),
            "showImages": bool(about_images),
            "text": curated_about,
            "images": about_images,
        },
        "faq": {
            "enabled": bool(effective_faq),
            "items": effective_faq,
        },
        "bookingContacts": {
            "enabled": True,
            "showMap": True,
        },
    }
    return sections


# ======================================================================
# Public entry-point
# ======================================================================


def build_config(lead_id: int) -> Dict[str, Any]:
    """Assemble the full site config dict for a lead.

    Reads lead data from the DB, curated / yandex / extracted JSON files,
    scans image directories, merges services, and builds the complete
    config structure expected by neuralsync.

    Raises:
        RuntimeError: if *lead_id* is not found in the database.
    """
    lead = load_lead(lead_id)
    if not lead:
        raise RuntimeError(f"Лид ID={lead_id} не найден")

    lead_name = str(lead.get("name") or f"Lead {lead_id}")
    slug = slugify_name(lead_name, lead_id)

    curated_path = CURATED_DIR / f"{slug}.json"
    yandex_path = YANDEX_DIR / f"{slug}.json"
    extracted_path = EXTRACTED_DIR / f"{slug}.json"

    log(f"📖 Читаю curated/{slug}.json")
    curated = read_json_if_exists(curated_path) or {}

    log(f"📖 Читаю yandex/{slug}.json")
    yandex_payload = read_json_if_exists(yandex_path) or {}
    yandex = yandex_payload.get("yandex") if isinstance(yandex_payload.get("yandex"), dict) else {}

    log(f"📖 Читаю extracted/{slug}.json")
    extracted_payload = read_json_if_exists(extracted_path) or {}

    # Image directories
    hero_images = list_image_urls(slug, "hero")
    gallery_images = list_image_urls(slug, "gallery")
    about_images = list_image_urls(slug, "about")
    team_images = list_image_urls(slug, "team")
    log(
        f"📖 Читаю public/{slug}/ ({len(hero_images)} фото в hero, "
        f"{len(gallery_images)} в gallery, {len(team_images)} в team)"
    )

    # Block flags
    photo_map = {"hero": hero_images, "gallery": gallery_images, "team": team_images, "about": about_images}
    block_flags = compute_block_flags(extracted=extracted_payload, photos=photo_map, yandex=yandex_payload)
    log(f"🚩 Block flags: {block_flags}")

    # Curated fields
    curated_meta = curated.get("meta") if isinstance(curated.get("meta"), dict) else {}
    curated_tagline = str(curated_meta.get("tagline") or "").strip()
    curated_about = str(curated.get("about") or "").strip()
    curated_reviews = curated.get("reviews") if isinstance(curated.get("reviews"), list) else []
    curated_faq = curated.get("faq") if isinstance(curated.get("faq"), list) else []
    curated_services = curated.get("services") if isinstance(curated.get("services"), list) else []

    # Extracted fields
    extracted_services_raw = extracted_payload.get("serviceCarousel") if isinstance(extracted_payload.get("serviceCarousel"), list) else []
    if not extracted_services_raw and isinstance(extracted_payload.get("services_carousel"), list):
        extracted_services_raw = extracted_payload.get("services_carousel")
    extracted_faq_raw = extracted_payload.get("faq_accordion") if isinstance(extracted_payload.get("faq_accordion"), list) else []
    extracted_services = normalize_extracted_services(extracted_services_raw)
    extracted_faq = normalize_extracted_faq(extracted_faq_raw)

    # Address / city
    yandex_address = str(yandex.get("address") or "").strip()
    lead_address = str(lead.get("address") or "").strip()
    city = detect_city(lead_address) or str(yandex_payload.get("city") or "").strip()
    address = yandex_address or lead_address

    # Phones
    y_phones = yandex.get("phones") if isinstance(yandex.get("phones"), list) else []
    phones = [str(p).strip() for p in y_phones if str(p).strip()]
    fallback_phone = str(lead.get("phone") or "").strip()
    if not phones and fallback_phone:
        phones = [fallback_phone]
    phone_main = phones[0] if phones else ""
    phone_raw = to_phone_raw(phone_main)

    # Contacts extras
    coords = yandex.get("coordinates") if isinstance(yandex.get("coordinates"), dict) else None
    additional_addresses = yandex.get("additional_addresses") if isinstance(yandex.get("additional_addresses"), list) else []
    social_links = pick_social_links(lead)

    # Services merging
    merged_services = merge_services(
        curated_services,
        yandex.get("services") if isinstance(yandex.get("services"), list) else [],
    )

    # FAQ
    effective_faq = extracted_faq if extracted_faq else curated_faq

    # Team
    team_items = [{"name": prettify_name_from_filename(Path(url).name), "photo": url} for url in team_images]

    # Section order
    sections_order = DEFAULT_SECTION_ORDER.copy()
    if extracted_services:
        sections_order = [item for item in sections_order if item != "serviceCarousel"]
        hero_index = sections_order.index("hero") if "hero" in sections_order else -1
        insert_at = hero_index + 1 if hero_index >= 0 else 0
        sections_order.insert(insert_at, "serviceCarousel")

    # Hero image
    hero_image = hero_images[0] if hero_images else (gallery_images[0] if gallery_images else "")

    # Booking
    booking_url = str(lead.get("yclients_url") or "").strip()

    # Config assembly
    log("🛠 Собираю секции")

    config: Dict[str, Any] = {
        "meta": _build_meta_block(lead, slug, curated, city),
        "contacts": _build_contacts_block(
            lead,
            yandex,
            yandex_payload,
            phones,
            phone_main,
            phone_raw,
            address,
            coords,
            additional_addresses,
            social_links,
        ),
        "booking": {"url": booking_url} if booking_url else {},
        "social": social_links,
        "tokens": DEFAULT_TOKENS,
        "legal": DEFAULT_LEGAL,
        "content": DEFAULT_CONTENT,
        "sectionsOrder": sections_order,
        "copyrightYear": datetime.now().year,
        "sections": _build_sections_config(
            lead,
            slug,
            hero_image=hero_image,
            gallery_images=gallery_images,
            about_images=about_images,
            team_images=team_images,
            curated_about=curated_about,
            curated_reviews=curated_reviews,
            effective_faq=effective_faq,
            merged_services=merged_services,
            extracted_services=extracted_services,
            team_items=team_items,
        ),
        "features": {
            "chatWidget": DEFAULT_CHAT_WIDGET,
        },
        "block_flags": block_flags,
    }

    return config
