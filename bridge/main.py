"""FastAPI bridge entrypoint."""

import uvicorn
from fastapi import FastAPI

from bridge.config import BRIDGE_HOST, BRIDGE_PORT
from bridge.middleware import add_middleware
from bridge.error_handlers import register_error_handlers
from bridge.routes import health, leads, curated, builder, extract, curator, critic

app = FastAPI(title="KURSOR Bridge", version="0.1.0")

add_middleware(app)
register_error_handlers(app)

app.include_router(health.router, prefix="/v1")
app.include_router(leads.router, prefix="/v1")
app.include_router(curated.router, prefix="/v1")
app.include_router(builder.router, prefix="/v1")
app.include_router(extract.router, prefix="/v1")
app.include_router(curator.router, prefix="/v1")
app.include_router(critic.router, prefix="/v1")


@app.get("/")
async def root():
    """Root endpoint for quick check."""
    return {"message": "KURSOR Bridge API", "version": "0.1.0"}


if __name__ == "__main__":
    uvicorn.run("bridge.main:app", host=BRIDGE_HOST, port=BRIDGE_PORT, reload=False)
