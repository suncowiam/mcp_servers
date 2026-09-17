"""Shared test fixtures: in-memory fake Drive/Sheets services.

These fakes stand in for googleapiclient's build() result so tests can
exercise drive.py and sheets.py without making real API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeCredentials:
    """Stand-in for google.oauth2.service_account.Credentials."""

    @classmethod
    def from_service_account_file(cls, _path: str, **_kwargs: Any) -> _FakeCredentials:
        return cls()

    def authorize(self, http):
        return http


@pytest.fixture(autouse=True)
def _fake_credentials_loader(monkeypatch):
    """Replace service_account.Credentials so tests don't try to load a real file."""
    monkeypatch.setattr(
        "pm_manage_csvs.drive.service_account.Credentials",
        _FakeCredentials,
    )


class _FakeFilesListRequest:
    def __init__(self, files_data: dict[str, Any]) -> None:
        self._files_data = files_data

    def execute(self) -> dict[str, Any]:
        return self._files_data


class _FakeFiles:
    def __init__(self, files_data: dict[str, Any]) -> None:
        self._files_data = files_data

    def list(self, **_kwargs: Any) -> _FakeFilesListRequest:
        return _FakeFilesListRequest(self._files_data)


class _FakeDriveService:
    def __init__(self, files_data: dict[str, Any]) -> None:
        self._files = _FakeFiles(files_data)

    def files(self) -> _FakeFiles:
        return self._files


class _FakeValuesGetRequest:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def execute(self) -> dict[str, Any]:
        return self._response


class _FakeBatchUpdateRequest:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def execute(self) -> dict[str, Any]:
        return {"replies": [{"updatedRange": "x"}] * len(self._body.get("data", []))}


class _FakeValuesUpdateRequest:
    """Fake for values().update() — echoes the requested range so tests can
    assert on the target row that was written."""

    def __init__(self, rng: str | None) -> None:
        self._range = rng or ""

    def execute(self) -> dict[str, Any]:
        return {"updatedRange": self._range}


class _FakeValues:
    """Fake Sheets values() API; dispatches by spreadsheetId when set."""

    def __init__(
        self,
        default_response: dict[str, Any],
        by_id: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._default = default_response
        self._by_id = by_id or {}

    def get(self, **_kwargs: Any) -> _FakeValuesGetRequest:
        sid = _kwargs.get("spreadsheetId")
        response = self._by_id.get(sid, self._default)
        return _FakeValuesGetRequest(response)

    def update(self, **_kwargs: Any) -> _FakeValuesUpdateRequest:
        return _FakeValuesUpdateRequest(_kwargs.get("range"))

    def batchUpdate(self, **_kwargs: Any) -> _FakeBatchUpdateRequest:
        return _FakeBatchUpdateRequest(_kwargs.get("body", {}))


class _FakeSpreadsheets:
    def __init__(
        self,
        default_response: dict[str, Any],
        by_id: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._values = _FakeValues(default_response, by_id)

    def values(self) -> _FakeValues:
        return self._values


class _FakeSheetsService:
    """Fake Sheets v4 service."""

    def __init__(
        self,
        default_response: dict[str, Any],
        by_id: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._spreadsheets = _FakeSpreadsheets(default_response, by_id)

    def spreadsheets(self) -> _FakeSpreadsheets:
        return self._spreadsheets


@pytest.fixture
def fake_folder_data() -> dict[str, Any]:
    return json.loads((FIXTURES / "sample_folder.json").read_text())


@pytest.fixture
def fake_sheet_data() -> dict[str, Any]:
    return json.loads((FIXTURES / "sample_sheet.json").read_text())


@pytest.fixture
def fake_maintenance_data() -> dict[str, Any]:
    """Sample maintenance data with today-relative dates (so tests are stable as time moves)."""
    from datetime import date, timedelta

    today = date.today()
    return {
        "values": [
            # open, chris, 14d old
            ["2423 Boxwood St", "", "chris", (today - timedelta(days=14)).isoformat(), "", "Repair gutter", "open", "", ""],
            # open, tuan, 4d old
            ["2755 Oakmont St", "", "tuan", (today - timedelta(days=4)).isoformat(), "", "Call vendor", "open", "", ""],
            # done, chris, 30d old (within default window so tests that expect this row need to check)
            ["2807 Pixie Dr", "", "chris", (today - timedelta(days=30)).isoformat(), "", "Electrical outlet", "done", "", (today - timedelta(days=29)).isoformat()],
            # open, chris, 56d old (OUTSIDE default 30d window — excluded by default)
            ["1800 Old St", "", "chris", (today - timedelta(days=56)).isoformat(), "", "Old item", "open", "", ""],
        ]
    }


@pytest.fixture
def fake_drive_service(monkeypatch, fake_folder_data, fake_sheet_data):
    """Returns a factory; default response is fake_sheet_data for any spreadsheetId."""

    def _factory(service_name, *_args, **_kwargs):
        if service_name == "sheets":
            return _FakeSheetsService(default_response=fake_sheet_data)
        return _FakeDriveService(fake_folder_data)

    monkeypatch.setattr("pm_manage_csvs.drive.build", _factory)
    return _factory


@pytest.fixture
def fake_sheets_service(monkeypatch, fake_drive_service):
    """Alias for fake_drive_service — both return correct service based on name."""
    return fake_drive_service


@pytest.fixture
def fake_drive_service_with_maintenance(
    monkeypatch, fake_folder_data, fake_maintenance_data
):
    """Like fake_drive_service but maintenance sheet returns fake_maintenance_data."""

    def _factory(service_name, *_args, **_kwargs):
        if service_name == "sheets":
            return _FakeSheetsService(
                default_response={},
                by_id={"sheet-mnt-id": fake_maintenance_data},
            )
        return _FakeDriveService(fake_folder_data)

    monkeypatch.setattr("pm_manage_csvs.drive.build", _factory)
    return _factory


@pytest.fixture(autouse=True)
def _clean_sheet_cache():
    """Reset the module-level sheet_id cache between tests."""
    from pm_manage_csvs.cache import get_sheet_id_cache

    get_sheet_id_cache().clear()
    yield
    get_sheet_id_cache().clear()


@pytest.fixture(autouse=True)
def _set_google_credentials(monkeypatch):
    """Default to a fake credentials path so tests don't trip ConfigError."""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/fake-creds.json")