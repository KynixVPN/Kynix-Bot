from __future__ import annotations

from aiogram.types import Message

from config import settings
from security.admin_session import is_admin_logged_in


async def require_admin_login(message: Message) -> bool:
    """Return True if admin may proceed. If admin but not logged in, responds and returns False."""
    uid = message.from_user.id if message.from_user else 0
    if uid not in settings.ADMINS:
        return True  # non-admin logic handled elsewhere
    if is_admin_logged_in(uid):
        return True
    await message.answer("🔐 Сначала авторизуйтесь: <code>/login</code>")
    return False
