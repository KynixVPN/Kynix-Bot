from datetime import datetime
import html

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from config import settings
from db.base import async_session
from db.models import SupportTicket, User
from db.repo_users import get_or_create_user
from security.memory_store import remember_support_user, forget_support_user, get_real_id

router = Router(name="support")


def _extract_fake_id(msg: Message | None, max_depth: int = 2) -> int | None:
    """
    Достаём FAKE ID из текста/подписи сообщения (или родительских reply).

    Важно: админам часто уходит служебное текстовое сообщение (с FAKE ID),
    а вложение отправляется reply на него. Тогда админ может ответить на вложение,
    и FAKE ID будет лежать уровнем выше.
    """
    cur = msg
    depth = 0
    while cur is not None and depth <= max_depth:
        payload = (cur.text or cur.caption or "")
        if payload:
            for word in payload.split():
                if word.isdigit() and len(word) == 8:
                    return int(word)
        cur = cur.reply_to_message
        depth += 1
    return None


def _extract_ticket_id(msg: Message | None, max_depth: int = 2) -> int | None:
    """
    Достаём Ticket ID из текста/подписи сообщения (или родительских reply).

    Ищем строку вида: "Ticket ID: 18"
    """
    cur = msg
    depth = 0
    while cur is not None and depth <= max_depth:
        payload = (cur.text or cur.caption or "")
        if payload:
            for line in payload.splitlines():
                if "ticket" in line.lower() and "id" in line.lower() and ":" in line:
                    tail = line.split(":", 1)[1].strip()
                    if tail.isdigit():
                        return int(tail)

            # фолбэк по токенам
            tokens = payload.replace("\n", " ").split()
            for i, tok in enumerate(tokens):
                if tok.lower().startswith("ticket") and i + 2 < len(tokens):
                    if tokens[i + 1].lower().startswith("id"):
                        cand = tokens[i + 2].strip()
                        if cand.isdigit():
                            return int(cand)

        cur = cur.reply_to_message
        depth += 1
    return None


async def _safe_copy(bot, to_chat_id: int, from_chat_id: int, message_id: int, reply_to_message_id: int | None = None):
    """Безопасно копируем сообщение (работает почти для всех типов вложений)."""
    try:
        return await bot.copy_message(
            chat_id=to_chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception:
        return None


# ============================
#     КОМАНДА /support
# ============================

@router.message(Command("support"))
async def cmd_support(message: Message):
    real_id = message.from_user.id
    user = await get_or_create_user(real_id)

    # Запоминаем real_id -> разрешаем пользователю писать в поддержку
    remember_support_user(user.fake_id, real_id)

    async with async_session() as session:
        ticket = SupportTicket(user_id=user.id, is_open=True)
        session.add(ticket)
        await session.commit()
        await session.refresh(ticket)

    # 1) Сообщение о создании тикета (ПОЛЬЗОВАТЕЛЮ)
    await message.answer(
        "✅ Тикет создан.\n"
        f"Ticket ID: {ticket.id}\n\n"
        "Опишите вашу проблему. Администраторы скоро ответят."
    )

    # Уведомление админам о новом тикете (ВНУТРЕННЕЕ)
    text_admin = (
        "📩 Обращение в поддержку\n"
        f"FAKE ID: {user.fake_id}\n"
        f"Ticket ID: {ticket.id}"
    )
    for admin_id in settings.ADMINS:
        try:
            await message.bot.send_message(admin_id, text_admin)
        except Exception:
            pass


# ============================
#   КНОПКА «ЗАКРЫТЬ» (пользователь)
# ============================

@router.callback_query(F.data == "support_close_user")
async def support_close_user(call: CallbackQuery):
    await call.answer("Обращение закрыто")

    real_id = call.from_user.id
    user = await get_or_create_user(real_id)

    closed_ticket_ids: list[int] = []

    async with async_session() as session:
        from sqlalchemy import select

        q = select(SupportTicket).where(
            SupportTicket.user_id == user.id,
            SupportTicket.is_open.is_(True)
        )
        res = await session.execute(q)
        tickets = res.scalars().all()

        if not tickets:
            try:
                await call.message.edit_text("У вас нет активных обращений.", reply_markup=None)
            except Exception:
                await call.message.answer("У вас нет активных обращений.")
            return

        for t in tickets:
            t.is_open = False
            t.closed_at = datetime.utcnow()
            closed_ticket_ids.append(t.id)

        await session.commit()

    # Запрещаем дальнейшие сообщения в поддержку, пока юзер снова не откроет /support
    forget_support_user(user.fake_id)

    # 4) Сообщение о закрытии тикета пользователем (ПОЛЬЗОВАТЕЛЮ)
    # (это отдельное сообщение, чтобы он точно увидел уведомление)
    try:
        await call.bot.send_message(
            real_id,
            "✅ Обращение закрыто.\n"
            + (f"Ticket ID: {closed_ticket_ids[0]}\n" if closed_ticket_ids else "")
        )
    except Exception:
        pass

    # Уведомление админам, что пользователь закрыл тикет (ВНУТРЕННЕЕ)
    for tid in closed_ticket_ids:
        text_admin = (
            "✅ Тикет закрыт пользователем\n"
            f"FAKE ID: {user.fake_id}\n"
            f"Ticket ID: {tid}"
        )
        for admin_id in settings.ADMINS:
            try:
                await call.bot.send_message(admin_id, text_admin)
            except Exception:
                pass

    # Если есть сообщение/меню, где была кнопка закрыть — редактируем (если можно)
    try:
        await call.message.edit_text(
            "Ваше обращение закрыто.\n"
            "Если появятся новые вопросы — используйте /support.",
            reply_markup=None
        )
    except Exception:
        pass


# ============================
#     ЗАКРЫТИЕ АДМИНОМ /close (reply на служебку)
# ============================

@router.message(Command("close"), F.reply_to_message)
async def cmd_close_ticket(message: Message):
    if message.from_user.id not in settings.ADMINS:
        return

    replied = message.reply_to_message
    fake_id = _extract_fake_id(replied)
    ticket_id = _extract_ticket_id(replied)

    if not fake_id:
        await message.answer("Не удалось определить FAKE ID.")
        return

    # real_id нужен, чтобы уведомить пользователя
    real_id = get_real_id(fake_id)

    async with async_session() as session:
        from sqlalchemy import select

        q = select(User).where(User.fake_id == fake_id)
        res = await session.execute(q)
        user = res.scalars().first()

        if not user:
            await message.answer("Пользователь не найден.")
            return

        q2 = select(SupportTicket).where(
            SupportTicket.user_id == user.id,
            SupportTicket.is_open.is_(True),
        )
        res2 = await session.execute(q2)
        tickets = res2.scalars().all()

        if ticket_id is None and tickets:
            ticket_id = tickets[0].id

        for t in tickets:
            t.is_open = False
            t.closed_at = datetime.utcnow()

        await session.commit()

    forget_support_user(fake_id)

    # Уведомляем пользователя о закрытии (без служебных данных)
    if real_id and ticket_id is not None:
        try:
            await message.bot.send_message(
                real_id,
                "Ваше обращение закрыто.\n"
                f"Если появятся новые вопросы — вы можете снова открыть поддержку."
            )
        except Exception:
            pass

    await message.answer(f"Тикет пользователя {fake_id} закрыт.")


# ============================
#     ОСНОВНАЯ ЛОГИКА СООБЩЕНИЙ
# ============================

@router.message()
async def support_messages(message: Message):
    # ----------------------------
    # Админ отвечает (reply) -> пользователю + всем админам (внутреннее)
    # ----------------------------
    if message.from_user.id in settings.ADMINS and message.reply_to_message:
        replied = message.reply_to_message

        fake_id = _extract_fake_id(replied)
        ticket_id = _extract_ticket_id(replied)

        if not fake_id:
            return

        real_id = get_real_id(fake_id)
        if not real_id:
            await message.answer("Не удалось доставить: real ID очищен (тикет закрыт/не открыт).")
            return

        # 3) Ответное сообщение клиенту должно быть ПРОСТО СООБЩЕНИЕМ (без служебки)
        if message.content_type == "text" and message.text:
            try:
                await message.bot.send_message(real_id, message.text)
            except Exception:
                pass
        else:
            copied = await _safe_copy(
                message.bot,
                real_id,
                message.chat.id,
                message.message_id,
                reply_to_message_id=None,
            )
            if copied is None:
                # fallback если copy не сработал
                fallback_text = message.text or message.caption or ""
                if fallback_text:
                    try:
                        await message.bot.send_message(real_id, fallback_text)
                    except Exception:
                        pass

        # Внутреннее дублирование всем админам (с заголовком)
        admin_label = message.from_user.username or message.from_user.full_name
        ticket_id_str = str(ticket_id) if ticket_id is not None else "?"
        header_admin = (
            "💬 Ответ администратора\n"
            f"Админ: {admin_label} ({message.from_user.id})\n"
            f"FAKE ID: {fake_id}\n"
            f"Ticket ID: {ticket_id_str}"
        )

        for admin_id in settings.ADMINS:
            try:
                if message.content_type == "text" and message.text:
                    safe_text = html.escape(message.text)
                    await message.bot.send_message(admin_id, f"{header_admin}\n\n<pre>{safe_text}</pre>")
                else:
                    header = await message.bot.send_message(admin_id, header_admin)
                    await _safe_copy(
                        message.bot,
                        admin_id,
                        message.chat.id,
                        message.message_id,
                        reply_to_message_id=header.message_id,
                    )
            except Exception:
                pass

        return

    # ----------------------------
    # Пользователь пишет в поддержку (любой контент, кроме команд)
    # ----------------------------
    payload_for_cmd_check = (message.text or message.caption or "")
    has_any_payload = bool(
        message.text
        or message.caption
        or message.photo
        or message.document
        or message.video
        or message.audio
        or message.voice
        or message.video_note
        or message.animation
        or message.sticker
    )

    if has_any_payload and not payload_for_cmd_check.startswith("/"):
        real_id = message.from_user.id
        user = await get_or_create_user(real_id)

        # Если пользователь не открывал /support — игнорируем
        if get_real_id(user.fake_id) is None:
            return

        async with async_session() as session:
            from sqlalchemy import select

            q = select(SupportTicket).where(
                SupportTicket.user_id == user.id,
                SupportTicket.is_open.is_(True),
            )
            res = await session.execute(q)
            ticket = res.scalars().first()

            if not ticket:
                ticket = SupportTicket(user_id=user.id, is_open=True)
                session.add(ticket)

            ticket.last_message = message.text or message.caption or f"<{message.content_type}>"
            await session.commit()
            await session.refresh(ticket)

        # 2) Сообщение в тикете от пользователя (АДМИНАМ)
        user_payload = message.text or message.caption or ""
        header_admin = (
            "🆘 Сообщение в поддержку\n"
            f"FAKE ID: {user.fake_id}\n"
            f"Ticket ID: {ticket.id}"
        )

        for admin_id in settings.ADMINS:
            try:
                if message.content_type == "text" and message.text:
                    safe_text = html.escape(message.text)
                    await message.bot.send_message(admin_id, f"{header_admin}\n\n<pre>{safe_text}</pre>")
                else:
                    # для вложений: заголовок + копия вложения reply'ем
                    header_text = f"{header_admin}\n\n{user_payload}" if user_payload else header_admin
                    header = await message.bot.send_message(admin_id, header_text)
                    await _safe_copy(
                        message.bot,
                        admin_id,
                        message.chat.id,
                        message.message_id,
                        reply_to_message_id=header.message_id,
                    )
            except Exception:
                pass
