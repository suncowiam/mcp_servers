"""One-shot: create the 5 PM Sheets in the configured Drive folder with headers.

Run with: GOOGLE_APPLICATION_CREDENTIALS=... uv run python scripts/create_sheets.py
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

    # Check what already exists in the folder so we can skip
    existing = (
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
    existing_names = {f["name"].lower() for f in existing}
    print(f"Existing sheets in folder: {sorted(existing_names)}")

    for spec in SHEETS:
        if spec["name"].lower() in existing_names:
            print(f"  {spec['name']}: already exists, skipping")
            continue

        # Create the empty Sheet file
        body = {
            "name": spec["name"],
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": [FOLDER_ID],
        }
        try:
            created = drive.files().create(body=body, fields="id, name").execute()
            file_id = created["id"]
            print(f"  {spec['name']}: created id={file_id}")
        except HttpError as exc:
            print(f"  {spec['name']}: FAILED to create — {exc}", file=sys.stderr)
            return 1

        # Write headers to row 1
        last_col = chr(ord("A") + len(spec["headers"]) - 1)
        rng = f"{spec['name']}!A1:{last_col}1"
        try:
            sheets.spreadsheets().values().update(
                spreadsheetId=file_id,
                range=rng,
                valueInputOption="RAW",
                body={"values": [spec["headers"]]},
            ).execute()
            print(f"    → wrote {len(spec['headers'])} headers")
        except HttpError as exc:
            print(f"    → FAILED to write headers — {exc}", file=sys.stderr)
            return 1

    print("\n✓ All 5 sheets created with headers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())