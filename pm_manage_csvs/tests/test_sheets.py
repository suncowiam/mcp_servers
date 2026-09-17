"""Tests for sheets.py using fake services."""

from __future__ import annotations

import pytest

from pm_manage_csvs.sheets import append_rows, read_sheet, update_row


def test_read_sheet_returns_dicts(fake_sheets_service) -> None:
    rows = read_sheet("sheet-prop-id", "properties")
    assert len(rows) == 2
    assert rows[0]["address"] == "2769 Oakmont St"
    assert rows[0]["city"] == "Sacramento"
    assert rows[1]["zip"] == "95815"


def test_read_sheet_unknown_csv_raises(fake_sheets_service) -> None:
    with pytest.raises(ValueError, match="unknown csv_name"):
        read_sheet("id", "bogus")


def test_append_single_row_uses_update_path(fake_sheets_service) -> None:
    """Single-row appends must NOT use values().append() — that endpoint's
    table-boundary detection silently misbehaves with one anchor row. The
    fix writes via values().update() at the first free row in column A.

    The sample sheet fixture has 2 data rows, so the next free row is 3.
    """
    rows = [["1 Main St", "Sac", "CA", "95815", "", ""]]
    indices = append_rows("sheet-prop-id", "properties", rows)
    assert indices == [3]


def test_append_multiple_rows(fake_sheets_service) -> None:
    rows = [
        ["1 Main St", "Sac", "CA", "95815", "", ""],
        ["2 Main St", "Sac", "CA", "95815", "", ""],
    ]
    indices = append_rows("sheet-prop-id", "properties", rows)
    # Local-walk path: fake_sheet_data has 2 data rows, so first free row is 3.
    # Both rows land contiguously at 3 and 4 — same logic as the single-row path.
    assert indices == [3, 4]


def test_append_empty_rows_returns_empty(fake_sheets_service) -> None:
    assert append_rows("sheet-prop-id", "properties", []) == []


def test_append_single_row_writes_to_explicit_range(monkeypatch) -> None:
    """The single-row path must call values().update() with an explicit
    A{row}:{last_col}{row} range — never values().append(). This is the
    contract that fixes the silent-drop bug.
    """
    from typing import Any

    calls: list[tuple[str, dict[str, Any]]] = []

    class _RecordingGet:
        def __init__(self, response: dict[str, Any]) -> None:
            self._response = response

        def execute(self) -> dict[str, Any]:
            return self._response

    class _RecordingUpdate:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        def execute(self) -> dict[str, Any]:
            calls.append(("update", self._kwargs))
            return {"updatedRange": self._kwargs.get("range", "")}

    class _RecordingAppend:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        def execute(self) -> dict[str, Any]:
            calls.append(("append", self._kwargs))
            return {"updates": {"updatedRange": "Sheet1!A9999:F9999"}}

    class _RecordingValues:
        def get(self, **kwargs: Any) -> _RecordingGet:
            # Only column-A reads are made on this path; return 2 filled rows.
            return _RecordingGet({"values": [["row1"], ["row2"]]})

        def update(self, **kwargs: Any) -> _RecordingUpdate:
            return _RecordingUpdate(**kwargs)

        def append(self, **kwargs: Any) -> _RecordingAppend:
            return _RecordingAppend(**kwargs)

    class _RecordingSpreadsheets:
        def values(self) -> _RecordingValues:
            return _RecordingValues()

    class _RecordingSheetsService:
        def spreadsheets(self) -> _RecordingSpreadsheets:
            return _RecordingSpreadsheets()

    def _factory(_service_name: str, *_args: Any, **_kwargs: Any) -> _RecordingSheetsService:
        return _RecordingSheetsService()

    monkeypatch.setattr("pm_manage_csvs.drive.build", _factory)

    rows = [["1 Main St", "Sac", "CA", "95815", "", ""]]
    indices = append_rows("sheet-prop-id", "properties", rows)

    assert indices == [3]  # 2 filled rows in A + 1
    # Only one call was made, and it was update() — never append().
    assert [c[0] for c in calls] == ["update"]
    update_kwargs = calls[0][1]
    assert update_kwargs["range"] == "A3:F3"
    assert update_kwargs["valueInputOption"] == "USER_ENTERED"
    assert update_kwargs["body"] == {"values": rows}


def test_update_row_calls_batchupdate(fake_sheets_service) -> None:
    # Should not raise; the fake records the call
    update_row(
        "sheet-prop-id",
        "properties",
        row_index=5,
        values={"city": "Stockton", "notes": "updated"},
    )


def test_update_row_no_op_on_empty(fake_sheets_service) -> None:
    # Empty values dict → no batchUpdate call → no error
    update_row("sheet-prop-id", "properties", row_index=5, values={})


def test_update_row_unknown_csv_raises(fake_sheets_service) -> None:
    with pytest.raises(ValueError, match="unknown csv_name"):
        update_row("id", "bogus", row_index=1, values={"x": "y"})


def test_append_single_row_breaks_at_first_gap(monkeypatch) -> None:
    """Regression: when column A has a contiguous block followed by an empty
    gap and then a polluted tail (e.g. orphan rows from a prior shift), the
    single-row append must land at the END of the contiguous block — not past
    the polluted tail.

    Real-world trigger (2026-09-08): after orphan rows landed at 1000/1001 in
    the maintenance sheet, the next "board up" append jumped to row 1002
    instead of row 56.
    """
    from typing import Any

    # Simulated column-A state for the bug scenario:
    #   row 1   = header (always empty in the A:A read; index 1 = row 1)
    #   rows 2-5 = real contiguous data
    #   rows 6-998 = empty (the gap)
    #   row  999 = stale orphan
    #   row 1000 = stale orphan (the polluted tail)
    sparse_column_a = (
        [["row1"]]
        + [[f"r{i}"] for i in range(2, 6)]
        + [[] for _ in range(6, 999)]
        + [["orphan-999"], ["orphan-1000"]]
    )

    calls: list[tuple[str, dict[str, Any]]] = []

    class _RecordingGet:
        def __init__(self, response: dict[str, Any]) -> None:
            self._response = response

        def execute(self) -> dict[str, Any]:
            return self._response

    class _RecordingUpdate:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        def execute(self) -> dict[str, Any]:
            calls.append(("update", self._kwargs))
            return {"updatedRange": self._kwargs.get("range", "")}

    class _RecordingAppend:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        def execute(self) -> dict[str, Any]:
            calls.append(("append", self._kwargs))
            return {"updates": {"updatedRange": "Sheet1!A9999:F9999"}}

    class _RecordingValues:
        def get(self, **kwargs: Any) -> _RecordingGet:
            # The single-row path reads column A; return the sparse state.
            return _RecordingGet({"values": sparse_column_a})

        def update(self, **kwargs: Any) -> _RecordingUpdate:
            return _RecordingUpdate(**kwargs)

        def append(self, **kwargs: Any) -> _RecordingAppend:
            return _RecordingAppend(**kwargs)

    class _RecordingSpreadsheets:
        def values(self) -> _RecordingValues:
            return _RecordingValues()

    class _RecordingSheetsService:
        def spreadsheets(self) -> _RecordingSpreadsheets:
            return _RecordingSpreadsheets()

    def _factory(_service_name: str, *_args: Any, **_kwargs: Any) -> _RecordingSheetsService:
        return _RecordingSheetsService()

    monkeypatch.setattr("pm_manage_csvs.drive.build", _factory)

    rows = [["1 Main St", "Sac", "CA", "95815", "", ""]]
    indices = append_rows("sheet-prop-id", "properties", rows)

    # Contiguous block ends at row 5 → new row must land at row 6,
    # NOT at row 1001 (which is what the old last_filled logic would produce).
    assert indices == [6]
    assert [c[0] for c in calls] == ["update"]
    update_kwargs = calls[0][1]
    assert update_kwargs["range"] == "A6:F6"
