from datetime import UTC, date, datetime

import httpx
import pytest

from app.poller import freshest, oldest_first, poll_all, poll_source
from app.sources import NewsItem, Source, SourceError

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


class FakeSource(Source):
    key = "fake"
    items: list[NewsItem] = []
    error: str | None = None

    async def fetch(self) -> list[NewsItem]:
        if FakeSource.error:
            raise SourceError(FakeSource.error)
        return list(FakeSource.items)


def item(slug: str, day: int) -> NewsItem:
    return NewsItem(url=f"https://ex.org/{slug}", title=slug, published_date=date(2026, 10, day))


@pytest.fixture
async def source(db):
    FakeSource.items, FakeSource.error = [], None
    return await db.sources.ensure("fake", "Fake", "https://ex.org")


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


async def poll(db, client):
    row = await db.sources.get_by_key("fake")
    return await poll_source(db, row, client, NOW)


def test_freshest_prefers_date_then_page_order():
    a, b, c = item("a", 5), item("b", 5), item("c", 3)
    assert freshest([c, a, b]) is a
    assert oldest_first([a, b, c]) == [c, b, a]


async def test_first_run_marks_all_but_freshest_skipped(db, source, client):
    FakeSource.items = [item("new1", 8), item("new2", 8), item("old", 2)]
    result = await poll(db, client)
    assert (result.found, result.new) == (3, 1)

    [pending] = await db.news.by_status("pending_translation")
    assert pending.title_de == "new1"
    assert len(await db.news.by_status("skipped_initial")) == 2
    row = await db.sources.get(source.id)
    assert row.initialized and row.last_new_item_at == NOW


async def test_first_run_with_empty_page_does_not_initialize(db, source, client):
    await poll(db, client)
    row = await db.sources.get(source.id)
    assert not row.initialized
    assert row.consecutive_empty_polls == 1


async def test_new_items_after_init_are_queued_oldest_first(db, source, client):
    FakeSource.items = [item("a", 1)]
    await poll(db, client)
    FakeSource.items = [item("c", 3), item("b", 2), item("a", 1)]
    result = await poll(db, client)
    assert result.new == 2
    titles = [n.title_de for n in await db.news.by_status("pending_translation")]
    assert titles == ["a", "b", "c"]


async def test_repeat_poll_creates_no_duplicates(db, source, client):
    FakeSource.items = [item("a", 1), item("b", 2)]
    await poll(db, client)
    result = await poll(db, client)
    assert result.new == 0
    counts = await db.news.count_by_status()
    assert sum(counts.values()) == 2


async def test_errors_track_streak_and_reset_on_success(db, source, client):
    FakeSource.error = "HTTP 503"
    first = await poll(db, client)
    assert first.error == "HTTP 503"
    row = await db.sources.get(source.id)
    assert row.first_error_at == NOW and row.last_error == "HTTP 503"

    await db.sources.update(source.id, down_notified=True)
    FakeSource.error = None
    FakeSource.items = [item("a", 1)]
    await poll(db, client)
    row = await db.sources.get(source.id)
    assert row.first_error_at is None and row.down_notified is False


async def test_empty_polls_counted_and_reset(db, source, client):
    FakeSource.items = [item("a", 1)]
    await poll(db, client)
    FakeSource.items = []
    await poll(db, client)
    await poll(db, client)
    assert (await db.sources.get(source.id)).consecutive_empty_polls == 2
    FakeSource.items = [item("a", 1)]
    await poll(db, client)
    assert (await db.sources.get(source.id)).consecutive_empty_polls == 0


async def test_new_item_resets_stale_flag(db, source, client):
    FakeSource.items = [item("a", 1)]
    await poll(db, client)
    await db.sources.update(source.id, stale_notified=True)
    FakeSource.items = [item("b", 2), item("a", 1)]
    await poll(db, client)
    assert (await db.sources.get(source.id)).stale_notified is False


async def test_poll_all_skips_disabled(db, source, client):
    await db.sources.update(source.id, enabled=False)
    assert await poll_all(db, client, NOW) == []


async def test_unknown_source_key_is_recorded_as_error(db, client):
    row = await db.sources.ensure("missing", "Missing", "https://ex.org")
    result = await poll_source(db, row, client, NOW)
    assert result.error
    assert (await db.sources.get(row.id)).first_error_at == NOW
