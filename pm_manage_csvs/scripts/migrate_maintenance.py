"""One-shot: drop the created_at column from the maintenance Sheet.

- Read all existing maintenance data via pm_manage_csvs
- Strip the now-unused created_at key from each row
- Write new 9-column header
- Clear column J (old created_at data)
- Re-append the 11 rows

Run: GOOGLE_APPLICATION_CREDENTIALS=... uv run python scripts/migrate_maintenance.py
"""

from __future__ import annotations

import os
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

FOLDER_ID = "1QHmCCOv1cMstrIt9I5HcxQx63530GYwA"

NEW_HEADERS = [
    "property",
    "unit",
    "owner",
    "start_date",
    "end_date",
    "issue",
    "status",
    "notes",
    "completed_at",
]


def main() -> int:
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        print("ERROR: GOOGLE_APPLICATION_CREDENTIALS not set", file=sys.stderr)
        return 2

    scopes = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # Find maintenance sheet ID
    files = (
        drive.files()
        .list(
            q=f"'{FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
        .get("files", [])
    )
    sheet_id = None
    for f in files:
        if f["name"].lower() == "maintenance":
            sheet_id = f["id"]
            break
    if not sheet_id:
        print("ERROR: maintenance sheet not found", file=sys.stderr)
        return 1

    # Read existing data
    sys.path.insert(0, "/home/suncowiam/.hermes/mcp_servers/pm_manage_csvs/src")
    from pm_manage_csvs.sheets import read_sheet

    existing = read_sheet(sheet_id, "maintenance", max_rows=20)
    print(f"Read {len(existing)} existing rows")

    # Strip created_at from each
    cleaned = []
    for row in existing:
        row.pop("created_at", None)
        # Map to 9-column layout in the order of NEW_HEADERS
        cleaned.append([row.get(h, "") for h in NEW_HEADERS])

    # Clear A2:J20 (wider than needed to cover the old J column)
    try:
        sheets.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range="Sheet1!A2:J20",
            body={},
        ).execute()
        print("Cleared A2:J20")
    except HttpError as exc:
        print(f"Clear failed: {exc}", file=sys.stderr)
        return 1

    # Write new header
    last_col = chr(ord("A") + len(NEW_HEADERS) - 1)
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"Sheet1!A1:{last_col}1",
            valueInputOption="RAW",
            body={"values": [NEW_HEADERS]},
        ).execute()
        print(f"Wrote {len(NEW_HEADERS)}-column header")
    except HttpError as exc:
        print(f"Header write failed: {exc}", file=sys.stderr)
        return 1

    # Re-append cleaned rows
    if cleaned:
        try:
            sheets.spreadsheets().values().append(
                spreadsheetId=sheet_id,
                range=f"Sheet1!A:{last_col}",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": cleaned},
            ).execute()
            print(f"Re-appended {len(cleaned)} rows")
        except HttpError as exc:
            print(f"Append failed: {exc}", file=sys.stderr)
            return 1

    print("\n✓ Maintenance sheet migrated to 9 columns (created_at dropped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())