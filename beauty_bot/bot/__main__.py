"""Точка входа: python -m bot"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, ErrorEvent

from . import keyboards as kb, texts
from .config import ConfigError, Settings, load_settings
from .db import Database
from .handlers import setup_routers
from .scheduler import setup_scheduler

log = logging.getLogger("bot")


async def on_error(event: ErrorEvent) -> bool:
    """Глобальный обработчик: логируем ошибку и вежливо отвечаем пользователю."""
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and "query is too old" in str(exc):
        return True
    log.exception("Ошибка при обработке апдейта %s", event.update.update_id, exc_info=exc)
    update = event.update
    try:
        if update.callback_query:
            await update.callback_query.answer(texts.ERROR, show_alert=True)
        elif update.message:
            await update.message.answer(texts.ERROR, reply_markup=kb.main_menu())
    except Exception:  # noqa: BLE001
        log.debug("Не удалось сообщить пользователю об ошибке", exc_info=True)
    return True


async def set_commands(bot: Bot, settings: Settings) -> None:
    client_cmds = [BotCommand(command="start", description="Главное меню")]
    await bot.set_my_commands(client_cmds, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(
                client_cmds + [BotCommand(command="admin", description="Меню мастера")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except TelegramBadRequest:
            log.warning("Мастер %s ещё не писал боту — команда /admin не добавлена в меню", admin_id)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("Ошибка настроек: %s", exc)
        sys.exit(1)

    db = Database(settings.db_path)
    await db.connect()
    await db.seed_services(settings.services_seed)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage(), settings=settings, db=db)
    dp.include_router(setup_routers())
    dp.errors.register(on_error)

    scheduler = setup_scheduler(bot, db, settings)
    try:
        me = await bot.get_me()
        await set_commands(bot, settings)
        await bot.delete_webhook(drop_pending_updates=False)
        scheduler.start()
        log.info("Бот @%s запущен. Мастер: %s, админы: %s",
                 me.username, settings.master.name, sorted(settings.admin_ids))
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except TelegramUnauthorizedError:
        log.error("Telegram отклонил BOT_TOKEN — проверьте токен в .env")
        raise SystemExit(1)
    except TelegramNetworkError as exc:
        log.error("Нет связи с Telegram при запуске: %s", exc)
        raise SystemExit(1)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await db.close()
        await bot.session.close()


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
