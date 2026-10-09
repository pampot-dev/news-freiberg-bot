from datetime import date

import aiosqlite
import pytest

from app.db import Database
from app.db.migrations import MIGRATIONS
from app.sources import NewsItem


def item(slug: str, day: int = 1, **kw) -> NewsItem:
    return NewsItem(
        url=f"https://example.org/{slug}", title=slug, published_date=date(2026, 10, day), **kw
    )


async def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "bot.db"
    first = await Database.open(path)
    await first.sources.ensure("s", "S", "https://example.org")
    await first.close()

    second = await Database.open(path)
    async with second.conn.execute("PRAGMA user_version") as cur:
        assert (await cur.fetchone())[0] == len(MIGRATIONS)
    assert len(await second.sources.list()) == 1
    await second.close()


async def test_ensure_source_keeps_state(db):
    src = await db.sources.ensure("s", "S", "https://example.org")
    await db.sources.update(src.id, enabled=False, initialized=True)
    again = await db.sources.ensure("s", "S2", "https://example.org/2")
    assert again.id == src.id
    assert again.enabled is False and again.initialized is True
    assert again.name == "S2"


async def test_update_rejects_unknown_columns(db):
    src = await db.sources.ensure("s", "S", "u")
    with pytest.raises(ValueError):
        await db.sources.update(src.id, key="other")


async def test_insert_is_idempotent_by_url(db):
    src = await db.sources.ensure("s", "S", "u")
    first = await db.news.insert(src.id, item("a"), "pending_translation")
    again = await db.news.insert(src.id, item("a"), "pending_translation")
    await db.news.commit()
    assert first is not None and again is None
    assert await db.news.known_urls(["https://example.org/a", "x"]) == {"https://example.org/a"}


async def test_chronological_order(db):
    src = await db.sources.ensure("s", "S", "u")
    # Inserted oldest-first, as the poller does; same-day items keep insertion order.
    for slug, day in [("old", 1), ("mid1", 2), ("mid2", 2), ("new", 3)]:
        await db.news.insert(src.id, item(slug, day), "pending_translation")
    await db.news.commit()
    titles = [n.title_de for n in await db.news.by_status("pending_translation")]
    assert titles == ["old", "mid1", "mid2", "new"]
    assert (await db.news.latest()).title_de == "new"


async def test_translation_and_failure(db):
    src = await db.sources.ensure("s", "S", "u")
    a = await db.news.insert(
        src.id, item("a", subtitle="Sub", teaser="Text…"), "pending_translation"
    )
    b = await db.news.insert(src.id, item("b"), "pending_translation")
    await db.news.commit()

    assert (await db.news.get(a)).text_de == "Sub\n\nText…"
    await db.news.set_translation(a, "А", "Текст")
    row = await db.news.get(a)
    assert (row.status, row.title_ru, row.text_ru) == ("ready", "А", "Текст")

    await db.news.record_translate_failure(b, "boom", give_up=False)
    assert (await db.news.get(b)).status == "pending_translation"
    await db.news.record_translate_failure(b, "boom", give_up=True)
    row = await db.news.get(b)
    assert (row.status, row.translate_attempts) == ("failed", 2)


async def test_recipient_unique_per_chat_and_thread(db):
    a = await db.recipients.add(-100, None, "Channel")
    again = await db.recipients.add(-100, None, None)
    topic = await db.recipients.add(-100, 7, "Topic")
    assert a.id == again.id and again.title == "Channel"
    assert topic.id != a.id
    assert await db.recipients.remove_chat(-100) == 2
    assert await db.recipients.list() == []


async def test_delivery_flow(db):
    src = await db.sources.ensure("s", "S", "u")
    item_id = await db.news.insert(src.id, item("a"), "pending_translation")
    await db.news.commit()
    await db.news.set_translation(item_id, "А", "Т")
    r1 = await db.recipients.add(-1, None, "one")
    r2 = await db.recipients.add(-2, None, "two")

    [ready] = await db.news.without_deliveries()
    await db.news.create_deliveries(ready.id, [r1.id, r2.id])
    await db.news.create_deliveries(ready.id, [r1.id, r2.id])  # no duplicates
    assert await db.news.without_deliveries() == []
    assert len(await db.deliveries.for_item(item_id)) == 2

    [(d1, news)] = await db.deliveries.pending_for(r1.id, limit=5)
    assert news.id == item_id
    await db.deliveries.mark_sent(d1.id, message_id=42)
    assert await db.news.finalize_published() == 0  # r2 still pending

    [(d2, _)] = await db.deliveries.pending_for(r2.id, limit=5)
    assert await db.deliveries.mark_failed_attempt(d2.id, "err", max_attempts=2) == 1
    assert (await db.deliveries.get(d2.id)).status == "pending"
    await db.deliveries.mark_failed_attempt(d2.id, "err", max_attempts=2)
    assert (await db.deliveries.get(d2.id)).status == "failed"

    assert await db.news.finalize_published() == 1
    assert (await db.news.get(item_id)).status == "published"


async def test_deactivating_recipient_skips_its_pending(db):
    src = await db.sources.ensure("s", "S", "u")
    item_id = await db.news.insert(src.id, item("a"), "ready")
    await db.news.commit()
    r = await db.recipients.add(-1, None, "one")
    await db.news.create_deliveries(item_id, [r.id])
    await db.recipients.set_active(r.id, False)
    [d] = await db.deliveries.for_item(item_id)
    assert d.status == "skipped"
    assert await db.news.finalize_published() == 1


async def test_kv(db):
    assert await db.kv.get_bool("paused") is False
    await db.kv.set_bool("paused", True)
    assert await db.kv.get_bool("paused") is True


async def test_foreign_keys_enforced(db):
    with pytest.raises(aiosqlite.IntegrityError):
        await db.news.insert(999, item("x"), "ready")
