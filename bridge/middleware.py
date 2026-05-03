"""Middleware for trace_id, duration_ms, and request logging."""

import time
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from config import settings


LOG_FILE = Path(__file__).parent.parent / "logs" / "bridge.jsonl"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


class TraceMiddleware(BaseHTTPMiddleware):
    """Injects trace_id, measures duration, logs requests."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        # Generate trace_id
        trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id

        # Start timer
        start_time = time.time()

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)

        # Add trace_id to response header
        response.headers["X-Trace-ID"] = trace_id

        # Add duration to response state (for endpoints to include in JSON)
        request.state.duration_ms = duration_ms

        # Log request
        log_entry = {
            "trace_id": trace_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        return response


def add_middleware(app):
    """Register middleware with FastAPI app."""
    app.add_middleware(TraceMiddleware)
