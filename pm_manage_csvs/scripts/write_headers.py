"""One-shot: write the row-1 headers into each of the 5 PM Sheets.

Run with: GOOGLE_APPLICATION_CREDENTIALS=... uv run python scripts/write_headers.py
"""

from __future__ import annotations

import os
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

FOLDER_ID = "1QHmCCOv1cMstrIt9I5HcxQx63530GYwA"

SHEETS = [
    {
        "name": "properties",
        "headers": ["address", "city", "state", "zip", "units", "notes"],
    },
    {
        "name": "tenants",
        "headers": [
            "property",
            "unit",
            "tenant_name",
            "phone",
            "email",
            "lease_start",
            "lease_end",
            "status",
            "notes",
        ],
    },
    {
        "name": "vendors",
        "headers": ["name", "role", "phone", "email", "telegram_id", "active", "notes"],
    },
    {
        "name": "maintenance",
        "headers": [
            "property",
            "unit",
            "owner",
            "start_date",
            "end_date",
            "issue",
            "status",
            "notes",
            "created_at",
            "completed_at",
        ],
    },
    {
        "name": "events",
        "headers": ["date", "property", "unit", "event_type", "description", "status", "notes"],
    },
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

    # Find each sheet by name → file_id
    name_to_id: dict[str, str] = {}
    page_token: str | None = None
    while True:
        resp = (
            drive.files()
            .list(
                q=f"'{FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for f in resp.get("files", []):
            name_to_id[f["name"].lower()] = f["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    for spec in SHEETS:
        file_id = name_to_id.get(spec["name"].lower())
        if not file_id:
            print(f"  {spec['name']}: NOT FOUND, skipping")
            continue
        last_col = chr(ord("A") + len(spec["headers"]) - 1)
        # Sheets have a default tab "Sheet1" — use it instead of the file name
        rng = f"Sheet1!A1:{last_col}1"
        try:
            sheets.spreadsheets().values().update(
                spreadsheetId=file_id,
                range=rng,
                valueInputOption="RAW",
                body={"values": [spec["headers"]]},
            ).execute()
            print(f"  {spec['name']}: wrote {len(spec['headers'])} headers ({spec['headers'][0]} ... {spec['headers'][-1]})")
        except HttpError as exc:
            print(f"  {spec['name']}: FAILED — {exc}", file=sys.stderr)
            return 1

    print("\n✓ All headers written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())