
from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery
from aiogram.filters import Command
from db.repo_users import get_or_create_user
from services.payments import TARIFFS, build_prices, handle_successful_payment
from config import ADMINS

router = Router(name="payments")

@router.message(Command("testbuy"))
async def test_buy(message: Message):
    """
    Имитирует успешную оплату без Telegram Stars.
    Полезно для тестирования 3x-ui и всей логики выдачи ключей.
    """
    # Разрешено только админам
    if message.from_user.id not in ADMINS:
        return await message.answer("❌ У вас нет прав для этой команды.")

    real_id = message.from_user.id
    user = await get_or_create_user(real_id)

    # Берём первый тариф
    tariff = TARIFFS[0]

    # Сообщение что имитация началась
    await message.answer("⚠️ Тестовая покупка...\nБез Stars, без оплаты.")

    # Вызываем обработчик успешной оплаты вручную
    await handle_successful_payment(
        bot=message.bot,
        message=message,
        user=user,
        tariff=tariff
    )

@router.message(Command("buy"))
async def cmd_buy(message: Message):
    """Пример покупки первого тарифа."""
    real_id = message.from_user.id
    user = await get_or_create_user(real_id)
    tariff = TARIFFS[0]

    await message.answer_invoice(
        title=tariff.title,
        description=tariff.description,
        prices=build_prices(tariff),
        payload=f"tariff:{0}",  # индекс тарифа
        currency="XTR",  # Telegram Stars
        provider_token="",  # для Stars оставляем пустым
    )


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    # Тут можно сделать дополнительные проверки, если нужно
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
