"""Обработка всего, что не поймали другие роутеры: бот всегда отвечает понятно."""
from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb, texts
from ..config import Settings

router = Router(name="fallback")


@router.message()
async def unknown_message(message: Message, settings: Settings) -> None:
    await message.answer(texts.UNKNOWN, reply_markup=kb.main_menu())


@router.callback_query()
async def unknown_callback(cb: CallbackQuery) -> None:
    await cb.answer("Эта кнопка устарела. Нажмите /start", show_alert=True)
