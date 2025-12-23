from datetime import datetime, timedelta
import math

from sqlalchemy import select, update
from db.base import async_session
from db.models import Subscription, User
from services.xui_client import create_client_inf, create_client_for_user


# ============================
# Получить последнюю подписку
# ============================

async def get_user_last_subscription(user_id: int):
    async with async_session() as session:
        q = (
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.id.desc())
            .limit(1)
        )
        res = await session.execute(q)
        return res.scalar_one_or_none()



# ============================
# Получить активную подписку
# ============================

async def get_user_active_subscription(user_id: int):
    async with async_session() as session:
        q = (
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.active.is_(True))
            .order_by(Subscription.id.desc())
            .limit(1)
        )
        res = await session.execute(q)
        return res.scalar_one_or_none()


# ============================
# Обновить (пересоздать) конфиг в X-UI для текущей подписки
# ============================

async def refresh_subscription_config(sub: Subscription, fake_id: int):
    """Пересоздаёт X-UI client для подписки и обновляет поля xui_* в БД.

    Тип подписки определяется по expires_at:
      - expires_at is None => Infinite (inbound XUI_INBOUND_ID_INF)
      - иначе Plus, срок берём как оставшиеся дни до expires_at (минимум 1 день)
    """
    # Создаём новый клиент в X-UI
    if sub.expires_at is None:
        xui = await create_client_inf(fake_id=fake_id)
    else:
        now = datetime.utcnow()
        delta = sub.expires_at - now
        days_left = max(1, math.ceil(delta.total_seconds() / 86400))
        xui = await create_client_for_user(fake_id=fake_id, days=days_left)

    async with async_session() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == sub.id)
            .values(
                xui_client_id=xui["uuid"],
                xui_email=xui["email"],
                xui_config=xui["vless"],
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    # Обновим объект в памяти для удобства
    sub.xui_client_id = xui["uuid"]
    sub.xui_email = xui["email"]
    sub.xui_config = xui["vless"]
    return sub
# ============================
# Деактивировать ВСЕ подписки пользователя
# ============================

async def deactivate_user_subscriptions(user_id: int):
    """
    Полностью деактивирует все подписки пользователя.
    Используется при возврате средств или выдаче новой INFINITE.
    """
    async with async_session() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(active=False)
        )
        await session.commit()


# ============================
# Создать обычную (Plus) подписку
# ============================

async def create_subscription(user_id: int, days: int):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()

        xui = await create_client_for_user(user.fake_id, days=days)

        sub = Subscription(
            user_id=user_id,
            active=True,
            expires_at=datetime.utcnow() + timedelta(days=days),
            xui_client_id=xui["uuid"],
            xui_email=xui["email"],
            xui_config=xui["vless"],
            created_at=datetime.utcnow(),
        )

        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return sub


# ============================
# Создать INFINITE
# ============================

async def create_subscription_inf(user_id: int, fake_id: int):
    async with async_session() as session:

        # Деактивируем все предыдущие подписки
        await session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(active=False)
        )

        # Создаём бесконечного клиента в XUI
        xui = await create_client_inf(fake_id)

        new_sub = Subscription(
            user_id=user_id,
            active=True,
            expires_at=None,
            xui_client_id=xui["uuid"],
            xui_email=xui["email"],
            xui_config=xui["vless"],
            created_at=datetime.utcnow(),
        )

        session.add(new_sub)
        await session.commit()
        await session.refresh(new_sub)
        return new_sub
