"""Health check endpoint."""

from pathlib import Path
from fastapi import APIRouter, Request
from google.auth import default as google_auth_default
from google.auth.exceptions import DefaultCredentialsError

from config import settings
from bridge.error_handlers import BridgeError

router = APIRouter()


@router.get("/health")
async def health_check():
    """Readiness check for bridge infrastructure."""
    checks = {}

    # Check database file
    db_path = Path(settings.db_path)
    if db_path.exists():
        checks["db"] = "ok"
    else:
        checks["db"] = f"fail: db file not found at {settings.db_path}"

    # Check Vertex ADC credentials
    try:
        google_auth_default()
        checks["vertex_adc"] = "ok"
    except DefaultCredentialsError:
        checks["vertex_adc"] = "fail: run gcloud auth application-default login"

    # Check neuralsync root
    neuralsync_path = Path(settings.neuralsync_root)
    if neuralsync_path.exists():
        checks["neuralsync_root"] = "ok"
    else:
        checks["neuralsync_root"] = f"fail: path not found at {settings.neuralsync_root}"

    # Determine overall status
    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "version": "0.1.0",
    }
