"""Sheets API wrapper: read/append/update operations."""

from __future__ import annotations

from typing import Any

from googleapiclient.errors import HttpError

from pm_manage_csvs.errors import APIRateLimitError
from pm_manage_csvs.schema import SCHEMAS, list_to_row


def _is_rate_limit(err: HttpError) -> bool:
    return err.resp.status in (429, 503)


def read_sheet(
    sheet_id: str,
    csv_name: str,
    *,
    max_rows: int = 1000,
    credentials_path: str | None = None,
) -> list[dict[str, str]]:
    """Read rows from a Sheet, returning a list of dicts aligned to schema columns.

    Skips the header row (row 1). Returns up to max_rows data rows.
    """
    from pm_manage_csvs.drive import get_sheets_service

    if csv_name not in SCHEMAS:
        raise ValueError(f"unknown csv_name: {csv_name}")
    schema = SCHEMAS[csv_name]

    svc = get_sheets_service(credentials_path)
    end_row = max_rows + 1  # +1 for header row
    rng = f"A2:{schema.columns[-1].col_letter}{end_row}"

    try:
        resp = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=rng, valueRenderOption="UNFORMATTED_VALUE")
            .execute()
        )
    except HttpError as exc:
        if _is_rate_limit(exc):
            raise APIRateLimitError(f"Drive rate limit hit reading {csv_name}") from exc
        raise

    raw_rows = resp.get("values", [])
    return [list_to_row(schema, list(r) + [""] * (len(schema.columns) - len(r))) for r in raw_rows]


def append_rows(
    sheet_id: str,
    csv_name: str,
    rows: list[list[str]],
    *,
    credentials_path: str | None = None,
) -> list[int]:
    """Append `rows` to the Sheet, returning the 1-indexed row numbers of appended rows.

    Each row in `rows` must already be aligned to the schema columns.

    Both single-row and multi-row appends go through the same path:
    `_find_first_free_row` walks column A to locate the end of the contiguous
    data block, then `values().update()` writes all rows starting at the
    first free row in one call. This is deliberately NOT `values().append()`:
    that endpoint's table-boundary detection is a black box and has known edge
    cases — silent single-row drops (logs/08-25-26/BUG-mcp-append-single-row.md)
    and polluted-tail misplacement (logs/09-08-26/BUG-row1002-contiguous-block.md).
    """
    from pm_manage_csvs.drive import get_sheets_service

    if csv_name not in SCHEMAS:
        raise ValueError(f"unknown csv_name: {csv_name}")
    schema = SCHEMAS[csv_name]
    last_col = schema.columns[-1].col_letter

    if not rows:
        return []

    svc = get_sheets_service(credentials_path)

    target_row = _find_first_free_row(svc, sheet_id, csv_name)
    end_row = target_row + len(rows) - 1
    rng = f"A{target_row}:{last_col}{end_row}"
    try:
        (
            svc.spreadsheets()
            .values()
            .update(
                spreadsheetId=sheet_id,
                range=rng,
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            )
            .execute()
        )
    except HttpError as exc:
        if _is_rate_limit(exc):
            raise APIRateLimitError(f"Drive rate limit hit appending to {csv_name}") from exc
        raise

    return list(range(target_row, end_row + 1))


def _find_first_free_row(svc: Any, sheet_id: str, csv_name: str) -> int:
    """Return the first empty row after the schema header (row 1).

    Walks column A and breaks at the first empty cell. Critically, this
    finds the END OF THE CONTIGUOUS DATA BLOCK, not the last filled row in
    the read window — if the sheet has stray data past an empty gap (e.g.
    orphan rows from a prior shift), `last_filled+1` would skip past the
    gap and place new rows far from the real data table.

    Real-world hit (2026-09-08): orphan rows at 1000/1001 in the maintenance
    sheet caused the next append to land at row 1002 instead of row 56.
    """
    try:
        col_a_resp = (
            svc.spreadsheets()
            .values()
            .get(
                spreadsheetId=sheet_id,
                range="A:A",
                valueRenderOption="UNFORMATTED_VALUE",
            )
            .execute()
        )
    except HttpError as exc:
        if _is_rate_limit(exc):
            raise APIRateLimitError(
                f"Drive rate limit hit reading {csv_name} for append"
            ) from exc
        raise

    # Row 1 is always the schema header; never write the data row on top of it.
    last_in_block = 1
    for i, cell in enumerate(col_a_resp.get("values", []), start=1):
        if i < 2:
            continue  # header row — skip without breaking
        if cell and any(str(c).strip() for c in cell):
            last_in_block = i
        else:
            break  # first gap after the header ends the contiguous block
    return max(last_in_block + 1, 2)


def update_row(
    sheet_id: str,
    csv_name: str,
    row_index: int,
    values: dict[str, str],
    *,
    credentials_path: str | None = None,
) -> None:
    """Overwrite cells in a specific row.

    `row_index` is 1-indexed (Sheet row number). Only columns present in `values`
    are touched — others are left intact.
    """
    from pm_manage_csvs.drive import get_sheets_service

    if csv_name not in SCHEMAS:
        raise ValueError(f"unknown csv_name: {csv_name}")
    schema = SCHEMAS[csv_name]

    svc = get_sheets_service(credentials_path)
    updates = []
    for header, value in values.items():
        col = schema.get_column(header)
        rng = f"{col.col_letter}{row_index}"
        updates.append({"range": rng, "values": [[value]]})

    if not updates:
        return

    body = {"valueInputOption": "USER_ENTERED", "data": updates}
    try:
        (
            svc.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=sheet_id, body=body)
            .execute()
        )
    except HttpError as exc:
        if _is_rate_limit(exc):
            raise APIRateLimitError(f"Drive rate limit hit updating {csv_name}") from exc
        raise


def update_rows(
    sheet_id: str,
    csv_name: str,
    row_indices: list[int],
    values: dict[str, str],
    *,
    credentials_path: str | None = None,
) -> None:
    """Apply the same `values` to multiple rows in a single Sheets API call.

    `row_indices` is a list of 1-indexed Sheet row numbers. Same semantics as
    `update_row` — only columns in `values` are touched. All rows are updated
    in a single batchUpdate HTTP request, so cost is constant regardless of
    row count.
    """
    from pm_manage_csvs.drive import get_sheets_service

    if csv_name not in SCHEMAS:
        raise ValueError(f"unknown csv_name: {csv_name}")
    if not row_indices:
        raise ValueError("row_indices must be non-empty")
    if not values:
        raise ValueError("values must be non-empty")

    schema = SCHEMAS[csv_name]
    svc = get_sheets_service(credentials_path)
    updates = []
    for row_index in row_indices:
        for header, value in values.items():
            col = schema.get_column(header)
            rng = f"{col.col_letter}{row_index}"
            updates.append({"range": rng, "values": [[value]]})

    body = {"valueInputOption": "USER_ENTERED", "data": updates}
    try:
        (
            svc.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=sheet_id, body=body)
            .execute()
        )
    except HttpError as exc:
        if _is_rate_limit(exc):
            raise APIRateLimitError(f"Drive rate limit hit updating {csv_name}") from exc
        raise
