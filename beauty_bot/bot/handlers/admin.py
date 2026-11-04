"""Панель мастера (/admin). Доступна только Telegram ID из ADMIN_IDS."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from .. import keyboards as kb, texts
from ..config import Settings
from ..db import Database
from ..notify import safe_edit, safe_send
from ..slots import overlaps, time_grid
from ..states import AdminSG
from ..utils import (
    esc, fmt_day, fmt_dt, fmt_duration, fmt_price, now_local,
    parse_day_code, parse_positive_int,
)
from .booking import show_services

router = Router(name="admin")


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, settings: Settings) -> bool:
        user = getattr(event, "from_user", None)
        return settings.is_admin(user.id if user else None)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

CLOSE_DAYS_AHEAD = 30


# ---------- меню ----------
@router.callback_query(kb.AdminCb.filter(F.action == "menu"))
async def admin_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await safe_edit(cb, "Меню мастера:", kb.admin_menu())


# ---------- списки записей ----------
@router.callback_query(kb.AdminCb.filter(F.action == "list"))
async def admin_list(cb: CallbackQuery, callback_data: kb.AdminCb, db: Database, settings: Settings) -> None:
    today = datetime.combine(now_local(settings).date(), time())
    ranges = {
        "today": (today, today + timedelta(days=1), "сегодня"),
        "tomorrow": (today + timedelta(days=1), today + timedelta(days=2), "завтра"),
        "week": (today, today + timedelta(days=7), "неделю"),
    }
    start, end, title = ranges.get(callback_data.arg, ranges["today"])
    appts = await db.appointments_between(start, end)
    await cb.answer()
    if not appts:
        await safe_edit(cb, f"Записей на {title} нет.", kb.admin_back())
        return
    lines = [f"<b>Записи на {title}</b> ({len(appts)}):\n"]
    current_day = None
    for a in appts:
        if callback_data.arg == "week" and a.start.date() != current_day:
            current_day = a.start.date()
            lines.append(f"\n<u>{fmt_day(current_day)}</u>")
        lines.append(f"• {texts.appointment_line(a, with_client=True)}")
    total = sum(a.price for a in appts)
    lines.append(f"\nИтого: {fmt_price(total)}")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    await safe_edit(cb, text, kb.admin_appts_kb(appts[:30]))


@router.callback_query(kb.AdminCb.filter(F.action == "acancel"))
async def admin_cancel_ask(cb: CallbackQuery, callback_data: kb.AdminCb, db: Database) -> None:
    appt = await db.get_appointment(int(callback_data.arg))
    if appt is None or appt.status != "active":
        await cb.answer("Запись уже отменена.", show_alert=True)
        return
    await cb.answer()
    await safe_edit(
        cb,
        f"Отменить запись? Клиент получит уведомление.\n\n{texts.appointment_line(appt, True)}",
        kb.admin_yes_no(kb.AdminCb(action="acancel_yes", arg=str(appt.id)), kb.AdminCb(action="menu")),
    )


@router.callback_query(kb.AdminCb.filter(F.action == "acancel_yes"))
async def admin_cancel(cb: CallbackQuery, callback_data: kb.AdminCb, db: Database, settings: Settings, bot: Bot) -> None:
    appt = await db.get_appointment(int(callback_data.arg))
    if appt is None or not await db.cancel_appointment(appt.id):
        await cb.answer("Запись уже отменена.", show_alert=True)
        return
    if appt.client_tg_id:
        await safe_send(
            bot, appt.client_tg_id,
            f"К сожалению, мастер отменил вашу запись на {fmt_dt(appt.start)} ({esc(appt.service_name)}).\n"
            f"Приносим извинения! Выберите другое время или позвоните: {esc(settings.master.phone)}",
            kb.back_home([kb.InlineKeyboardButton(text="📅 Записаться", callback_data=kb.MenuCb(action="book").pack())]),
        )
    await cb.answer("Отменено")
    await safe_edit(cb, "Запись отменена, клиент уведомлён.", kb.admin_back())


# ---------- закрыть день / часы ----------
@router.callback_query(kb.AdminCb.filter(F.action == "close"))
async def close_choose_day(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    today = now_local(settings).date()
    days = [
        today + timedelta(days=i) for i in range(CLOSE_DAYS_AHEAD)
        if (today + timedelta(days=i)).weekday() in settings.schedule
    ]
    await cb.answer()
    await safe_edit(cb, "Какой день закрыть?", kb.admin_days_kb(days))


@router.callback_query(kb.AdminCb.filter(F.action == "close_day"))
async def close_day_options(cb: CallbackQuery, callback_data: kb.AdminCb, state: FSMContext) -> None:
    day = parse_day_code(callback_data.arg)
    await state.update_data(close_day=callback_data.arg)
    await cb.answer()
    await safe_edit(
        cb,
        f"🗓 {fmt_day(day)}\n\nЗакрыть весь день или отдельные часы?",
        kb.admin_back([
            [kb.InlineKeyboardButton(text="Весь день", callback_data=kb.AdminCb(action="close_full").pack())],
            [kb.InlineKeyboardButton(text="Отдельные часы", callback_data=kb.AdminCb(action="close_from").pack())],
        ]),
    )


async def _get_close_day(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("close_day"):
        await cb.answer(texts.SESSION_EXPIRED, show_alert=True)
        await safe_edit(cb, "Меню мастера:", kb.admin_menu())
        return None, data
    return parse_day_code(data["close_day"]), data


async def _finish_block(cb, state, db: Database, start: datetime, end: datetime) -> None:
    await db.add_block(start, end, "закрыто мастером")
    await state.clear()
    affected = [a for a in await db.appointments_between(start - timedelta(days=1), end)
                if overlaps(a.start, a.end, start, end)]
    text = f"Готово, закрыто: {fmt_day(start.date())}"
    if end - start < timedelta(days=1):
        text += f", {start:%H:%M}–{end:%H:%M}"
    if affected:
        text += (
            f"\n\n⚠️ В это время уже есть записи ({len(affected)}). Они не отменены автоматически — "
            "отмените их через «Сегодня/Завтра/Неделя» или договоритесь с клиентом:\n"
            + "\n".join(f"• {texts.appointment_line(a, True)}" for a in affected)
        )
    await cb.answer("Закрыто")
    await safe_edit(cb, text, kb.admin_back())


@router.callback_query(kb.AdminCb.filter(F.action == "close_full"))
async def close_full(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    day, _ = await _get_close_day(cb, state)
    if day is None:
        return
    start = datetime.combine(day, time())
    await _finish_block(cb, state, db, start, start + timedelta(days=1))


@router.callback_query(kb.AdminCb.filter(F.action == "close_from"))
async def close_from(cb: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    day, _ = await _get_close_day(cb, state)
    if day is None:
        return
    grid = time_grid(day, settings.schedule, settings.slot_step)[:-1]
    await cb.answer()
    await safe_edit(cb, f"🗓 {fmt_day(day)}\n\nС какого времени закрыть?", kb.admin_hours_kb(grid, "close_start"))


@router.callback_query(kb.AdminCb.filter(F.action == "close_start"))
async def close_start(cb: CallbackQuery, callback_data: kb.AdminCb, state: FSMContext, settings: Settings) -> None:
    day, _ = await _get_close_day(cb, state)
    if day is None:
        return
    start = datetime.combine(day, time(int(callback_data.arg[:2]), int(callback_data.arg[2:])))
    await state.update_data(close_start=callback_data.arg)
    grid = [t for t in time_grid(day, settings.schedule, settings.slot_step) if t > start]
    await cb.answer()
    await safe_edit(cb, f"🗓 {fmt_day(day)}, с {start:%H:%M}\n\nДо какого времени?", kb.admin_hours_kb(grid, "close_end"))


@router.callback_query(kb.AdminCb.filter(F.action == "close_end"))
async def close_end(cb: CallbackQuery, callback_data: kb.AdminCb, state: FSMContext, db: Database) -> None:
    day, data = await _get_close_day(cb, state)
    if day is None:
        return
    if not data.get("close_start"):
        await cb.answer(texts.SESSION_EXPIRED, show_alert=True)
        return
    s, e = data["close_start"], callback_data.arg
    start = datetime.combine(day, time(int(s[:2]), int(s[2:])))
    end = datetime.combine(day, time(int(e[:2]), int(e[2:])))
    if end <= start:
        await cb.answer("Конец должен быть позже начала.", show_alert=True)
        return
    await _finish_block(cb, state, db, start, end)


@router.callback_query(kb.AdminCb.filter(F.action == "blocks"))
async def blocks_list(cb: CallbackQuery, db: Database, settings: Settings) -> None:
    blocks = await db.upcoming_blocks(now_local(settings))
    await cb.answer()
    if not blocks:
        await safe_edit(cb, "Закрытого времени нет.", kb.admin_back())
        return
    lines = ["<b>Закрытое время</b> (нажмите, чтобы открыть):\n"]
    for b in blocks:
        if b.end - b.start >= timedelta(days=1) and b.start.time() == time():
            lines.append(f"• {fmt_day(b.start.date())} — весь день")
        else:
            lines.append(f"• {fmt_day(b.start.date())}, {b.start:%H:%M}–{b.end:%H:%M}")
    await safe_edit(cb, "\n".join(lines), kb.admin_blocks_kb(blocks[:40]))


@router.callback_query(kb.AdminCb.filter(F.action == "bdel"))
async def block_delete(cb: CallbackQuery, callback_data: kb.AdminCb, db: Database, settings: Settings) -> None:
    await db.delete_block(int(callback_data.arg))
    await cb.answer("Время открыто для записи")
    await blocks_list(cb, db, settings)


# ---------- ручная запись ----------
@router.callback_query(kb.AdminCb.filter(F.action == "add"))
async def manual_add(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await state.update_data(flow="admin_add")
    await cb.answer()
    await show_services(cb, db, "admin_add")


# ---------- услуги ----------
@router.callback_query(kb.AdminCb.filter(F.action == "services"))
async def services_list(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    services = await db.list_services(include_inactive=True)
    await cb.answer()
    await safe_edit(
        cb, "Услуги (🙈 — скрыта от клиентов). Выберите, что изменить:", kb.admin_services_kb(services)
    )


def _service_card(s) -> str:
    status = "показывается клиентам" if s.active else "скрыта от клиентов"
    return (
        f"<b>{esc(s.name)}</b>\n💰 {fmt_price(s.price)}\n⏱ {fmt_duration(s.duration)}\nСтатус: {status}\n\n"
        "Изменения цены не затрагивают уже сделанные записи."
    )


@router.callback_query(kb.AdminCb.filter(F.action == "svc"))
async def service_card(cb: CallbackQuery, callback_data: kb.AdminCb, db: Database) -> None:
    s = await db.get_service(int(callback_data.arg))
    if s is None:
        await cb.answer("Услуга не найдена.", show_alert=True)
        return
    await cb.answer()
    await safe_edit(cb, _service_card(s), kb.admin_service_kb(s))


@router.callback_query(kb.AdminCb.filter(F.action == "svc_toggle"))
async def service_toggle(cb: CallbackQuery, callback_data: kb.AdminCb, db: Database) -> None:
    s = await db.get_service(int(callback_data.arg))
    if s is None:
        await cb.answer("Услуга не найдена.", show_alert=True)
        return
    await db.update_service(s.id, "active", 0 if s.active else 1)
    s = await db.get_service(s.id)
    await cb.answer("Сохранено")
    await safe_edit(cb, _service_card(s), kb.admin_service_kb(s))


FIELD_PROMPTS = {
    "name": "Введите новое название услуги:",
    "price": "Введите новую цену в рублях (только число):",
    "duration": "Введите длительность в минутах (например, 90):",
}


@router.callback_query(kb.AdminCb.filter(F.action == "svc_edit"))
async def service_edit_ask(cb: CallbackQuery, callback_data: kb.AdminCb, state: FSMContext) -> None:
    svc_id, _, field = callback_data.arg.partition("-")
    if field not in FIELD_PROMPTS:
        await cb.answer()
        return
    await state.set_state(AdminSG.svc_edit)
    await state.update_data(svc_id=int(svc_id), svc_field=field)
    await cb.answer()
    await safe_edit(cb, FIELD_PROMPTS[field], kb.admin_back())


def _validate_field(field: str, text: str | None):
    if field == "name":
        name = " ".join((text or "").split())
        return name if 2 <= len(name) <= 60 else None
    if field == "price":
        return parse_positive_int(text, 1_000_000)
    if field == "duration":
        value = parse_positive_int(text, 12 * 60)
        return value if value and value >= 5 else None
    return None


@router.message(AdminSG.svc_edit)
async def service_edit_save(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    field = data.get("svc_field")
    value = _validate_field(field, message.text)
    if value is None:
        await message.answer("Не получилось распознать значение. " + FIELD_PROMPTS.get(field, ""),
                             reply_markup=kb.admin_back())
        return
    await db.update_service(data["svc_id"], field, value)
    await state.clear()
    s = await db.get_service(data["svc_id"])
    await message.answer("Сохранено ✅\n\n" + _service_card(s), reply_markup=kb.admin_service_kb(s))


@router.callback_query(kb.AdminCb.filter(F.action == "svc_new"))
async def service_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminSG.svc_name)
    await cb.answer()
    await safe_edit(cb, "Название новой услуги:", kb.admin_back())


@router.message(AdminSG.svc_name)
async def service_new_name(message: Message, state: FSMContext) -> None:
    name = _validate_field("name", message.text)
    if not name:
        await message.answer("Название — от 2 до 60 символов.", reply_markup=kb.admin_back())
        return
    await state.update_data(new_name=name)
    await state.set_state(AdminSG.svc_price)
    await message.answer(FIELD_PROMPTS["price"], reply_markup=kb.admin_back())


@router.message(AdminSG.svc_price)
async def service_new_price(message: Message, state: FSMContext) -> None:
    price = _validate_field("price", message.text)
    if not price:
        await message.answer(FIELD_PROMPTS["price"], reply_markup=kb.admin_back())
        return
    await state.update_data(new_price=price)
    await state.set_state(AdminSG.svc_duration)
    await message.answer(FIELD_PROMPTS["duration"], reply_markup=kb.admin_back())


@router.message(AdminSG.svc_duration)
async def service_new_duration(message: Message, state: FSMContext, db: Database) -> None:
    duration = _validate_field("duration", message.text)
    if not duration:
        await message.answer(FIELD_PROMPTS["duration"] + " (от 5 до 720)", reply_markup=kb.admin_back())
        return
    data = await state.get_data()
    svc_id = await db.add_service(data["new_name"], data["new_price"], duration)
    await state.clear()
    s = await db.get_service(svc_id)
    await message.answer("Услуга добавлена ✅\n\n" + _service_card(s), reply_markup=kb.admin_service_kb(s))


# ---------- рассылка ----------
@router.callback_query(kb.AdminCb.filter(F.action == "broadcast"))
async def broadcast_ask(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    recipients = await db.broadcast_recipients()
    await state.clear()
    await state.set_state(AdminSG.broadcast)
    await cb.answer()
    await safe_edit(
        cb,
        f"Получателей: {len(recipients)}.\n\nНапишите текст рассылки, например:\n"
        "<i>Есть свободные окна на пятницу — 12:00 и 16:00. Записывайтесь!</i>",
        kb.admin_back(),
    )


@router.message(AdminSG.broadcast, F.text)
async def broadcast_preview(message: Message, state: FSMContext) -> None:
    if len(message.text) > 3500:
        await message.answer("Слишком длинный текст, сократите до 3500 символов.", reply_markup=kb.admin_back())
        return
    await state.update_data(bc_text=message.html_text)
    await state.set_state(None)
    await message.answer(
        f"Предпросмотр:\n\n{message.html_text}\n\nОтправить всем клиентам?",
        reply_markup=kb.admin_yes_no(kb.AdminCb(action="bc_send"), kb.AdminCb(action="menu")),
    )


@router.message(AdminSG.broadcast)
async def broadcast_not_text(message: Message) -> None:
    await message.answer("Пришлите, пожалуйста, текстовое сообщение.", reply_markup=kb.admin_back())


@router.callback_query(kb.AdminCb.filter(F.action == "bc_send"))
async def broadcast_send(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    data = await state.get_data()
    text = data.get("bc_text")
    await state.clear()
    if not text:
        await cb.answer(texts.SESSION_EXPIRED, show_alert=True)
        return
    await cb.answer("Отправляю…")
    await safe_edit(cb, "Рассылка запущена, это может занять немного времени…")
    markup = kb.back_home([kb.InlineKeyboardButton(text="📅 Записаться", callback_data=kb.MenuCb(action="book").pack())])
    sent = failed = 0
    for chat_id in await db.broadcast_recipients():
        ok = await safe_send(bot, chat_id, text, markup)
        sent += ok
        failed += not ok
        await asyncio.sleep(0.05)  # ~20 сообщений/сек — в пределах лимитов Telegram
    await cb.message.answer(f"Рассылка завершена: доставлено {sent}, не доставлено {failed}.",
                            reply_markup=kb.admin_back())
