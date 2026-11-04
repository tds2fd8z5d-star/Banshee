"""Состояния FSM."""
from aiogram.fsm.state import State, StatesGroup


class BookingSG(StatesGroup):
    name = State()
    phone = State()


class AdminSG(StatesGroup):
    svc_name = State()
    svc_price = State()
    svc_duration = State()
    svc_edit = State()
    broadcast = State()
