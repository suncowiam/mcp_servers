"""One-shot: set PLAIN_TEXT number format on string columns that risk auto-conversion.

Sheets auto-converts numeric-looking values (e.g. "1", "100") to int/float when
written with USER_ENTERED. Setting a column's number format to PLAIN_TEXT
prevents this. After format change, any new writes stay as text.

This is preventative — the read path already coerces int/float back to str.

Run: GOOGLE_APPLICATION_CREDENTIALS=... uv run python scripts/set_plain_text_format.py
"""

from __future__ import annotations

import os
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build

FOLDER_ID = "1QHmCCOv1cMstrIt9I5HcxQx63530GYwA"

# (csv_name, column_letter, label)
COLUMNS_TO_FORMAT = [
    ("tenants", "B", "unit"),
    ("tenants", "D", "phone"),
    ("maintenance", "B", "unit"),
    ("properties", "E", "units"),
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

    # Find sheet IDs by name
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
    name_to_id = {f["name"].lower(): f["id"] for f in files}

    requests: list[dict] = []
    for csv_name, col_letter, label in COLUMNS_TO_FORMAT:
        sid = name_to_id.get(csv_name.lower())
        if not sid:
            print(f"  {csv_name}: sheet not found, skipping")
            continue
        # Get the sheetId (gid) for this spreadsheet's first tab
        meta = sheets.spreadsheets().get(spreadsheetId=sid).execute()
        sheet_id_num = meta["sheets"][0]["properties"]["sheetId"]
        col_index = ord(col_letter) - ord("A")
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id_num,
                    "startColumnIndex": col_index,
                    "endColumnIndex": col_index + 1,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })
        print(f"  queued {csv_name}.{col_letter} ({label})")

    if not requests:
        return 0

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=name_to_id["tenants"],  # any valid id; applies to its own requests
        body={"requests": requests},
    ).execute()
    print(f"\n✓ Applied PLAIN_TEXT format to {len(requests)} column(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())