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
    # 2 open rows, 1 done (filtered out) → 3 lines: "tuan:", "1. ...", "chris:", "2. ..."
    # Wait — only 2 open rows, so we expect 4 lines: "tuan:" + 1 item + "chris:" + 1 item
    assert len(lines) == 4
    # Owner headers
    assert lines[0] == "tuan:"
    assert lines[2] == "chris:"
    # Globally numbered items
    assert lines[1].startswith("1. ")
    assert lines[3].startswith("2. ")
    # Each item carries (row=R) action key
    assert "row=" in lines[1]
    assert "row=" in lines[3]
    # Age shown in days
    assert "14d" in lines[1]
    assert "56d" in lines[3]


def test_list_open_maintenance_filter_by_owner(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    from pm_manage_csvs.server import list_open_maintenance

    lines = list_open_maintenance.__wrapped__(owner="chris")
    # chris filter → only 1 item → 2 lines: "chris:" + item
    assert len(lines) == 2
    assert lines[0] == "chris:"
    # Filter resets enumeration: only one chris row → it's #1
    assert lines[1].startswith("1. ")


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
    from pm_manage_csvs.cache import get_sheet_id_cache

    get_sheet_id_cache().clear()

    lines = list_open_maintenance.__wrapped__()
    # 1 item → 2 lines: "chris:" + item
    assert len(lines) == 2
    # Unit is formatted as ", 9" in the property string
    assert ", 9" in lines[1]


def test_list_open_maintenance_handles_missing_start_date(
    fake_drive_service_with_maintenance, monkeypatch
) -> None:
    """Rows without start_date should still appear but without days marker."""
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
    assert len(lines) == 2  # "chris:" + item
    # Item is rendered but age marker is missing (no Nd, no "today")
    assert "today" not in lines[1]
    assert "d)" not in lines[1]
    # row=R is still present
    assert "row=" in lines[1]


def test_list_open_maintenance_includes_row_action_key(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    """Each item line carries (row=R) for update_row translation."""
    import re

    from pm_manage_csvs.server import list_open_maintenance

    lines = list_open_maintenance.__wrapped__()
    # Skip owner header lines (which have no row=)
    item_lines = [line for line in lines if line.startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."))]
    rows = [int(re.search(r"row=(\d+)", line).group(1)) for line in item_lines]
    # Data rows start at row 2 (after header)
    assert all(r >= 2 for r in rows)
    # 2 open rows → 2 distinct row indices
    assert len(set(rows)) == 2


def test_list_open_maintenance_global_enumeration_across_owners(
    fake_drive_service_with_maintenance, monkeypatch
) -> None:
    """#N is global across owner groups, not reset per group."""
    import json
    from pathlib import Path

    from pm_manage_csvs.server import list_open_maintenance
    from tests.conftest import _FakeDriveService, _FakeSheetsService

    # 3 chris + 2 tuan (5 total). After sort: 2 tuan first, then 3 chris.
    # #1, #2 = tuan; #3, #4, #5 = chris.
    multi = {
        "values": [
            ["A St", "", "tuan", "2026-04-01", "", "T1", "open", "", ""],
            ["B St", "", "chris", "2026-04-02", "", "C1", "open", "", ""],
            ["C St", "", "tuan", "2026-04-03", "", "T2", "open", "", ""],
            ["D St", "", "chris", "2026-04-04", "", "C2", "open", "", ""],
            ["E St", "", "chris", "2026-04-05", "", "C3", "open", "", ""],
        ]
    }

    def factory(service_name, *_args, **_kwargs):
        if service_name == "sheets":
            return _FakeSheetsService(default_response={}, by_id={"sheet-mnt-id": multi})
        return _FakeDriveService(
            json.loads((Path(__file__).parent / "fixtures" / "sample_folder.json").read_text())
        )

    import pm_manage_csvs.drive
    from pm_manage_csvs.cache import get_sheet_id_cache

    monkeypatch.setattr(pm_manage_csvs.drive, "build", factory)
    get_sheet_id_cache().clear()

    lines = list_open_maintenance.__wrapped__()
    # 5 items + 2 owner headers = 7 lines (no Notes section since no today items)
    item_lines = [line for line in lines if line[0].isdigit()]
    assert len(item_lines) == 5
    # #1 and #2 are tuan; #3, #4, #5 are chris
    assert item_lines[0].startswith("1. A St")
    assert item_lines[1].startswith("2. C St")  # T2 is later
    assert item_lines[2].startswith("3. B St")
    assert item_lines[3].startswith("4. D St")
    assert item_lines[4].startswith("5. E St")


def test_list_open_maintenance_translation_round_trip(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    """Parse N → row=R → update_row(R, ...) should work end-to-end."""
    import re

    from pm_manage_csvs.server import list_open_maintenance, update_row

    lines = list_open_maintenance.__wrapped__()
    # #2 is the second item; find it (chris's row in fake_maintenance_data)
    target = next(line for line in lines if line.startswith("2. "))
    row = int(re.search(r"row=(\d+)", target).group(1))

    result = update_row.__wrapped__("maintenance", row, {"status": "done"})
    assert result["ok"] is True


def test_list_open_maintenance_notes_section_for_today_items(
    fake_drive_service_with_maintenance, monkeypatch
) -> None:
    """If any open item has start_date == today, append a Notes section."""
    import json
    from datetime import date as _date
    from pathlib import Path

    from pm_manage_csvs.server import list_open_maintenance
    from tests.conftest import _FakeDriveService, _FakeSheetsService

    today = _date.today().isoformat()
    with_today = {
        "values": [
            ["Past St", "", "tuan", "2026-04-01", "", "Past issue", "open", "", ""],
            ["Today St", "", "chris", today, "", "Today issue", "open", "", ""],
        ]
    }

    def factory(service_name, *_args, **_kwargs):
        if service_name == "sheets":
            return _FakeSheetsService(default_response={}, by_id={"sheet-mnt-id": with_today})
        return _FakeDriveService(
            json.loads((Path(__file__).parent / "fixtures" / "sample_folder.json").read_text())
        )

    import pm_manage_csvs.drive
    from pm_manage_csvs.cache import get_sheet_id_cache

    monkeypatch.setattr(pm_manage_csvs.drive, "build", factory)
    get_sheet_id_cache().clear()

    lines = list_open_maintenance.__wrapped__()
    # 2 items + 2 owner headers + blank + Notes = 6 lines
    assert lines[-1] == "Notes: #2 is today"
    assert lines[-2] == ""  # blank line between items and Notes
    # the second item line uses "(today," not "(14d,"
    assert "today" in lines[-3]


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


def test_update_rows_marks_multiple_done(fake_drive_service, fake_sheets_service) -> None:
    """Bulk update: 1 call marks N rows."""
    from pm_manage_csvs.server import update_rows

    result = update_rows("maintenance", [3, 5, 7], {"status": "done"})
    assert isinstance(result, dict)
    assert result["ok"] is True
    assert result["rows_updated"] == [3, 5, 7]
    assert result["values_applied"]["status"] == "done"


def test_update_rows_empty_row_indices_rejected(fake_drive_service, fake_sheets_service) -> None:
    """Empty list is an error, not a silent no-op."""
    from pm_manage_csvs.server import update_rows

    result = update_rows("maintenance", [], {"status": "done"})
    assert isinstance(result, dict)
    assert result["error"] == "PMCError"
    assert "non-empty" in result["detail"]


def test_update_rows_blocks_primary_key(fake_drive_service, fake_sheets_service) -> None:
    """Same protection as update_row."""
    from pm_manage_csvs.server import update_rows

    result = update_rows("tenants", [1, 2], {"property": "new addr"})
    assert isinstance(result, dict)
    assert "primary-key columns" in result["detail"]


def test_update_rows_auto_fills_completed_at(fake_drive_service, fake_sheets_service) -> None:
    """Same auto-fill behavior as update_row — status='done' sets completed_at."""
    from pm_manage_csvs.server import update_rows

    result = update_rows("maintenance", [2, 4, 6], {"status": "done"})
    assert result["ok"] is True
    assert "completed_at" in result["values_applied"]
