"""Curated write endpoint: POST /v1/curated/write."""

import json
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException

from config import settings
from db.database import get_lead_by_id
from utils.text import slugify_name
from bridge.error_handlers import BridgeError
from bridge.schemas.curated import CuratedWriteRequest

router = APIRouter()


@router.post("/curated/write")
async def write_curated(request: Request, body: CuratedWriteRequest):
    """Write curated data to JSON file with validation."""
    lead = await get_lead_by_id(body.lead_id)
    if not lead:
        raise BridgeError(
            code="lead_not_found",
            message=f"Лид с ID={body.lead_id} не найден в БД",
            details={"lead_id": body.lead_id},
            status_code=404,
        )

    # Validate slug
    expected_slug = slugify_name(lead.name, lead.id)
    if body.curated_data.slug != expected_slug:
        raise BridgeError(
            code="validation_error",
            message=f"slug mismatch: expected {expected_slug}, got {body.curated_data.slug}",
            details={
                "expected": expected_slug,
                "received": body.curated_data.slug,
            },
            status_code=422,
        )

    # Check for previous curated file
    curated_path = settings.data_dir / "curated" / f"{expected_slug}.json"
    had_previous_curated = curated_path.exists()
    previous_was_from = None

    if had_previous_curated:
        try:
            with open(curated_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            previous_was_from = existing.get("_meta", {}).get("written_by")
        except (json.JSONDecodeError, IOError):
            previous_was_from = None

    # Build payload
    payload = body.curated_data.model_dump()
    payload["about"] = body.curated_data.about.text  # string, not object
    payload["_meta"] = {
        "written_by": "generator",
        "written_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "trace_id": request.state.trace_id,
        "lead_id": body.lead_id,
    }

    # Write to file
    curated_path.parent.mkdir(parents=True, exist_ok=True)
    with open(curated_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Build summary
    summary = {
        "tagline_chars": len(body.curated_data.meta.tagline),
        "about_chars": len(body.curated_data.about.text),
        "reviews_count": len(body.curated_data.reviews),
        "faq_count": len(body.curated_data.faq),
        "services_count": len(body.curated_data.services),
        "had_previous_curated": had_previous_curated,
        "previous_was_from": previous_was_from,
    }

    trace_id = getattr(request.state, "trace_id", "unknown")
    duration_ms = getattr(request.state, "duration_ms", 0)

    return {
        "lead_id": body.lead_id,
        "slug": expected_slug,
        "output_path": str(curated_path),
        "summary": summary,
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }
