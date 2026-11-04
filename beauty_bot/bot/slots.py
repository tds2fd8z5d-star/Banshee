"""Чистая логика расчёта свободного времени (без БД и Telegram)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .config import DaySchedule

Interval = tuple[datetime, datetime]


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def work_bounds(day: date, schedule: dict[int, DaySchedule]) -> Interval | None:
    """Начало и конец рабочего дня или None, если выходной."""
    ds = schedule.get(day.weekday())
    if ds is None:
        return None
    return datetime.combine(day, ds.start), datetime.combine(day, ds.end)


def break_intervals(day: date, schedule: dict[int, DaySchedule]) -> list[Interval]:
    ds = schedule.get(day.weekday())
    if ds is None:
        return []
    return [(datetime.combine(day, s), datetime.combine(day, e)) for s, e in ds.breaks]


def free_slots(
    day: date,
    duration_min: int,
    schedule: dict[int, DaySchedule],
    busy: list[Interval],
    now: datetime,
    step_min: int = 30,
    min_lead_min: int = 0,
) -> list[datetime]:
    """Список времён начала, в которые помещается услуга длительностью duration_min.

    Учитывает рабочие часы, перерывы, занятые интервалы (записи и закрытое
    мастером время) и то, что нельзя записаться в прошлое / слишком близко
    к текущему моменту.
    """
    bounds = work_bounds(day, schedule)
    if bounds is None or duration_min <= 0:
        return []
    day_start, day_end = bounds
    blocked = list(busy) + break_intervals(day, schedule)
    earliest = now + timedelta(minutes=min_lead_min)
    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=step_min)

    result = []
    t = day_start
    while t + duration <= day_end:
        end = t + duration
        if t >= earliest and not any(overlaps(t, end, b_s, b_e) for b_s, b_e in blocked):
            result.append(t)
        t += step
    return result


def time_grid(day: date, schedule: dict[int, DaySchedule], step_min: int) -> list[datetime]:
    """Все отметки времени рабочего дня с шагом step_min, включая время окончания."""
    bounds = work_bounds(day, schedule)
    if bounds is None:
        return []
    start, end = bounds
    grid = []
    t = start
    while t <= end:
        grid.append(t)
        t += timedelta(minutes=step_min)
    return grid
