"""Drive API wrapper: auth, folder listing, sheet lookup."""

from __future__ import annotations

import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

from pm_manage_csvs.cache import get_sheet_id_cache
from pm_manage_csvs.errors import ConfigError, SheetNotFoundError

_SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
)


def get_drive_service(credentials_path: str | None = None):
    """Build an authenticated Drive v3 service.

    credentials_path: explicit path to service account JSON.
                      Defaults to env GOOGLE_APPLICATION_CREDENTIALS.
    """
    path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        raise ConfigError(
            "GOOGLE_APPLICATION_CREDENTIALS env var is not set and no "
            "credentials_path was provided"
        )
    creds = service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_sheets_service(credentials_path: str | None = None):
    """Build an authenticated Sheets v4 service."""
    path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        raise ConfigError(
            "GOOGLE_APPLICATION_CREDENTIALS env var is not set and no "
            "credentials_path was provided"
        )
    creds = service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def list_sheets_in_folder(folder_id: str, *, credentials_path: str | None = None) -> list[dict]:
    """List Google Sheets directly under `folder_id`.

    Returns a list of dicts: [{id, name, ...}] sorted by name case-insensitive.
    """
    svc = get_drive_service(credentials_path)
    q = (
        f"'{folder_id}' in parents "
        "and mimeType='application/vnd.google-apps.spreadsheet' "
        "and trashed=false"
    )
    results: list[dict] = []
    page_token: str | None = None
    while True:
        resp = (
            svc.files()
            .list(
                q=q,
                spaces="drive",
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    results.sort(key=lambda f: f["name"].lower())
    return results


def resolve_sheet_id(
    folder_id: str,
    logical_name: str,
    *,
    filename_override: str | None = None,
    credentials_path: str | None = None,
    use_cache: bool = True,
) -> str:
    """Return the Drive file ID for `logical_name`.

    Lookup order:
    1. Cache (if use_cache)
    2. Folder listing, match by filename (logical_name or override)
    """
    cache = get_sheet_id_cache()
    target_filename = (filename_override or logical_name).strip().lower()
    cache_key = f"{folder_id}::{target_filename}"

    if use_cache and cache_key in cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    files = list_sheets_in_folder(folder_id, credentials_path=credentials_path)
    for f in files:
        if f["name"].strip().lower() == target_filename:
            if use_cache:
                cache.set(cache_key, f["id"])
            return f["id"]
    raise SheetNotFoundError(logical_name, folder_id)
