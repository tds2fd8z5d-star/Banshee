"""Работа с SQLite (aiosqlite). Время хранится строкой 'YYYY-MM-DD HH:MM' в поясе мастера."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from .utils import from_db, to_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id       INTEGER UNIQUE,
    name        TEXT NOT NULL,
    phone       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_clients_phone ON clients(phone);

CREATE TABLE IF NOT EXISTS services (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    price       INTEGER NOT NULL,
    duration    INTEGER NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    sort        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS appointments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     INTEGER NOT NULL REFERENCES clients(id),
    service_id    INTEGER REFERENCES services(id),
    service_name  TEXT NOT NULL,
    price         INTEGER NOT NULL,
    duration      INTEGER NOT NULL,
    start_at      TEXT NOT NULL,
    end_at        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | cancelled
    confirmed     INTEGER NOT NULL DEFAULT 0,
    reminded_1    INTEGER NOT NULL DEFAULT 0,
    reminded_2    INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL DEFAULT 'bot',       -- bot | admin
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_appt_start ON appointments(status, start_at);
-- Защита от двойной записи на одно и то же время на уровне БД
CREATE UNIQUE INDEX IF NOT EXISTS ux_appt_active_start
    ON appointments(start_at) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS blocks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    start_at  TEXT NOT NULL,
    end_at    TEXT NOT NULL,
    reason    TEXT
);
CREATE INDEX IF NOT EXISTS ix_blocks_range ON blocks(start_at, end_at);
"""


@dataclass
class Service:
    id: int
    name: str
    price: int
    duration: int
    active: bool


@dataclass
class Client:
    id: int
    tg_id: int | None
    name: str
    phone: str | None


@dataclass
class Appointment:
    id: int
    client_id: int
    service_id: int | None
    service_name: str
    price: int
    duration: int
    start: datetime
    end: datetime
    status: str
    confirmed: bool
    source: str
    client_name: str
    client_phone: str | None
    client_tg_id: int | None


@dataclass
class Block:
    id: int
    start: datetime
    end: datetime
    reason: str | None


_APPT_SELECT = """
SELECT a.*, c.name AS client_name, c.phone AS client_phone, c.tg_id AS client_tg_id
FROM appointments a JOIN clients c ON c.id = a.client_id
"""


def _appt(row: aiosqlite.Row) -> Appointment:
    return Appointment(
        id=row["id"],
        client_id=row["client_id"],
        service_id=row["service_id"],
        service_name=row["service_name"],
        price=row["price"],
        duration=row["duration"],
        start=from_db(row["start_at"]),
        end=from_db(row["end_at"]),
        status=row["status"],
        confirmed=bool(row["confirmed"]),
        source=row["source"],
        client_name=row["client_name"],
        client_phone=row["client_phone"],
        client_tg_id=row["client_tg_id"],
    )


def _service(row: aiosqlite.Row) -> Service:
    return Service(row["id"], row["name"], row["price"], row["duration"], bool(row["active"]))


def _client(row: aiosqlite.Row) -> Client:
    return Client(row["id"], row["tg_id"], row["name"], row["phone"])


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None
        # Сериализует операции «проверить свободно → записать», исключая гонки
        self.booking_lock = asyncio.Lock()

    # ---------- lifecycle ----------
    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def _fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _execute(self, sql: str, params: tuple = ()) -> int:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur.lastrowid or cur.rowcount

    # ---------- services ----------
    async def seed_services(self, services: list[dict]) -> None:
        row = await self._fetchone("SELECT COUNT(*) AS n FROM services")
        if row["n"]:
            return
        for i, s in enumerate(services):
            await self.conn.execute(
                "INSERT INTO services(name, price, duration, sort) VALUES (?, ?, ?, ?)",
                (s["name"], s["price"], s["duration"], i),
            )
        await self.conn.commit()

    async def list_services(self, include_inactive: bool = False) -> list[Service]:
        sql = "SELECT * FROM services"
        if not include_inactive:
            sql += " WHERE active = 1"
        sql += " ORDER BY sort, id"
        return [_service(r) for r in await self._fetchall(sql)]

    async def get_service(self, service_id: int) -> Service | None:
        row = await self._fetchone("SELECT * FROM services WHERE id = ?", (service_id,))
        return _service(row) if row else None

    async def add_service(self, name: str, price: int, duration: int) -> int:
        row = await self._fetchone("SELECT COALESCE(MAX(sort), 0) + 1 AS s FROM services")
        return await self._execute(
            "INSERT INTO services(name, price, duration, sort) VALUES (?, ?, ?, ?)",
            (name, price, duration, row["s"]),
        )

    async def update_service(self, service_id: int, field: str, value: object) -> None:
        if field not in {"name", "price", "duration", "active"}:
            raise ValueError(field)
        await self._execute(f"UPDATE services SET {field} = ? WHERE id = ?", (value, service_id))

    # ---------- clients ----------
    async def get_client_by_tg(self, tg_id: int) -> Client | None:
        row = await self._fetchone("SELECT * FROM clients WHERE tg_id = ?", (tg_id,))
        return _client(row) if row else None

    async def upsert_client(self, tg_id: int, name: str, phone: str) -> int:
        existing = await self.get_client_by_tg(tg_id)
        if existing:
            await self._execute(
                "UPDATE clients SET name = ?, phone = ? WHERE id = ?", (name, phone, existing.id)
            )
            return existing.id
        return await self._execute(
            "INSERT INTO clients(tg_id, name, phone) VALUES (?, ?, ?)", (tg_id, name, phone)
        )

    async def get_or_create_offline_client(self, name: str, phone: str) -> int:
        """Клиент, записанный мастером вручную: ищем по телефону, иначе создаём."""
        row = await self._fetchone(
            "SELECT * FROM clients WHERE phone = ? ORDER BY tg_id IS NULL, id LIMIT 1", (phone,)
        )
        if row:
            return row["id"]
        return await self._execute("INSERT INTO clients(name, phone) VALUES (?, ?)", (name, phone))

    async def broadcast_recipients(self) -> list[int]:
        rows = await self._fetchall("SELECT tg_id FROM clients WHERE tg_id IS NOT NULL")
        return [r["tg_id"] for r in rows]

    # ---------- appointments ----------
    async def busy_intervals(
        self, start: datetime, end: datetime, exclude_appointment_id: int | None = None
    ) -> list[tuple[datetime, datetime]]:
        """Занятые интервалы (активные записи + закрытое время), пересекающие [start, end)."""
        s, e = to_db(start), to_db(end)
        rows = await self._fetchall(
            "SELECT start_at, end_at FROM appointments "
            "WHERE status = 'active' AND start_at < ? AND end_at > ? AND id != ?",
            (e, s, exclude_appointment_id or -1),
        )
        rows += await self._fetchall(
            "SELECT start_at, end_at FROM blocks WHERE start_at < ? AND end_at > ?", (e, s)
        )
        return [(from_db(r["start_at"]), from_db(r["end_at"])) for r in rows]

    async def insert_appointment(
        self,
        client_id: int,
        service: Service,
        start: datetime,
        end: datetime,
        source: str,
        reminded_1: bool,
        reminded_2: bool,
    ) -> int:
        return await self._execute(
            "INSERT INTO appointments(client_id, service_id, service_name, price, duration, "
            "start_at, end_at, source, reminded_1, reminded_2) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                client_id, service.id, service.name, service.price, service.duration,
                to_db(start), to_db(end), source, int(reminded_1), int(reminded_2),
            ),
        )

    async def move_appointment(
        self, appointment_id: int, start: datetime, end: datetime, reminded_1: bool, reminded_2: bool
    ) -> None:
        await self._execute(
            "UPDATE appointments SET start_at = ?, end_at = ?, confirmed = 0, "
            "reminded_1 = ?, reminded_2 = ? WHERE id = ?",
            (to_db(start), to_db(end), int(reminded_1), int(reminded_2), appointment_id),
        )

    async def get_appointment(self, appointment_id: int) -> Appointment | None:
        row = await self._fetchone(_APPT_SELECT + " WHERE a.id = ?", (appointment_id,))
        return _appt(row) if row else None

    async def client_upcoming(self, tg_id: int, now: datetime) -> list[Appointment]:
        rows = await self._fetchall(
            _APPT_SELECT + " WHERE c.tg_id = ? AND a.status = 'active' AND a.start_at > ? "
            "ORDER BY a.start_at",
            (tg_id, to_db(now)),
        )
        return [_appt(r) for r in rows]

    async def appointments_between(self, start: datetime, end: datetime) -> list[Appointment]:
        rows = await self._fetchall(
            _APPT_SELECT + " WHERE a.status = 'active' AND a.start_at >= ? AND a.start_at < ? "
            "ORDER BY a.start_at",
            (to_db(start), to_db(end)),
        )
        return [_appt(r) for r in rows]

    async def cancel_appointment(self, appointment_id: int) -> bool:
        cur = await self.conn.execute(
            "UPDATE appointments SET status = 'cancelled' WHERE id = ? AND status = 'active'",
            (appointment_id,),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def confirm_appointment(self, appointment_id: int) -> None:
        await self._execute("UPDATE appointments SET confirmed = 1 WHERE id = ?", (appointment_id,))

    async def due_reminders(self, now: datetime, hours: int, flag: str) -> list[Appointment]:
        """Активные будущие записи с tg-клиентом, до начала которых ≤ hours и напоминание ещё не слали."""
        if flag not in {"reminded_1", "reminded_2"}:
            raise ValueError(flag)
        rows = await self._fetchall(
            _APPT_SELECT + f" WHERE a.status = 'active' AND a.{flag} = 0 AND c.tg_id IS NOT NULL "
            "AND a.start_at > ? AND a.start_at <= ? ORDER BY a.start_at",
            (to_db(now), to_db(now + timedelta(hours=hours))),
        )
        return [_appt(r) for r in rows]

    async def mark_reminded(self, appointment_id: int, flag: str) -> None:
        if flag not in {"reminded_1", "reminded_2"}:
            raise ValueError(flag)
        await self._execute(f"UPDATE appointments SET {flag} = 1 WHERE id = ?", (appointment_id,))

    # ---------- blocks (закрытое время) ----------
    async def add_block(self, start: datetime, end: datetime, reason: str | None = None) -> int:
        return await self._execute(
            "INSERT INTO blocks(start_at, end_at, reason) VALUES (?, ?, ?)",
            (to_db(start), to_db(end), reason),
        )

    async def upcoming_blocks(self, now: datetime) -> list[Block]:
        rows = await self._fetchall(
            "SELECT * FROM blocks WHERE end_at > ? ORDER BY start_at", (to_db(now),)
        )
        return [Block(r["id"], from_db(r["start_at"]), from_db(r["end_at"]), r["reason"]) for r in rows]

    async def delete_block(self, block_id: int) -> None:
        await self._execute("DELETE FROM blocks WHERE id = ?", (block_id,))
