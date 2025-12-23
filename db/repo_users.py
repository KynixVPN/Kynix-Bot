from typing import Optional

from sqlalchemy import select

from .base import async_session
from .models import User
from security.hash_utils import hash_tg_id
from security.id_utils import generate_fake_id


async def get_or_create_user(real_tg_id: int) -> User:
    """
    Не сохраняем реальный Telegram ID.
    В базу кладём только tg_hash + fake_id.
    """
    tg_hash = hash_tg_id(str(real_tg_id))

    async with async_session() as session:
        q = select(User).where(User.tg_hash == tg_hash)
        result = await session.execute(q)
        user: Optional[User] = result.scalars().first()

        if user:
            return user

        fake_id = await _generate_unique_fake_id(session)

        user = User(
            tg_hash=tg_hash,
            fake_id=fake_id,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _generate_unique_fake_id(session) -> int:
    while True:
        fake_id = generate_fake_id()
        q = select(User).where(User.fake_id == fake_id)
        res = await session.execute(q)
        if not res.scalars().first():
            return fake_id


async def get_user_by_fakeid(fake_id: int) -> User | None:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.fake_id == fake_id)
        )
        return result.scalar_one_or_none()