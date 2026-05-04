"""Critic endpoint: POST /v1/critic/run."""

import asyncio
from google.auth import default as google_auth_default
from google.auth.exceptions import DefaultCredentialsError
from fastapi import APIRouter, Request

from db.database import get_lead_by_id
from llm.critic import criticize_lead
from bridge.error_handlers import BridgeError

router = APIRouter()


@router.post("/critic/run")
async def run_critic(request: Request, body: dict):
    """Run LLM critic for a lead."""
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
        result = await asyncio.wait_for(criticize_lead(lead_id), timeout=90.0)
    except asyncio.TimeoutError:
        raise BridgeError(
            code="upstream_timeout",
            message="Critic timeout (90s)",
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
            message="Critic returned None (curated file not found?)",
            status_code=502,
        )

    trace_id = getattr(request.state, "trace_id", "unknown")
    duration_ms = getattr(request.state, "duration_ms", 0)

    return {
        "lead_id": lead_id,
        "slug": result.get("slug"),
        "score": result.get("score"),
        "verdict_summary": result.get("verdict_summary"),
        "criticized_at": result.get("criticized_at"),
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }
