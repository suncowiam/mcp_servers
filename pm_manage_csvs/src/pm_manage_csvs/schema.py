"""Column definitions per CSV type. Source of truth for header validation
and enum/date coercion.

Auto-fill rules:
- `maintenance`: created_at auto-set on append; completed_at auto-set when
  status transitions to 'done'.
- `events`: `date` is required and validated as ISO 8601.

Headers are 1-indexed to match Google Sheets A1 notation; the SHEETS_HEADER_ROWS
constant indicates which row contains headers (row 1 by default).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from pm_manage_csvs.errors import ValidationError

SHEETS_HEADER_ROW = 1
ISO_DATE = "%Y-%m-%d"


@dataclass(frozen=True)
class ColumnDef:
    header: str
    col_letter: str  # A, B, C...
    col_index: int  # 0-indexed for list-of-dicts
    type: str  # "string" | "date" | "enum" | "bool" | "int"
    required: bool = False
    enum: tuple[str, ...] | None = None
    auto_fill: str | None = None  # callable name; resolved via auto_fills()

    def coerce(self, value: Any) -> str:
        """Convert a Python value into the string form Sheets expects."""
        if value is None or value == "":
            return ""
        if self.type == "date":
            if isinstance(value, date):
                return value.strftime(ISO_DATE)
            if isinstance(value, str):
                try:
                    datetime.strptime(value, ISO_DATE)
                except ValueError as exc:
                    raise ValueError(f"{self.header}: cannot coerce {value!r} to date") from exc
                return value
            raise ValueError(f"{self.header}: cannot coerce {type(value).__name__} to date")
        if self.type == "enum":
            s = str(value).strip().lower()
            if self.enum and s not in self.enum:
                raise ValueError(
                    f"{self.header}: {value!r} not in allowed values {list(self.enum)}"
                )
            return s
        if self.type == "bool":
            if isinstance(value, bool):
                return "TRUE" if value else "FALSE"
            s = str(value).strip().upper()
            if s in ("TRUE", "FALSE", "1", "0", "YES", "NO"):
                return "TRUE" if s in ("TRUE", "1", "YES") else "FALSE"
            raise ValueError(f"{self.header}: cannot coerce {value!r} to bool")
        if self.type == "int":
            return str(int(value))
        return str(value)


@dataclass(frozen=True)
class Schema:
    name: str  # logical name, e.g. "properties"
    columns: tuple[ColumnDef, ...]
    primary_key: tuple[str, ...]  # headers forming the natural key

    @property
    def headers(self) -> list[str]:
        return [c.header for c in self.columns]

    @property
    def col_letter_by_header(self) -> dict[str, str]:
        return {c.header: c.col_letter for c in self.columns}

    def get_column(self, header: str) -> ColumnDef:
        for c in self.columns:
            if c.header == header:
                return c
        raise KeyError(f"Unknown column: {header}")


# ── Schemas ──────────────────────────────────────────────────────────────

PROPERTIES = Schema(
    name="properties",
    columns=(
        ColumnDef("address", "A", 0, "string", required=True),
        ColumnDef("city", "B", 1, "string"),
        ColumnDef("state", "C", 2, "string"),
        ColumnDef("zip", "D", 3, "string"),
        ColumnDef("units", "E", 4, "string"),
        ColumnDef("notes", "F", 5, "string"),
    ),
    primary_key=("address",),
)

TENANTS = Schema(
    name="tenants",
    columns=(
        ColumnDef("property", "A", 0, "string", required=True),
        ColumnDef("unit", "B", 1, "string"),
        ColumnDef("tenant_name", "C", 2, "string", required=True),
        ColumnDef("phone", "D", 3, "string"),
        ColumnDef("email", "E", 4, "string"),
        ColumnDef("lease_start", "F", 5, "date"),
        ColumnDef("lease_end", "G", 6, "date"),
        ColumnDef("status", "H", 7, "enum", enum=("active", "notice", "moved_out")),
        ColumnDef("notes", "I", 8, "string"),
    ),
    primary_key=("property", "unit"),
)

VENDORS = Schema(
    name="vendors",
    columns=(
        ColumnDef("name", "A", 0, "string", required=True),
        ColumnDef("role", "B", 1, "string"),
        ColumnDef("phone", "C", 2, "string"),
        ColumnDef("email", "D", 3, "string"),
        ColumnDef("telegram_id", "E", 4, "string"),
        ColumnDef("active", "F", 5, "bool"),
        ColumnDef("notes", "G", 6, "string"),
    ),
    primary_key=("name",),
)

MAINTENANCE = Schema(
    name="maintenance",
    columns=(
        ColumnDef("property", "A", 0, "string", required=True),
        ColumnDef("unit", "B", 1, "string"),
        ColumnDef("owner", "C", 2, "enum", enum=("tuan", "chris", "sharon", "vendor")),
        ColumnDef("start_date", "D", 3, "date"),
        ColumnDef("end_date", "E", 4, "date"),
        ColumnDef("issue", "F", 5, "string", required=True),
        ColumnDef(
            "status", "G", 6, "enum", enum=("open", "in_progress", "done", "cancelled")
        ),
        ColumnDef("notes", "H", 7, "string"),
        ColumnDef("completed_at", "I", 8, "date", auto_fill="on_done"),
    ),
    primary_key=(),  # append-only; row index is the implicit key
)

EVENTS = Schema(
    name="events",
    columns=(
        ColumnDef("date", "A", 0, "date", required=True),
        ColumnDef("property", "B", 1, "string"),
        ColumnDef("unit", "C", 2, "string"),
        ColumnDef(
            "event_type",
            "D",
            3,
            "enum",
            enum=(
                "shra_inspection",
                "move_in",
                "move_out",
                "walk_through",
                "notice_served",
                "eviction",
                "other",
            ),
        ),
        ColumnDef("description", "E", 4, "string", required=True),
        ColumnDef(
            "status",
            "F",
            5,
            "enum",
            enum=("scheduled", "completed", "cancelled", "pending_result"),
        ),
        ColumnDef("notes", "G", 6, "string"),
    ),
    primary_key=(),  # append-only; row index is the implicit key
)


SCHEMAS: dict[str, Schema] = {
    s.name: s for s in (PROPERTIES, TENANTS, VENDORS, MAINTENANCE, EVENTS)
}


# Maps each CSV to the column used to filter rows by recency in read tools.
# None = this CSV has no date column / doesn't grow unboundedly; read everything.
ENTRY_DATE_COLUMN: dict[str, str | None] = {
    "properties": None,
    "tenants": None,
    "vendors": None,
    "maintenance": "start_date",
    "events": "date",
}


def get_schema(name: str) -> Schema:
    if name not in SCHEMAS:
        raise ValidationError(name, f"unknown csv_name (valid: {sorted(SCHEMAS)})")
    return SCHEMAS[name]


def _within_entry_date(row: dict[str, str], schema: Schema, days: int, today: date) -> bool:
    """True if the row's entry date is within the last `days`.

    - days <= 0: no filter (always True)
    - schema has no entry-date column: no filter (always True)
    - row's date is empty or unparseable: include (don't silently hide data)
    """
    if days <= 0:
        return True
    col_name = ENTRY_DATE_COLUMN.get(schema.name)
    if col_name is None:
        return True
    v = row.get(col_name, "").strip()
    if not v:
        return True
    try:
        d = datetime.strptime(v, ISO_DATE).date()
    except ValueError:
        return True
    return (today - d).days <= days


def row_to_list(schema: Schema, row: dict[str, Any]) -> list[str]:
    """Coerce a row dict (header → value) to a list of strings aligned with schema.columns."""
    out: list[str] = []
    for col in schema.columns:
        if col.header not in row:
            out.append("")  # leave blank
            continue
        out.append(col.coerce(row[col.header]))
    return out


def list_to_row(schema: Schema, values: list[str]) -> dict[str, str]:
    """Map a list of values (aligned with schema.columns) back to a dict.

    Two normalizations happen here:
    1. Cells written with USER_ENTERED may be returned as Excel serial numbers
       (e.g. 46119 for 2026-04-07). For date-typed columns, normalize those
       back to ISO 8601 strings.
    2. Cells written with USER_ENTERED auto-convert numeric-looking strings
       (e.g. "1", "100") to int/float. For string-typed columns, normalize
       those back to strings so downstream consumers don't trip type checks.
    """
    if len(values) < len(schema.columns):
        values = values + [""] * (len(schema.columns) - len(values))
    out: dict[str, str] = {}
    for col in schema.columns:
        v = values[col.col_index]
        if v in ("", None):
            out[col.header] = ""
            continue
        if col.type == "date":
            v = _normalize_date_value(v)
        elif col.type == "string" and not isinstance(v, str):
            v = str(v)
        out[col.header] = v
    return out


def _normalize_date_value(v: Any) -> str:
    """Coerce a cell value to ISO 8601 YYYY-MM-DD string.

    Handles three forms:
    - str already in ISO format → returned as-is
    - int/float Excel serial date → converted (Sheets epoch is 1899-12-30)
    - str parseable by ISO_DATE → returned as-is
    """
    if isinstance(v, str):
        try:
            datetime.strptime(v, ISO_DATE)
            return v
        except ValueError:
            return v  # unknown string form; pass through
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        # Sheets serial date: days since 1899-12-30 (Excel quirk)
        epoch = datetime(1899, 12, 30)
        return (epoch + timedelta(days=v)).strftime(ISO_DATE)
    return str(v)


def validate_rows(csv_name: str, rows: list[dict[str, Any]]) -> None:
    """Reject rows with unknown columns or missing required fields."""
    schema = get_schema(csv_name)
    valid_headers = set(schema.headers)
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValidationError(csv_name, f"row {i}: expected dict, got {type(row).__name__}")
        unknown = set(row) - valid_headers
        if unknown:
            raise ValidationError(
                csv_name, f"row {i}: unknown columns {sorted(unknown)}"
            )
        for col in schema.columns:
            if col.required and (col.header not in row or row[col.header] in ("", None)):
                raise ValidationError(csv_name, f"row {i}: missing required '{col.header}'")
            if col.header in row and row[col.header] not in ("", None):
                try:
                    col.coerce(row[col.header])
                except (ValueError, TypeError) as exc:
                    raise ValidationError(csv_name, f"row {i}: {exc}") from exc


@dataclass
class AutoFillResult:
    created_at: str | None = None
    completed_at: str | None = None


def apply_auto_fills(
    csv_name: str,
    rows: list[dict[str, Any]],
    *,
    existing_rows: list[dict[str, str]] | None = None,
    is_update: bool = False,
) -> list[dict[str, Any]]:
    """Apply schema-driven auto-fills and return the (possibly modified) rows.

    - `maintenance.created_at` set on append (today, ISO).
    - `maintenance.completed_at` set on update when status flips to `done`.
    - `events.date` left to the caller — it's required input.
    """
    out: list[dict[str, Any]] = []
    today = date.today().strftime(ISO_DATE)
    for row in rows:
        new_row = dict(row)
        if csv_name == "maintenance":
            status = (new_row.get("status") or "").strip().lower()
            if is_update and status == "done":
                new_row["completed_at"] = today
            elif is_update and status != "done":
                # Don't overwrite a completed_at when reopening
                new_row.pop("completed_at", None)
        out.append(new_row)
    return out
