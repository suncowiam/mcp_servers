"""Tests for drive.py using fake services."""

from __future__ import annotations

import pytest

from pm_manage_csvs.cache import get_sheet_id_cache
from pm_manage_csvs.drive import (
    get_drive_service,
    list_sheets_in_folder,
    resolve_sheet_id,
)
from pm_manage_csvs.errors import ConfigError, SheetNotFoundError


def test_get_drive_service_missing_env(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(ConfigError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        get_drive_service()


def test_list_sheets_returns_files(fake_drive_service) -> None:
    files = list_sheets_in_folder("folder123")
    names = sorted(f["name"] for f in files)
    assert names == ["events", "maintenance", "properties", "tenants", "vendors"]


def test_resolve_sheet_id_case_insensitive(fake_drive_service) -> None:
    sid = resolve_sheet_id("folder123", "Properties", use_cache=False)
    assert sid == "sheet-prop-id"
    sid2 = resolve_sheet_id("folder123", "properties", use_cache=False)
    assert sid2 == "sheet-prop-id"


def test_resolve_sheet_id_not_found(fake_drive_service) -> None:
    with pytest.raises(SheetNotFoundError, match="not found"):
        resolve_sheet_id("folder123", "doesnotexist", use_cache=False)


def test_resolve_sheet_id_caches(fake_drive_service) -> None:
    resolve_sheet_id("folder123", "tenants")
    cache = get_sheet_id_cache()
    assert "folder123::tenants" in cache


def test_resolve_sheet_id_uses_cache(fake_drive_service, monkeypatch) -> None:
    """After first resolution, second call shouldn't hit list_sheets_in_folder."""
    # Prime the cache
    resolve_sheet_id("folder123", "vendors")
    # Spy on list_sheets_in_folder to confirm it's not called the second time
    called = {"n": 0}

    def _spy(*_args, **_kwargs):
        called["n"] += 1
        return []

    monkeypatch.setattr("pm_manage_csvs.drive.list_sheets_in_folder", _spy)
    sid = resolve_sheet_id("folder123", "vendors")
    assert sid == "sheet-ven-id"
    assert called["n"] == 0


def test_resolve_sheet_id_with_override(fake_drive_service) -> None:
    sid = resolve_sheet_id(
        "folder123",
        "properties",
        filename_override="PROPERTIES",
        use_cache=False,
    )
    assert sid == "sheet-prop-id"
