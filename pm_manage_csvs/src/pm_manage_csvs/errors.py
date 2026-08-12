"""Exception hierarchy for pm_manage_csvs."""

from __future__ import annotations


class PMCError(Exception):
    """Base error. All server-raised exceptions subclass this."""


class ConfigError(PMCError):
    """Configuration is missing or invalid (folder_id not set, schema mismatch)."""


class SheetNotFoundError(PMCError):
    """The requested CSV/sheet doesn't exist in the configured Drive folder."""

    def __init__(self, logical_name: str, folder_id: str) -> None:
        super().__init__(f"Sheet '{logical_name}' not found in Drive folder '{folder_id}'")
        self.logical_name = logical_name
        self.folder_id = folder_id


class ValidationError(PMCError):
    """Row data doesn't match the schema (unknown column, bad enum, bad date)."""

    def __init__(self, csv_name: str, message: str) -> None:
        super().__init__(f"[{csv_name}] {message}")
        self.csv_name = csv_name


class APIRateLimitError(PMCError):
    """Google API rate limit hit; caller should retry later."""
