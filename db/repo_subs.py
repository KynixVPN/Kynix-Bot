from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from config import settings
from db.base import async_session
from db.models import Subscription, User
from services.xui_client import (
    PLAN_INF,
    PLAN_PLUS,
    TRANSPORT_TCP,
    build_vless_for_email,
    build_xui_email,
    create_client_for_user_until,
    create_client_inf,
    delete_xui_client,
    get_inbound_id_for_plan_transport,
    get_plan_for_expires_at,
    get_supported_transports,
    update_xui_client_expiry,
)


def _all_known_inbound_ids() -> list[int]:
    raw_values = [
        getattr(settings, "XUI_INBOUND_ID", None),
        getattr(settings, "XUI_INBOUND_ID_INF", None),
        getattr(settings, "XUI_INBOUND_ID_PLUS_TCP", None),
        getattr(settings, "XUI_INBOUND_ID_PLUS_XHTTP", None),
        getattr(settings, "XUI_INBOUND_ID_INF_TCP", None),
        getattr(settings, "XUI_INBOUND_ID_INF_XHTTP", None),
    ]
    result: list[int] = []
    for value in raw_values:
        if value in (None, ""):
            continue
        try:
            inbound_id = int(value)
        except Exception:
            continue
        if inbound_id not in result:
            result.append(inbound_id)
    return result


async def _delete_subscription_clients(fake_id: int, expires_at) -> None:
    emails = [build_xui_email(fake_id, transport) for transport in get_supported_transports()]
    inbound_ids = _all_known_inbound_ids()

    plan = get_plan_for_expires_at(expires_at)
    for transport in get_supported_transports():
        try:
            inbound_id = get_inbound_id_for_plan_transport(plan, transport)
            if inbound_id not in inbound_ids:
                inbound_ids.append(inbound_id)
        except Exception:
            pass

    for inbound_id in inbound_ids:
        for email in emails:
            try:
                await delete_xui_client(email=email, inbound_id=inbound_id)
            except Exception:
                pass


async def purge_expired_subscriptions() -> int:
    now = datetime.utcnow()

    async with async_session() as session:
        q = select(Subscription).where(
            Subscription.expires_at.is_not(None),
            Subscription.expires_at < now,
        )
        res = await session.execute(q)
        expired = res.scalars().all()

        deleted = 0
        user_ids = {sub.user_id for sub in expired}
        users_by_id = {}
        if user_ids:
            user_res = await session.execute(select(User).where(User.id.in_(user_ids)))
            users_by_id = {u.id: u for u in user_res.scalars().all()}

        for sub in expired:
            user = users_by_id.get(sub.user_id)
            if user:
                await _delete_subscription_clients(user.fake_id, sub.expires_at)

            await session.delete(sub)
            deleted += 1

        if deleted:
            await session.commit()
        return deleted


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


async def get_subscription_key(sub: Subscription, fake_id: int, transport: str) -> str:
    return await build_vless_for_email(
        email=build_xui_email(fake_id, transport),
        fake_id=fake_id,
        expires_at=sub.expires_at,
        transport=transport,
    )


async def refresh_subscription_config(sub: Subscription, fake_id: int) -> None:
    await _delete_subscription_clients(fake_id, sub.expires_at)

    if sub.expires_at is None:
        for transport in get_supported_transports():
            await create_client_inf(fake_id=fake_id, transport=transport)
    else:
        for transport in get_supported_transports():
            await create_client_for_user_until(fake_id=fake_id, expires_at=sub.expires_at, transport=transport)

    async with async_session() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == sub.id)
            .values(
                xui_email=build_xui_email(fake_id, TRANSPORT_TCP),
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    sub.xui_email = build_xui_email(fake_id, TRANSPORT_TCP)


async def deactivate_user_subscriptions(user_id: int):
    async with async_session() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(active=False)
        )
        await session.commit()


async def create_subscription(user_id: int, days: int) -> Subscription:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
        expires_at = datetime.utcnow() + timedelta(days=days)

        for transport in get_supported_transports():
            await create_client_for_user_until(user.fake_id, expires_at=expires_at, transport=transport)

        sub = Subscription(
            user_id=user_id,
            active=True,
            expires_at=expires_at,
            xui_email=build_xui_email(user.fake_id, TRANSPORT_TCP),
            created_at=datetime.utcnow(),
        )

        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return sub


async def create_subscription_inf(user_id: int, fake_id: int) -> Subscription:
    async with async_session() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(active=False)
        )

        for transport in get_supported_transports():
            await create_client_inf(fake_id, transport=transport)

        new_sub = Subscription(
            user_id=user_id,
            active=True,
            expires_at=None,
            xui_email=build_xui_email(fake_id, TRANSPORT_TCP),
            created_at=datetime.utcnow(),
        )

        session.add(new_sub)
        await session.commit()
        await session.refresh(new_sub)
        return new_sub


async def upsert_plus_subscription_until(user_id: int, fake_id: int, expires_at: datetime) -> Subscription:
    async with async_session() as session:
        q_plus = (
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.expires_at.is_not(None))
            .order_by(Subscription.id.desc())
            .limit(1)
        )
        res_plus = await session.execute(q_plus)
        last_plus = res_plus.scalar_one_or_none()

        if expires_at.tzinfo is None:
            expiry_ts = int(expires_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
        else:
            expiry_ts = int(expires_at.timestamp() * 1000)

        if last_plus:
            try:
                for transport in get_supported_transports():
                    await update_xui_client_expiry(
                        email=build_xui_email(fake_id, transport),
                        inbound_id=get_inbound_id_for_plan_transport(PLAN_PLUS, transport),
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
                        xui_email=build_xui_email(fake_id, TRANSPORT_TCP),
                    )
                )
                await session.commit()

                last_plus.active = True
                last_plus.expires_at = expires_at
                last_plus.xui_email = build_xui_email(fake_id, TRANSPORT_TCP)
                return last_plus
            except Exception:
                await _delete_subscription_clients(fake_id, expires_at)

        for transport in get_supported_transports():
            await create_client_for_user_until(fake_id=fake_id, expires_at=expires_at, transport=transport)

        await session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(active=False)
        )

        new_sub = Subscription(
            user_id=user_id,
            active=True,
            expires_at=expires_at,
            xui_email=build_xui_email(fake_id, TRANSPORT_TCP),
            created_at=datetime.utcnow(),
        )

        session.add(new_sub)
        await session.commit()
        await session.refresh(new_sub)
        return new_sub
