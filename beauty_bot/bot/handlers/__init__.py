from aiogram import Router

from . import admin, booking, client, commands, fallback


def setup_routers() -> Router:
    root = Router(name="root")
    # Порядок важен: команды → мастер → запись → клиент → всё остальное
    root.include_routers(commands.router, admin.router, booking.router, client.router, fallback.router)
    return root
