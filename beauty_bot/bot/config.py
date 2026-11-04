"""Загрузка настроек из .env и config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class ConfigError(Exception):
    """Ошибка в config.yaml или .env."""


@dataclass(frozen=True)
class MasterInfo:
    name: str
    specialization: str
    address: str
    phone: str
    portfolio_url: str


@dataclass(frozen=True)
class DaySchedule:
    start: time
    end: time
    breaks: tuple[tuple[time, time], ...] = ()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    master: MasterInfo
    tz: ZoneInfo
    schedule: dict[int, DaySchedule]  # 0 = понедельник
    services_seed: list[dict]
    db_path: Path
    days_ahead: int = 14
    slot_step: int = 30
    min_lead_minutes: int = 60
    cancel_deadline_hours: int = 0
    remind_first_hours: int = 24
    remind_second_hours: int = 2
    admin_digest_time: time | None = None
    extra: dict = field(default_factory=dict)

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


def _parse_time(value: str, where: str) -> time:
    try:
        hh, mm = str(value).strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{where}: неверное время {value!r}, ожидается ЧЧ:ММ") from exc


def _parse_schedule(raw: dict | None) -> dict[int, DaySchedule]:
    raw = raw or {}
    unknown = set(raw) - set(WEEKDAYS)
    if unknown:
        raise ConfigError(f"schedule: неизвестные дни {sorted(unknown)}, допустимо {WEEKDAYS}")
    result: dict[int, DaySchedule] = {}
    for idx, key in enumerate(WEEKDAYS):
        day = raw.get(key)
        if not day:
            continue
        start = _parse_time(day.get("start"), f"schedule.{key}.start")
        end = _parse_time(day.get("end"), f"schedule.{key}.end")
        if start >= end:
            raise ConfigError(f"schedule.{key}: начало должно быть раньше конца")
        breaks = []
        for br in day.get("breaks") or []:
            try:
                b_start, b_end = str(br).split("-")
            except ValueError as exc:
                raise ConfigError(f"schedule.{key}.breaks: {br!r}, ожидается ЧЧ:ММ-ЧЧ:ММ") from exc
            bs = _parse_time(b_start, f"schedule.{key}.breaks")
            be = _parse_time(b_end, f"schedule.{key}.breaks")
            if bs >= be:
                raise ConfigError(f"schedule.{key}.breaks: {br!r} — начало позже конца")
            breaks.append((bs, be))
        result[idx] = DaySchedule(start, end, tuple(breaks))
    if not result:
        raise ConfigError("schedule: не задано ни одного рабочего дня")
    return result


def _parse_services(raw: list | None) -> list[dict]:
    services = []
    for i, item in enumerate(raw or []):
        try:
            name = str(item["name"]).strip()
            price = int(item["price"])
            duration = int(item["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"services[{i}]: нужны поля name, price, duration") from exc
        if not name or price < 0 or duration <= 0:
            raise ConfigError(f"services[{i}]: некорректные значения")
        services.append({"name": name, "price": price, "duration": duration})
    return services


def _parse_admin_ids(value: str | None) -> set[int]:
    ids: set[int] = set()
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(f"ADMIN_IDS: {part!r} не число") from exc
    return ids


def load_settings(config_path: str | Path | None = None, env_path: str | Path | None = None) -> Settings:
    base_dir = Path(__file__).resolve().parent.parent
    load_dotenv(env_path or base_dir / ".env")

    config_path = Path(config_path or os.getenv("CONFIG_PATH") or base_dir / "config.yaml")
    if not config_path.exists():
        raise ConfigError(f"Не найден файл настроек {config_path}")
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("BOT_TOKEN не задан в .env")

    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID"))
    admin_ids |= {int(x) for x in cfg.get("admin_ids") or []}
    if not admin_ids:
        raise ConfigError("ADMIN_IDS не задан в .env — укажите Telegram ID мастера")

    m = cfg.get("master") or {}
    master = MasterInfo(
        name=str(m.get("name", "Мастер")),
        specialization=str(m.get("specialization", "")),
        address=str(m.get("address", "")),
        phone=str(m.get("phone", "")),
        portfolio_url=str(m.get("portfolio_url", "")),
    )

    try:
        tz = ZoneInfo(cfg.get("timezone", "Europe/Moscow"))
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"timezone: неизвестный часовой пояс {cfg.get('timezone')!r}") from exc

    booking = cfg.get("booking") or {}
    reminders = cfg.get("reminders") or {}
    digest = reminders.get("admin_digest_time")
    db_path = Path(os.getenv("DB_PATH") or base_dir / "data" / "bot.sqlite3")

    settings = Settings(
        bot_token=token,
        admin_ids=frozenset(admin_ids),
        master=master,
        tz=tz,
        schedule=_parse_schedule(cfg.get("schedule")),
        services_seed=_parse_services(cfg.get("services")),
        db_path=db_path,
        days_ahead=int(booking.get("days_ahead", 14)),
        slot_step=int(booking.get("slot_step_minutes", 30)),
        min_lead_minutes=int(booking.get("min_lead_minutes", 60)),
        cancel_deadline_hours=int(booking.get("cancel_deadline_hours", 0)),
        remind_first_hours=int(reminders.get("first_hours", 24)),
        remind_second_hours=int(reminders.get("second_hours", 2)),
        admin_digest_time=_parse_time(digest, "reminders.admin_digest_time") if digest else None,
    )
    if settings.slot_step <= 0 or settings.days_ahead <= 0:
        raise ConfigError("booking: slot_step_minutes и days_ahead должны быть > 0")
    return settings
