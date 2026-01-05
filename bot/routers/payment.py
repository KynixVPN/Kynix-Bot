
from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery
from aiogram.filters import Command
from db.repo_users import get_or_create_user
from services.payments import TARIFFS, build_prices, handle_successful_payment
from config import ADMINS

router = Router(name="payments")

@router.message(Command("testbuy"))
async def test_buy(message: Message):

    if message.from_user.id not in ADMINS:
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
    real_id = message.from_user.id
    user = await get_or_create_user(real_id)
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
