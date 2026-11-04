import asyncio
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bot import booking
from bot.booking import BookingError
from bot.config import DaySchedule, MasterInfo, Settings
from bot.db import Database
from bot.utils import now_local

ALL_DAYS = {i: DaySchedule(time(0), time(23, 59)) for i in range(7)}


def make_settings() -> Settings:
    return Settings(
        bot_token="x", admin_ids=frozenset({1}), master=MasterInfo("М", "", "", "", ""),
        tz=ZoneInfo("Europe/Moscow"), schedule=ALL_DAYS, services_seed=[], db_path=Path(":memory:"),
        min_lead_minutes=60, cancel_deadline_hours=3,
    )


def run(coro):
    return asyncio.run(coro)


async def _setup():
    s = make_settings()
    db = Database(":memory:")
    await db.connect()
    await db.seed_services([{"name": "Маникюр", "price": 1500, "duration": 60}])
    service = (await db.list_services())[0]
    c1 = await db.upsert_client(100, "Анна", "+79000000001")
    c2 = await db.upsert_client(200, "Олья", "+79000000002")
    tomorrow = datetime.combine(now_local(s).date() + timedelta(days=1), time(12, 0))
    return s, db, service, c1, c2, tomorrow


def test_no_double_booking_and_overlap():
    async def scenario():
        s, db, service, c1, c2, start = await _setup()
        appt_id = await booking.book(db, s, c1, service, start)
        assert appt_id
        with pytest.raises(BookingError):
            await booking.book(db, s, c2, service, start)  # то же время
        with pytest.raises(BookingError):
            await booking.book(db, s, c2, service, start + timedelta(minutes=30))  # пересечение
        assert await booking.book(db, s, c2, service, start + timedelta(minutes=60))  # сразу после
        await db.close()
    run(scenario())


def test_concurrent_booking_only_one_wins():
    async def scenario():
        s, db, service, c1, c2, start = await _setup()
        results = await asyncio.gather(
            booking.book(db, s, c1, service, start),
            booking.book(db, s, c2, service, start),
            return_exceptions=True,
        )
        assert sum(isinstance(r, int) for r in results) == 1
        assert sum(isinstance(r, BookingError) for r in results) == 1
        await db.close()
    run(scenario())


def test_past_time_rejected():
    async def scenario():
        s, db, service, c1, _, _ = await _setup()
        past = now_local(s) - timedelta(hours=2)
        past = past.replace(minute=0)
        with pytest.raises(BookingError):
            await booking.book(db, s, c1, service, past)
        await db.close()
    run(scenario())


def test_block_and_cancel_free_slot():
    async def scenario():
        s, db, service, c1, c2, start = await _setup()
        block_id = await db.add_block(start, start + timedelta(hours=1))
        with pytest.raises(BookingError):
            await booking.book(db, s, c1, service, start)
        await db.delete_block(block_id)
        appt_id = await booking.book(db, s, c1, service, start)
        assert await db.cancel_appointment(appt_id)
        assert not await db.cancel_appointment(appt_id)  # повторная отмена — no-op
        assert await booking.book(db, s, c2, service, start)  # слот освободился
        await db.close()
    run(scenario())


def test_reschedule_excludes_own_slot_and_resets_reminders():
    async def scenario():
        s, db, service, c1, _, start = await _setup()
        appt_id = await booking.book(db, s, c1, service, start)
        await db.mark_reminded(appt_id, "reminded_1")
        appt = await db.get_appointment(appt_id)
        new_start = start + timedelta(minutes=30)  # пересекается со старым временем — допустимо
        await booking.reschedule(db, s, appt, new_start)
        moved = await db.get_appointment(appt_id)
        assert moved.start == new_start
        # перенос далеко вперёд «перезаводит» 24-часовое напоминание
        new_start = start + timedelta(days=3)
        await booking.reschedule(db, s, appt, new_start)
        moved = await db.get_appointment(appt_id)
        assert moved.start == new_start and moved.end == new_start + timedelta(minutes=60)
        due = await db.due_reminders(now_local(s), 24 * 5, "reminded_1")
        assert [a.id for a in due] == [appt_id]
        await db.close()
    run(scenario())


def test_cancel_deadline():
    async def scenario():
        s, db, service, c1, _, start = await _setup()
        appt = await db.get_appointment(await booking.book(db, s, c1, service, start))
        assert booking.can_client_change(s, appt)
        appt.start = now_local(s) + timedelta(hours=2)
        assert not booking.can_client_change(s, appt)
        await db.close()
    run(scenario())
