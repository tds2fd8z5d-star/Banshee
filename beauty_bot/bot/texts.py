"""Тексты сообщений (HTML-разметка)."""
from __future__ import annotations

from .config import Settings
from .db import Appointment, Service
from .utils import esc, fmt_dt, fmt_duration, fmt_price

SESSION_EXPIRED = "Кажется, этот шаг устарел. Начнём заново 🙂"
ERROR = "Ой, что-то пошло не так. Попробуйте ещё раз, пожалуйста."
UNKNOWN = "Не совсем поняла 🙂 Выберите, что нужно:"
NO_SERVICES = "Список услуг пока пуст. Свяжитесь с мастером, пожалуйста."
NO_DAYS = "К сожалению, в ближайшие дни свободных окон нет. Напишите или позвоните мастеру — постараемся что-нибудь придумать."
ASK_NAME = "Как вас зовут?"
BAD_NAME = "Пожалуйста, напишите имя буквами (2–50 символов)."
ASK_PHONE = "Оставьте номер телефона: нажмите «Поделиться контактом» ниже или введите вручную."
BAD_PHONE = "Не получилось распознать номер. Пример: +7 900 123-45-67"
THANKS_CONTACT = "Спасибо!"
CANCELLED_FLOW = "Хорошо, отменили. Чем ещё помочь?"


def welcome(s: Settings, first_name: str | None) -> str:
    hello = f"Здравствуйте, {esc(first_name)}!" if first_name else "Здравствуйте!"
    spec = f"\n{esc(s.master.specialization)}" if s.master.specialization else ""
    return (
        f"{hello} 👋\n\nЯ помогу записаться к мастеру <b>{esc(s.master.name)}</b>.{spec}\n\n"
        "Выберите, что нужно:"
    )


def prices(services: list[Service]) -> str:
    if not services:
        return NO_SERVICES
    lines = ["<b>Услуги и цены</b>\n"]
    for s in services:
        lines.append(f"• {esc(s.name)} — <b>{fmt_price(s.price)}</b> ({fmt_duration(s.duration)})")
    return "\n".join(lines)


def contacts(s: Settings) -> str:
    m = s.master
    lines = [f"<b>{esc(m.name)}</b>"]
    if m.specialization:
        lines.append(esc(m.specialization))
    lines.append("")
    if m.address:
        lines.append(f"📍 {esc(m.address)}")
    if m.phone:
        lines.append(f"📞 {esc(m.phone)}")
    if m.portfolio_url:
        lines.append(f'🖼 <a href="{esc(m.portfolio_url)}">Портфолио</a>')
    return "\n".join(lines)


def booking_summary(
    service_name: str, duration: int, price: int, start, name: str | None = None,
    phone: str | None = None, title: str = "Проверьте, всё ли верно:",
) -> str:
    lines = [
        f"<b>{title}</b>\n",
        f"💅 {esc(service_name)}",
        f"🗓 {fmt_dt(start)}",
        f"⏱ {fmt_duration(duration)}",
        f"💰 {fmt_price(price)}",
    ]
    if name:
        lines.append(f"👤 {esc(name)}, {esc(phone)}")
    return "\n".join(lines)


def booked(a: Appointment, s: Settings) -> str:
    addr = f"\n📍 {esc(s.master.address)}" if s.master.address else ""
    return (
        "Готово, вы записаны! ✨\n\n"
        f"💅 {esc(a.service_name)}\n🗓 {fmt_dt(a.start)}{addr}\n\n"
        "Я напомню о визите заранее."
    )


def appointment_line(a: Appointment, with_client: bool = False) -> str:
    line = f"<b>{fmt_dt(a.start)}</b> — {esc(a.service_name)} ({fmt_duration(a.duration)}, {fmt_price(a.price)})"
    if with_client:
        phone = f", {esc(a.client_phone)}" if a.client_phone else ""
        mark = " ✅" if a.confirmed else ""
        line += f"\n    👤 {esc(a.client_name)}{phone}{mark}"
        if a.source == "admin":
            line += " · вручную"
    return line


def my_appointments(appts: list[Appointment], locked: bool, s: Settings) -> str:
    if not appts:
        return "У вас пока нет предстоящих записей."
    lines = ["<b>Ваши записи:</b>\n"] + [f"• {appointment_line(a)}" for a in appts]
    if locked and s.cancel_deadline_hours:
        lines.append(
            f"\nОтменить или перенести запись менее чем за {s.cancel_deadline_hours} ч можно только "
            f"по телефону {esc(s.master.phone)}."
        )
    return "\n".join(lines)


def reminder(a: Appointment, s: Settings, now) -> str:
    if a.start.date() == now.date():
        when = f"сегодня в {a.start:%H:%M}"
    elif (a.start.date() - now.date()).days == 1:
        when = f"завтра в {a.start:%H:%M}"
    else:
        when = fmt_dt(a.start)
    addr = f"\n📍 {esc(s.master.address)}" if s.master.address else ""
    return (
        f"Напоминаю: {when} у вас запись ⏰\n\n"
        f"💅 {esc(a.service_name)}\n🗓 {fmt_dt(a.start)}{addr}\n\n"
        "Подтвердите, пожалуйста, что придёте."
    )


# ---------- уведомления мастеру ----------
def admin_new(a: Appointment) -> str:
    src = " (вручную)" if a.source == "admin" else ""
    return f"🆕 Новая запись{src}\n\n{appointment_line(a, with_client=True)}"


def admin_cancelled(a: Appointment, by: str = "клиент") -> str:
    return f"❌ Отмена записи ({by})\n\n{appointment_line(a, with_client=True)}"


def admin_moved(old: Appointment, new: Appointment) -> str:
    return (
        f"🔁 Перенос записи\n\nБыло: {fmt_dt(old.start)}\n"
        f"Стало: {appointment_line(new, with_client=True)}"
    )


def admin_confirmed(a: Appointment) -> str:
    return f"✅ Клиент подтвердил визит\n\n{appointment_line(a, with_client=True)}"
