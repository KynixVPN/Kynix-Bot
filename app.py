import asyncio
import logging
import os
from contextlib import suppress
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from config import settings
from security.integrity import verify_project_integrity
from security.memory_store import start_schedulers
from bot.routers.menu import router as menu_router
from bot.routers.payment import router as payments_router
from bot.routers.support import router as support_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

logger = logging.getLogger("kynix_bot")


async def notify_admins_integrity_failed(bot: Bot, current_hash: str, reason: str | None = None) -> None:
    text = (
        "⚠️ Обнаружено изменение или некорректная конфигурация исходного кода бота.\n"
        "Бот остановлен из соображений безопасности.\n"
    )
    if reason:
        text += f"\nТекущий хэш: <code>{current_hash}</code>"

    for admin_id in settings.ADMINS:
        with suppress(Exception):
            await bot.send_message(admin_id, text)


async def main() -> None:
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    dp = Dispatcher()

    dp.include_router(menu_router)
    dp.include_router(payments_router)
    dp.include_router(support_router)

    current_hash = verify_project_integrity(base_path=os.path.dirname(__file__))

    code_hash = (settings.CODE_HASH or "").strip()

    if not code_hash:
        reason = "CODE_HASH не задан или пустой"
        logger.error(
            "%s. Бот остановлен из соображений безопасности. Текущий хэш проекта: %s",
            reason,
            current_hash,
        )
        await notify_admins_integrity_failed(bot, current_hash, reason)
        return

    if current_hash != code_hash:
        reason = f"Integrity check failed. Expected {code_hash}, got {current_hash}"
        logger.error(reason)
        await notify_admins_integrity_failed(bot, current_hash, reason)
        return

    start_schedulers()

    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
