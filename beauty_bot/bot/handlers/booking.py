"""Мастер записи: услуга → дата → время → контакты → подтверждение.

Один и тот же сценарий обслуживает три потока (поле flow в FSM):
  book       — клиент записывается сам;
  resched    — клиент переносит существующую запись;
  admin_add  — мастер добавляет запись вручную.
"""
from __future__ import annotations

from datetime import datetime, time

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from .. import booking, keyboards as kb, texts
from ..booking import BookingError
from ..config import Settings
from ..db import Database
from ..notify import notify_admins, remove_reply_keyboard, safe_edit, safe_send
from ..states import BookingSG
from ..utils import (
    clean_name, clean_phone, esc, fmt_day, fmt_dt, fmt_duration, from_db, parse_day_code, to_db,
)

router = Router(name="booking")

FLOWS = {"book", "resched", "admin_add"}


async def _expired(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await cb.answer()
    markup = kb.admin_menu() if settings.is_admin(cb.from_user.id) else kb.main_menu()
    await safe_edit(cb, texts.SESSION_EXPIRED, markup)


async def _flow_data(cb: CallbackQuery, state: FSMContext, settings: Settings) -> dict | None:
    """Данные текущего сценария или None (и сообщение об устаревшем шаге)."""
    data = await state.get_data()
    flow = data.get("flow")
    if flow not in FLOWS or (flow == "admin_add" and not settings.is_admin(cb.from_user.id)):
        await _expired(cb, state, settings)
        return None
    return data


# ---------- 1. Услуга ----------
async def show_services(cb: CallbackQuery, db: Database, flow: str) -> None:
    services = await db.list_services()
    back = kb.AdminCb(action="menu") if flow == "admin_add" else kb.MenuCb(action="home")
    if not services:
        await safe_edit(cb, texts.NO_SERVICES, kb.back_home())
        return
    await safe_edit(cb, "Выберите услугу:", kb.services_kb(services, back))


@router.callback_query(kb.MenuCb.filter(F.action == "book"))
async def book_start(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await state.update_data(flow="book")
    await cb.answer()
    await show_services(cb, db, "book")


@router.callback_query(kb.SvcCb.filter())
async def service_chosen(
    cb: CallbackQuery, callback_data: kb.SvcCb, state: FSMContext, db: Database, settings: Settings
) -> None:
    data = await _flow_data(cb, state, settings)
    if data is None:
        return
    service = await db.get_service(callback_data.id)
    if service is None or not service.active:
        await cb.answer("Эта услуга сейчас недоступна.", show_alert=True)
        await show_services(cb, db, data["flow"])
        return
    await state.update_data(
        service_id=service.id, service_name=service.name, duration=service.duration, price=service.price
    )
    await cb.answer()
    await show_dates(cb, state, db, settings)


# ---------- 2. Дата ----------
async def show_dates(cb: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    data = await state.get_data()
    if not data.get("duration"):
        await _expired(cb, state, settings)
        return
    flow = data["flow"]
    days = await booking.available_days(
        db, settings, data["duration"], data.get("appt_id"), ignore_lead=flow == "admin_add"
    )
    back = kb.MenuCb(action="my") if flow == "resched" else kb.NavCb(to="services")
    header = f"💅 {data['service_name']} ({fmt_duration(data['duration'])})\n\n"
    if not days:
        await safe_edit(cb, header + texts.NO_DAYS, kb.dates_kb([], back))
        return
    await safe_edit(cb, header + "Выберите удобную дату:", kb.dates_kb(days, back))


@router.callback_query(kb.DayCb.filter())
async def day_chosen(
    cb: CallbackQuery, callback_data: kb.DayCb, state: FSMContext, db: Database, settings: Settings
) -> None:
    data = await _flow_data(cb, state, settings)
    if data is None or not data.get("duration"):
        if data is not None:
            await _expired(cb, state, settings)
        return
    try:
        day = parse_day_code(callback_data.day)
    except ValueError:
        await _expired(cb, state, settings)
        return
    await state.update_data(day=callback_data.day)
    await show_times(cb, state, db, settings, day, data)


async def show_times(cb, state, db, settings, day, data) -> None:
    slots = await booking.slots_for_day(
        db, settings, day, data["duration"], data.get("appt_id"), ignore_lead=data["flow"] == "admin_add"
    )
    if not slots:
        await cb.answer("На этот день свободного времени уже нет.", show_alert=True)
        await show_dates(cb, state, db, settings)
        return
    await cb.answer()
    await safe_edit(cb, f"🗓 {fmt_day(day)}\n\nВыберите время:", kb.times_kb(slots))


# ---------- 3. Время ----------
@router.callback_query(kb.TimeCb.filter())
async def time_chosen(
    cb: CallbackQuery, callback_data: kb.TimeCb, state: FSMContext, db: Database, settings: Settings
) -> None:
    data = await _flow_data(cb, state, settings)
    if data is None:
        return
    if not data.get("day") or not data.get("duration"):
        await _expired(cb, state, settings)
        return
    try:
        day = parse_day_code(data["day"])
        start = datetime.combine(day, time(int(callback_data.hm[:2]), int(callback_data.hm[2:])))
    except (ValueError, IndexError):
        await _expired(cb, state, settings)
        return

    flow = data["flow"]
    slots = await booking.slots_for_day(
        db, settings, day, data["duration"], data.get("appt_id"), ignore_lead=flow == "admin_add"
    )
    if start not in slots:
        await cb.answer("Это время уже занято, выберите другое.", show_alert=True)
        await show_times(cb, state, db, settings, day, data)
        return
    await state.update_data(start=to_db(start))
    await cb.answer()

    if flow == "resched":
        await safe_edit(
            cb,
            texts.booking_summary(data["service_name"], data["duration"], data["price"], start,
                                  title="Перенести запись на это время?"),
            kb.confirm_kb(),
        )
    elif flow == "book":
        client = await db.get_client_by_tg(cb.from_user.id)
        if client and client.phone:
            await state.update_data(name=client.name, phone=client.phone)
            await show_confirm(cb, state)
        else:
            await state.set_state(BookingSG.name)
            await safe_edit(cb, texts.ASK_NAME, kb.cancel_input_kb())
    else:  # admin_add
        await state.set_state(BookingSG.name)
        await safe_edit(cb, "Имя клиента?", kb.cancel_input_kb())


async def show_confirm(target: CallbackQuery | Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.booking_summary(
        data["service_name"], data["duration"], data["price"], from_db(data["start"]),
        data.get("name"), data.get("phone"),
    )
    markup = kb.confirm_kb(with_contacts=data["flow"] in {"book", "admin_add"})
    if isinstance(target, CallbackQuery):
        await safe_edit(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


# ---------- 4. Контакты ----------
@router.message(BookingSG.name, F.text)
async def name_entered(message: Message, state: FSMContext, settings: Settings) -> None:
    name = clean_name(message.text)
    if not name:
        await message.answer(texts.BAD_NAME, reply_markup=kb.cancel_input_kb())
        return
    data = await state.get_data()
    await state.update_data(name=name)
    await state.set_state(BookingSG.phone)
    if data.get("flow") == "admin_add":
        await message.answer("Телефон клиента?", reply_markup=kb.cancel_input_kb())
    else:
        await message.answer(texts.ASK_PHONE, reply_markup=kb.share_contact_kb())


@router.message(BookingSG.name)
async def name_invalid(message: Message) -> None:
    await message.answer(texts.BAD_NAME, reply_markup=kb.cancel_input_kb())


@router.message(BookingSG.phone, F.contact | F.text)
async def phone_entered(message: Message, state: FSMContext) -> None:
    raw = message.contact.phone_number if message.contact else message.text
    phone = clean_phone(raw)
    data = await state.get_data()
    if not phone:
        markup = kb.cancel_input_kb() if data.get("flow") == "admin_add" else kb.share_contact_kb()
        await message.answer(texts.BAD_PHONE, reply_markup=markup)
        return
    if not data.get("start"):
        await state.clear()
        await message.answer(texts.SESSION_EXPIRED, reply_markup=ReplyKeyboardRemove())
        await message.answer("Выберите, что нужно:", reply_markup=kb.main_menu())
        return
    await state.update_data(phone=phone)
    await state.set_state(None)
    if data.get("flow") != "admin_add":
        await message.answer(texts.THANKS_CONTACT, reply_markup=ReplyKeyboardRemove())
    await show_confirm(message, state)


@router.message(BookingSG.phone)
async def phone_invalid(message: Message) -> None:
    await message.answer(texts.BAD_PHONE)


# ---------- навигация ----------
@router.callback_query(kb.NavCb.filter())
async def navigate(
    cb: CallbackQuery, callback_data: kb.NavCb, state: FSMContext, db: Database, settings: Settings, bot: Bot
) -> None:
    current = await state.get_state()
    data = await state.get_data()
    flow = data.get("flow")
    if current == BookingSG.phone.state:
        await remove_reply_keyboard(bot, cb.from_user.id)

    if callback_data.to == "cancel" or flow not in FLOWS:
        await state.clear()
        await cb.answer()
        if flow == "admin_add" and settings.is_admin(cb.from_user.id):
            await safe_edit(cb, "Меню мастера:", kb.admin_menu())
        else:
            await safe_edit(cb, texts.CANCELLED_FLOW, kb.main_menu())
        return

    await state.set_state(None)
    await cb.answer()
    if callback_data.to == "services" and flow in {"book", "admin_add"}:
        await show_services(cb, db, flow)
    else:
        await show_dates(cb, state, db, settings)


# ---------- 5. Подтверждение ----------
@router.callback_query(kb.ConfirmCb.filter(F.action == "contacts"))
async def change_contacts(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    data = await _flow_data(cb, state, settings)
    if data is None:
        return
    await state.set_state(BookingSG.name)
    await cb.answer()
    await safe_edit(cb, texts.ASK_NAME, kb.cancel_input_kb())


@router.callback_query(kb.ConfirmCb.filter(F.action == "yes"))
async def confirm(cb: CallbackQuery, state: FSMContext, db: Database, settings: Settings, bot: Bot) -> None:
    data = await _flow_data(cb, state, settings)
    if data is None:
        return
    if not data.get("start"):
        await _expired(cb, state, settings)
        return
    start = from_db(data["start"])
    flow = data["flow"]

    try:
        if flow == "resched":
            await _confirm_reschedule(cb, state, db, settings, bot, data, start)
        else:
            await _confirm_new(cb, state, db, settings, bot, data, start, flow)
    except BookingError as exc:
        await cb.answer(str(exc), show_alert=True)
        await show_dates(cb, state, db, settings)


async def _confirm_new(cb, state, db: Database, settings: Settings, bot: Bot, data, start, flow) -> None:
    service = await db.get_service(data["service_id"])
    if service is None or not service.active:
        raise BookingError("Эта услуга сейчас недоступна.")
    if not data.get("name") or not data.get("phone"):
        await _expired(cb, state, settings)
        return

    if flow == "book":
        client_id = await db.upsert_client(cb.from_user.id, data["name"], data["phone"])
        appt_id = await booking.book(db, settings, client_id, service, start, source="bot")
    else:
        client_id = await db.get_or_create_offline_client(data["name"], data["phone"])
        appt_id = await booking.book(db, settings, client_id, service, start, source="admin")

    await state.clear()
    appt = await db.get_appointment(appt_id)
    await cb.answer("Записано!")
    if flow == "book":
        await safe_edit(cb, texts.booked(appt, settings), kb.back_home())
        await notify_admins(bot, settings, texts.admin_new(appt))
    else:
        await safe_edit(cb, "Запись добавлена ✅\n\n" + texts.appointment_line(appt, True), kb.admin_back())
        if appt.client_tg_id:
            await safe_send(
                bot, appt.client_tg_id,
                f"Мастер записал вас ✨\n\n💅 {esc(appt.service_name)}\n🗓 {fmt_dt(appt.start)}",
                kb.back_home(),
            )


async def _confirm_reschedule(cb, state, db: Database, settings: Settings, bot: Bot, data, start) -> None:
    appt = await db.get_appointment(data.get("appt_id") or 0)
    if appt is None or appt.client_tg_id != cb.from_user.id:
        await _expired(cb, state, settings)
        return
    if not booking.can_client_change(settings, appt):
        await state.clear()
        await cb.answer()
        await safe_edit(
            cb,
            f"Перенести эту запись через бота уже нельзя. Позвоните мастеру: {esc(settings.master.phone)}",
            kb.back_home(),
        )
        return
    await booking.reschedule(db, settings, appt, start)
    await state.clear()
    new = await db.get_appointment(appt.id)
    await cb.answer("Перенесено!")
    await safe_edit(cb, f"Запись перенесена ✨\n\n💅 {esc(new.service_name)}\n🗓 {fmt_dt(new.start)}", kb.back_home())
    await notify_admins(bot, settings, texts.admin_moved(appt, new))
