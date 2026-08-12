"""Tests for cache.py."""

from __future__ import annotations

import time

from pm_manage_csvs.cache import TTLCache


def test_set_and_get() -> None:
    c: TTLCache[str, int] = TTLCache(ttl_seconds=10)
    c.set("k", 1)
    assert c.get("k") == 1


def test_get_missing_returns_none() -> None:
    c: TTLCache[str, int] = TTLCache()
    assert c.get("missing") is None


def test_ttl_expiry() -> None:
    c: TTLCache[str, int] = TTLCache(ttl_seconds=0.05)
    c.set("k", 1)
    assert c.get("k") == 1
    time.sleep(0.1)
    assert c.get("k") is None


def test_clear() -> None:
    c: TTLCache[str, int] = TTLCache()
    c.set("a", 1)
    c.set("b", 2)
    c.clear()
    assert c.get("a") is None
    assert c.get("b") is None


def test_contains() -> None:
    c: TTLCache[str, int] = TTLCache()
    assert "x" not in c
    c.set("x", 1)
    assert "x" in c
