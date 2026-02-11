from __future__ import annotations

from typing import Dict
import time

from config import settings

# In-memory admin sessions. Cleared on scheduler cleanup (see memory_store.clean_memory).
_admin_logged_in: Dict[int, float] = {}


def is_admin_logged_in(tg_id: int) -> bool:
    """Return True if admin session is active.

    Uses an inactivity TTL controlled by settings.ADMIN_SESSION_TTL_SECONDS.
    When the session is active, we refresh its timestamp (sliding expiration).
    """

    ts = _admin_logged_in.get(tg_id)
    if ts is None:
        return False

    now = time.time()
    # Backwards-compatible default (30 minutes) if env/config is missing.
    ttl = getattr(settings, "ADMIN_SESSION_TTL_SECONDS", 30 * 60)
    if ttl and (now - ts) > ttl:
        _admin_logged_in.pop(tg_id, None)
        return False

    # Sliding expiration: any successful check extends the session.
    _admin_logged_in[tg_id] = now
    return True


def mark_admin_logged_in(tg_id: int) -> None:
    _admin_logged_in[tg_id] = time.time()


def mark_admin_logged_out(tg_id: int) -> None:
    """Remove admin from in-memory session store."""
    _admin_logged_in.pop(tg_id, None)


def clear_admin_sessions() -> None:
    _admin_logged_in.clear()
