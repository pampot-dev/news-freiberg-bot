import logging
from datetime import datetime, timedelta

from app.config import Settings
from app.db import Database, utcnow
from app.notify import Notifier

log = logging.getLogger(__name__)

EMPTY_POLLS_ALERT = 3
SOURCE_DOWN_AFTER = timedelta(hours=24)


async def check_sources(
    db: Database, notifier: Notifier, settings: Settings, now: datetime | None = None
) -> list[str]:
    """Alert admins once per incident; the poller resets the flags when the incident ends."""
    now = now or utcnow()
    sent: list[str] = []
    stale_after = timedelta(days=settings.stale_days)

    for src in await db.sources.list(enabled_only=True):
        if (
            src.initialized
            and src.last_new_item_at
            and not src.stale_notified
            and now - src.last_new_item_at >= stale_after
        ):
            last = src.last_new_item_at.astimezone(settings.tz)
            await notifier.send(
                f"Источник «{src.name}»: нет новых новостей {settings.stale_days} дн. "
                f"Последняя новая новость: {last:%d.%m.%Y %H:%M}."
            )
            await db.sources.update(src.id, stale_notified=True)
            sent.append(f"stale:{src.key}")

        if src.consecutive_empty_polls >= EMPTY_POLLS_ALERT and not src.empty_notified:
            await notifier.send(
                f"Источник «{src.name}»: парсер не нашёл ни одной новости "
                f"{src.consecutive_empty_polls} опроса подряд. Вероятно, изменилась вёрстка "
                f"сайта.\n{src.url}"
            )
            await db.sources.update(src.id, empty_notified=True)
            sent.append(f"empty:{src.key}")

        if (
            src.first_error_at
            and not src.down_notified
            and now - src.first_error_at >= SOURCE_DOWN_AFTER
        ):
            await notifier.send(
                f"Источник «{src.name}» недоступен больше 24 часов.\n"
                f"Последняя ошибка: {src.last_error}\n{src.url}"
            )
            await db.sources.update(src.id, down_notified=True)
            sent.append(f"down:{src.key}")

    for alert in sent:
        log.info("monitor alert sent: %s", alert)
    return sent
