"""End-to-end cycles through poller, translator and publisher with fakes at the edges."""

import asyncio
import contextlib
from datetime import UTC, date, datetime
from unittest.mock import patch

import deepl
import httpx
import pytest

from app.notify import Notifier
from app.publish.publisher import Publisher
from app.services import Services
from app.sources import NewsItem, Source
from app.worker import Worker

DAY = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)  # 12:00 in Berlin


class PageSource(Source):
    key = "page"
    items: list[NewsItem] = []

    async def fetch(self):
        return list(PageSource.items)


class FakeTranslator:
    def __init__(self):
        self.error = None

    async def translate(self, title, text):
        if self.error:
            raise self.error
        return f"RU {title}", f"RU {text}"

    async def usage(self):
        return None


def item(slug, day):
    return NewsItem(url=f"https://ex.org/{slug}", title=slug, published_date=date(2026, 10, day))


async def no_sleep(_):
    pass


@pytest.fixture
async def worker(db, bot, settings):
    PageSource.items = []
    await db.sources.ensure("page", "Page", "https://ex.org")
    notifier = Notifier(bot, [1001], db)
    services = Services(db, settings, FakeTranslator(), notifier)
    async with httpx.AsyncClient() as client:
        publisher = Publisher(db, bot, notifier, settings, sleep=no_sleep)
        w = Worker(services, bot, client, publisher)
        with patch("app.worker.utcnow", return_value=DAY):
            yield w


async def test_first_run_publishes_exactly_the_freshest(worker, db, bot):
    await db.recipients.add(-1, None, "one")
    await db.recipients.add(-2, None, "two")
    PageSource.items = [item("new", 8), item("older", 8), item("old", 2)]
    report = await worker.run_cycle()
    assert report.errors == []
    for chat in (-1, -2):
        [post] = bot.texts_to(chat)
        assert post.startswith("<b>RU new</b>\n<i>new</i>")


async def test_new_item_reaches_everyone_in_one_cycle_without_duplicates(worker, db, bot):
    await db.recipients.add(-1, None, "one")
    PageSource.items = [item("a", 1)]
    await worker.run_cycle()
    PageSource.items = [item("b", 2), item("a", 1)]
    await worker.run_cycle()
    await worker.run_cycle()
    assert [t.split("</b>")[0] for t in bot.texts_to(-1)] == ["<b>RU a", "<b>RU b"]


async def test_deepl_down_never_publishes_german(worker, db, bot):
    await db.recipients.add(-1, None, "one")
    worker.services.translator.error = deepl.ConnectionException("down", should_retry=True)
    PageSource.items = [item("a", 1)]
    report = await worker.run_cycle()
    assert bot.texts_to(-1) == []
    assert report.translation.stopped == "unavailable"
    assert "DeepL недоступен" in bot.texts_to(1001)[0]

    worker.services.translator.error = None
    await worker.run_cycle()
    assert bot.texts_to(-1)[0].startswith("<b>RU a")


async def test_step_failure_does_not_stop_cycle(worker, db, bot, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.worker.poll_all", broken)
    report = await worker.run_cycle()
    assert report.errors == ["опрос: boom"]
    assert report.publishing is not None
    assert "⚠️ опрос: boom" in report.summary()


async def wait_until(condition, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def test_run_forever_reports_to_requester_and_waits_for_trigger(worker, bot):
    worker.services.settings.poll_interval_min = 60
    worker.services.trigger.request(1001)
    task = asyncio.create_task(worker.run_forever())
    try:
        await wait_until(lambda: len(bot.texts_to(1001)) == 1)
        assert bot.texts_to(1001)[0].startswith("Опрос завершён.")
        # Without a trigger the loop sleeps for the interval...
        await asyncio.sleep(0.1)
        assert len(bot.texts_to(1001)) == 1
        # ...and /run wakes it up early.
        worker.services.trigger.request(1001)
        await wait_until(lambda: len(bot.texts_to(1001)) == 2)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
