"""Builder endpoints: POST /v1/builder/build (dry-run) + POST /v1/builder/render."""

import asyncio
import shutil
from pathlib import Path
from fastapi import APIRouter, Request

from config import settings
from db.database import get_lead_by_id
from utils.text import slugify_name
from config_builder.builder import build_config
from config_builder.cli import run_build
from config_builder.io import read_json_if_exists, list_image_urls
from bridge.error_handlers import BridgeError

router = APIRouter()


@router.post("/builder/build")
async def build_config_dry_run(request: Request, body: dict):
    """Dry-run build config without writing files."""
    lead_id = body.get("lead_id")
    if lead_id is None:
        raise BridgeError(
            code="invalid_request",
            message="Missing lead_id in request body",
            status_code=400,
        )

    lead = await get_lead_by_id(lead_id)
    if not lead:
        raise BridgeError(
            code="lead_not_found",
            message=f"Лид с ID={lead_id} не найден в БД",
            details={"lead_id": lead_id},
            status_code=404,
        )

    # Run build_config in thread pool (it's synchronous)
    try:
        config = await asyncio.to_thread(build_config, lead_id)
    except RuntimeError as e:
        raise BridgeError(
            code="build_failed",
            message=str(e),
            details={"message": str(e)},
            status_code=500,
        )

    # Compute sources_used and fallbacks_applied
    slug = slugify_name(lead.name, lead.id)
    data_dir = settings.data_dir
    public_dir = settings.public_dir

    # Check file existence
    curated_path = data_dir / "curated" / f"{slug}.json"
    extracted_path = data_dir / "extracted" / f"{slug}.json"
    yandex_path = data_dir / "yandex" / f"{slug}.json"

    curated_exists = curated_path.exists()
    extracted_exists = extracted_path.exists()
    yandex_exists = yandex_path.exists()

    # Load curated for fallback detection
    curated = read_json_if_exists(curated_path) if curated_exists else {}
    yandex_payload = read_json_if_exists(yandex_path) if yandex_exists else {}
    yandex_data = yandex_payload.get("yandex", {})

    # Detect fallbacks
    fallbacks_applied = []

    # tagline=fallback_category
    tagline = config.get("meta", {}).get("tagline", "")
    if tagline == lead.category or tagline == "Студия красоты":
        fallbacks_applied.append("tagline=fallback_category")

    # about=empty
    about_section = config.get("sections", {}).get("about", {})
    about_text = curated.get("about", "")
    if not about_section.get("enabled") or not about_text:
        fallbacks_applied.append("about=empty")

    # reviews=empty
    reviews_section = config.get("sections", {}).get("reviews", {})
    if not reviews_section.get("items"):
        fallbacks_applied.append("reviews=empty")

    # services=yandex_only
    services_section = config.get("sections", {}).get("services", {})
    curated_services = curated.get("services", [])
    if services_section.get("enabled") and not curated_services:
        fallbacks_applied.append("services=yandex_only")

    # hero_image=fallback_gallery
    hero_images = list_image_urls(slug, "hero")
    gallery_images = list_image_urls(slug, "gallery")
    if not hero_images and gallery_images:
        fallbacks_applied.append("hero_image=fallback_gallery")

    # team=disabled_no_photos
    team_section = config.get("sections", {}).get("team", {})
    if not team_section.get("enabled"):
        fallbacks_applied.append("team=disabled_no_photos")

    # faq=empty
    faq_section = config.get("sections", {}).get("faq", {})
    if not faq_section.get("items"):
        fallbacks_applied.append("faq=empty")

    # phones=lead_only
    yandex_phones = yandex_data.get("phones", [])
    if not yandex_phones:
        fallbacks_applied.append("phones=lead_only")

    # Determine if curated is actually used
    curated_used = False
    if curated_exists:
        tagline_non_default = tagline and tagline != lead.category and tagline != "Студия красоты"
        about_non_empty = bool(about_text)
        reviews_non_empty = bool(curated.get("reviews"))
        faq_non_empty = bool(curated.get("faq"))
        services_non_empty = bool(curated_services)
        curated_used = tagline_non_default or about_non_empty or reviews_non_empty or faq_non_empty or services_non_empty

    # Count photos
    photos_count = {
        "hero": len(list_image_urls(slug, "hero")),
        "gallery": len(list_image_urls(slug, "gallery")),
        "about": len(list_image_urls(slug, "about")),
        "team": len(list_image_urls(slug, "team")),
    }

    sources_used = {
        "curated": curated_used,
        "extracted": extracted_exists,
        "yandex": yandex_exists,
        "photos": photos_count,
        "fallbacks_applied": fallbacks_applied,
    }

    trace_id = getattr(request.state, "trace_id", "unknown")
    duration_ms = getattr(request.state, "duration_ms", 0)

    return {
        "lead_id": lead_id,
        "slug": slug,
        "config": config,
        "sources_used": sources_used,
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }


@router.post("/builder/render")
async def render_config(request: Request, body: dict):
    """Final build: writes .config.js and copies photos."""
    lead_id = body.get("lead_id")
    no_copy = body.get("no_copy", False)

    if lead_id is None:
        raise BridgeError(
            code="invalid_request",
            message="Missing lead_id in request body",
            status_code=400,
        )

    lead = await get_lead_by_id(lead_id)
    if not lead:
        raise BridgeError(
            code="lead_not_found",
            message=f"Лид с ID={lead_id} не найден в БД",
            details={"lead_id": lead_id},
            status_code=404,
        )

    # Run run_build in thread pool (it's synchronous)
    try:
        exit_code = await asyncio.to_thread(run_build, lead_id, dry_run=False, no_copy=no_copy)
    except RuntimeError as e:
        raise BridgeError(
            code="build_failed",
            message=str(e),
            details={"message": str(e)},
            status_code=500,
        )
    except Exception as e:
        # Check if it's a node validation error
        error_msg = str(e)
        if "node" in error_msg.lower() or "validation" in error_msg.lower():
            raise BridgeError(
                code="js_validation_failed",
                message="Node.js validation failed",
                details={"node_output": error_msg},
                status_code=500,
            )
        raise BridgeError(
            code="build_failed",
            message=str(e),
            details={"message": error_msg},
            status_code=500,
        )

    if exit_code != 0:
        raise BridgeError(
            code="build_failed",
            message="run_build returned non-zero exit code",
            details={"exit_code": exit_code},
            status_code=500,
        )

    # Build response
    slug = slugify_name(lead.name, lead_id)
    neuralsync_configs_dir = Path(settings.neuralsync_root) / "src" / "configs"
    config_js_path = neuralsync_configs_dir / f"{slug}.config.js"

    # Count copied photos
    photos_copied = None
    if not no_copy:
        neuralsync_public_dir = Path(settings.neuralsync_root) / "public" / slug
        if neuralsync_public_dir.exists():
            photos_copied = sum(1 for p in neuralsync_public_dir.rglob("*") if p.is_file())

    trace_id = getattr(request.state, "trace_id", "unknown")
    duration_ms = getattr(request.state, "duration_ms", 0)

    return {
        "lead_id": lead_id,
        "slug": slug,
        "config_js_path": str(config_js_path),
        "photos_copied": photos_copied,
        "validated": True,
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }
