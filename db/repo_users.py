from typing import Optional

from sqlalchemy import select, delete

from .base import async_session
from .models import User, Subscription, SupportTicket
from security.hash_utils import hash_tg_id
from security.id_utils import generate_fake_id
from services.xui_client import delete_xui_client


async def get_or_create_user(real_tg_id: int) -> User:
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


async def delete_user_data_by_fakeid(fake_id: int) -> bool:
    """Удаляет пользователя и все связанные записи по fake_id.
    Также пытается удалить клиентов/конфиги в XUI (если сохранены client_id).
    Возвращает True если пользователь найден и удалён, иначе False.
    """
    async with async_session() as session:
        # Найдём пользователя
        res = await session.execute(select(User).where(User.fake_id == fake_id))
        user = res.scalar_one_or_none()
        if user is None:
            return False

        # Удалим XUI клиентов (если есть)
        sub_res = await session.execute(select(Subscription).where(Subscription.user_id == user.id))
        subs = list(sub_res.scalars().all())
        for sub in subs:
            if sub.xui_client_id:
                try:
                    await delete_xui_client(sub.xui_client_id)
                except Exception:
                    # Не прерываем удаление данных в БД, даже если XUI недоступен
                    pass

        # Удаляем связанные записи
        await session.execute(delete(Subscription).where(Subscription.user_id == user.id))
        await session.execute(delete(SupportTicket).where(SupportTicket.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()
        return True
