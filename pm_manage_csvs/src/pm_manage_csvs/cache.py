"""In-memory TTL cache + sheet_id resolver singleton."""

from __future__ import annotations

import threading
import time
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """Simple thread-safe TTL cache.

    Used for short-lived caches inside the MCP subprocess. Survives only
    within the lifetime of one process. For longer-lived persistence,
    use Drive/Sheets itself.
    """

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._data: dict[K, tuple[float, V]] = {}
        self._lock = threading.Lock()

    def get(self, key: K) -> V | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                del self._data[key]
                return None
            return value

    def set(self, key: K, value: V) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __contains__(self, key: K) -> bool:
        return self.get(key) is not None


_sheet_id_cache: TTLCache[str, str] = TTLCache()


def get_sheet_id_cache() -> TTLCache[str, str]:
    """Return the module-level sheet_id cache.

    Sheet IDs are stable for a given logical name → Drive filename mapping,
    so cache TTL can be long (5 min default). Cleared on schema re-discovery.
    """
    return _sheet_id_cache
