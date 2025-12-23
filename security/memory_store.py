import asyncio
import time
from typing import Dict, Tuple

from config import settings

# real_ids: fake_id -> real_tg_id
real_ids: Dict[int, int] = {}

# support_real_ids: fake_id -> real_tg_id (не очищаем, пока тикет не закрыт)
support_real_ids: Dict[int, int] = {}

# refresh cooldowns: real_tg_id -> last_refresh_ts
refresh_last_ts: Dict[int, float] = {}

# 30 minutes
REFRESH_COOLDOWN_SECONDS = 30 * 60


def remember_user(fake_id: int, real_tg_id: int) -> None:
    real_ids[fake_id] = real_tg_id


def remember_support_user(fake_id: int, real_tg_id: int) -> None:
    support_real_ids[fake_id] = real_tg_id


def forget_support_user(fake_id: int) -> None:
    support_real_ids.pop(fake_id, None)


def get_real_id(fake_id: int) -> int | None:
    return real_ids.get(fake_id) or support_real_ids.get(fake_id)


def refresh_can_run(real_tg_id: int) -> Tuple[bool, int]:
    """Возвращает (можно ли выполнить, сколько секунд осталось до конца кулдауна)."""
    last = refresh_last_ts.get(real_tg_id)
    if not last:
        return True, 0
    now = time.time()
    elapsed = now - last
    if elapsed >= REFRESH_COOLDOWN_SECONDS:
        return True, 0
    return False, int(REFRESH_COOLDOWN_SECONDS - elapsed)


def refresh_mark_run(real_tg_id: int) -> None:
    refresh_last_ts[real_tg_id] = time.time()


async def clean_memory():
    while True:
        await asyncio.sleep(settings.MEMORY_CLEAN_INTERVAL_HOURS * 3600)
        real_ids.clear()
        refresh_last_ts.clear()


def start_schedulers():
    asyncio.create_task(clean_memory())
