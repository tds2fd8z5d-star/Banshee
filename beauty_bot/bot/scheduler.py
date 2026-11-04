"""Фоновые задачи APScheduler: напоминания клиентам и утренняя сводка мастеру.

Напоминания не планируются на конкретное время, а раз в минуту выбираются
из БД по флагам reminded_1/reminded_2. Так они переживают перезапуск бота,
а перенос записи автоматически «перезаводит» напоминания.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import keyboards as kb, texts
from .config import Settings
from .db import Database
from .notify import notify_admins, safe_send
from .utils import fmt_day, fmt_price, now_local

log = logging.getLogger(__name__)


async def send_reminders(bot: Bot, db: Database, settings: Settings) -> None:
    now = now_local(settings)
    plan = (
        ("reminded_2", settings.remind_second_hours),
        ("reminded_1", settings.remind_first_hours),
    )
    for flag, hours in plan:
        for appt in await db.due_reminders(now, hours, flag):
            # помечаем до отправки — лучше не дослать, чем прислать дважды
            await db.mark_reminded(appt.id, flag)
            if flag == "reminded_2":
                await db.mark_reminded(appt.id, "reminded_1")  # первое уже неактуально
            await safe_send(bot, appt.client_tg_id, texts.reminder(appt, settings, now),
                            kb.reminder_kb(appt.id))
            log.info("Напоминание %s отправлено для записи #%s", flag, appt.id)


async def send_admin_digest(bot: Bot, db: Database, settings: Settings) -> None:
    today = datetime.combine(now_local(settings).date(), time())
    appts = await db.appointments_between(today, today + timedelta(days=1))
    if not appts:
        text = f"Доброе утро! На сегодня ({fmt_day(today.date())}) записей нет ☕️"
    else:
        lines = [f"Доброе утро! Записи на сегодня ({len(appts)}):\n"]
        lines += [f"• {texts.appointment_line(a, with_client=True)}" for a in appts]
        lines.append(f"\nИтого: {fmt_price(sum(a.price for a in appts))}")
        text = "\n".join(lines)
    await notify_admins(bot, settings, text)


def setup_scheduler(bot: Bot, db: Database, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(
        send_reminders, "interval", minutes=1, args=(bot, db, settings),
        id="reminders", max_instances=1, coalesce=True, next_run_time=datetime.now(settings.tz),
    )
    if settings.admin_digest_time:
        t = settings.admin_digest_time
        scheduler.add_job(
            send_admin_digest, "cron", hour=t.hour, minute=t.minute, args=(bot, db, settings),
            id="admin_digest", max_instances=1, coalesce=True, misfire_grace_time=3600,
        )
    return scheduler
