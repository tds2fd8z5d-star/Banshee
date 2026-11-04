"""Команды. Подключаются первыми, чтобы /start и /admin работали из любого состояния."""
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import keyboards as kb, texts
from ..config import Settings
from ..notify import remove_reply_keyboard

router = Router(name="commands")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, settings: Settings, bot: Bot) -> None:
    await state.clear()
    await remove_reply_keyboard(bot, message.chat.id)  # если осталась кнопка «Поделиться контактом»
    await message.answer(texts.welcome(settings, message.from_user.first_name), reply_markup=kb.main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await remove_reply_keyboard(bot, message.chat.id)
    await message.answer(texts.CANCELLED_FLOW, reply_markup=kb.main_menu())


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext, settings: Settings) -> None:
    if not settings.is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только мастеру.", reply_markup=kb.main_menu())
        return
    await state.clear()
    await message.answer("Меню мастера:", reply_markup=kb.admin_menu())
