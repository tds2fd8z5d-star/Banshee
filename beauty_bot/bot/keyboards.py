"""Инлайн-клавиатуры и callback-данные."""
from __future__ import annotations

from datetime import date, datetime

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import Appointment, Block, Service
from .utils import day_code, fmt_day_short, fmt_price


# ---------- callback data ----------
class MenuCb(CallbackData, prefix="m"):
    action: str  # home | book | my | prices | contacts


class SvcCb(CallbackData, prefix="s"):
    id: int


class DayCb(CallbackData, prefix="d"):
    day: str  # YYYYMMDD


class TimeCb(CallbackData, prefix="t"):
    hm: str  # HHMM


class NavCb(CallbackData, prefix="n"):
    to: str  # services | dates | cancel


class ConfirmCb(CallbackData, prefix="c"):
    action: str  # yes | contacts


class ApptCb(CallbackData, prefix="a"):
    action: str  # cancel | cancel_yes | resched | confirm
    id: int


class AdminCb(CallbackData, prefix="ad"):
    action: str
    arg: str = ""


# ---------- клиент ----------
def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Записаться", callback_data=MenuCb(action="book"))
    kb.button(text="🗂 Мои записи", callback_data=MenuCb(action="my"))
    kb.button(text="💅 Цены", callback_data=MenuCb(action="prices"))
    kb.button(text="📍 Контакты", callback_data=MenuCb(action="contacts"))
    kb.adjust(1, 1, 2)
    return kb.as_markup()


def back_home(extra: list[InlineKeyboardButton] | None = None) -> InlineKeyboardMarkup:
    rows = [[b] for b in extra or []]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data=MenuCb(action="home").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def services_kb(services: list[Service], back: CallbackData) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in services:
        kb.button(text=f"{s.name} — {fmt_price(s.price)}", callback_data=SvcCb(id=s.id))
    kb.button(text="⬅️ Назад", callback_data=back)
    kb.adjust(1)
    return kb.as_markup()


def dates_kb(days: list[date], back: CallbackData) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d in days:
        kb.button(text=fmt_day_short(d), callback_data=DayCb(day=day_code(d)))
    kb.adjust(3)
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=back.pack()))
    return kb.as_markup()


def times_kb(slots: list[datetime]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in slots:
        kb.button(text=t.strftime("%H:%M"), callback_data=TimeCb(hm=t.strftime("%H%M")))
    kb.adjust(4)
    kb.row(InlineKeyboardButton(text="⬅️ К датам", callback_data=NavCb(to="dates").pack()))
    return kb.as_markup()


def confirm_kb(with_contacts: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=ConfirmCb(action="yes"))
    if with_contacts:
        kb.button(text="✏️ Изменить имя/телефон", callback_data=ConfirmCb(action="contacts"))
    kb.button(text="⬅️ Другое время", callback_data=NavCb(to="dates"))
    kb.button(text="✖️ Отмена", callback_data=NavCb(to="cancel"))
    kb.adjust(1)
    return kb.as_markup()


def cancel_input_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data=NavCb(to="cancel").pack())]]
    )


def share_contact_kb() -> ReplyKeyboardMarkup:
    # Кнопка запроса контакта в Telegram бывает только у обычной (reply) клавиатуры.
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="+7 900 000-00-00",
    )


def my_appointments_kb(appts: list[Appointment], changeable: set[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for a in appts:
        if a.id not in changeable:
            continue
        label = a.start.strftime("%d.%m %H:%M")
        kb.row(
            InlineKeyboardButton(text=f"🔁 Перенести {label}", callback_data=ApptCb(action="resched", id=a.id).pack()),
            InlineKeyboardButton(text=f"✖️ Отменить {label}", callback_data=ApptCb(action="cancel", id=a.id).pack()),
        )
    kb.row(InlineKeyboardButton(text="⬅️ В меню", callback_data=MenuCb(action="home").pack()))
    return kb.as_markup()


def cancel_confirm_kb(appt_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, отменить", callback_data=ApptCb(action="cancel_yes", id=appt_id))
    kb.button(text="Нет, оставить", callback_data=MenuCb(action="my"))
    kb.adjust(2)
    return kb.as_markup()


def reminder_kb(appt_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтверждаю", callback_data=ApptCb(action="confirm", id=appt_id))
    kb.button(text="✖️ Отменить", callback_data=ApptCb(action="cancel", id=appt_id))
    kb.adjust(2)
    return kb.as_markup()


# ---------- мастер ----------
def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Сегодня", callback_data=AdminCb(action="list", arg="today"))
    kb.button(text="Завтра", callback_data=AdminCb(action="list", arg="tomorrow"))
    kb.button(text="Неделя", callback_data=AdminCb(action="list", arg="week"))
    kb.button(text="🚫 Закрыть день/часы", callback_data=AdminCb(action="close"))
    kb.button(text="🔓 Закрытое время", callback_data=AdminCb(action="blocks"))
    kb.button(text="➕ Добавить запись", callback_data=AdminCb(action="add"))
    kb.button(text="💅 Услуги и цены", callback_data=AdminCb(action="services"))
    kb.button(text="📣 Рассылка", callback_data=AdminCb(action="broadcast"))
    kb.adjust(3, 2, 1, 1, 1)
    return kb.as_markup()


def admin_back(extra_rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.append([InlineKeyboardButton(text="⬅️ Меню мастера", callback_data=AdminCb(action="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_appts_kb(appts: list[Appointment]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"✖️ Отменить {a.start:%d.%m %H:%M} — {a.client_name}"[:60],
            callback_data=AdminCb(action="acancel", arg=str(a.id)).pack(),
        )]
        for a in appts
    ]
    return admin_back(rows)


def admin_days_kb(days: list[date]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d in days:
        kb.button(text=fmt_day_short(d), callback_data=AdminCb(action="close_day", arg=day_code(d)))
    kb.adjust(3)
    kb.row(InlineKeyboardButton(text="⬅️ Меню мастера", callback_data=AdminCb(action="menu").pack()))
    return kb.as_markup()


def admin_hours_kb(times: list[datetime], action: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in times:
        kb.button(text=t.strftime("%H:%M"), callback_data=AdminCb(action=action, arg=t.strftime("%H%M")))
    kb.adjust(4)
    kb.row(InlineKeyboardButton(text="⬅️ Меню мастера", callback_data=AdminCb(action="menu").pack()))
    return kb.as_markup()


def admin_blocks_kb(blocks: list[Block]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"🔓 Открыть {b.start:%d.%m %H:%M}–{b.end:%H:%M}",
            callback_data=AdminCb(action="bdel", arg=str(b.id)).pack(),
        )]
        for b in blocks
    ]
    return admin_back(rows)


def admin_services_kb(services: list[Service]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in services:
        mark = "" if s.active else "🙈 "
        kb.button(text=f"{mark}{s.name} — {fmt_price(s.price)}", callback_data=AdminCb(action="svc", arg=str(s.id)))
    kb.button(text="➕ Новая услуга", callback_data=AdminCb(action="svc_new"))
    kb.button(text="⬅️ Меню мастера", callback_data=AdminCb(action="menu"))
    kb.adjust(1)
    return kb.as_markup()


def admin_service_kb(s: Service) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название", callback_data=AdminCb(action="svc_edit", arg=f"{s.id}-name"))
    kb.button(text="💰 Цена", callback_data=AdminCb(action="svc_edit", arg=f"{s.id}-price"))
    kb.button(text="⏱ Длительность", callback_data=AdminCb(action="svc_edit", arg=f"{s.id}-duration"))
    kb.button(
        text="🙈 Скрыть из списка" if s.active else "👁 Показать клиентам",
        callback_data=AdminCb(action="svc_toggle", arg=str(s.id)),
    )
    kb.button(text="⬅️ К услугам", callback_data=AdminCb(action="services"))
    kb.adjust(3, 1, 1)
    return kb.as_markup()


def admin_yes_no(yes: AdminCb, no: AdminCb) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да", callback_data=yes)
    kb.button(text="✖️ Нет", callback_data=no)
    kb.adjust(2)
    return kb.as_markup()
