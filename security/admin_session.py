from __future__ import annotations

from typing import Dict
import time

# In-memory admin sessions. Cleared on scheduler cleanup (see memory_store.clean_memory).
_admin_logged_in: Dict[int, float] = {}


def is_admin_logged_in(tg_id: int) -> bool:
    return tg_id in _admin_logged_in


def mark_admin_logged_in(tg_id: int) -> None:
    _admin_logged_in[tg_id] = time.time()


def clear_admin_sessions() -> None:
    _admin_logged_in.clear()
