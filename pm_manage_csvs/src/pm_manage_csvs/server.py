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
    _within_entry_date,
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
from pm_manage_csvs.sheets import update_rows as sheets_update_rows

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


def _unwrap_rows_param(rows: Any) -> list[dict[str, Any]]:
    """Normalize the `rows` argument from a model into `list[dict]`.

    The pm agent has a known schema-amnesia pattern: after several turns
    of conversation it sometimes emits `rows={"item": {...}}` (a dict
    with key "item") or `rows={...}` (a single dict) instead of the
    expected `rows=[{...}]` (a list with one dict). This is a model-side
    wrap-object hallucination that pydantic would otherwise reject with
    "Input should be a valid list".

    This helper is the server-side defense: it accepts the common wrong
    shapes and silently unwraps them, so the agent's call still succeeds.
    Logged so we can see when the model is misbehaving.

    Accepts:
        - `[{...}, {...}]` (the happy path)
        - `{"item": {...}}` → `[<item>]` (the wrap hallucination)
        - `{"item": [{...}, {...}]}` → `[<items>]`
        - `{...}` (a single dict, no "item" key) → `[<dict>]`

    Rejects:
        - Anything that doesn't fit the above patterns (empty list is
          fine; the upstream `validate_rows` checks emptiness downstream).
    """
    if isinstance(rows, list):
        return rows

    if isinstance(rows, dict):
        # The two known hallucination shapes.
        if "item" in rows and isinstance(rows["item"], (dict, list)):
            inner = rows["item"]
            if isinstance(inner, dict):
                _log.info(
                    "unwrap_rows_param",
                    reason="item_wrapper_single",
                    count=1,
                )
                return [inner]
            # inner is a list
            _log.info(
                "unwrap_rows_param",
                reason="item_wrapper_list",
                count=len(inner),
            )
            return inner

        # Single-dict hallucination: no "item" key, but the whole thing is
        # a row. Heuristic: it's a flat dict (no nested dicts/lists of
        # rows) — the row schema columns.
        if all(isinstance(v, (str, type(None))) for v in rows.values()):
            _log.info(
                "unwrap_rows_param",
                reason="single_dict",
                count=1,
            )
            return [rows]

        raise PMCError(
            f"cannot unwrap rows: got dict but no 'item' key and values "
            f"don't look like a flat row: keys={list(rows.keys())[:5]}"
        )

    raise PMCError(
        f"cannot unwrap rows: expected list or dict, got {type(rows).__name__}"
    )


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
def read_csv(csv_name: str, max_rows: int = 1000, days: int = 30) -> list[dict[str, str]]:
    """Read all rows from a CSV. Header row (row 1) is excluded.

    For maintenance/events: filtered to last `days` days (0 = all time).
    For tenants/properties/vendors: `days` is ignored (always read everything).
    """
    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    rows = read_sheet(sheet_id, csv_name, max_rows=max_rows)
    schema = _get_schema_obj(csv_name)
    today = date.today()
    rows = [r for r in rows if _within_entry_date(r, schema, days, today)]
    _log.info("read_csv", csv=csv_name, days=days, rows=len(rows))
    return rows


@mcp.tool()
@_envelope("read_csv_rows")
def read_csv_rows(
    csv_name: str, filters: dict[str, Any], days: int = 30
) -> list[dict[str, str]]:
    """Read rows from a CSV matching all key=value filters (exact match).

    For maintenance/events: also restricted to last `days` days.
    For tenants/properties/vendors: `days` is ignored.
    """
    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    rows = read_sheet(sheet_id, csv_name)
    schema = _get_schema_obj(csv_name)
    today = date.today()
    matched = [
        r
        for r in rows
        if _matches_filters(r, filters) and _within_entry_date(r, schema, days, today)
    ]
    _log.info("read_csv_rows", csv=csv_name, filters=filters, days=days, matched=len(matched))
    return matched


@mcp.tool()
@_envelope("count_csv_rows")
def count_csv_rows(
    csv_name: str, filters: dict[str, Any] | None = None, days: int = 30
) -> int:
    """Count rows, optionally filtered. For maintenance/events: also restricted to last `days` days."""
    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    rows = read_sheet(sheet_id, csv_name)
    schema = _get_schema_obj(csv_name)
    today = date.today()
    if filters:
        rows = [r for r in rows if _matches_filters(r, filters)]
    return sum(1 for r in rows if _within_entry_date(r, schema, days, today))


@mcp.tool()
@_envelope("list_open_maintenance")
def list_open_maintenance(
    owner: str | None = None,
    property: str | None = None,
    days: int = 0,
) -> list[str]:
    """Return open maintenance items, grouped by owner with global numbering.

    Format:
        <owner>:
        N. Property [Unit] — Issue (Nd, row=R)        ← one per open item

        <owner>:
        ...

        Notes: #N is today, #M is overdue              ← only if applicable

    - N: display index, 1-indexed, global across owner groups
    - row=R: Sheet row for update_row("maintenance", R, {...})
    - "(today)" or "(Nd)": age marker; "(today)" highlights critical items
    - Filters re-enumerate (#1 = oldest matching item)
    - Always re-list before any update — statuses change.

    Args:
        owner: optional filter (case-insensitive) - "tuan" | "chris" | "sharon" | "vendor"
        property: optional filter - exact-match property name
    """
    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(
        folder_id, "maintenance", filename_override=_get_filename_override("maintenance")
    )
    rows = read_sheet(sheet_id, "maintenance")

    today = date.today()

    # Pair each row with its 1-indexed Sheet row (header is row 1, data starts at 2)
    all_indexed = [(i, r) for i, r in enumerate(rows, start=2)]
    open_indexed = [(i, r) for i, r in all_indexed if r.get("status", "").strip().lower() == "open"]
    if owner:
        open_indexed = [
            (i, r)
            for i, r in open_indexed
            if r.get("owner", "").strip().lower() == owner.strip().lower()
        ]
    if property:
        open_indexed = [
            (i, r)
            for i, r in open_indexed
            if r.get("property", "").strip() == property.strip()
        ]
    # Date filter — default 0 (no filter) since list_open_maintenance is exhaustive
    # For "recent only" views, use read_csv_rows("maintenance", filters={"status":"open"}, days=N)
    if days > 0:
        maintenance_schema = _get_schema_obj("maintenance")
        open_indexed = [
            (i, r) for i, r in open_indexed if _within_entry_date(r, maintenance_schema, days, today)
        ]

    def _days(start: str) -> int:
        try:
            return (today - datetime.strptime(start, "%Y-%m-%d").date()).days
        except (ValueError, TypeError):
            return 0

    owner_order = {"tuan": 0, "chris": 1, "sharon": 2, "vendor": 3}
    open_indexed.sort(
        key=lambda pair: (
            owner_order.get(pair[1].get("owner", ""), 99),
            pair[1].get("start_date", "9999-99-99"),
        )
    )

    def _format_item(n: int, sheet_row: int, r: dict[str, str]) -> str:
        prop = r.get("property", "?")
        unit = r.get("unit", "")
        issue = r.get("issue", "")
        start = r.get("start_date", "")
        days = _days(start)
        if days == 0 and start:
            age_marker = "today"
        elif days < 0:
            # Future-dated event: "in 3d" instead of the misleading "-3d"
            age_marker = f"in {-days}d"
        else:
            age_marker = f"{days}d"
        unit_str = f", {unit}" if unit else ""
        return f"{n}. {prop}{unit_str} — {issue} ({age_marker}, row={sheet_row})"

    lines: list[str] = []
    today_numbers: list[int] = []
    current_owner: str | None = None
    for n, (sheet_row, r) in enumerate(open_indexed, start=1):
        owner_v = r.get("owner", "?")
        if owner_v != current_owner:
            if current_owner is not None:
                lines.append("")  # blank line between owner groups
            current_owner = owner_v
            lines.append(f"{owner_v.upper()}:")
        lines.append(f"  {_format_item(n, sheet_row, r)}")
        if r.get("start_date", "") == today.strftime("%Y-%m-%d"):
            today_numbers.append(n)

    if today_numbers:
        lines.append("")
        if len(today_numbers) == 1:
            lines.append(f"Notes: #{today_numbers[0]} is today")
        else:
            nums = ", #".join(str(x) for x in today_numbers)
            lines.append(f"Notes: #{nums} are today")

    _log.info(
        "list_open_maintenance",
        count=len(open_indexed),
        owner=owner,
        property=property,
        today_count=len(today_numbers),
    )
    return lines


@mcp.tool()
@_envelope("append_rows")
def append_rows(
    csv_name: str, rows: list[dict[str, Any]] | dict[str, Any]
) -> list[int]:
    """Append one or more rows. Returns the row indices (1-indexed) of appended rows.

    Auto-fills `created_at` on maintenance. Validates headers, enums, and dates.

    **Even for a single row, `rows` MUST be a list of length 1, e.g.
    `rows=[{...}]`. A single-row append is not a single dict — it is a
    list of one item.** This framing is what the rest of the codebase
    assumes; sending a single dict (or `{"item": {...}}`) will fail with
    "Input should be a valid list".

    The `rows` parameter is a LIST of dicts — one dict per row to append.
    Use this exact shape:

        mcp_pm_manage_csvs_append_rows(
            csv_name="maintenance",
            rows=[
                {
                    "property": "2755 Oakmont",
                    "unit": "A2",
                    "owner": "sharon",
                    "start_date": "2026-09-11",
                    "end_date": "",
                    "issue": "Insurance inspection 9:30am",
                    "status": "open",
                    "notes": "",
                }
            ]
        )

    The above call appends a single row (a "list of one") at the next
    free row in the maintenance sheet. To append multiple rows, add
    more dicts to the list — same shape, same tool.

    **Server-side defensive unwrapping.** The pm agent has a known
    schema-amnesia pattern where after many turns of conversation it
    emits `rows={"item": {...}}` (a dict with key "item") or
    `rows={...}` (a single dict) instead of the expected list shape.
    These common wrap-pattern hallucinations are silently unwrapped
    server-side — the call still succeeds, and the unwrap is logged
    so we can see when the model is misbehaving.

    Common mistake: a single-row append (a "list of one") looks like
    `rows=[{...}]` (a list with one dict), NOT `rows={...}` (a single
    dict) and NOT `rows={"item": {...}}` (a dict with key "item").
    The latter two are tolerated but should be considered a bug in
    the calling agent — they're unwrapped automatically.
    Call `mcp_pm_manage_csvs_get_schema(csv_name="maintenance")` first
    if you're unsure of the column names — never guess.
    """
    rows = _unwrap_rows_param(rows)
    validate_rows(csv_name, rows)
    rows = apply_auto_fills(csv_name, rows, is_update=False)
    schema = _get_schema_obj(csv_name)
    values = [row_to_list(schema, r) for r in rows]

    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    indices = sheets_append_rows(sheet_id, csv_name, values)
    _log.info("append_rows", csv=csv_name, count=len(rows), indices=indices)
    if len(rows) == 1:
        # Grep-able breadcrumb so logs and human readers can tell
        # single-row ("list of one") from multi-row at a glance.
        _log.info(
            "append_rows_single",
            csv=csv_name,
            index=indices[0],
            framed_as="list_of_one",
        )
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


@mcp.tool(name="bulk_update_rows")
@_envelope("update_rows")
def update_rows(
    csv_name: str,
    row_indices: list[int],
    values: dict[str, str],
) -> dict[str, Any]:
    """Bulk update — apply the same `values` to multiple rows in one Sheets API call.

    SCHEMA (multi-row):
        csv_name   (str):       target sheet (e.g. "maintenance")
        row_indices (list[int]): 1-indexed Sheet row numbers, e.g. [3, 5, 7]
        values     (dict[str,str]): field→value map applied to ALL rows, e.g. {"status": "done"}

    Use this when the SAME change applies to N rows (e.g. "mark #2, #4, #7 done").
    Token-efficient: 1 tool call instead of N. Same shape on every row.

    For ONE row only, use `update_row(row_index=int)` instead.

    Refuses to overwrite primary-key columns. Auto-fills `completed_at` on
    maintenance when status flips to 'done'.
    """
    schema = _get_schema_obj(csv_name)
    if not row_indices:
        raise PMCError("row_indices must be non-empty")
    if not values:
        raise PMCError("values must be non-empty")
    forbidden = set(schema.primary_key)
    bad = forbidden & set(values.keys())
    if bad:
        raise PMCError(f"cannot update primary-key columns: {sorted(bad)}")

    # Coerce values once, then apply to all rows
    coerced: dict[str, str] = {}
    for header, value in values.items():
        col = schema.get_column(header)
        coerced[header] = col.coerce(value)
    coerced_dict = apply_auto_fills(csv_name, [coerced], is_update=True)[0]
    coerced = {k: v for k, v in coerced_dict.items() if k in coerced or k == "completed_at"}

    folder_id = _get_folder_id()
    sheet_id = resolve_sheet_id(folder_id, csv_name, filename_override=_get_filename_override(csv_name))
    sheets_update_rows(sheet_id, csv_name, row_indices, coerced)
    _log.info(
        "update_rows",
        csv=csv_name,
        row_count=len(row_indices),
        fields=sorted(coerced.keys()),
    )
    return {
        "ok": True,
        "rows_updated": list(row_indices),
        "values_applied": coerced,
    }


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
