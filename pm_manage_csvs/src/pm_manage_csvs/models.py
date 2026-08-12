"""Pydantic models for typed row records.

These are runtime-only types — not persisted. They mirror the schema
defined in schema.py and exist to validate input at the MCP tool boundary.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Property(_Base):
    address: str
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    units: str | None = None
    notes: str | None = None


class Tenant(_Base):
    property: str
    unit: str
    tenant_name: str
    phone: str | None = None
    email: str | None = None
    lease_start: _date | None = None
    lease_end: _date | None = None
    status: Literal["active", "notice", "moved_out"] | None = None
    notes: str | None = None


class Vendor(_Base):
    name: str
    role: str | None = None
    phone: str | None = None
    email: str | None = None
    telegram_id: str | None = None
    active: bool | None = None
    notes: str | None = None

    @field_validator("active", mode="before")
    @classmethod
    def _coerce_bool(cls, v: object) -> object:
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("true", "1", "yes"):
                return True
            if s in ("false", "0", "no"):
                return False
        return v


class Maintenance(_Base):
    property: str
    unit: str | None = None
    owner: Literal["tuan", "chris", "sharon", "vendor"] | None = None
    start_date: _date | None = None
    end_date: _date | None = None
    issue: str
    status: Literal["open", "in_progress", "done", "cancelled"] | None = None
    notes: str | None = None
    created_at: _date | None = None
    completed_at: _date | None = None


class Event(_Base):
    date: _date
    property: str | None = None
    unit: str | None = None
    event_type: (
        Literal[
            "shra_inspection",
            "move_in",
            "move_out",
            "walk_through",
            "notice_served",
            "eviction",
            "other",
        ]
        | None
    ) = None
    description: str
    status: Literal["scheduled", "completed", "cancelled", "pending_result"] | None = None
    notes: str | None = None


MODELS = {
    "properties": Property,
    "tenants": Tenant,
    "vendors": Vendor,
    "maintenance": Maintenance,
    "events": Event,
}
