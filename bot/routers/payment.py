
from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery
from aiogram.filters import Command
from db.repo_users import get_or_create_user
from services.payments import TARIFFS, build_prices, handle_successful_payment
from config import ADMINS
from services.buy_control import (
    apply_buy_settings,
    is_buy_enabled,
    set_buy_enabled,
    set_buy_price,
)

router = Router(name="payments")


def _is_admin(user_id: int) -> bool:
    return user_id in ADMINS


@router.message(Command("closebuy"))
async def cmd_closebuy(message: Message):
    """Toggle buy availability. Admin-only."""
    if not _is_admin(message.from_user.id):
        return await message.answer("❌ У вас нет прав для этой команды.")

    # Toggle enabled flag
    currently_enabled = is_buy_enabled(TARIFFS)
    data = set_buy_enabled(not currently_enabled, TARIFFS)
    apply_buy_settings(TARIFFS)

    state = "открыта ✅" if data["enabled"] else "закрыта ❌"
    await message.answer(
        f"Покупка {state}.\n"
        f"Текущая цена: {data['price']} ⭐"
    )


@router.message(Command("editbuy"))
async def cmd_editbuy(message: Message):
    """/editbuy <стоимость> — change tariff price in Stars. Admin-only."""
    if not _is_admin(message.from_user.id):
        return await message.answer("❌ У вас нет прав для этой команды.")

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("Использование: /editbuy <стоимость в ⭐>")

    raw = parts[1].strip()
    try:
        price = int(raw)
    except ValueError:
        return await message.answer("❌ Стоимость должна быть целым числом.")

    if price <= 0:
        return await message.answer("❌ Стоимость должна быть больше 0.")

    data = set_buy_price(price, TARIFFS)
    apply_buy_settings(TARIFFS)
    await message.answer(
        f"✅ Цена обновлена.\n"
        f"Покупка: {'открыта ✅' if data['enabled'] else 'закрыта ❌'}\n"
        f"Новая цена: {data['price']} ⭐"
    )

@router.message(Command("testbuy"))
async def test_buy(message: Message):

    if not _is_admin(message.from_user.id):
        return await message.answer("❌ У вас нет прав для этой команды.")

    real_id = message.from_user.id
    user = await get_or_create_user(real_id)

    tariff = TARIFFS[0]

    await message.answer("⚠️ Тестовая покупка...\nБез Stars, без оплаты.")

    await handle_successful_payment(
        bot=message.bot,
        message=message,
        user=user,
        tariff=tariff
    )

@router.message(Command("buy"))
async def cmd_buy(message: Message):
    if not is_buy_enabled(TARIFFS):
        return await message.answer("🚫 Покупка временно закрыта. Попробуйте позже.")

    real_id = message.from_user.id
    user = await get_or_create_user(real_id)
    apply_buy_settings(TARIFFS)
    tariff = TARIFFS[0]

    await message.answer_invoice(
        title=tariff.title,
        description=tariff.description,
        prices=build_prices(tariff),
        payload=f"tariff:{0}", 
        currency="XTR",  
        provider_token="",  
    )


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("tariff:"):
        return

    index = int(payload.split(":", 1)[1])
    tariff = TARIFFS[index]

    real_id = message.from_user.id
    user = await get_or_create_user(real_id)

    await handle_successful_payment(message.bot, message, user, tariff)
