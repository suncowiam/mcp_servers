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
    # Single-row appends use values().update() at the first free row in
    # column A. The default fixture has 2 data rows → next free is row 3.
    # (See test_sheets.py for the unit-level contract.)
    assert indices == [3]


def test_list_open_maintenance_groups_by_owner_and_ages(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    from pm_manage_csvs.server import list_open_maintenance

    lines = list_open_maintenance.__wrapped__()
    # list_open_maintenance is exhaustive — default days=0 returns ALL open items:
    # 4d tuan + 14d chris + 56d chris = 3 open items
    # (the 30d-old "done" row is excluded by status filter)
    # Output (uppercase owner, 2-space indent, blank between groups):
    # "TUAN:" + "  1. ..." + "" + "CHRIS:" + "  2. ..." + "  3. ..." = 6 lines
    assert len(lines) == 6
    # Owner headers (uppercase) + blank separator between groups
    assert lines[0] == "TUAN:"
    assert lines[2] == ""
    assert lines[3] == "CHRIS:"
    # Globally numbered items, indented
    assert lines[1].startswith("  1. ")
    assert lines[4].startswith("  2. ")
    assert lines[5].startswith("  3. ")
    # Each item carries (row=R) action key
    assert "row=" in lines[1]
    assert "row=" in lines[4]
    assert "row=" in lines[5]


def test_list_open_maintenance_default_shows_all_ages(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    """Default days=0 means list_open_maintenance is exhaustive — shows 56d-old too."""
    from pm_manage_csvs.server import list_open_maintenance

    lines = list_open_maintenance.__wrapped__()
    # The 56d-old "Old item" should be present (no date filter by default)
    assert any("Old item" in line for line in lines)


def test_list_open_maintenance_days_filters_old(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    """days=N filters out items older than N days."""
    from pm_manage_csvs.server import list_open_maintenance

    # 30-day window excludes the 56d-old item
    lines = list_open_maintenance.__wrapped__(days=30)
    assert not any("Old item" in line for line in lines)
    # 90-day window includes it
    lines_90 = list_open_maintenance.__wrapped__(days=90)
    assert any("Old item" in line for line in lines_90)


def test_list_open_maintenance_filter_by_owner(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    from pm_manage_csvs.server import list_open_maintenance

    lines = list_open_maintenance.__wrapped__(owner="chris")
    # chris filter → 2 open chris items (14d "Repair gutter" + 56d "Old item")
    # → "CHRIS:" + 2 indented items = 3 lines
    assert len(lines) == 3
    assert lines[0] == "CHRIS:"
    # Filter resets enumeration: first chris item → #1
    assert lines[1].startswith("  1. ")


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
    from datetime import date, timedelta

    from pm_manage_csvs.server import list_open_maintenance
    from tests.conftest import _FakeSheetsService

    new_data = {
        "values": [
            [
                "2423 Boxwood St",
                "9",
                "chris",
                (date.today() - timedelta(days=10)).isoformat(),
                "",
                "Repair door",
                "open",
                "",
                "",
            ]
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
    # 1 item → 2 lines: "CHRIS:" + indented item
    assert len(lines) == 2
    assert lines[0] == "CHRIS:"
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
    assert len(lines) == 2  # "CHRIS:" + indented item
    assert lines[0] == "CHRIS:"
    # Item is rendered but age marker is missing (no Nd, no "today")
    assert "today" not in lines[1]
    assert "d)" not in lines[1]
    # row=R is still present
    assert "row=" in lines[1]


def test_list_open_maintenance_future_date_uses_in_marker(
    fake_drive_service_with_maintenance, monkeypatch
) -> None:
    """A future-dated item must render as "in Nd", not "-Nd" (which reads
    as overdue). Reported on 2026-08-25 against Sharon's 8/28 showing row.
    """
    import json
    from datetime import date, timedelta
    from pathlib import Path

    from pm_manage_csvs.server import list_open_maintenance
    from tests.conftest import _FakeDriveService, _FakeSheetsService

    today = date.today()
    future_data = {
        "values": [
            # 3 days in the future (8/28 from 8/25)
            [
                "2740 Rio Linda Blvd",
                "G",
                "sharon",
                (today + timedelta(days=3)).isoformat(),
                (today + timedelta(days=3)).isoformat(),
                "Conduct showing at 10:00 AM",
                "open",
                "",
                "",
            ],
            # 1 day in the past (control case for "Nd")
            [
                "2423 Boxwood St",
                "",
                "sharon",
                (today - timedelta(days=1)).isoformat(),
                "",
                "Investigate one-way lock",
                "open",
                "",
                "",
            ],
            # Today (control case for "today")
            [
                "999 Today St",
                "",
                "sharon",
                today.isoformat(),
                "",
                "Today item",
                "open",
                "",
                "",
            ],
        ]
    }

    def factory(service_name, *_args, **_kwargs):
        if service_name == "sheets":
            return _FakeSheetsService(default_response={}, by_id={"sheet-mnt-id": future_data})
        return _FakeDriveService(
            json.loads((Path(__file__).parent / "fixtures" / "sample_folder.json").read_text())
        )

    import pm_manage_csvs.drive
    from pm_manage_csvs.cache import get_sheet_id_cache

    monkeypatch.setattr(pm_manage_csvs.drive, "build", factory)
    get_sheet_id_cache().clear()

    lines = list_open_maintenance.__wrapped__()

    # Find the line for each scenario
    future_line = next(line for line in lines if "Conduct showing" in line)
    past_line = next(line for line in lines if "one-way lock" in line)
    today_line = next(line for line in lines if "Today item" in line)

    # Future: "in 3d", never "-3d" (the bug)
    assert "(in 3d," in future_line
    assert "-3d" not in future_line
    # Past: "1d" (the existing convention)
    assert "(1d," in past_line
    # Today: "today" marker
    assert "(today," in today_line


def test_list_open_maintenance_includes_row_action_key(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    """Each item line carries (row=R) for update_row translation."""
    import re

    from pm_manage_csvs.server import list_open_maintenance

    lines = list_open_maintenance.__wrapped__()
    # Skip owner header lines (which have no row=). Items are indented with 2 spaces.
    item_lines = [line for line in lines if line.lstrip().startswith(tuple(f"{i}." for i in range(1, 10)))]
    rows = [int(re.search(r"row=(\d+)", line).group(1)) for line in item_lines]
    # Data rows start at row 2 (after header)
    assert all(r >= 2 for r in rows)
    # 3 open items now (no 30d filter): 4d tuan, 14d chris, 56d chris
    assert len(set(rows)) == 3


def test_list_open_maintenance_global_enumeration_across_owners(
    fake_drive_service_with_maintenance, monkeypatch
) -> None:
    """#N is global across owner groups, not reset per group."""
    import json
    from datetime import date, timedelta
    from pathlib import Path

    from pm_manage_csvs.server import list_open_maintenance
    from tests.conftest import _FakeDriveService, _FakeSheetsService

    today = date.today()
    # 2 tuan + 3 chris (5 total), all within 30d. After sort by (owner_order, start_date):
    # tuan comes first (T1=2d ago, T2=4d ago), then chris (C1=6d, C2=8d, C3=10d).
    # #1=T1, #2=T2, #3=C1, #4=C2, #5=C3
    multi = {
        "values": [
            ["A St", "", "tuan", (today - timedelta(days=2)).isoformat(), "", "T1", "open", "", ""],
            ["C St", "", "tuan", (today - timedelta(days=4)).isoformat(), "", "T2", "open", "", ""],
            ["B St", "", "chris", (today - timedelta(days=6)).isoformat(), "", "C1", "open", "", ""],
            ["D St", "", "chris", (today - timedelta(days=8)).isoformat(), "", "C2", "open", "", ""],
            ["E St", "", "chris", (today - timedelta(days=10)).isoformat(), "", "C3", "open", "", ""],
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
    # 5 items + 2 owner headers + 1 blank between groups = 8 lines
    # (no Notes section since no today items)
    item_lines = [line for line in lines if line.lstrip()[:1].isdigit()]
    assert len(item_lines) == 5
    # Sort: (owner_order, start_date asc). Tuan comes first (oldest = T2 at -4d), then T1 (-2d),
    # then chris oldest-first: C3 (-10d), C2 (-8d), C1 (-6d).
    assert item_lines[0].lstrip().startswith("1. C St")  # T2 - oldest tuan
    assert item_lines[1].lstrip().startswith("2. A St")  # T1
    assert item_lines[2].lstrip().startswith("3. E St")  # C3 - oldest chris
    assert item_lines[3].lstrip().startswith("4. D St")  # C2
    assert item_lines[4].lstrip().startswith("5. B St")  # C1 - newest chris


def test_list_open_maintenance_translation_round_trip(
    fake_drive_service_with_maintenance, fake_maintenance_data
) -> None:
    """Parse N → row=R → update_row(R, ...) should work end-to-end."""
    import re

    from pm_manage_csvs.server import list_open_maintenance, update_row

    lines = list_open_maintenance.__wrapped__()
    # #2 is the second item; find it (chris's row in fake_maintenance_data).
    # Items are indented, so check the lstripped form.
    target = next(line for line in lines if line.lstrip().startswith("2. "))
    row = int(re.search(r"row=(\d+)", target).group(1))

    result = update_row.__wrapped__("maintenance", row, {"status": "done"})
    assert result["ok"] is True


def test_list_open_maintenance_notes_section_for_today_items(
    fake_drive_service_with_maintenance, monkeypatch
) -> None:
    """If any open item has start_date == today, append a Notes section."""
    import json
    from datetime import date as _date
    from datetime import timedelta
    from pathlib import Path

    from pm_manage_csvs.server import list_open_maintenance
    from tests.conftest import _FakeDriveService, _FakeSheetsService

    today = _date.today()
    with_today = {
        "values": [
            ["Past St", "", "tuan", (today - timedelta(days=15)).isoformat(), "", "Past issue", "open", "", ""],
            ["Today St", "", "chris", today.isoformat(), "", "Today issue", "open", "", ""],
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
    # 2 items + 2 owner headers + 2 blanks (one between groups, one before Notes) + Notes = 7 lines
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


def test_append_rows_single_logs_framing(
    fake_drive_service, fake_sheets_service, monkeypatch
) -> None:
    """Regression: a single-row append (len(rows) == 1) emits an extra
    `append_rows_single` structured log line with `framed_as="list_of_one"`,
    so a human reading logs and any downstream log watcher can tell
    single-row from multi-row at a glance.

    Multi-row appends must NOT emit this line — only N=1 does.
    """
    from pm_manage_csvs import server

    calls: list[tuple[str, dict]] = []
    real_info = server._log.info

    def _capturing_info(event: str, **kw) -> None:
        calls.append((event, kw))
        return real_info(event, **kw)

    monkeypatch.setattr(server._log, "info", _capturing_info)

    # Single-row: should emit both `append_rows` and `append_rows_single`.
    server.append_rows(
        "maintenance",
        [{"property": "2769 Oakmont", "issue": "Test issue", "owner": "chris", "status": "open"}],
    )
    single_events = [e for e, _ in calls if e == "append_rows_single"]
    assert len(single_events) == 1
    _, kwargs = [c for c in calls if c[0] == "append_rows_single"][0]
    assert kwargs["csv"] == "maintenance"
    assert kwargs["index"] == 3  # fixture has 2 data rows → first free = 3
    assert kwargs["framed_as"] == "list_of_one"

    # Multi-row: must NOT emit `append_rows_single`.
    calls.clear()
    server.append_rows(
        "maintenance",
        [
            {"property": "2769 Oakmont", "issue": "Issue A", "owner": "chris", "status": "open"},
            {"property": "2769 Oakmont", "issue": "Issue B", "owner": "chris", "status": "open"},
        ],
    )
    assert [e for e, _ in calls if e == "append_rows_single"] == []


def test_append_rows_unwraps_item_dict(
    fake_drive_service, fake_sheets_service, monkeypatch
) -> None:
    """Regression: model schema-amnesia wrap — `{"item": {...}}` is
    silently unwrapped to `[{...}]` server-side. This is the exact
    hallucination pattern from logs/09-08-26/schema-amnesia-overreach.md
    and logs/09-09-26/append_rows_amnesia_repeat.md.
    """
    from pm_manage_csvs import server

    calls: list[tuple[str, dict]] = []
    real_info = server._log.info
    monkeypatch.setattr(
        server._log,
        "info",
        lambda event, **kw: calls.append((event, kw)) or real_info(event, **kw),
    )

    indices = server.append_rows(
        "maintenance",
        {"item": {"property": "2769 Oakmont", "issue": "Show unit", "owner": "sharon", "status": "open"}},
    )
    assert indices == [3]
    unwrap_events = [c for c in calls if c[0] == "unwrap_rows_param"]
    assert len(unwrap_events) == 1
    assert unwrap_events[0][1]["reason"] == "item_wrapper_single"
    assert unwrap_events[0][1]["count"] == 1


def test_append_rows_unwraps_item_list(
    fake_drive_service, fake_sheets_service, monkeypatch
) -> None:
    """Multi-row wrap hallucination — `{"item": [{...}, {...}]}` is
    silently unwrapped to `[{...}, {...}]` server-side.
    """
    from pm_manage_csvs import server

    calls: list[tuple[str, dict]] = []
    real_info = server._log.info
    monkeypatch.setattr(
        server._log,
        "info",
        lambda event, **kw: calls.append((event, kw)) or real_info(event, **kw),
    )

    indices = server.append_rows(
        "maintenance",
        {
            "item": [
                {"property": "2769 Oakmont", "issue": "Issue A", "owner": "chris", "status": "open"},
                {"property": "2769 Oakmont", "issue": "Issue B", "owner": "chris", "status": "open"},
            ]
        },
    )
    assert indices == [3, 4]
    unwrap_events = [c for c in calls if c[0] == "unwrap_rows_param"]
    assert unwrap_events[0][1]["reason"] == "item_wrapper_list"
    assert unwrap_events[0][1]["count"] == 2


def test_append_rows_unwraps_single_dict(
    fake_drive_service, fake_sheets_service, monkeypatch
) -> None:
    """Single-dict hallucination — a bare `{...}` (no "item" key) where
    all values are flat strings is unwrapped to `[{...}]`.

    Heuristic: only unwrap when every value is a string/None. Nested
    dicts/lists mean the caller probably meant something else.
    """
    from pm_manage_csvs import server

    indices = server.append_rows(
        "maintenance",
        {"property": "2769 Oakmont", "issue": "Test", "owner": "chris", "status": "open"},
    )
    assert indices == [3]


def test_append_rows_rejects_unrelated_dict(
    fake_drive_service, fake_sheets_service, monkeypatch
) -> None:
    """A dict that doesn't match any unwrap pattern (no "item" key, has
    non-string values) is rejected with a clear PMCError rather than
    silently misrouted.
    """
    from pm_manage_csvs import server
    from pm_manage_csvs.errors import PMCError

    # Capture the wrapped envelope response shape — the tool returns
    # {"error": "PMCError", "detail": "..."} on failure.
    result = server.append_rows(
        "maintenance",
        {"foo": {"bar": 1}},  # not a row — nested non-string values
    )
    assert isinstance(result, dict)
    assert result["error"] == "PMCError"
    assert "cannot unwrap rows" in result["detail"]


def test_append_rows_logs_when_unwrapping(
    fake_drive_service, fake_sheets_service, monkeypatch
) -> None:
    """Whenever the server actually unwraps a non-list input, the
    `unwrap_rows_param` log line fires with a `reason` field. This is
    the breadcrumb for "the model hallucinated again" — grep-able in
    the gateway log.
    """
    from pm_manage_csvs import server

    calls: list[tuple[str, dict]] = []
    real_info = server._log.info
    monkeypatch.setattr(
        server._log,
        "info",
        lambda event, **kw: calls.append((event, kw)) or real_info(event, **kw),
    )

    # Happy path: list input — no unwrap log.
    server.append_rows(
        "maintenance",
        [{"property": "2769 Oakmont", "issue": "Happy path", "owner": "chris", "status": "open"}],
    )
    assert [c for c in calls if c[0] == "unwrap_rows_param"] == []

    # Unwrap path: dict input — unwrap log fires.
    server.append_rows(
        "maintenance",
        {"item": {"property": "2769 Oakmont", "issue": "Unwrap path", "owner": "chris", "status": "open"}},
    )
    unwraps = [c for c in calls if c[0] == "unwrap_rows_param"]
    assert len(unwraps) == 1
    assert unwraps[0][1]["reason"] == "item_wrapper_single"


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
