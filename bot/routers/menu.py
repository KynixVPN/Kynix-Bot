from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    PreCheckoutQuery,
)

from db.repo_users import get_or_create_user, get_user_by_fakeid, delete_user_data_by_fakeid
from db.repo_subs import (
    get_user_last_subscription,
    get_user_active_subscription,
    refresh_subscription_config,
    create_subscription_inf,
    create_subscription,
    deactivate_user_subscriptions,
    upsert_plus_subscription_until,
)

from services.payments import TARIFFS, build_prices, handle_successful_payment
from services.payments_refund import refund_stars
from services.xui_client import delete_xui_client

from config import ADMINS, settings

from db.base import async_session
from db.models import SupportTicket
from security.memory_store import remember_support_user, refresh_can_run, refresh_mark_run

router = Router(name="menu")


async def safe_delete_message(message: Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except TelegramForbiddenError:
        return
    except TelegramBadRequest as e:
        err = str(e)
        if "message to delete not found" in err or "message can't be deleted" in err:
            return
        raise


def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Plus", callback_data="menu_plus")],
        [InlineKeyboardButton(text="Профиль", callback_data="menu_profile")],
        [InlineKeyboardButton(text="Support", callback_data="menu_support")],
    ])


def plus_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Купить", callback_data="menu_buy_plus")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_home")],
    ])


def profile_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 УДАЛИТЬ", callback_data="profile_delete_start")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_home")],
    ])


def profile_delete_confirm_1_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="profile_delete_confirm_1")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_profile")],
    ])


def profile_delete_confirm_2_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 УДАЛИТЬ НАВСЕГДА", callback_data="profile_delete_confirm_2")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_profile")],
    ])



def support_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Закрыть обращение", callback_data="support_close_user")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_home")]
    ])


@router.callback_query(F.data == "menu_support")
async def menu_support(call: CallbackQuery):
    await call.answer()

    real_id = call.from_user.id
    user = await get_or_create_user(real_id)

    remember_support_user(user.fake_id, real_id)

    async with async_session() as session:
        from sqlalchemy import select

        q = select(SupportTicket).where(
            SupportTicket.user_id == user.id,
            SupportTicket.is_open.is_(True),
        )
        res = await session.execute(q)
        ticket = res.scalars().first()

        new_ticket_created = False
        if not ticket:
            ticket = SupportTicket(user_id=user.id, is_open=True)
            session.add(ticket)
            await session.commit()
            await session.refresh(ticket)
            new_ticket_created = True

    text = (
        "🛠 <b>Поддержка</b>\n\n"
        "Опишите вашу проблему в сообщении.\n"
        "Ваши сообщения будут отправлены администратору.\n\n"
        "Если вопрос решён — закройте обращение кнопкой ниже."
    )

    try:
        if call.message.text:
            await call.message.edit_text(text, reply_markup=support_menu_kb())
        elif call.message.caption:
            await call.message.edit_caption(
                caption=text,
                reply_markup=support_menu_kb()
            )
        else:
            await call.message.answer(text, reply_markup=support_menu_kb())
    except Exception:
        await call.message.answer(text, reply_markup=support_menu_kb())

    if new_ticket_created:
        text_admin = f"""📩 Обращение в поддержку
FAKE ID: {user.fake_id}
Ticket ID: {ticket.id}
"""
        for admin_id in settings.ADMINS:
            try:
                await call.message.bot.send_message(admin_id, text_admin)
            except Exception:
                pass


@router.message(F.text == "/start")
async def cmd_start(message: Message):
    user = await get_or_create_user(message.from_user.id)

    photo = FSInputFile("images/start.jpg")

    text = (
        "<b>Добро пожаловать в Kynix VPN 💜</b>\n\n"
        "<b>📦 Тарифный план:</b>\n\n"
        "<b>Plus</b>\n"
        "• Безлимитный трафик\n"
        "• 10 устройств\n"
        "• Цена: 100⭐ / месяц\n\n"
        f"Ваш Fake ID: <code>{user.fake_id}</code>"
    )

    await message.answer_photo(photo, caption=text, reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu_plus")
async def menu_plus(call: CallbackQuery):
    await call.answer()

    photo = FSInputFile("images/plus.jpg")
    text = (
        "<b>Тариф Plus</b>\n\n"
        "• Безлимитный трафик\n"
        "• До 10 устройств\n"
        "• Приоритетная поддержка\n"
        "• Цена: 100⭐ / месяц\n\n"
        "Нажатие на кнопку «Купить» или последующая покупка "
        "подразумевает согласие с:\n"
        f"• <a href='{settings.PRIVACY_URL}'>Политикой конфиденциальности</a>\n"
        f"• <a href='{settings.TERMS_URL}'>Правилами использования</a>"
    )

    await call.message.answer_photo(photo, caption=text, reply_markup=plus_menu_kb())
    await safe_delete_message(call.message)




@router.callback_query(F.data == "menu_buy_plus")
async def menu_buy_plus(call: CallbackQuery):
    await call.answer()

    tariff = TARIFFS[0]

    await call.message.answer_invoice(
        title=f"Kynix VPN — {tariff.title}",
        description=tariff.description,
        payload="vpn_plus",
        provider_token="",
        currency="XTR",
        prices=build_prices(tariff),
    )


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_q: PreCheckoutQuery):
    await pre_checkout_q.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    user = await get_or_create_user(message.from_user.id)
    tariff = TARIFFS[0]

    await handle_successful_payment(
        bot=message.bot,
        message=message,
        user=user,
        tariff=tariff
    )


@router.callback_query(F.data == "menu_profile")
async def menu_profile(call: CallbackQuery):
    await call.answer()

    user = await get_or_create_user(call.from_user.id)
    sub = await get_user_last_subscription(user.id)

    sub_type = "Нет"
    expires = "Нет"

    if sub and sub.active:
        sub_type = "Infinite ♾️" if sub.expires_at is None else "Plus"
        if sub.expires_at:
            expires = sub.expires_at.strftime("%Y-%m-%d %H:%M")

    photo = FSInputFile("images/start.jpg")

    text = (
        "<b>Ваш профиль</b>\n\n"
        f"• FakeID: <code>{user.fake_id}</code>\n"
        f"• Тип подписки: {sub_type}\n"
        f"• Срок окончания: {expires}"
    )

    await call.message.answer_photo(photo, caption=text, reply_markup=profile_menu_kb())
    await safe_delete_message(call.message)


    
@router.callback_query(F.data == "profile_delete_start")
async def profile_delete_start(call: CallbackQuery):
    await call.answer()
    user = await get_or_create_user(call.from_user.id)

    text = (
        "⚠️ <b>Удаление данных</b>\n\n"
        "Будут удалены <b>все</b> записи в базе, связанные с вашим FakeID, "
        "а также конфиг (если он был создан).\n\n"
        f"FakeID: <code>{user.fake_id}</code>\n\n"
        "Продолжить?"
    )

    await call.message.answer(text, reply_markup=profile_delete_confirm_1_kb())
    await safe_delete_message(call.message)


@router.callback_query(F.data == "profile_delete_confirm_1")
async def profile_delete_confirm_1(call: CallbackQuery):
    await call.answer()
    user = await get_or_create_user(call.from_user.id)

    text = (
        "⚠️ <b>Последнее предупреждение</b>\n\n"
        "Это действие необратимо. После удаления доступ "
        "будет потерян (при следующем обращении бот создаст новый профиль).\n\n"
        f"FakeID: <code>{user.fake_id}</code>\n\n"
        "Точно удалить?"
    )

    await call.message.answer(text, reply_markup=profile_delete_confirm_2_kb())
    await safe_delete_message(call.message)


@router.callback_query(F.data == "profile_delete_confirm_2")
async def profile_delete_confirm_2(call: CallbackQuery):
    await call.answer()
    user = await get_or_create_user(call.from_user.id)

    ok = await delete_user_data_by_fakeid(user.fake_id)

    if ok:
        text = "✅ Данные удалены. Если вы продолжите пользоваться ботом, будет создан новый профиль."
    else:
        text = "ℹ️ Профиль не найден (возможно, уже был удалён)."

    await call.message.answer(text, reply_markup=main_menu_kb())
    await safe_delete_message(call.message)




@router.message(F.text.startswith("/inf"))
async def cmd_inf(message: Message):
    if message.from_user.id not in ADMINS:
        return await message.answer("❌ У вас нет прав.")

    parts = message.text.split()
    if len(parts) != 2:
        return await message.answer("Использование: /inf FAKE_ID")

    fake_id = int(parts[1])
    user = await get_user_by_fakeid(fake_id)

    if not user:
        return await message.answer("❌ Пользователь не найден.")

    sub = await create_subscription_inf(user.id, fake_id)

    return await message.answer(
        "🎁 Выдана <b>бессрочная подписка</b>!\n\n"
        f"<code>{sub.xui_config}</code>"
    )


async def _try_delete_xui_for_fake_id(fake_id: int) -> tuple[bool, str | None]:
    sub = None
    try:
        user = await get_user_by_fakeid(fake_id)
        if user:
            sub = await get_user_last_subscription(user.id)
    except Exception:
        sub = None

    if sub and sub.active:
        inbound_candidates = [
            int(settings.XUI_INBOUND_ID_INF) if sub.expires_at is None else int(settings.XUI_INBOUND_ID)
        ]
    else:
        inbound_candidates = [int(settings.XUI_INBOUND_ID), int(settings.XUI_INBOUND_ID_INF)]

    last_err: str | None = None
    for inbound_id in inbound_candidates:
        try:
            await delete_xui_client(email=str(fake_id), inbound_id=inbound_id)
            return True, None
        except Exception as e:
            last_err = str(e)

    return False, last_err


@router.message(F.text.startswith("/del"))
async def cmd_del(message: Message):
    if message.from_user.id not in ADMINS:
        return await message.answer("❌ У вас нет прав.")

    parts = (message.text or "").split()
    if len(parts) != 2:
        return await message.answer("Использование: /del FAKE_ID")

    try:
        fake_id = int(parts[1])
    except ValueError:
        return await message.answer("❌ FAKE_ID должен быть числом.")

    user = await get_user_by_fakeid(fake_id)
    if not user:
        return await message.answer("❌ Пользователь не найден.")

    deleted, err = await _try_delete_xui_for_fake_id(fake_id)
    await deactivate_user_subscriptions(user.id)

    if deleted:
        return await message.answer("✅ Подписка удалена: конфиг удалён, подписка деактивирована.")

    return await message.answer(
        "⚠️ Подписка деактивирована, но не удалось удалить конфиг в X-UI:\n"
        f"<code>{err or 'Неизвестная ошибка'}</code>"
    )


@router.message(F.text.startswith("/month"))
async def cmd_month(message: Message):
    if message.from_user.id not in ADMINS:
        return await message.answer("❌ У вас нет прав.")

    parts = (message.text or "").split()
    if len(parts) != 2:
        return await message.answer("Использование: /month FAKE_ID")

    try:
        fake_id = int(parts[1])
    except ValueError:
        return await message.answer("❌ FAKE_ID должен быть числом.")

    user = await get_user_by_fakeid(fake_id)
    if not user:
        return await message.answer("❌ Пользователь не найден.")

    await _try_delete_xui_for_fake_id(fake_id)
    await deactivate_user_subscriptions(user.id)

    sub = await create_subscription(user.id, days=30)

    return await message.answer(
        "📅 Выдана подписка на <b>1 месяц</b>!\n\n"
        f"<code>{sub.xui_config}</code>"
    )


@router.message(F.text.startswith("/year"))
async def cmd_year(message: Message):
    if message.from_user.id not in ADMINS:
        return await message.answer("❌ У вас нет прав.")

    parts = (message.text or "").split()
    if len(parts) != 2:
        return await message.answer("Использование: /year FAKE_ID")

    try:
        fake_id = int(parts[1])
    except ValueError:
        return await message.answer("❌ FAKE_ID должен быть числом.")

    user = await get_user_by_fakeid(fake_id)
    if not user:
        return await message.answer("❌ Пользователь не найден.")

    await _try_delete_xui_for_fake_id(fake_id)
    await deactivate_user_subscriptions(user.id)

    sub = await create_subscription(user.id, days=365)

    return await message.answer(
        "📅 Выдана подписка на <b>1 год</b>!\n\n"
        f"<code>{sub.xui_config}</code>"
    )


@router.message(F.text.startswith("/subs"))
async def cmd_subs_until(message: Message):
    if message.from_user.id not in ADMINS:
        return await message.answer("❌ У вас нет прав.")

    parts = (message.text or "").split()
    if len(parts) != 3:
        return await message.answer(
            "Использование: <code>/subs FAKE_ID ДД.ММ.ГГГГ</code>\n"
            "Пример: <code>/subs 123456 31.12.2026</code>"
        )

    try:
        fake_id = int(parts[1])
    except ValueError:
        return await message.answer("❌ FAKE_ID должен быть числом.")

    date_str = parts[2].strip()
    from datetime import datetime

    try:
        d = datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        return await message.answer(
            "❌ Неверный формат даты. Используйте <code>ДД.ММ.ГГГГ</code>, например <code>01.01.2026</code>."
        )

    expires_at = d.replace(hour=23, minute=59, second=59, microsecond=0)

    user = await get_user_by_fakeid(fake_id)
    if not user:
        return await message.answer("❌ Пользователь не найден.")

    active_sub = await get_user_active_subscription(user.id)
    if active_sub and active_sub.expires_at is not None and active_sub.active:
        if active_sub.expires_at >= expires_at:
            return await message.answer(
                "✅ У пользователя уже есть подписка Plus, срок которой не меньше указанного.\n"
                f"Текущий срок: <b>{active_sub.expires_at.strftime('%Y-%m-%d %H:%M')}</b>"
            )

    sub = await upsert_plus_subscription_until(user.id, fake_id=fake_id, expires_at=expires_at)

    return await message.answer(
        "📅 Подписка Plus выдана/продлена до <b>{}</b>!\n\n"
        "<code>{}</code>".format(sub.expires_at.strftime("%Y-%m-%d %H:%M"), sub.xui_config)
    )


@router.message(F.text.startswith("/refresh"))
async def cmd_refresh(message: Message):
    parts = (message.text or "").split()
    if len(parts) > 1:
        return await message.answer("Использование: /refresh")

    real_id = message.from_user.id
    user = await get_or_create_user(real_id)
    fake_id = user.fake_id

    ok, remaining_sec = refresh_can_run(real_id)
    if not ok:
        remaining_min = max(5, (remaining_sec + 59) // 60)
        return await message.answer(
            "⏳ Команду можно использовать раз в 30 минут.\n"
            f"Попробуйте снова примерно через <b>{remaining_min}</b> мин."
        )

    sub = await get_user_active_subscription(user.id)
    if not sub:
        return await message.answer(
            "❌ У вас нет активной подписки.\n\n"
            "Откройте меню и оформите тариф <b>Plus</b>."
        )

    inbound_id = int(settings.XUI_INBOUND_ID_INF) if sub.expires_at is None else int(settings.XUI_INBOUND_ID)

    try:
        await delete_xui_client(email=str(fake_id), inbound_id=inbound_id)
    except Exception:
        pass

    try:
        sub = await refresh_subscription_config(sub=sub, fake_id=fake_id)
    except Exception as e:
        return await message.answer(
            "❌ Ошибка при создании нового конфига:\n"
            f"<code>{e}</code>"
        )

    refresh_mark_run(real_id)

    return await message.answer(
        "✅ Конфиг обновлён!\n\n"
        f"<code>{sub.xui_config}</code>"
    )

@router.message(F.text.startswith("/refund"))
async def cmd_refund(message: Message):
    if message.from_user.id not in ADMINS:
        return await message.answer("❌ У вас нет прав.")

    parts = message.text.split()
    if len(parts) != 4:
        return await message.answer(
            "Использование:\n"
            "<code>/refund FAKE_ID REAL_ID CHARGE_ID</code>"
        )

    try:
        fake_id = int(parts[1])
        real_id = int(parts[2])
    except ValueError:
        return await message.answer("❌ FAKE_ID и REAL_ID должны быть числами.")

    charge_id = parts[3]

    user = await get_user_by_fakeid(fake_id)
    if not user:
        return await message.answer("❌ Пользователь с таким FAKE_ID не найден.")

    sub = await get_user_last_subscription(user.id)
    if not sub or not sub.active:
        return await message.answer("❌ У пользователя нет активной подписки.")

    if getattr(sub, "expires_at", None) is None:
        inbound_id = int(settings.XUI_INBOUND_ID_INF)
    else:
        inbound_id = int(settings.XUI_INBOUND_ID)

    try:
        await delete_xui_client(email=str(fake_id), inbound_id=inbound_id)
    except Exception as e:
        return await message.answer(
            "❌ Ошибка при удалении конфига в X-UI:\n"
            f"<code>{e}</code>"
        )

    await deactivate_user_subscriptions(user.id)

    result = await refund_stars(
        user_id=real_id,
        charge_id=charge_id
    )

    if result.get("ok"):
        return await message.answer(
            "✅ Возврат выполнен!\n"
            "• Конфиг удалён\n"
            "• Подписка деактивирована\n"
            "• Средства возвращены пользователю"
        )
    else:
        desc = result.get("description", "Неизвестная ошибка Telegram")
        return await message.answer(
            "❌ Telegram отклонил возврат:\n"
            f"<code>{desc}</code>"
        )


@router.callback_query(F.data == "menu_home")
async def menu_home(call: CallbackQuery):
    await call.answer()

    user = await get_or_create_user(call.from_user.id)
    photo = FSInputFile("images/start.jpg")

    text = (
        "<b>Добро пожаловать в Kynix VPN 💜</b>\n\n"
        "<b>Plus</b>\n"
        "• Безлимитный VPN\n"
        "• 10 устройств\n"
        "• Цена: 100⭐ / месяц\n\n"
        f"Ваш FakeID: <code>{user.fake_id}</code>"
    )

    await call.message.answer_photo(photo, caption=text, reply_markup=main_menu_kb())

    await safe_delete_message(call.message)