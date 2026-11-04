"""Бизнес-логика записи: свободные дни/слоты, создание и перенос без двойных записей."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from .config import Settings
from .db import Appointment, Database, Service
from .slots import free_slots
from .utils import now_local


class BookingError(Exception):
    """Ошибка бизнес-логики с понятным пользователю текстом."""


async def slots_for_day(
    db: Database,
    settings: Settings,
    day: date,
    duration: int,
    exclude_appointment_id: int | None = None,
    ignore_lead: bool = False,
) -> list[datetime]:
    day_start = datetime.combine(day, datetime.min.time())
    busy = await db.busy_intervals(day_start, day_start + timedelta(days=1), exclude_appointment_id)
    return free_slots(
        day=day,
        duration_min=duration,
        schedule=settings.schedule,
        busy=busy,
        now=now_local(settings),
        step_min=settings.slot_step,
        min_lead_min=0 if ignore_lead else settings.min_lead_minutes,
    )


async def available_days(
    db: Database,
    settings: Settings,
    duration: int,
    exclude_appointment_id: int | None = None,
    ignore_lead: bool = False,
) -> list[date]:
    """Ближайшие days_ahead дней, в которые есть хотя бы одно свободное окно."""
    today = now_local(settings).date()
    days = []
    for i in range(settings.days_ahead):
        day = today + timedelta(days=i)
        if day.weekday() not in settings.schedule:
            continue
        if await slots_for_day(db, settings, day, duration, exclude_appointment_id, ignore_lead):
            days.append(day)
    return days


def _reminder_flags(settings: Settings, start: datetime) -> tuple[bool, bool]:
    """Если до записи уже меньше N часов, соответствующее напоминание не нужно."""
    left = start - now_local(settings)
    return (
        left <= timedelta(hours=settings.remind_first_hours),
        left <= timedelta(hours=settings.remind_second_hours),
    )


async def book(
    db: Database,
    settings: Settings,
    client_id: int,
    service: Service,
    start: datetime,
    source: str = "bot",
) -> int:
    """Создаёт запись, если время всё ещё свободно. Возвращает id записи."""
    ignore_lead = source == "admin"
    async with db.booking_lock:
        slots = await slots_for_day(db, settings, start.date(), service.duration, ignore_lead=ignore_lead)
        if start not in slots:
            raise BookingError("Это время уже занято или недоступно. Выберите другое, пожалуйста.")
        r1, r2 = _reminder_flags(settings, start)
        try:
            return await db.insert_appointment(
                client_id, service, start, start + timedelta(minutes=service.duration), source, r1, r2
            )
        except sqlite3.IntegrityError as exc:
            raise BookingError("Это время только что заняли. Выберите другое, пожалуйста.") from exc


async def reschedule(
    db: Database, settings: Settings, appointment: Appointment, new_start: datetime, by_admin: bool = False
) -> None:
    async with db.booking_lock:
        fresh = await db.get_appointment(appointment.id)
        if fresh is None or fresh.status != "active":
            raise BookingError("Запись уже отменена.")
        slots = await slots_for_day(
            db, settings, new_start.date(), fresh.duration,
            exclude_appointment_id=fresh.id, ignore_lead=by_admin,
        )
        if new_start not in slots:
            raise BookingError("Это время уже занято или недоступно. Выберите другое, пожалуйста.")
        r1, r2 = _reminder_flags(settings, new_start)
        try:
            await db.move_appointment(
                fresh.id, new_start, new_start + timedelta(minutes=fresh.duration), r1, r2
            )
        except sqlite3.IntegrityError as exc:
            raise BookingError("Это время только что заняли. Выберите другое, пожалуйста.") from exc


def can_client_change(settings: Settings, appointment: Appointment) -> bool:
    """Можно ли клиенту самому отменить/перенести запись (с учётом дедлайна)."""
    now = now_local(settings)
    if appointment.status != "active" or appointment.start <= now:
        return False
    if settings.cancel_deadline_hours <= 0:
        return True
    return appointment.start - now >= timedelta(hours=settings.cancel_deadline_hours)
