import html
import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.db import Database, utcnow

log = logging.getLogger(__name__)


class MessageSender(Protocol):
    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Any: ...


class Notifier:
    """Sends plain-text alerts to every admin's private chat."""

    def __init__(self, bot: MessageSender, admin_ids: list[int], db: Database) -> None:
        self.bot = bot
        self.admin_ids = admin_ids
        self.db = db

    async def send(self, text: str, *, is_html: bool = False, reply_markup: Any = None) -> None:
        """Plain text is escaped; pass is_html=True for pre-built HTML."""
        body = text if is_html else html.escape(text)
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    admin_id, body, parse_mode="HTML", reply_markup=reply_markup
                )
            except Exception:
                # An admin who never started the bot can't be messaged; don't break the cycle.
                log.exception("failed to notify admin %s", admin_id)

    async def send_throttled(
        self,
        key: str,
        text: str,
        interval: timedelta = timedelta(days=1),
        now: datetime | None = None,
    ) -> bool:
        """Send at most once per `interval` for the given key. Returns True if sent."""
        now = now or utcnow()
        kv_key = f"notified_at:{key}"
        last = await self.db.kv.get_datetime(kv_key)
        if last and now - last < interval:
            return False
        await self.send(text)
        await self.db.kv.set_datetime(kv_key, now)
        return True
