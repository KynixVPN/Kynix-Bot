from dataclasses import dataclass
from typing import List

from aiogram import Bot
from aiogram.types import LabeledPrice, PreCheckoutQuery, Message

from config import settings
from db.base import async_session
from db.models import Subscription, User
from services.xui_client import create_client_for_user, XuiError


@dataclass
class Tariff:
    title: str
    description: str
    stars_amount: int
    days: int


TARIFFS: List[Tariff] = [
    Tariff(
        title="VPN на 1 месяц",
        description="Подписка на 31 день",
        stars_amount=100,
        days=31,
    ),
]


def build_prices(tariff: Tariff) -> List[LabeledPrice]:
    # Telegram Stars используются как обычные параметры цены
    return [LabeledPrice(label=tariff.title, amount=tariff.stars_amount)]


async def handle_successful_payment(bot: Bot, message: Message, user: User, tariff: Tariff):
    """
    Вызывается:
      — либо после real successful_payment
      — либо из /testbuy для имитации покупки
    """
    try:
        # 🔹 создаём клиента в X-UI
        xui_data = await create_client_for_user(user.fake_id, days=tariff.days)
        # xui_data ожидаем ТАКОЙ:
        # {
        #   "clientId": "uuid",
        #   "config": "vless://...",
        #   "email": "FAKE_ID"
        # }

        config_text = xui_data["vless"]
        client_id = xui_data.get("clientId")
        email = xui_data.get("email")

    except XuiError as e:
        # уведомить админов
        from config import settings as _s

        text_admin = (
            "❗ Ошибка 3x-ui\n"
            f"FAKE ID: {user.fake_id}\n"
            f"Ошибка: {e}\n"
        )
        for admin_id in _s.ADMINS:
            try:
                await bot.send_message(admin_id, text_admin)
            except Exception:
                pass

        await message.answer(
            "Произошла ошибка при выдаче VPN-конфига. "
            "Мы уже занимаемся этим, попробуйте позже."
        )
        return

    # 🔹 сохраняем подписку в БД
    async with async_session() as session:
        sub = Subscription(
            user_id=user.id,
            active=True,
            xui_client_id=str(client_id) if client_id else None,
            xui_email=email,
            xui_config=config_text,
        )
        session.add(sub)
        await session.commit()

    # 🔹 отправляем конфиг пользователю
    await message.answer(
        "✅ Подписка активирована!\n"
        "Вот ваш VPN-конфиг:\n\n"
        f"<code>{config_text}</code>"
        "\n\n"
        f"- <a href=\"{settings.INSTRUCTION_URL}\">Инструкция по подключению Kynix VPN и приложения</a>"
    )

    # 🔹 уведомление админам
    from config import settings as _s

    text_admin = (
        "💸 Успешная (в том числе тестовая) выдача конфига\n"
        f"FAKE ID: {user.fake_id}\n"
        f"Тариф: {tariff.title}\n"
    )
    for admin_id in _s.ADMINS:
        try:
            await bot.send_message(admin_id, text_admin)
        except Exception:
            pass
