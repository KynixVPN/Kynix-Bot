from __future__ import annotations

from datetime import datetime

from argon2 import PasswordHasher
from argon2.low_level import Type as Argon2Type
from argon2.exceptions import VerifyMismatchError

from db.base import async_session
from db.models import AdminAuth

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, hash_len=32, type=Argon2Type.ID)


async def get_admin_auth(tg_id: int) -> AdminAuth | None:
    async with async_session() as session:
        return await session.get(AdminAuth, tg_id)


async def create_admin_auth(tg_id: int, password: str) -> AdminAuth:
    password_hash = _hasher.hash(password)

    async with async_session() as session:
        row = AdminAuth(tg_id=tg_id, password_hash=password_hash, created_at=datetime.utcnow())
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def verify_admin_password(tg_id: int, password: str) -> bool:
    row = await get_admin_auth(tg_id)
    if row is None:
        return False
    try:
        return _hasher.verify(row.password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


async def mark_admin_logged_in_db(tg_id: int) -> None:
    async with async_session() as session:
        row = await session.get(AdminAuth, tg_id)
        if not row:
            return
        row.last_login_at = datetime.utcnow()
        await session.commit()
