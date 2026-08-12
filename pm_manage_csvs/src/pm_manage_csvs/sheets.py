"""Sheets API wrapper: read/append/update operations."""

from __future__ import annotations

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
    """
    from pm_manage_csvs.drive import get_sheets_service

    if csv_name not in SCHEMAS:
        raise ValueError(f"unknown csv_name: {csv_name}")
    schema = SCHEMAS[csv_name]
    last_col = schema.columns[-1].col_letter

    svc = get_sheets_service(credentials_path)
    rng = f"A:{last_col}"

    try:
        resp = (
            svc.spreadsheets()
            .values()
            .append(
                spreadsheetId=sheet_id,
                range=rng,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
            .execute()
        )
    except HttpError as exc:
        if _is_rate_limit(exc):
            raise APIRateLimitError(f"Drive rate limit hit appending to {csv_name}") from exc
        raise

    # Parse "updates" to find row numbers; API returns updatedRange like "Sheet1!A5:J5"
    updated_range = resp.get("updates", {}).get("updatedRange", "")
    rows_added: list[int] = []
    if "!" in updated_range and ":" in updated_range:
        # Extract first row number from the range
        # e.g. "Sheet1!A5:J7" → 5
        cell_ref = updated_range.split("!", 1)[1]
        first_cell = cell_ref.split(":", 1)[0]
        digits = "".join(c for c in first_cell if c.isdigit())
        if digits:
            start_row = int(digits)
            rows_added = list(range(start_row, start_row + len(rows)))
    return rows_added


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
