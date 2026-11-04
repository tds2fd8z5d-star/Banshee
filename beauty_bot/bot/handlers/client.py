"""Клиентская часть: /start, меню, «Мои записи», отмена, перенос, подтверждение визита."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from .. import booking, keyboards as kb, texts
from ..config import Settings
from ..db import Database
from ..notify import notify_admins, safe_edit
from ..utils import esc, fmt_dt, now_local
from .booking import show_dates

router = Router(name="client")


@router.callback_query(kb.MenuCb.filter(F.action == "home"))
async def menu_home(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await cb.answer()
    await safe_edit(cb, texts.welcome(settings, cb.from_user.first_name), kb.main_menu())


@router.callback_query(kb.MenuCb.filter(F.action == "prices"))
async def menu_prices(cb: CallbackQuery, db: Database) -> None:
    services = await db.list_services()
    book_btn = kb.InlineKeyboardButton(text="📅 Записаться", callback_data=kb.MenuCb(action="book").pack())
    await cb.answer()
    await safe_edit(cb, texts.prices(services), kb.back_home([book_btn]))


@router.callback_query(kb.MenuCb.filter(F.action == "contacts"))
async def menu_contacts(cb: CallbackQuery, settings: Settings) -> None:
    await cb.answer()
    await safe_edit(cb, texts.contacts(settings), kb.back_home())


@router.callback_query(kb.MenuCb.filter(F.action == "my"))
async def menu_my(cb: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    appts = await db.client_upcoming(cb.from_user.id, now_local(settings))
    changeable = {a.id for a in appts if booking.can_client_change(settings, a)}
    locked = len(changeable) < len(appts)
    await cb.answer()
    await safe_edit(cb, texts.my_appointments(appts, locked, settings), kb.my_appointments_kb(appts, changeable))


async def _own_appointment(cb: CallbackQuery, db: Database, appt_id: int):
    appt = await db.get_appointment(appt_id)
    if appt is None or appt.client_tg_id != cb.from_user.id:
        await cb.answer("Запись не найдена.", show_alert=True)
        return None
    if appt.status != "active":
        await cb.answer("Эта запись уже отменена.", show_alert=True)
        return None
    return appt


@router.callback_query(kb.ApptCb.filter(F.action == "cancel"))
async def appt_cancel_ask(cb: CallbackQuery, callback_data: kb.ApptCb, db: Database, settings: Settings) -> None:
    appt = await _own_appointment(cb, db, callback_data.id)
    if appt is None:
        return
    if not booking.can_client_change(settings, appt):
        await cb.answer(
            f"Отменить через бота уже нельзя. Позвоните мастеру: {settings.master.phone}", show_alert=True
        )
        return
    await cb.answer()
    await safe_edit(
        cb,
        f"Отменить запись?\n\n{texts.appointment_line(appt)}",
        kb.cancel_confirm_kb(appt.id),
    )


@router.callback_query(kb.ApptCb.filter(F.action == "cancel_yes"))
async def appt_cancel(
    cb: CallbackQuery, callback_data: kb.ApptCb, db: Database, settings: Settings, bot: Bot
) -> None:
    appt = await _own_appointment(cb, db, callback_data.id)
    if appt is None:
        return
    if not booking.can_client_change(settings, appt):
        await cb.answer(
            f"Отменить через бота уже нельзя. Позвоните мастеру: {settings.master.phone}", show_alert=True
        )
        return
    if await db.cancel_appointment(appt.id):
        await notify_admins(bot, settings, texts.admin_cancelled(appt))
    await cb.answer("Запись отменена")
    await safe_edit(
        cb,
        "Запись отменена. Будем рады видеть вас в другой раз 🌸",
        kb.back_home([kb.InlineKeyboardButton(text="📅 Записаться снова",
                                              callback_data=kb.MenuCb(action="book").pack())]),
    )


@router.callback_query(kb.ApptCb.filter(F.action == "resched"))
async def appt_reschedule(
    cb: CallbackQuery, callback_data: kb.ApptCb, state: FSMContext, db: Database, settings: Settings
) -> None:
    appt = await _own_appointment(cb, db, callback_data.id)
    if appt is None:
        return
    if not booking.can_client_change(settings, appt):
        await cb.answer(
            f"Перенести через бота уже нельзя. Позвоните мастеру: {settings.master.phone}", show_alert=True
        )
        return
    await state.clear()
    await state.update_data(
        flow="resched", appt_id=appt.id, service_id=appt.service_id, service_name=appt.service_name,
        duration=appt.duration, price=appt.price,
    )
    await cb.answer()
    await show_dates(cb, state, db, settings)


@router.callback_query(kb.ApptCb.filter(F.action == "confirm"))
async def appt_confirm(
    cb: CallbackQuery, callback_data: kb.ApptCb, db: Database, settings: Settings, bot: Bot
) -> None:
    appt = await _own_appointment(cb, db, callback_data.id)
    if appt is None:
        return
    if not appt.confirmed:
        await db.confirm_appointment(appt.id)
        appt.confirmed = True
        await notify_admins(bot, settings, texts.admin_confirmed(appt))
    await cb.answer("Спасибо!")
    await safe_edit(
        cb,
        f"Спасибо, ждём вас {fmt_dt(appt.start)} 💖\n\n💅 {esc(appt.service_name)}",
        kb.back_home(),
    )
