"""Curator endpoint: POST /v1/curator/run."""

import asyncio
from google.auth import default as google_auth_default
from google.auth.exceptions import DefaultCredentialsError
from fastapi import APIRouter, Request

from db.database import get_lead_by_id
from llm.curator import curate_lead
from bridge.error_handlers import BridgeError

router = APIRouter()


@router.post("/curator/run")
async def run_curator(request: Request, body: dict):
    """Run LLM curation for a lead."""
    lead_id = body.get("lead_id")
    if lead_id is None:
        raise BridgeError(
            code="invalid_request",
            message="Missing lead_id in request body",
            status_code=400,
        )

    # Check ADC credentials
    try:
        google_auth_default()
    except DefaultCredentialsError:
        raise BridgeError(
            code="infra_misconfigured",
            message="Не настроены Application Default Credentials",
            details={"hint": "run gcloud auth application-default login"},
            status_code=503,
        )

    lead = await get_lead_by_id(lead_id)
    if not lead:
        raise BridgeError(
            code="lead_not_found",
            message=f"Лид с ID={lead_id} не найден в БД",
            details={"lead_id": lead_id},
            status_code=404,
        )

    try:
        result = await asyncio.wait_for(curate_lead(lead_id), timeout=90.0)
    except asyncio.TimeoutError:
        raise BridgeError(
            code="upstream_timeout",
            message="Curator timeout (90s)",
            status_code=504,
        )
    except Exception as exc:
        raise BridgeError(
            code="upstream_error",
            message=str(exc),
            status_code=502,
        )

    if result is None:
        raise BridgeError(
            code="upstream_error",
            message="Curator returned None",
            status_code=502,
        )

    data = result.get("data", {})
    trace_id = getattr(request.state, "trace_id", "unknown")
    duration_ms = getattr(request.state, "duration_ms", 0)

    return {
        "lead_id": lead_id,
        "slug": result.get("slug"),
        "output_path": result.get("output_path"),
        "summary": {
            "tagline_chars": len(data.get("meta", {}).get("tagline", "")),
            "about_chars": len(data.get("about", "")),
            "reviews_count": len(data.get("reviews", [])),
            "faq_count": len(data.get("faq", [])),
            "services_count": len(data.get("services", [])),
        },
        "model": "gemini-2.5-flash-lite",
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }
