from datetime import date, datetime, time

from bot.config import DaySchedule, _parse_schedule
from bot.slots import free_slots, time_grid

SCHEDULE = {0: DaySchedule(time(10), time(14), ((time(12), time(13)),))}  # пн 10–14, перерыв 12–13
MONDAY = date(2026, 11, 9)
EARLY = datetime(2026, 11, 1, 9, 0)


def hm(slots):
    return [s.strftime("%H:%M") for s in slots]


def test_respects_break_and_end_of_day():
    assert hm(free_slots(MONDAY, 60, SCHEDULE, [], EARLY, 30)) == ["10:00", "10:30", "11:00", "13:00"]


def test_long_service_does_not_fit_across_break():
    assert hm(free_slots(MONDAY, 120, SCHEDULE, [], EARLY, 30)) == ["10:00"]


def test_busy_interval_blocks_overlaps_only():
    busy = [(datetime(2026, 11, 9, 10, 30), datetime(2026, 11, 9, 11, 0))]
    assert hm(free_slots(MONDAY, 30, SCHEDULE, busy, EARLY, 30)) == ["10:00", "11:00", "11:30", "13:00", "13:30"]


def test_past_and_lead_time():
    now = datetime(2026, 11, 9, 10, 10)
    assert hm(free_slots(MONDAY, 30, SCHEDULE, [], now, 30, min_lead_min=60)) == ["11:30", "13:00", "13:30"]


def test_day_off():
    assert free_slots(date(2026, 11, 10), 30, SCHEDULE, [], EARLY) == []


def test_time_grid_includes_end():
    assert hm(time_grid(MONDAY, SCHEDULE, 60)) == ["10:00", "11:00", "12:00", "13:00", "14:00"]


def test_parse_schedule():
    sch = _parse_schedule({"mon": {"start": "10:00", "end": "20:00", "breaks": ["14:00-15:00"]}, "sun": None})
    assert list(sch) == [0]
    assert sch[0].breaks == ((time(14), time(15)),)
