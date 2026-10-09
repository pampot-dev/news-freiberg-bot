import logging
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from app.db import Database, SourceRow, utcnow
from app.sources import NewsItem, SourceError, create_source

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PollResult:
    source_key: str
    found: int = 0
    new: int = 0
    error: str | None = None


def oldest_first(items: list[NewsItem]) -> list[NewsItem]:
    """Sort by date ascending; within a day the page lists newer items first."""
    indexed = list(enumerate(items))
    indexed.sort(key=lambda pair: (pair[1].published_date or date.min, -pair[0]))
    return [item for _, item in indexed]


def freshest(items: list[NewsItem]) -> NewsItem:
    """Newest by date; on equal dates the first one on the page wins."""
    return max(enumerate(items), key=lambda pair: (pair[1].published_date or date.min, -pair[0]))[1]


async def poll_source(
    db: Database, row: SourceRow, client: httpx.AsyncClient, now: datetime | None = None
) -> PollResult:
    now = now or utcnow()
    result = PollResult(row.key)
    try:
        items = await create_source(row.key, row.url, client).fetch()
    except (SourceError, KeyError) as exc:
        log.warning("source %s failed: %s", row.key, exc)
        result.error = str(exc)
        await db.sources.update(
            row.id,
            last_poll_at=now,
            last_error_at=now,
            last_error=result.error[:500],
            first_error_at=row.first_error_at or now,
        )
        return result

    result.found = len(items)
    state: dict[str, object] = {"last_poll_at": now, "first_error_at": None, "down_notified": False}
    if items:
        state.update(consecutive_empty_polls=0, empty_notified=False)
    else:
        state["consecutive_empty_polls"] = row.consecutive_empty_polls + 1
        log.warning("source %s returned no items", row.key)

    known = await db.news.known_urls(i.url for i in items)
    new_items = [i for i in items if i.url not in known]

    if not row.initialized and items:
        # First run: everything counts as seen except the freshest item.
        publish = freshest(items)
        for item in oldest_first(new_items):
            status = "pending_translation" if item is publish else "skipped_initial"
            await db.news.insert(row.id, item, status, now)
        await db.news.commit()
        state.update(initialized=True, last_new_item_at=now, stale_notified=False)
        result.new = 1 if publish in new_items else 0
        log.info("source %s initialized: %d items seen", row.key, len(items))
    elif new_items:
        inserted = 0
        for item in oldest_first(new_items):
            if await db.news.insert(row.id, item, "pending_translation", now):
                inserted += 1
        await db.news.commit()
        result.new = inserted
        if inserted:
            state.update(last_new_item_at=now, stale_notified=False)
            log.info("source %s: %d new items", row.key, inserted)

    await db.sources.update(row.id, **state)
    return result


async def poll_all(
    db: Database, client: httpx.AsyncClient, now: datetime | None = None
) -> list[PollResult]:
    """Poll enabled sources one after another (no parallel requests)."""
    return [await poll_source(db, row, client, now) for row in await db.sources.list(True)]
