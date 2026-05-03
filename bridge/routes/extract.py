"""Extract endpoint: POST /v1/extract/run."""

import asyncio
from fastapi import APIRouter, Request

from db.database import get_lead_by_id
from services.block_extractor import extract_all
from bridge.error_handlers import BridgeError

router = APIRouter()


@router.post("/extract/run")
async def run_extraction(request: Request, body: dict):
    """Run site extraction for a lead."""
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

    if not lead.website:
        raise BridgeError(
            code="invalid_request",
            message="У лида отсутствует website",
            details={"lead_id": lead_id},
            status_code=400,
        )

    try:
        result = await asyncio.wait_for(extract_all(lead_id), timeout=110.0)
    except asyncio.TimeoutError:
        raise BridgeError(
            code="upstream_timeout",
            message="Extraction timeout (110s)",
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
            message="Extraction returned None",
            status_code=502,
        )

    trace_id = getattr(request.state, "trace_id", "unknown")
    duration_ms = getattr(request.state, "duration_ms", 0)

    return {
        "lead_id": lead_id,
        "slug": result.get("slug"),
        "output_path": result.get("output_path"),
        "summary": {
            "services": result.get("services", 0),
            "faq": result.get("faq", 0),
            "team": len(result.get("data", {}).get("team", [])),
        },
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }
