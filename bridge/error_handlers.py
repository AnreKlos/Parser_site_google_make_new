"""Global error handlers with unified error format."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError


class BridgeError(Exception):
    """Custom base exception for bridge errors."""

    def __init__(self, code: str, message: str, details: dict = None, status_code: int = 400):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)


def register_error_handlers(app: FastAPI):
    """Register all error handlers with FastAPI app."""

    @app.exception_handler(BridgeError)
    async def bridge_error_handler(request: Request, exc: BridgeError):
        duration_ms = getattr(request.state, "duration_ms", 0)
        trace_id = getattr(request.state, "trace_id", "unknown")

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
                "trace_id": trace_id,
                "duration_ms": duration_ms,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        duration_ms = getattr(request.state, "duration_ms", 0)
        trace_id = getattr(request.state, "trace_id", "unknown")

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Ошибка валидации запроса",
                    "details": {"errors": exc.errors()},
                },
                "trace_id": trace_id,
                "duration_ms": duration_ms,
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        duration_ms = getattr(request.state, "duration_ms", 0)
        trace_id = getattr(request.state, "trace_id", "unknown")

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": str(exc),
                    "details": {},
                },
                "trace_id": trace_id,
                "duration_ms": duration_ms,
            },
        )
