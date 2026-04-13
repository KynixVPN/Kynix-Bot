from dataclasses import dataclass
from typing import List
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import LabeledPrice, Message

from db.repo_subs import upsert_plus_subscription_until
from db.models import User
from services.xui_client import XuiError
from services.buy_control import apply_buy_settings


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

apply_buy_settings(TARIFFS)


def build_prices(tariff: Tariff) -> List[LabeledPrice]:
    return [LabeledPrice(label=tariff.title, amount=tariff.stars_amount)]


async def handle_successful_payment(bot: Bot, message: Message, user: User, tariff: Tariff):
    try:
        expires_at = None if not getattr(tariff, "days", None) else datetime.utcnow() + timedelta(days=tariff.days)
        await upsert_plus_subscription_until(user.id, fake_id=user.fake_id, expires_at=expires_at)
    except XuiError as e:
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
            "Произошла ошибка при активации VPN-ключей. "
            "Мы уже занимаемся этим, попробуйте позже."
        )
        return

    from config import settings as _s

    await message.answer(
        "✅ Подписка активирована!\n\n"
        "Спасибо за оплату. Перейдите в <b>Профиль → Мои ключи</b> и выберите нужный ключ.\n\n"
        "• <b>VLESS TCP</b> — наиболее совместимый\n"
        "• <b>VLESS xHTTP</b> — более устойчивый к блокировкам\n\n"
        f"- <a href=\"{_s.INSTRUCTION_URL}\">Инструкция по подключению Kynix VPN и приложения</a>"
    )

    text_admin = (
        "💸 Успешная (в том числе тестовая) активация подписки\n"
        f"FAKE ID: {user.fake_id}\n"
        f"Тариф: {tariff.title}\n"
    )
    for admin_id in _s.ADMINS:
        try:
            await bot.send_message(admin_id, text_admin)
        except Exception:
            pass
