import asyncio
import contextlib
import logging

import httpx
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, BotCommandScopeChat

from app.config import Settings
from app.db import Database
from app.handlers import create_dispatcher
from app.logging_setup import setup_logging
from app.notify import Notifier
from app.services import Services
from app.sources import DEFAULT_SOURCES
from app.translate import DeeplTranslator, Glossary
from app.worker import Worker

log = logging.getLogger(__name__)

ADMIN_COMMANDS = [
    BotCommand(command="menu", description="Меню администратора"),
    BotCommand(command="status", description="Состояние бота"),
    BotCommand(command="pause", description="Приостановить публикацию"),
    BotCommand(command="resume", description="Возобновить публикацию"),
    BotCommand(command="sources", description="Источники"),
    BotCommand(command="recipients", description="Получатели"),
    BotCommand(command="add_recipient", description="Добавить получателя: chat_id [thread_id]"),
    BotCommand(command="remove_recipient", description="Удалить получателя: chat_id"),
    BotCommand(command="preview", description="Предпросмотр последней новости"),
    BotCommand(command="run", description="Опросить источники сейчас"),
]


async def set_admin_commands(bot: Bot, admin_ids: list[int]) -> None:
    """Show the command list only in admins' private chats."""
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as exc:  # admin hasn't started the bot yet
            log.warning("cannot set commands for admin %s: %s", admin_id, exc)


async def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    if not settings.contact:
        log.warning("CONTACT is empty; the User-Agent will carry no contact for site owners")

    db = await Database.open(settings.db_path)
    for key, name, url in DEFAULT_SOURCES:
        await db.sources.ensure(key, name, url)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    translator = DeeplTranslator(settings.deepl_api_key, Glossary.load(settings.glossary_path))
    services = Services(db, settings, translator, Notifier(bot, settings.admin_ids, db))
    dp = create_dispatcher(services)

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent}, timeout=httpx.Timeout(30.0)
    ) as client:
        worker = asyncio.create_task(Worker(services, bot, client).run_forever(), name="worker")
        try:
            await set_admin_commands(bot, settings.admin_ids)
            log.info("bot started, polling every %s min", settings.poll_interval_min)
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            await bot.session.close()
            await db.close()
            log.info("bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
