"""Stale-while-revalidate cache for expensive read-only responses.

A stale entry is served at once while a single background thread recomputes it, so
pages that rebuild on-the-fly forecasts never block the viewer twice for the same data.
"""

import logging
from threading import Lock, Thread
from time import monotonic

logger = logging.getLogger(__name__)
_lock = Lock()
_entries = {}  # key -> (computed_at, value)
_refreshing = set()
MAX_ENTRIES = 64


def _refresh(key, compute):
    try:
        value = compute()
        with _lock:
            if len(_entries) >= MAX_ENTRIES and key not in _entries:
                _entries.pop(min(_entries, key=lambda k: _entries[k][0]))
            _entries[key] = (monotonic(), value)
        return value
    finally:
        with _lock:
            _refreshing.discard(key)


def cached(key, compute, *, fresh_seconds=300, max_stale_seconds=3600):
    """Return (value, age_seconds). `compute` must open its own database session."""
    with _lock:
        entry = _entries.get(key)
        age = monotonic() - entry[0] if entry else None
        stale_ok = entry is not None and age <= max_stale_seconds
        start = entry is None or age > fresh_seconds
        if start and stale_ok and key not in _refreshing:
            _refreshing.add(key)
            Thread(target=_background, args=(key, compute), daemon=True).start()
        if stale_ok:
            return entry[1], age
        _refreshing.add(key)
    return _refresh(key, compute), 0.


def _background(key, compute):
    try:
        _refresh(key, compute)
    except Exception:  # noqa: BLE001 - the stale value keeps being served
        logger.exception("Background refresh failed for %s", key)


def peek(key):
    """Cached value if any, without computing or refreshing it."""
    with _lock:
        entry = _entries.get(key)
    return entry[1] if entry else None


def clear():
    with _lock:
        _entries.clear()
