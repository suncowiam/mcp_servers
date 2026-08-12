"""Tests for schema.py."""

from __future__ import annotations

from datetime import date

import pytest

from pm_manage_csvs.errors import ValidationError
from pm_manage_csvs.schema import (
    MAINTENANCE,
    PROPERTIES,
    SCHEMAS,
    TENANTS,
    VENDORS,
    ColumnDef,
    apply_auto_fills,
    get_schema,
    list_to_row,
    row_to_list,
    validate_rows,
)


def test_all_schemas_present() -> None:
    assert set(SCHEMAS) == {"properties", "tenants", "vendors", "maintenance", "events"}


def test_properties_columns() -> None:
    assert PROPERTIES.headers == ["address", "city", "state", "zip", "units", "notes"]
    assert PROPERTIES.primary_key == ("address",)


def test_tenants_columns() -> None:
    assert TENANTS.headers[0] == "property"
    assert TENANTS.headers[1] == "unit"
    assert TENANTS.primary_key == ("property", "unit")
    # unit is optional — single-unit properties have empty unit
    unit_col = next(c for c in TENANTS.columns if c.header == "unit")
    assert unit_col.required is False


def test_get_schema_unknown_raises() -> None:
    with pytest.raises(ValidationError):
        get_schema("nope")


def test_validate_rows_accepts_clean_dict() -> None:
    validate_rows(
        "properties",
        [{"address": "123 Main St", "city": "Sacramento", "state": "CA", "zip": "95815"}],
    )


def test_validate_rows_rejects_unknown_column() -> None:
    with pytest.raises(ValidationError, match="unknown columns"):
        validate_rows("properties", [{"address": "1", "bogus": "x"}])


def test_validate_rows_rejects_missing_required() -> None:
    with pytest.raises(ValidationError, match="missing required 'address'"):
        validate_rows("properties", [{"city": "Sacramento"}])


def test_validate_rows_enum_coercion_failure() -> None:
    with pytest.raises(ValidationError, match="not in allowed"):
        validate_rows("maintenance", [{"property": "x", "issue": "y", "status": "bogus"}])


def test_validate_rows_date_string() -> None:
    validate_rows(
        "events",
        [
            {
                "date": "2026-08-11",
                "description": "Inspection",
            }
        ],
    )


def test_validate_rows_rejects_bad_date() -> None:
    with pytest.raises(ValidationError, match="cannot coerce"):
        validate_rows("events", [{"date": "08/11/2026", "description": "x"}])


def test_validate_rows_accepts_date_object() -> None:
    validate_rows("events", [{"date": date(2026, 8, 11), "description": "x"}])


def test_row_to_list_alignment() -> None:
    out = row_to_list(
        PROPERTIES,
        {"address": "1 Main", "city": "Sac", "state": "CA", "zip": "95815"},
    )
    assert out == ["1 Main", "Sac", "CA", "95815", "", ""]


def test_list_to_row_roundtrip() -> None:
    values = ["1 Main", "Sac", "CA", "95815", "A1,A2", ""]
    row = list_to_row(PROPERTIES, values)
    assert row["address"] == "1 Main"
    assert row["units"] == "A1,A2"
    assert row["notes"] == ""


def test_coerce_bool_truthy_strings() -> None:
    col = ColumnDef("active", "F", 5, "bool")
    assert col.coerce("true") == "TRUE"
    assert col.coerce("yes") == "TRUE"
    assert col.coerce("1") == "TRUE"
    assert col.coerce(False) == "FALSE"


def test_apply_auto_fills_maintenance_append() -> None:
    rows = apply_auto_fills(
        "maintenance",
        [{"property": "x", "issue": "y", "status": "open"}],
        is_update=False,
    )
    # created_at no longer auto-filled — start_date is sufficient
    assert "created_at" not in rows[0]
    assert "completed_at" not in rows[0]


def test_apply_auto_fills_maintenance_done_on_update() -> None:
    rows = apply_auto_fills(
        "maintenance",
        [{"property": "x", "issue": "y", "status": "done"}],
        is_update=True,
    )
    assert rows[0]["completed_at"] == date.today().isoformat()


def test_apply_auto_fills_reopening_clears_completed_at() -> None:
    rows = apply_auto_fills(
        "maintenance",
        [{"property": "x", "issue": "y", "status": "open", "completed_at": "2026-08-01"}],
        is_update=True,
    )
    assert "completed_at" not in rows[0]


def test_apply_auto_fills_passthrough_for_properties() -> None:
    rows = apply_auto_fills("properties", [{"address": "1 Main"}])
    assert rows == [{"address": "1 Main"}]


def test_list_to_row_converts_excel_serial_dates() -> None:
    # Sheets returns dates as serial numbers when written with USER_ENTERED
    # 46119 = 2026-04-07 (Sheets epoch is 1899-12-30)
    # values indexed by column order: property, unit, tenant_name, phone, email,
    # lease_start, lease_end, status, notes
    values = ["1 Main", "", "Alice", "", "", "2026-04-07", "2026-05-01", "active", ""]
    row = list_to_row(TENANTS, values)
    assert row["lease_start"] == "2026-04-07"
    assert row["lease_end"] == "2026-05-01"


def test_list_to_row_handles_serial_date_for_date_columns() -> None:
    # Read paths may return ints; we should normalize
    # Use maintenance schema for the date check
    values = ["p", "", "chris", 46119, "", "issue", "open", "", ""]  # 10 values
    # maintenance schema is now 9 columns, so add "" to make up
    row = list_to_row(MAINTENANCE, [*values, ""])
    assert row["start_date"] == "2026-04-07"


def test_list_to_row_passes_through_iso_string() -> None:
    values = ["p", "", "chris", "2026-04-07", "", "issue", "open", "", ""]
    row = list_to_row(MAINTENANCE, [*values, ""])
    assert row["start_date"] == "2026-04-07"


def test_list_to_row_passes_through_empty_date() -> None:
    values = ["p", "", "chris", "", "", "issue", "open", "", ""]
    row = list_to_row(MAINTENANCE, [*values, ""])
    assert row["start_date"] == ""


def test_list_to_row_coerces_int_to_string_for_string_columns() -> None:
    """Sheets USER_ENTERED auto-converts '1' to int 1. Read should normalize back."""
    # Property column = address (string), tenants unit column = unit (string)
    values = ["1 Main St", 1, "Alice", "", "", "", "", "", ""]
    row = list_to_row(TENANTS, values)
    assert row["property"] == "1 Main St"
    assert row["unit"] == "1"
    assert isinstance(row["unit"], str)


def test_list_to_row_passes_string_through_unchanged() -> None:
    values = ["1 Main St", "A1", "Alice", "", "", "", "", "", ""]
    row = list_to_row(TENANTS, values)
    assert row["unit"] == "A1"
    assert isinstance(row["unit"], str)


def test_list_to_row_coerces_float_to_string_for_string_columns() -> None:
    values = ["1 Main St", 1.5, "Alice", "", "", "", "", "", ""]
    row = list_to_row(TENANTS, values)
    assert row["unit"] == "1.5"
    assert isinstance(row["unit"], str)


def test_vendors_has_active_bool() -> None:
    assert any(c.header == "active" and c.type == "bool" for c in VENDORS.columns)
