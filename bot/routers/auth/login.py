from __future__ import annotations

import secrets
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import settings
from db.repo_admin_auth import (
    get_admin_auth,
    create_admin_auth,
    verify_admin_password,
    mark_admin_logged_in_db,
)
from security.admin_session import is_admin_logged_in, mark_admin_logged_in

router = Router(name="admin_login")


def _is_admin(user_id: int) -> bool:
    return user_id in settings.ADMINS


@router.message(Command("login"))
async def cmd_login(message: Message) -> None:
    """Admin login.

    /login
      - first time: generates a password and stores argon2id hash in DB
      - next times: /login <password>
    """
    uid = message.from_user.id if message.from_user else 0

    if not _is_admin(uid):
        await message.answer("❌ У вас нет прав для этой команды.")
        return

    if is_admin_logged_in(uid):
        await message.answer("✅ Вы уже авторизованы.")
        return

    parts = (message.text or "").split(maxsplit=1)
    supplied_password = parts[1].strip() if len(parts) == 2 else None

    auth = await get_admin_auth(uid)

    if auth is None:
        password = secrets.token_urlsafe(12)
        await create_admin_auth(uid, password)
        mark_admin_logged_in(uid)
        await message.answer(
            (
                "🔐 <b>Создан пароль администратора</b> (первый вход).\n\n"
                f"Пароль: <code>{password}</code>\n\n"
                "Сохраните его в надёжном месте. Повторно показать пароль нельзя.\n"
                "Для следующих входов: <code>/login пароль</code>"
            )
        )
        return

    if not supplied_password:
        await message.answer("Использование: <code>/login пароль</code>")
        return

    ok = await verify_admin_password(uid, supplied_password)
    if not ok:
        await message.answer("❌ Неверный пароль.")
        return

    mark_admin_logged_in(uid)
    await mark_admin_logged_in_db(uid)
    await message.answer("✅ Авторизация успешна.")
