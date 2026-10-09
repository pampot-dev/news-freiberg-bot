import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)
from aiogram.types import LinkPreviewOptions

from app.config import Settings
from app.db import Database, Delivery, NewsRow, Recipient, utcnow
from app.notify import MessageSender, Notifier
from app.publish.formatter import format_item

log = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 3
# Telegram allows ~20 messages per minute into one group/channel.
PER_CHAT_PAUSE = 3.0
PAUSED_KEY = "paused"

Sleep = Callable[[float], Awaitable[None]]


def in_quiet_hours(now: datetime, tz: ZoneInfo, hours: tuple[int, int] | None) -> bool:
    if hours is None:
        return False
    start, end = hours
    hour = now.astimezone(tz).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def seconds_until_quiet_end(now: datetime, tz: ZoneInfo, hours: tuple[int, int] | None) -> float:
    """Time left in the current quiet period; 0 outside quiet hours."""
    if not in_quiet_hours(now, tz, hours):
        return 0.0
    local = now.astimezone(tz)
    end = local.replace(hour=hours[1], minute=0, second=0, microsecond=0)
    if end <= local:
        end += timedelta(days=1)
    # Subtract in UTC: same-tzinfo subtraction ignores DST shifts.
    return (end.astimezone(UTC) - now.astimezone(UTC)).total_seconds()


@dataclass(slots=True)
class PublishResult:
    skipped: str | None = None  # paused / quiet_hours / no_recipients
    sent: int = 0
    failed: int = 0
    deactivated: list[int] = field(default_factory=list)
    published: int = 0


async def send_post(bot: MessageSender, recipient: Recipient, text: str) -> int:
    message = await bot.send_message(
        recipient.chat_id,
        text,
        message_thread_id=recipient.thread_id,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    return message.message_id


class Publisher:
    def __init__(
        self,
        db: Database,
        bot: MessageSender,
        notifier: Notifier,
        settings: Settings,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.db = db
        self.bot = bot
        self.notifier = notifier
        self.settings = settings
        self.sleep = sleep

    async def run(self, now: datetime | None = None) -> PublishResult:
        now = now or utcnow()
        result = PublishResult()
        if await self.db.kv.get_bool(PAUSED_KEY):
            result.skipped = "paused"
            return result
        if in_quiet_hours(now, self.settings.tz, self.settings.quiet_hours):
            result.skipped = "quiet_hours"
            return result

        recipients = await self.db.recipients.list(active_only=True)
        if not recipients:
            result.skipped = "no_recipients"
            return result

        for item in await self.db.news.without_deliveries():
            await self.db.news.create_deliveries(item.id, [r.id for r in recipients], now)

        queues = {
            r.id: await self.db.deliveries.pending_for(r.id, self.settings.batch_limit)
            for r in recipients
        }
        by_id = {r.id: r for r in recipients}
        # Round-robin: post #1 to every chat, then #2, ... so the per-chat pause overlaps.
        for round_no in range(self.settings.batch_limit):
            batch = [(by_id[rid], q[round_no]) for rid, q in queues.items() if len(q) > round_no]
            if not batch:
                break
            if round_no:
                await self.sleep(PER_CHAT_PAUSE)
            for recipient, (delivery, item) in batch:
                ok = await self._deliver(recipient, delivery, item, result, now)
                if not ok:
                    # Keep chronological order: nothing newer goes to this chat this cycle.
                    queues[recipient.id] = queues[recipient.id][: round_no + 1]

        result.published = await self.db.news.finalize_published(now)
        return result

    async def _deliver(
        self,
        recipient: Recipient,
        delivery: Delivery,
        item: NewsRow,
        result: PublishResult,
        now: datetime,
    ) -> bool:
        text = format_item(item)
        for _ in range(3):  # retry only for flood control and chat migration
            try:
                message_id = await send_post(self.bot, recipient, text)
            except TelegramRetryAfter as exc:
                log.warning("flood control for %s, waiting %ss", recipient.chat_id, exc.retry_after)
                await self.sleep(exc.retry_after)
                continue
            except TelegramMigrateToChat as exc:
                if await self.db.recipients.change_chat_id(recipient.id, exc.migrate_to_chat_id):
                    log.info("chat %s migrated to %s", recipient.chat_id, exc.migrate_to_chat_id)
                    recipient.chat_id = exc.migrate_to_chat_id
                    continue
                await self._deactivate(recipient, f"чат стал супергруппой {exc.migrate_to_chat_id}")
                result.deactivated.append(recipient.id)
                return False
            except TelegramForbiddenError as exc:
                await self._deactivate(recipient, exc.message)
                result.deactivated.append(recipient.id)
                return False
            except (TimeoutError, TelegramAPIError, OSError) as exc:
                await self._record_failure(recipient, delivery, item, str(exc), result)
                return False
            await self.db.deliveries.mark_sent(delivery.id, message_id, now)
            result.sent += 1
            return True
        await self._record_failure(recipient, delivery, item, "flood control", result)
        return False

    async def _deactivate(self, recipient: Recipient, reason: str) -> None:
        log.warning("recipient %s deactivated: %s", recipient.chat_id, reason)
        await self.db.recipients.set_active(recipient.id, False)
        await self.notifier.send(
            f"Получатель «{recipient.display_name}» ({recipient.chat_id}) отключён: {reason}.\n"
            "Проверьте права бота и включите получателя снова через меню «Получатели»."
        )

    async def _record_failure(
        self,
        recipient: Recipient,
        delivery: Delivery,
        item: NewsRow,
        error: str,
        result: PublishResult,
    ) -> None:
        log.warning("delivery %s to %s failed: %s", delivery.id, recipient.chat_id, error)
        attempts = await self.db.deliveries.mark_failed_attempt(
            delivery.id, error[:500], MAX_DELIVERY_ATTEMPTS
        )
        if attempts >= MAX_DELIVERY_ATTEMPTS:
            result.failed += 1
            await self.notifier.send(
                f"Не удалось отправить новость в «{recipient.display_name}» "
                f"после {attempts} попыток.\n{item.title_de}\nОшибка: {error}"
            )
