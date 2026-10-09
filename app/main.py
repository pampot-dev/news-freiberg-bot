import asyncio
import contextlib
import logging

import httpx
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from pydantic import ValidationError

from app.config import Settings
from app.db import Database
from app.handlers import create_dispatcher
from app.handlers.commands import set_admin_commands
from app.logging_setup import setup_logging
from app.notify import Notifier
from app.services import Services
from app.sources import DEFAULT_SOURCES
from app.translate import DeeplTranslator, Glossary
from app.worker import Worker

log = logging.getLogger(__name__)


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


def load_settings_or_exit() -> None:
    try:
        Settings()
    except ValidationError as exc:
        problems = "\n".join(
            f"  {'.'.join(map(str, e['loc'])).upper()}: {e['msg']}" for e in exc.errors()
        )
        raise SystemExit(
            f"Configuration error (check .env, see .env.example):\n{problems}"
        ) from None


if __name__ == "__main__":
    load_settings_or_exit()
    asyncio.run(main())
