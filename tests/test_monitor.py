from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.monitor import check_sources
from app.poller import poll_source
from app.sources import NewsItem, Source, SourceError

T0 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


class ScriptedSource(Source):
    key = "scripted"
    items: list[NewsItem] = []
    error: str | None = None

    async def fetch(self):
        if ScriptedSource.error:
            raise SourceError(ScriptedSource.error)
        return list(ScriptedSource.items)


def item(slug):
    return NewsItem(url=f"https://ex.org/{slug}", title=slug, published_date=date(2026, 10, 1))


@pytest.fixture
async def env(db, notifier, bot, settings):
    ScriptedSource.items, ScriptedSource.error = [item("a")], None
    await db.sources.ensure("scripted", "Скрипт", "https://ex.org")

    async with httpx.AsyncClient() as client:

        async def poll(now):
            row = await db.sources.get_by_key("scripted")
            await poll_source(db, row, client, now)

        async def check(now):
            return await check_sources(db, notifier, settings, now)

        yield poll, check


async def test_stale_alert_once_until_new_item(env, bot):
    poll, check = env
    await poll(T0)
    assert await check(T0 + timedelta(days=6)) == []
    assert await check(T0 + timedelta(days=7)) == ["stale:scripted"]
    assert await check(T0 + timedelta(days=8)) == []
    assert await check(T0 + timedelta(days=30)) == []

    ScriptedSource.items = [item("b"), item("a")]
    new_at = T0 + timedelta(days=31)
    await poll(new_at)
    assert await check(new_at + timedelta(days=6)) == []
    assert await check(new_at + timedelta(days=7)) == ["stale:scripted"]
    assert len(bot.texts_to(1001)) == 2


async def test_empty_polls_alert_after_three(env, bot):
    poll, check = env
    await poll(T0)
    ScriptedSource.items = []
    for n in range(1, 4):
        await poll(T0 + timedelta(minutes=30 * n))
        alerts = await check(T0 + timedelta(minutes=30 * n))
        assert alerts == ([] if n < 3 else ["empty:scripted"])
    await poll(T0 + timedelta(hours=3))
    assert await check(T0 + timedelta(hours=3)) == []
    assert "вёрстка" in bot.texts_to(1001)[0]

    # Items come back -> re-armed.
    ScriptedSource.items = [item("a")]
    await poll(T0 + timedelta(hours=4))
    ScriptedSource.items = []
    for n in range(3):
        await poll(T0 + timedelta(hours=5 + n))
    assert await check(T0 + timedelta(hours=8)) == ["empty:scripted"]


async def test_source_down_for_24_hours(env, bot):
    poll, check = env
    ScriptedSource.error = "HTTP 503"
    await poll(T0)
    await poll(T0 + timedelta(hours=12))
    assert await check(T0 + timedelta(hours=23)) == []
    await poll(T0 + timedelta(hours=24))
    assert await check(T0 + timedelta(hours=24)) == ["down:scripted"]
    assert await check(T0 + timedelta(hours=30)) == []
    assert "HTTP 503" in bot.texts_to(1001)[0]

    ScriptedSource.error = None
    await poll(T0 + timedelta(hours=31))
    ScriptedSource.error = "timeout"
    await poll(T0 + timedelta(hours=32))
    assert await check(T0 + timedelta(hours=40)) == []
    assert await check(T0 + timedelta(hours=56)) == ["down:scripted"]


async def test_disabled_sources_are_not_checked(env, db):
    poll, check = env
    await poll(T0)
    src = await db.sources.get_by_key("scripted")
    await db.sources.update(src.id, enabled=False)
    assert await check(T0 + timedelta(days=30)) == []
