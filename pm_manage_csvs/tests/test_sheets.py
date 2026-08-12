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


def test_append_rows_returns_indices(fake_sheets_service) -> None:
    rows = [["1 Main St", "Sac", "CA", "95815", "", ""]]
    indices = append_rows("sheet-prop-id", "properties", rows)
    assert indices == [101]


def test_append_multiple_rows(fake_sheets_service) -> None:
    rows = [
        ["1 Main St", "Sac", "CA", "95815", "", ""],
        ["2 Main St", "Sac", "CA", "95815", "", ""],
    ]
    indices = append_rows("sheet-prop-id", "properties", rows)
    assert indices == [101, 102]


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
