"""
Pydantic schemas for structured audit_logs.details payloads.

Each action type maps to a dedicated schema.  Validation happens at write
time in db/database._log_action — old rows in the DB remain parseable
as raw JSON, and new rows are guaranteed to match one of the schemas below.
"""

from pydantic import BaseModel, Field
from typing import Any


class LogCreated(BaseModel):
    """action='created': a new lead was upserted."""

    name: str
    website: str | None = None
    status: str
    category: str


class LogStatusChanged(BaseModel):
    """action='status_changed': lead status transition.

    Serialises ``from_status`` → ``"from"`` so the JSON stored in the DB
    matches the conventional ``{"from": …, "to": …}`` shape.
    """

    from_status: str = Field(alias="from")
    to: str


LogDetails = LogCreated | LogStatusChanged | dict[str, Any]
"""Union of all known detail shapes + free-form dict for ad-hoc actions."""
