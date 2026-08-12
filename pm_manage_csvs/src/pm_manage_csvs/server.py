"""MCP server: tool handlers for pm_manage_csvs.

The 8 tools exposed (auto-prefixed `mcp_pm_manage_csvs_*` by Hermes):
- list_csvs
- get_schema
- read_csv
- read_csv_rows
- count_csv_rows
- list_open_maintenance  (token-optimized: returns compact text lines)
- append_rows
- update_row

All errors are caught at the tool boundary and returned as
`{"error": ..., "detail": ...}` envelopes rather than raised.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from mcp.server.fastmcp import FastMCP

from pm_manage_csvs.drive import resolve_sheet_id
from pm_manage_csvs.errors import PMCError
from pm_manage_csvs.logging import configure_logging, get_logger
from pm_manage_csvs.schema import (
    SCHEMAS,
    apply_auto_fills,
    row_to_list,
    validate_rows,
)
from pm_manage_csvs.schema import (
    get_schema as _get_schema_obj,
)
from pm_manage_csvs.sheets import append_rows as sheets_append_rows
from pm_manage_csvs.sheets import read_sheet
from pm_manage_csvs.sheets import update_row as sheets_update_row

mcp = FastMCP("pm_manage_csvs")
_log = get_logger("pm_manage_csvs.server")

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "sheets.yaml"
_CONFIG: dict[str, Any] = {}


def _load_config() -> dict[str, Any]:
    """Load config/sheets.yaml once at startup. Cached at module level."""
    global _CONFIG
    if _CONFIG:
        return _CONFIG
    if not _CONFIG_PATH.exists():
        _CONFIG = {}
        return _CONFIG
    _CONFIG = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
    return _CONFIG


def _get_folder_id() -> str:
    cfg = _load_config()
    folder_id = (cfg.get("folder_id") or "").strip()
    if not folder_id or folder_id == "PASTE_FOLDER_ID_HERE":
        raise PMCError(
            "folder_id not configured: edit config/sheets.yaml and paste your Drive folder ID"
        )
    return folder_id


def _get_filename_override(logical_name: str) -> str | None:
    cfg = _load_config()
    sheets_map = cfg.get("sheets") or {}
    name = sheets_map.get(logical_name)
    if isinstance(name, str) and name.strip():
        return name
    return None


def _envelope(tool_name: str):
    """Decorator that wraps a tool body with try/except PMCError."""

    def decorator(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except PMCError as exc:
                _log.warning("tool_error", tool=tool_name, error=type(exc).__name__, detail=str(exc))
                return {"error": type(exc).__name__, "detail": str(exc)}
            except Exception as exc:
                import traceback
                _log.error(
                    "tool_unexpected",
                    tool=tool_name,
                    exc_type=type(exc).__name__,
                    detail=str(exc),
                    traceback=traceback.format_exc(),
                )
                return {"error": "UnexpectedError", "detail": str(exc)}

        wrapper.__name__ = fn.__name__
        return wrapper

    return decorator


def _matches_filters(row: dict[str, str], filters: dict[str, Any]) -> bool:
    return all(row.get(k) == str(v) for k, v in filters.items())


# ── Tools ─────────────────────────────────────────────────────────────────


@mcp.tool()
@_envelope("list_csvs")
def list_csvs() -> list[str]:
    """Return the 5 logical CSV names this server manages."""
    return sorted(SCHEMAS.keys())


@mcp.tool()
@_envelope("get_schema")
def get_schema(csv_name: str) -> dict[str, Any]:
    """Return headers, types, primary key, and enum constraints for a CSV type."""
    schema = _get_schema_obj(csv_name)
    return {
        "name": schema.name,
        "headers": schema.headers,
        "primary_key": list(schema.primary_key),
        "columns": [
            {
                "header": c.header,
                "type": c.type,
                "required": c.required,
                "enum": list(c.enum) if c.enum else None,
            }
            for c in schema.columns
        ],
    }


@mcp.tool()
@_envelope("read_csv")
def read_csv(csv_name: str, max_rows: int = 1000) -> list[dict[str, str]]:
    """Read all rows from a CSV. Header row (row 1) is excluded."""
    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    rows = read_sheet(sheet_id, csv_name, max_rows=max_rows)
    _log.info("read_csv", csv=csv_name, rows=len(rows))
    return rows


@mcp.tool()
@_envelope("read_csv_rows")
def read_csv_rows(csv_name: str, filters: dict[str, Any]) -> list[dict[str, str]]:
    """Read rows from a CSV matching all key=value filters (exact match)."""
    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    rows = read_sheet(sheet_id, csv_name)
    matched = [r for r in rows if _matches_filters(r, filters)]
    _log.info("read_csv_rows", csv=csv_name, filters=filters, matched=len(matched))
    return matched


@mcp.tool()
@_envelope("count_csv_rows")
def count_csv_rows(csv_name: str, filters: dict[str, Any] | None = None) -> int:
    """Count rows, optionally filtered. Returns int."""
    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    rows = read_sheet(sheet_id, csv_name)
    if filters:
        rows = [r for r in rows if _matches_filters(r, filters)]
    return len(rows)


@mcp.tool()
@_envelope("list_open_maintenance")
def list_open_maintenance(
    owner: str | None = None,
    property: str | None = None,
) -> list[str]:
    """Return open maintenance items as compact grouped text lines.

    Token-optimized alternative to read_csv_rows for the common
    "what are the open maintenance items?" query. Roughly 70% fewer
    chars than the JSON path for the same data.

    Each line: "[owner] Property [Unit] - Issue (since YYYY-MM-DD, Nd ago)"
    Grouped by owner (tuan, chris, sharon, vendor), oldest-first within group.
    Empty list if no matches.

    Args:
        owner: optional filter (case-insensitive) - "tuan" | "chris" | "sharon" | "vendor"
        property: optional filter - exact-match property name
    """
    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(
        folder_id, "maintenance", filename_override=_get_filename_override("maintenance")
    )
    rows = read_sheet(sheet_id, "maintenance")
    open_rows = [r for r in rows if r.get("status", "").strip().lower() == "open"]
    if owner:
        open_rows = [
            r for r in open_rows if r.get("owner", "").strip().lower() == owner.strip().lower()
        ]
    if property:
        open_rows = [
            r for r in open_rows if r.get("property", "").strip() == property.strip()
        ]

    today = date.today()

    def _days(start: str) -> int:
        try:
            return (today - datetime.strptime(start, "%Y-%m-%d").date()).days
        except (ValueError, TypeError):
            return 0

    def _format_row(r: dict[str, str]) -> str:
        owner_v = r.get("owner", "?")
        prop = r.get("property", "?")
        unit = r.get("unit", "")
        issue = r.get("issue", "")
        start = r.get("start_date", "")
        age = f" (since {start}, {_days(start)}d)" if start else ""
        unit_str = f" {unit}" if unit else ""
        return f"[{owner_v}] {prop}{unit_str} - {issue}{age}"

    owner_order = {"tuan": 0, "chris": 1, "sharon": 2, "vendor": 3}
    open_rows.sort(
        key=lambda r: (
            owner_order.get(r.get("owner", ""), 99),
            r.get("start_date", "9999-99-99"),
        )
    )

    lines = [_format_row(r) for r in open_rows]
    _log.info("list_open_maintenance", count=len(lines), owner=owner, property=property)
    return lines


@mcp.tool()
@_envelope("append_rows")
def append_rows(csv_name: str, rows: list[dict[str, Any]]) -> list[int]:
    """Append one or more rows. Returns the row indices (1-indexed) of appended rows.

    Auto-fills `created_at` on maintenance. Validates headers, enums, and dates.
    """
    validate_rows(csv_name, rows)
    rows = apply_auto_fills(csv_name, rows, is_update=False)
    schema = _get_schema_obj(csv_name)
    values = [row_to_list(schema, r) for r in rows]

    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    indices = sheets_append_rows(sheet_id, csv_name, values)
    _log.info("append_rows", csv=csv_name, count=len(rows), indices=indices)
    return indices


@mcp.tool()
@_envelope("update_row")
def update_row(csv_name: str, row_index: int, values: dict[str, str]) -> dict[str, Any]:
    """Overwrite specific cells in an existing row (1-indexed row_index).

    Refuses to overwrite the primary key columns (would shift row identity).
    Auto-fills `completed_at` on maintenance when status flips to 'done'.
    """
    schema = _get_schema_obj(csv_name)
    forbidden = set(schema.primary_key)
    bad = forbidden & set(values.keys())
    if bad:
        raise PMCError(f"cannot update primary-key columns: {sorted(bad)}")

    # Coerce values via schema, then re-apply auto-fills for completed_at
    coerced: dict[str, str] = {}
    for header, value in values.items():
        col = schema.get_column(header)
        coerced[header] = col.coerce(value)
    coerced_dict = apply_auto_fills(csv_name, [coerced], is_update=True)[0]
    coerced = {k: v for k, v in coerced_dict.items() if k in coerced or k == "completed_at"}

    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    sheets_update_row(sheet_id, csv_name, row_index, coerced)
    _log.info("update_row", csv=csv_name, row=row_index, fields=sorted(coerced.keys()))
    return {"ok": True, "row": row_index, "updated_fields": sorted(coerced.keys())}


# ── Startup ───────────────────────────────────────────────────────────────


def main() -> None:
    configure_logging()
    _log.info("server_starting", config_path=str(_CONFIG_PATH))
    # Eager-load config so missing folder_id fails fast at startup
    folder_id = _get_folder_id()
    _log.info("server_ready", folder_id=folder_id, csvs=sorted(SCHEMAS.keys()))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
