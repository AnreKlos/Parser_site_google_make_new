"""Leads endpoints: GET /v1/leads/{id}, /v1/leads/{id}/data-bundle, POST /v1/leads/{id}/finalize."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import settings
from db.database import get_lead_by_id, update_lead_status, log_action
from utils.text import slugify_name
from config_builder.io import read_json_if_exists, list_image_urls
from bridge.error_handlers import BridgeError

router = APIRouter()


class LeadResponse(BaseModel):
    """Response model for lead data."""
    id: int
    name: str
    google_rating: Optional[float] = None
    reviews_count: Optional[int] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    google_maps_url: Optional[str] = None
    emails: list = []
    social_links: dict = {}
    status: str
    tech_score: Optional[int] = None
    load_time_sec: Optional[float] = None
    audit_notes: Optional[str] = None
    category: Optional[str] = None
    site_config_path: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def parse_json_field(value: Optional[str], default):
    """Safely parse JSON string field, return default on error."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: int, request: Request):
    """Get lead data with artifact flags."""
    lead = await get_lead_by_id(lead_id)
    if not lead:
        raise BridgeError(
            code="lead_not_found",
            message=f"Лид с ID={lead_id} не найден в БД",
            details={"lead_id": lead_id},
            status_code=404,
        )

    # Generate slug
    slug = slugify_name(lead.name, lead.id)

    # Check artifacts
    data_dir = settings.data_dir
    artifacts = {
        "extracted": (data_dir / "extracted" / f"{slug}.json").exists(),
        "curated": (data_dir / "curated" / f"{slug}.json").exists(),
        "yandex": (data_dir / "yandex" / f"{slug}.json").exists(),
    }

    # Count photos in public folder
    public_dir = settings.public_dir / slug
    photo_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    artifacts["photos"] = {}

    for folder in ["hero", "gallery", "about", "team"]:
        folder_path = public_dir / folder
        if folder_path.exists() and folder_path.is_dir():
            count = sum(
                1 for f in folder_path.iterdir()
                if f.is_file() and f.suffix.lower() in photo_extensions
            )
            artifacts["photos"][folder] = count
        else:
            artifacts["photos"][folder] = 0

    # Parse JSON fields
    emails = parse_json_field(lead.emails, [])
    social_links = parse_json_field(lead.social_links, {})

    # Build response
    lead_data = {
        "id": lead.id,
        "name": lead.name,
        "google_rating": lead.google_rating,
        "reviews_count": lead.reviews_count,
        "address": lead.address,
        "phone": lead.phone,
        "website": lead.website,
        "google_maps_url": lead.google_maps_url,
        "emails": emails,
        "social_links": social_links,
        "status": lead.status,
        "tech_score": lead.tech_score,
        "load_time_sec": lead.load_time_sec,
        "audit_notes": lead.audit_notes,
        "category": lead.category,
        "site_config_path": lead.site_config_path,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
    }

    trace_id = getattr(request.state, "trace_id", "unknown")
    duration_ms = getattr(request.state, "duration_ms", 0)

    return {
        "lead": lead_data,
        "slug": slug,
        "artifacts": artifacts,
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }


@router.get("/leads/{lead_id}/data-bundle")
async def get_lead_data_bundle(lead_id: int, request: Request):
    """Get lead data with full artifact contents."""
    lead = await get_lead_by_id(lead_id)
    if not lead:
        raise BridgeError(
            code="lead_not_found",
            message=f"Лид с ID={lead_id} не найден в БД",
            details={"lead_id": lead_id},
            status_code=404,
        )

    # Generate slug
    slug = slugify_name(lead.name, lead.id)

    # Get base lead data (reuse logic from /leads/{id})
    data_dir = settings.data_dir
    artifacts = {
        "extracted": (data_dir / "extracted" / f"{slug}.json").exists(),
        "curated": (data_dir / "curated" / f"{slug}.json").exists(),
        "yandex": (data_dir / "yandex" / f"{slug}.json").exists(),
    }

    public_dir = settings.public_dir / slug
    photo_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    artifacts["photos"] = {}

    for folder in ["hero", "gallery", "about", "team"]:
        folder_path = public_dir / folder
        if folder_path.exists() and folder_path.is_dir():
            count = sum(
                1 for f in folder_path.iterdir()
                if f.is_file() and f.suffix.lower() in photo_extensions
            )
            artifacts["photos"][folder] = count
        else:
            artifacts["photos"][folder] = 0

    # Parse JSON fields
    emails = parse_json_field(lead.emails, [])
    social_links = parse_json_field(lead.social_links, {})

    lead_data = {
        "id": lead.id,
        "name": lead.name,
        "google_rating": lead.google_rating,
        "reviews_count": lead.reviews_count,
        "address": lead.address,
        "phone": lead.phone,
        "website": lead.website,
        "google_maps_url": lead.google_maps_url,
        "emails": emails,
        "social_links": social_links,
        "status": lead.status,
        "tech_score": lead.tech_score,
        "load_time_sec": lead.load_time_sec,
        "audit_notes": lead.audit_notes,
        "category": lead.category,
        "site_config_path": lead.site_config_path,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
    }

    # Read artifact contents
    data = {
        "curated": read_json_if_exists(data_dir / "curated" / f"{slug}.json") or None,
        "extracted": read_json_if_exists(data_dir / "extracted" / f"{slug}.json") or None,
        "yandex": read_json_if_exists(data_dir / "yandex" / f"{slug}.json") or None,
    }

    # Get photo URLs
    data["photos"] = {
        "hero": list_image_urls(slug, "hero"),
        "gallery": list_image_urls(slug, "gallery"),
        "about": list_image_urls(slug, "about"),
        "team": list_image_urls(slug, "team"),
    }

    trace_id = getattr(request.state, "trace_id", "unknown")
    duration_ms = getattr(request.state, "duration_ms", 0)

    return {
        "lead": lead_data,
        "slug": slug,
        "artifacts": artifacts,
        "data": data,
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }


@router.post("/leads/{lead_id}/finalize")
async def finalize_lead(lead_id: int, request: Request, body: dict):
    """Finalize lead status after agents workflow."""
    agents_status = body.get("agents_status")
    site_config_path = body.get("site_config_path")
    rejection_reason = body.get("rejection_reason")
    critic_score = body.get("critic_score")
    critic_verdict_summary = body.get("critic_verdict_summary")

    # Validation
    if agents_status not in ["ready", "needs_review", "rejected"]:
        raise BridgeError(
            code="invalid_request",
            message="Invalid agents_status. Must be 'ready', 'needs_review', or 'rejected'",
            status_code=400,
        )

    if agents_status == "rejected" and not rejection_reason:
        raise BridgeError(
            code="invalid_request",
            message="rejection_reason required when agents_status is 'rejected'",
            status_code=400,
        )

    if agents_status == "ready" and not site_config_path:
        raise BridgeError(
            code="invalid_request",
            message="site_config_path required when agents_status is 'ready'",
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

    # Map agents_status to Lead.status
    status_mapping = {
        "ready": "agents_ready",
        "needs_review": "agents_needs_review",
        "rejected": "agents_rejected",
    }
    new_status = status_mapping[agents_status]

    # Update lead status
    previous_status = lead.status
    updated_lead = await update_lead_status(lead_id, new_status)

    if not updated_lead:
        raise BridgeError(
            code="internal_error",
            message="Failed to update lead status",
            status_code=500,
        )

    # Update site_config_path if provided
    if site_config_path:
        from sqlalchemy import update
        from db.database import async_session
        from db.models import Lead

        async with async_session() as session:
            await session.execute(
                update(Lead)
                .where(Lead.id == lead_id)
                .values(site_config_path=site_config_path)
            )
            await session.commit()

    # Log action
    await log_action(
        lead_id=lead_id,
        action="agents_workflow_finalize",
        details=body,
    )

    # Generate slug
    slug = slugify_name(lead.name, lead_id)

    trace_id = getattr(request.state, "trace_id", "unknown")
    duration_ms = getattr(request.state, "duration_ms", 0)

    return {
        "lead_id": lead_id,
        "slug": slug,
        "previous_status": previous_status,
        "new_status": new_status,
        "site_config_path": site_config_path,
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }
