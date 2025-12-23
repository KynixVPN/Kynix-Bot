import asyncio
import aiohttp
import sys
import logging

from config import settings
from services.xui_client import delete_xui_client
from db.base import async_session
from db.models import User, Subscription
from sqlalchemy import select


logger = logging.getLogger("refund")
logging.basicConfig(level=logging.INFO)


async def refund_stars(user_id: int, charge_id: str, token: str | None = None):
    """
    Вызывает метод refundStarPayment у Telegram Bot API.

    user_id  – НАСТОЯЩИЙ Telegram ID пользователя
    charge_id – telegram_payment_charge_id
    token    – токен бота, если None -> берём из settings.BOT_TOKEN
    """
    if token is None:
        token = settings.BOT_TOKEN

    url = f"https://api.telegram.org/bot{token}/refundStarPayment"

    payload = {
        "user_id": user_id,
        "telegram_payment_charge_id": charge_id
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = {"ok": False, "description": "Invalid JSON response"}

            return data


async def remove_user_subscription(fake_id: int):
    """
    Удаление VPN-клиента в XUI + деактивация подписки в БД.
    """

    async with async_session() as session:
        # ищем пользователя по fake_id
        res_user = await session.execute(
            select(User).where(User.fake_id == fake_id)
        )
        user = res_user.scalar_one_or_none()
        if not user:
            raise ValueError(f"User with FakeID {fake_id} not found")

        # подписка
        res_sub = await session.execute(
            select(Subscription)
            .where(Subscription.user_id == user.id, Subscription.active == True)  # noqa: E712
            .order_by(Subscription.id.desc())
        )
        sub = res_sub.scalar_one_or_none()

        if not sub:
            return "No active subscription."

        # удаляем клиента из XUI
        if sub.xui_email:
            try:
                await delete_xui_client(sub.xui_email)
                logger.info(f"Deleted XUI client {sub.xui_email}")
            except Exception as e:
                logger.warning(f"XUI delete failed: {e}")

        # деактивируем подписку
        sub.active = False
        await session.commit()

        return "Subscription removed."


async def refund_and_remove(fake_id: int, tg_user_id: int, charge_id: str):
    """
    Полный цикл:
    1) Возврат Stars
    2) Удаление подписки и XUI клиента
    """

    # 1) Возврат Stars
    logger.info("Refunding Stars...")
    res = await refund_stars(
        user_id=tg_user_id,
        charge_id=charge_id,
    )

    if not res.get("ok"):
        logger.error(f"Refund ERROR: {res}")
        return f"❌ Refund failed: {res.get('description')}"

    # 2) Удаление подписки
    logger.info("Removing subscription...")
    delete_msg = await remove_user_subscription(fake_id)

    return f"✅ REFUND DONE\n{delete_msg}"


# ============================
# CLI MODE (python payments_refund.py ...)
# ============================

async def main():
    if len(sys.argv) != 4:
        print("Использование:")
        print("python payments_refund.py <FAKE_ID> <TG_USER_ID> <CHARGE_ID>")
        sys.exit(1)

    fake_id = int(sys.argv[1])
    tg_user_id = int(sys.argv[2])
    charge_id = sys.argv[3]

    result = await refund_and_remove(fake_id, tg_user_id, charge_id)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
