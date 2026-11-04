"""Безопасная отправка сообщений (бот не падает, если пользователь заблокировал бота)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from .config import Settings

log = logging.getLogger(__name__)


async def safe_send(bot: Bot, chat_id: int, text: str, reply_markup=None) -> bool:
    for attempt in range(2):
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup, disable_web_page_preview=True)
            return True
        except TelegramRetryAfter as exc:  # лимит Telegram — подождать и повторить один раз
            if attempt:
                break
            await asyncio.sleep(exc.retry_after)
        except TelegramAPIError as exc:
            log.warning("Не удалось отправить сообщение %s: %s", chat_id, exc)
            return False
    return False


async def notify_admins(bot: Bot, settings: Settings, text: str) -> None:
    for admin_id in settings.admin_ids:
        await safe_send(bot, admin_id, text)


async def safe_edit(cb: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Редактирует сообщение с кнопками, а если нельзя — отправляет новое."""
    if not isinstance(cb.message, Message):  # слишком старое сообщение
        await cb.bot.send_message(cb.from_user.id, text, reply_markup=reply_markup, disable_web_page_preview=True)
        return
    try:
        await cb.message.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        await cb.message.answer(text, reply_markup=reply_markup, disable_web_page_preview=True)


async def remove_reply_keyboard(bot: Bot, chat_id: int) -> None:
    """Убирает reply-клавиатуру «Поделиться контактом», не оставляя лишних сообщений."""
    try:
        msg = await bot.send_message(chat_id, "…", reply_markup=ReplyKeyboardRemove())
        await bot.delete_message(chat_id, msg.message_id)
    except TelegramAPIError:
        pass
