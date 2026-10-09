from aiogram import Dispatcher

from app.handlers.admin import create_admin_router
from app.handlers.chat_member import create_chat_member_router
from app.services import Services


def create_dispatcher(services: Services) -> Dispatcher:
    dp = Dispatcher()
    dp["services"] = services
    dp.include_router(create_admin_router(services.settings.admin_ids))
    dp.include_router(create_chat_member_router())
    return dp


__all__ = ["create_dispatcher"]
