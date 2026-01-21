from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from db.base import async_session
from db.models import Subscription, User
from config import settings
from services.xui_client import (
    build_vless_for_email,
    create_client_inf,
    create_client_for_user,
    create_client_for_user_until,
    delete_xui_client,
    update_xui_client_expiry,
)


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


async def refresh_subscription_config(sub: Subscription, fake_id: int) -> str:
    """Recreate X-UI client (new UUID) and return VLESS config WITHOUT storing it in DB."""
    if sub.expires_at is None:
        xui = await create_client_inf(fake_id=fake_id)
    else:
        xui = await create_client_for_user_until(fake_id=fake_id, expires_at=sub.expires_at)

    # Save only non-sensitive metadata
    async with async_session() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == sub.id)
            .values(
                xui_email=xui["email"],
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    sub.xui_email = xui["email"]
    return xui["vless"]


async def deactivate_user_subscriptions(user_id: int):
    async with async_session() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(active=False)
        )
        await session.commit()


async def create_subscription(user_id: int, days: int) -> tuple[Subscription, str]:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()

        xui = await create_client_for_user(user.fake_id, days=days)

        sub = Subscription(
            user_id=user_id,
            active=True,
            expires_at=datetime.utcnow() + timedelta(days=days),
            xui_email=xui["email"],
            created_at=datetime.utcnow(),
        )

        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return sub, xui["vless"]


async def create_subscription_inf(user_id: int, fake_id: int) -> tuple[Subscription, str]:
    async with async_session() as session:

        await session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(active=False)
        )

        xui = await create_client_inf(fake_id)

        new_sub = Subscription(
            user_id=user_id,
            active=True,
            expires_at=None,
            xui_email=xui["email"],
            created_at=datetime.utcnow(),
        )

        session.add(new_sub)
        await session.commit()
        await session.refresh(new_sub)
        return new_sub, xui["vless"]


async def upsert_plus_subscription_until(user_id: int, fake_id: int, expires_at: datetime) -> tuple[Subscription, str]:
    async with async_session() as session:

        q_plus = (
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.expires_at.is_not(None))
            .order_by(Subscription.id.desc())
            .limit(1)
        )
        res_plus = await session.execute(q_plus)
        last_plus = res_plus.scalar_one_or_none()

        if last_plus and last_plus.xui_email:
            ts = expires_at
            if ts.tzinfo is None:
                expiry_ts = int(ts.replace(tzinfo=timezone.utc).timestamp() * 1000)
            else:
                expiry_ts = int(ts.timestamp() * 1000)

            try:
                await update_xui_client_expiry(
                    email=str(last_plus.xui_email),
                    inbound_id=int(settings.XUI_INBOUND_ID),
                    expiry_ts=expiry_ts,
                )

                await session.execute(
                    update(Subscription)
                    .where(Subscription.user_id == user_id, Subscription.id != last_plus.id)
                    .values(active=False)
                )
                await session.execute(
                    update(Subscription)
                    .where(Subscription.id == last_plus.id)
                    .values(
                        active=True,
                        expires_at=expires_at,
                    )
                )
                await session.commit()

                last_plus.active = True
                last_plus.expires_at = expires_at

                # Rebuild config from X-UI (do not store it in DB)
                cfg = await build_vless_for_email(
                    email=str(last_plus.xui_email),
                    fake_id=fake_id,
                    expires_at=expires_at,
                    inbound_id=int(settings.XUI_INBOUND_ID),
                    tag="Plus",
                )
                return last_plus, cfg
            except Exception:
                try:
                    await delete_xui_client(
                        email=str(last_plus.xui_email),
                        inbound_id=int(settings.XUI_INBOUND_ID),
                    )
                except Exception:
                    pass

        xui = await create_client_for_user_until(fake_id=fake_id, expires_at=expires_at)
        await session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(active=False)
        )

        new_sub = Subscription(
            user_id=user_id,
            active=True,
            expires_at=expires_at,
            xui_email=xui["email"],
            created_at=datetime.utcnow(),
        )

        session.add(new_sub)
        await session.commit()
        await session.refresh(new_sub)
        return new_sub, xui["vless"]
