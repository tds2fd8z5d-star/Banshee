"""Вспомогательные функции: время, форматирование, валидация ввода."""
from __future__ import annotations

import html
import re
from datetime import date, datetime

from .config import Settings

DT_FMT = "%Y-%m-%d %H:%M"  # формат хранения в БД (локальное время мастера)

WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
WEEKDAYS_FULL = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def now_local(settings: Settings) -> datetime:
    """Текущее время в часовом поясе мастера (naive)."""
    return datetime.now(settings.tz).replace(tzinfo=None, second=0, microsecond=0)


def to_db(dt: datetime) -> str:
    return dt.strftime(DT_FMT)


def from_db(value: str) -> datetime:
    return datetime.strptime(value, DT_FMT)


def day_code(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_day_code(code: str) -> date:
    return datetime.strptime(code, "%Y%m%d").date()


def fmt_day_short(d: date) -> str:
    return f"{WEEKDAYS_SHORT[d.weekday()]}, {d:%d.%m}"


def fmt_day(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]}, {WEEKDAYS_FULL[d.weekday()]}"


def fmt_dt(dt: datetime) -> str:
    return f"{fmt_day(dt.date())}, {dt:%H:%M}"


def fmt_price(price: int) -> str:
    return f"{price:,}".replace(",", " ") + " ₽"


def fmt_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    parts = []
    if h:
        parts.append(f"{h} ч")
    if m:
        parts.append(f"{m} мин")
    return " ".join(parts) or "0 мин"


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\s\-'.]{0,49}$")


def clean_name(text: str | None) -> str | None:
    if not text:
        return None
    name = " ".join(text.split())
    if len(name) < 2 or not _NAME_RE.match(name):
        return None
    return name


def clean_phone(text: str | None) -> str | None:
    """Нормализует номер к виду +7XXXXXXXXXX (или +<код> для других стран)."""
    if not text:
        return None
    raw = text.strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10 and not raw.startswith("+"):
        digits = "7" + digits
    if not 10 <= len(digits) <= 15:
        return None
    return "+" + digits


def parse_positive_int(text: str | None, max_value: int = 1_000_000) -> int | None:
    if not text:
        return None
    cleaned = re.sub(r"[\s₽руб.]", "", text.strip().lower())
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    return value if 0 < value <= max_value else None
