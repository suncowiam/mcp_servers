"""End-to-end tests for server.py tool handlers.

These exercise the full path through resolve_sheet_id → read/append/update.
We bypass MCP protocol and call the underlying functions directly (after
removing the @mcp.tool() wrapper) — the MCP layer is tested by the
smoke_test.py script.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Configure config path BEFORE importing server
CONFIG_PATH = Path(__file__).parent / "fixtures" / "test_sheets.yaml"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
CONFIG_PATH.write_text(
    """\
folder_id: "folder123"
sheets:
  properties:  "properties"
  tenants:     "tenants"
  vendors:     "vendors"
  maintenance: "maintenance"
  events:      "events"
cache_ttl_seconds: 60
"""
)


@pytest.fixture(autouse=True)
def _patch_config_path(monkeypatch):
    """Point the server at the test config file."""
    from pm_manage_csvs import server

    monkeypatch.setattr(server, "_CONFIG_PATH", CONFIG_PATH)
    server._CONFIG = {}  # force re-load
    yield
    server._CONFIG = {}


def test_list_csvs() -> None:
    from pm_manage_csvs.server import list_csvs

    assert list_csvs() == ["events", "maintenance", "properties", "tenants", "vendors"]


def test_get_schema_tool(fake_sheets_service) -> None:
    from pm_manage_csvs.server import get_schema

    schema = get_schema("maintenance")
    assert schema["name"] == "maintenance"
    headers = [c["header"] for c in schema["columns"]]
    assert headers[0] == "property"
    assert headers[-1] == "completed_at"
    # owner column has enum
    owner_col = next(c for c in schema["columns"] if c["header"] == "owner")
    assert owner_col["enum"] == ["tuan", "chris", "sharon", "vendor"]


def test_read_csv(fake_drive_service, fake_sheets_service) -> None:
    from pm_manage_csvs.server import read_csv

    rows = read_csv("properties")
    assert rows[0]["address"] == "2769 Oakmont St"


def test_read_csv_rows_filter(fake_drive_service, fake_sheets_service) -> None:
    from pm_manage_csvs.server import read_csv_rows

    rows = read_csv_rows("properties", filters={"address": "2773 Oakmont St"})
    assert len(rows) == 1
    assert rows[0]["city"] == "Sacramento"


def test_read_csv_rows_no_match(fake_drive_service, fake_sheets_service) -> None:
    from pm_manage_csvs.server import read_csv_rows

    rows = read_csv_rows("properties", filters={"address": "999 Nonexistent"})
    assert rows == []


def test_count_csv_rows(fake_drive_service, fake_sheets_service) -> None:
    from pm_manage_csvs.server import count_csv_rows

    assert count_csv_rows("properties") == 2
    assert count_csv_rows("properties", filters={"city": "Sacramento"}) == 2
    assert count_csv_rows("properties", filters={"city": "Stockton"}) == 0


def test_append_rows_auto_fills_maintenance(fake_drive_service, fake_sheets_service) -> None:
    from pm_manage_csvs.server import append_rows

    indices = append_rows(
        "maintenance",
        [{"property": "2769 Oakmont", "issue": "Test issue", "owner": "chris", "status": "open"}],
    )
    assert indices == [101]


def test_list_open_maintenance_groups_by_owner_and_ages(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    from pm_manage_csvs.server import list_open_maintenance

    lines = list_open_maintenance.__wrapped__()
    # 2 open rows, 1 done (filtered out)
    assert len(lines) == 2
    # owner_order: tuan < chris < sharon < vendor → tuan first
    assert lines[0].startswith("[tuan]")
    assert lines[1].startswith("[chris]")
    # Age shown in days
    assert "since 2026-06-26" in lines[1]
    assert "since 2026-08-07" in lines[0]


def test_list_open_maintenance_filter_by_owner(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    from pm_manage_csvs.server import list_open_maintenance

    lines = list_open_maintenance.__wrapped__(owner="chris")
    assert len(lines) == 1
    assert "[chris]" in lines[0]


def test_list_open_maintenance_empty_when_no_match(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    from pm_manage_csvs.server import list_open_maintenance

    lines = list_open_maintenance.__wrapped__(owner="sharon")
    assert lines == []


def test_list_open_maintenance_includes_unit_when_present(
    fake_drive_service_with_maintenance, monkeypatch
) -> None:
    """If a row has a unit, it should appear in the line."""
    from pm_manage_csvs.server import list_open_maintenance

    # Override fake to return one row with a unit
    from tests.conftest import _FakeSheetsService

    new_data = {
        "values": [
            ["2423 Boxwood St", "9", "chris", "2026-06-26", "", "Repair door", "open", "", ""]
        ]
    }

    def factory(service_name, *_args, **_kwargs):
        if service_name == "sheets":
            return _FakeSheetsService(default_response={}, by_id={"sheet-mnt-id": new_data})
        import json
        from pathlib import Path

        from tests.conftest import _FakeDriveService

        return _FakeDriveService(
            json.loads((Path(__file__).parent / "fixtures" / "sample_folder.json").read_text())
        )

    import pm_manage_csvs.drive

    monkeypatch.setattr(pm_manage_csvs.drive, "build", factory)
    # Clear cached sheet_id so it re-resolves
    from pm_manage_csvs.cache import get_sheet_id_cache

    get_sheet_id_cache().clear()

    lines = list_open_maintenance.__wrapped__()
    assert len(lines) == 1
    assert "Unit 9" in lines[0] or " 9" in lines[0]


def test_list_open_maintenance_handles_missing_start_date(
    fake_drive_service_with_maintenance, monkeypatch
) -> None:
    """Rows without start_date should still appear but without the age suffix."""
    import json
    from pathlib import Path

    from pm_manage_csvs.server import list_open_maintenance
    from tests.conftest import _FakeDriveService, _FakeSheetsService

    no_date = {"values": [["X St", "", "chris", "", "", "No date", "open", "", ""]]}

    def factory(service_name, *_args, **_kwargs):
        if service_name == "sheets":
            return _FakeSheetsService(default_response={}, by_id={"sheet-mnt-id": no_date})
        return _FakeDriveService(
            json.loads((Path(__file__).parent / "fixtures" / "sample_folder.json").read_text())
        )

    import pm_manage_csvs.drive
    from pm_manage_csvs.cache import get_sheet_id_cache

    monkeypatch.setattr(pm_manage_csvs.drive, "build", factory)
    get_sheet_id_cache().clear()

    lines = list_open_maintenance.__wrapped__()
    assert len(lines) == 1
    # No "since" or "d ago" when start_date is empty
    assert "since" not in lines[0]
    assert "d ago" not in lines[0]


def test_append_rows_rejects_bad_enum(fake_drive_service, fake_sheets_service) -> None:
    from pm_manage_csvs.server import append_rows

    result = append_rows(
        "maintenance",
        [{"property": "x", "issue": "y", "status": "bogus"}],
    )
    assert isinstance(result, dict)
    assert result["error"] == "ValidationError"


def test_append_rows_rejects_unknown_column(fake_drive_service, fake_sheets_service) -> None:
    from pm_manage_csvs.server import append_rows

    result = append_rows(
        "properties",
        [{"address": "1 Main", "bogus_col": "x"}],
    )
    assert isinstance(result, dict)
    assert result["error"] == "ValidationError"


def test_update_row_blocks_primary_key(fake_drive_service, fake_sheets_service) -> None:
    from pm_manage_csvs.server import update_row

    result = update_row("tenants", 5, {"property": "new addr", "tenant_name": "Alice"})
    assert isinstance(result, dict)
    assert "primary-key columns" in result["detail"]


def test_update_row_status_done_sets_completed_at(fake_drive_service, fake_sheets_service) -> None:

    from pm_manage_csvs.server import update_row

    result = update_row("maintenance", 5, {"status": "done"})
    assert isinstance(result, dict)
    assert result["ok"] is True
    assert "completed_at" in result["updated_fields"]


def test_update_row_reopen_clears_completed_at(fake_drive_service, fake_sheets_service) -> None:
    from pm_manage_csvs.server import update_row

    result = update_row("maintenance", 5, {"status": "open"})
    assert isinstance(result, dict)
    assert result["ok"] is True


def test_missing_folder_id_returns_error(tmp_path, monkeypatch) -> None:
    from pm_manage_csvs import server

    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("folder_id: PASTE_FOLDER_ID_HERE\n")
    monkeypatch.setattr(server, "_CONFIG_PATH", bad_config)
    server._CONFIG = {}

    result = server.list_csvs()
    # list_csvs itself doesn't need folder_id, so this returns normally
    assert result == ["events", "maintenance", "properties", "tenants", "vendors"]

    result = server.read_csv("properties")
    assert isinstance(result, dict)
    assert result["error"] == "PMCError"
