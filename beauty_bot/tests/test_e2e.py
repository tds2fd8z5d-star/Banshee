"""Сквозной прогон сценариев через Dispatcher с фейковой сессией Telegram (без сети)."""
import asyncio
import itertools
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery, EditMessageText, SendMessage, TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, Contact, InlineKeyboardMarkup, Message, Update, User

from bot import keyboards as kb
from bot.__main__ import on_error
from bot.config import DaySchedule, MasterInfo, Settings
from bot.db import Database
from bot.handlers import setup_routers
from bot.scheduler import send_admin_digest, send_reminders
from bot.utils import now_local

ADMIN, CLIENT = 1, 100
_ids = itertools.count(1000)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = getattr(method, "chat_id", None) or 0
            markup = method.reply_markup if isinstance(method.reply_markup, InlineKeyboardMarkup) else None
            return Message(message_id=next(_ids), date=datetime.now(), text=method.text,
                           chat=Chat(id=chat_id, type="private"), reply_markup=markup)
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    def texts(self):
        return [c.text for c in self.calls if isinstance(c, (SendMessage, EditMessageText))]

    def last_markup(self):
        for c in reversed(self.calls):
            if isinstance(c, (SendMessage, EditMessageText)) and c.reply_markup is not None:
                return c.reply_markup
        return None

    def alerts(self):
        return [c.text for c in self.calls if isinstance(c, AnswerCallbackQuery) and c.text]


def user(uid):
    return User(id=uid, is_bot=False, first_name=f"U{uid}")


def msg_update(uid, text=None, contact=None):
    m = Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=uid, type="private"),
                from_user=user(uid), text=text, contact=contact)
    return Update(update_id=next(_ids), message=m)


def cb_update(uid, data):
    m = Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=uid, type="private"), text="x")
    return Update(update_id=next(_ids), callback_query=CallbackQuery(
        id=str(next(_ids)), from_user=user(uid), chat_instance="ci", message=m, data=data))


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


async def scenario():
    settings = Settings(
        bot_token="42:TEST", admin_ids=frozenset({ADMIN}), master=MasterInfo("Анна", "Ногти", "Адрес", "+7900", "https://x"),
        tz=ZoneInfo("Europe/Moscow"),
        schedule={i: DaySchedule(time(10), time(20), ((time(14), time(15)),)) for i in range(6)},
        services_seed=[{"name": "Маникюр", "price": 1500, "duration": 60}],
        db_path=Path(":memory:"), cancel_deadline_hours=3,
    )
    db = Database(":memory:")
    await db.connect()
    try:
        await _run(settings, db)
    finally:
        await db.close()


async def _run(settings, db):
    await db.seed_services(settings.services_seed)
    session = FakeSession()
    bot = Bot("42:TEST", session=session)
    dp = Dispatcher(storage=MemoryStorage(), settings=settings, db=db)
    dp.include_router(setup_routers())
    dp.errors.register(on_error)

    async def send(update):
        await dp.feed_update(bot, update)

    # --- клиент записывается ---
    await send(msg_update(CLIENT, "/start"))
    assert "Анна" in session.texts()[-1]
    await send(cb_update(CLIENT, kb.MenuCb(action="prices").pack()))
    assert "1 500 ₽" in session.texts()[-1]
    await send(cb_update(CLIENT, kb.MenuCb(action="contacts").pack()))
    await send(cb_update(CLIENT, kb.MenuCb(action="book").pack()))
    svc_btn = buttons(session.last_markup())[0]
    await send(cb_update(CLIENT, svc_btn.callback_data))
    day_btn = buttons(session.last_markup())[1]  # второй доступный день
    await send(cb_update(CLIENT, day_btn.callback_data))
    time_btns = buttons(session.last_markup())
    assert time_btns[0].text == "10:00"
    assert "14:00" not in [b.text for b in time_btns]  # перерыв
    await send(cb_update(CLIENT, time_btns[0].callback_data))
    assert session.texts()[-1] == "Как вас зовут?"
    await send(msg_update(CLIENT, "1"))  # неверное имя
    assert "имя" in session.texts()[-1]
    await send(msg_update(CLIENT, "Мария"))
    await send(msg_update(CLIENT, "не телефон"))
    assert "номер" in session.texts()[-1]
    await send(msg_update(CLIENT, contact=Contact(phone_number="79001112233", first_name="M", user_id=CLIENT)))
    assert "Проверьте" in session.texts()[-1] and "+79001112233" in session.texts()[-1]
    await send(cb_update(CLIENT, kb.ConfirmCb(action="yes").pack()))
    assert "вы записаны" in "\n".join(session.texts()[-2:])
    assert any("Новая запись" in t for t in session.texts()[-2:])
    await send(cb_update(CLIENT, kb.ConfirmCb(action="yes").pack()))  # двойное нажатие
    assert "устарел" in session.texts()[-1]

    # --- второй клиент не может занять то же время ---
    other = 200
    await send(cb_update(other, kb.MenuCb(action="book").pack()))
    await send(cb_update(other, svc_btn.callback_data))
    await send(cb_update(other, day_btn.callback_data))
    assert "10:00" not in [b.text for b in buttons(session.last_markup())]
    await send(cb_update(other, time_btns[0].callback_data))  # устаревшая кнопка 10:00
    assert any("занято" in a for a in session.alerts())

    # --- мои записи, перенос, отмена ---
    await send(cb_update(CLIENT, kb.MenuCb(action="my").pack()))
    assert "Ваши записи" in session.texts()[-1]
    appt = (await db.client_upcoming(CLIENT, now_local(settings)))[0]
    await send(cb_update(CLIENT, kb.ApptCb(action="resched", id=appt.id).pack()))
    await send(cb_update(CLIENT, day_btn.callback_data))
    await send(cb_update(CLIENT, kb.TimeCb(hm="1100").pack()))
    await send(cb_update(CLIENT, kb.ConfirmCb(action="yes").pack()))
    assert any("Перенос" in t for t in session.texts()[-2:])
    assert (await db.get_appointment(appt.id)).start.time() == time(11)
    await send(cb_update(other, kb.ApptCb(action="cancel_yes", id=appt.id).pack()))  # чужая запись
    assert "не найдена" in session.alerts()[-1]
    await send(cb_update(CLIENT, kb.ApptCb(action="confirm", id=appt.id).pack()))
    assert (await db.get_appointment(appt.id)).confirmed

    # --- мастер ---
    await send(msg_update(CLIENT, "/admin"))
    assert "только мастеру" in session.texts()[-1]
    await send(cb_update(CLIENT, kb.AdminCb(action="list", arg="week").pack()))  # не админ
    assert "устарела" in session.alerts()[-1]
    await send(msg_update(ADMIN, "/admin"))
    for arg in ("today", "tomorrow", "week"):
        await send(cb_update(ADMIN, kb.AdminCb(action="list", arg=arg).pack()))
    assert "Мария" in session.texts()[-1]

    # закрыть часы в день записи
    day_code = day_btn.callback_data.split(":")[1]
    await send(cb_update(ADMIN, kb.AdminCb(action="close").pack()))
    await send(cb_update(ADMIN, kb.AdminCb(action="close_day", arg=day_code).pack()))
    await send(cb_update(ADMIN, kb.AdminCb(action="close_from").pack()))
    await send(cb_update(ADMIN, kb.AdminCb(action="close_start", arg="1600").pack()))
    await send(cb_update(ADMIN, kb.AdminCb(action="close_end", arg="1800").pack()))
    assert "закрыто" in session.texts()[-1]
    await send(cb_update(ADMIN, kb.AdminCb(action="close_day", arg=day_code).pack()))
    await send(cb_update(ADMIN, kb.AdminCb(action="close_full").pack()))
    assert "уже есть записи" in session.texts()[-1]
    await send(cb_update(ADMIN, kb.AdminCb(action="blocks").pack()))
    blocks = await db.upcoming_blocks(now_local(settings))
    assert len(blocks) == 2
    for b in blocks:
        await send(cb_update(ADMIN, kb.AdminCb(action="bdel", arg=str(b.id)).pack()))
    assert not await db.upcoming_blocks(now_local(settings))

    # ручная запись
    await send(cb_update(ADMIN, kb.AdminCb(action="add").pack()))
    await send(cb_update(ADMIN, svc_btn.callback_data))
    await send(cb_update(ADMIN, day_btn.callback_data))
    await send(cb_update(ADMIN, kb.TimeCb(hm="1500").pack()))
    await send(msg_update(ADMIN, "Ольга"))
    await send(msg_update(ADMIN, "8 900 555 44 33"))
    await send(cb_update(ADMIN, kb.ConfirmCb(action="yes").pack()))
    assert "Запись добавлена" in session.texts()[-1]

    # услуги
    s_id = (await db.list_services())[0].id
    await send(cb_update(ADMIN, kb.AdminCb(action="services").pack()))
    await send(cb_update(ADMIN, kb.AdminCb(action="svc_edit", arg=f"{s_id}-price").pack()))
    await send(msg_update(ADMIN, "дорого"))
    await send(msg_update(ADMIN, "1800"))
    assert (await db.get_service(s_id)).price == 1800
    await send(cb_update(ADMIN, kb.AdminCb(action="svc_new").pack()))
    await send(msg_update(ADMIN, "Брови"))
    await send(msg_update(ADMIN, "1000"))
    await send(msg_update(ADMIN, "45"))
    assert len(await db.list_services()) == 2
    await send(cb_update(ADMIN, kb.AdminCb(action="svc_toggle", arg=str(s_id)).pack()))
    assert len(await db.list_services()) == 1

    # рассылка
    await send(cb_update(ADMIN, kb.AdminCb(action="broadcast").pack()))
    await send(msg_update(ADMIN, "Есть окна на пятницу!"))
    await send(cb_update(ADMIN, kb.AdminCb(action="bc_send").pack()))
    assert "доставлено 1" in session.texts()[-1]

    # напоминания и сводка
    await db.conn.execute("UPDATE appointments SET reminded_1 = 0, reminded_2 = 0")
    await db.conn.commit()
    before = len(session.calls)
    await send_reminders(bot, db, settings)
    await send_reminders(bot, db, settings)  # повторно не шлёт
    reminders = [c for c in session.calls[before:] if isinstance(c, SendMessage)]
    assert len(reminders) == 1 and "Напоминаю" in reminders[0].text
    await send_admin_digest(bot, db, settings)

    # мусорный ввод
    await send(msg_update(CLIENT, "привет"))
    assert "Выберите" in session.texts()[-1]
    await send(cb_update(CLIENT, "garbage:data"))
    await send(cb_update(CLIENT, kb.DayCb(day="20261301").pack()))  # без сценария


def test_full_flow():
    asyncio.run(scenario())
