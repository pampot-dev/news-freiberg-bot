from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage

from app.publish.publisher import PAUSED_KEY, Publisher, in_quiet_hours
from app.sources import NewsItem

BERLIN = ZoneInfo("Europe/Berlin")
DAY = datetime(2026, 10, 9, 12, 0, tzinfo=BERLIN)  # 12:00 local
NIGHT = datetime(2026, 10, 9, 23, 30, tzinfo=BERLIN)
METHOD = SendMessage(chat_id=1, text="x")


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(21, False), (22, True), (23, True), (0, True), (6, True), (7, False), (12, False)],
)
def test_quiet_hours_across_midnight(hour, expected):
    now = datetime(2026, 10, 9, hour, 30, tzinfo=BERLIN).astimezone(UTC)
    assert in_quiet_hours(now, BERLIN, (22, 7)) is expected


def test_quiet_hours_same_day_and_disabled():
    assert in_quiet_hours(datetime(2026, 1, 1, 13, tzinfo=BERLIN), BERLIN, (12, 14))
    assert not in_quiet_hours(datetime(2026, 1, 1, 23, tzinfo=BERLIN), BERLIN, None)


class Sleeps:
    def __init__(self):
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)


@pytest.fixture
def sleeps():
    return Sleeps()


@pytest.fixture
def publisher(db, bot, notifier, settings, sleeps):
    return Publisher(db, bot, notifier, settings, sleep=sleeps)


async def add_ready(db, *slugs, day=1):
    src = await db.sources.ensure("s", "S", "u")
    ids = []
    for slug in slugs:
        ids.append(
            await db.news.insert(
                src.id,
                NewsItem(
                    url=f"https://ex.org/{slug}", title=slug, published_date=date(2026, 10, day)
                ),
                "pending_translation",
            )
        )
    await db.news.commit()
    for item_id, slug in zip(ids, slugs, strict=True):
        await db.news.set_translation(item_id, f"RU {slug}", f"text {slug}")
    return ids


async def test_sends_to_all_active_recipients(db, bot, publisher):
    [item_id] = await add_ready(db, "a")
    await db.recipients.add(-1, None, "one")
    await db.recipients.add(-2, 5, "topic")
    inactive = await db.recipients.add(-3, None, "off")
    await db.recipients.set_active(inactive.id, False)

    result = await publisher.run(DAY)
    assert (result.sent, result.published) == (2, 1)
    assert [cid for cid, _, _ in bot.sent] == [-1, -2]
    _, text, kwargs = bot.sent[1]
    assert text.startswith("<b>RU a</b>\n<i>a</i>")
    assert kwargs["message_thread_id"] == 5
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["link_preview_options"].is_disabled is True
    assert (await db.news.get(item_id)).status == "published"


async def test_nothing_published_at_night_then_backlog_in_order(db, bot, publisher, settings):
    settings.batch_limit = 2
    await add_ready(db, "old", day=1)
    await add_ready(db, "mid", "new", day=2)
    await db.recipients.add(-1, None, "one")

    result = await publisher.run(NIGHT)
    assert result.skipped == "quiet_hours" and bot.sent == []

    morning = datetime(2026, 10, 10, 7, 0, tzinfo=BERLIN)
    result = await publisher.run(morning)
    assert result.sent == 2
    assert [t.split("</b>")[0] for t in bot.texts_to(-1)] == ["<b>RU old", "<b>RU mid"]

    await publisher.run(morning)
    assert bot.texts_to(-1)[-1].startswith("<b>RU new")
    assert len(bot.texts_to(-1)) == 3


async def test_pause_between_posts_to_same_chat(db, publisher, sleeps):
    await add_ready(db, "a", "b", "c")
    await db.recipients.add(-1, None, "one")
    await db.recipients.add(-2, None, "two")
    await publisher.run(DAY)
    assert sleeps.calls == [3.0, 3.0]  # one pause per round, not per message


async def test_paused_publishes_nothing(db, bot, publisher):
    await add_ready(db, "a")
    await db.recipients.add(-1, None, "one")
    await db.kv.set_bool(PAUSED_KEY, True)
    assert (await publisher.run(DAY)).skipped == "paused"
    assert bot.sent == []


async def test_waits_for_first_recipient(db, bot, publisher):
    [item_id] = await add_ready(db, "a")
    assert (await publisher.run(DAY)).skipped == "no_recipients"
    assert (await db.news.get(item_id)).deliveries_created_at is None
    await db.recipients.add(-1, None, "one")
    assert (await publisher.run(DAY)).sent == 1


async def test_recipient_added_later_does_not_get_old_items(db, bot, publisher):
    await add_ready(db, "a")
    await db.recipients.add(-1, None, "one")
    await publisher.run(DAY)
    await db.recipients.add(-2, None, "late")
    await publisher.run(DAY)
    assert bot.texts_to(-2) == []


async def test_no_duplicates_on_rerun(db, bot, publisher):
    await add_ready(db, "a", "b")
    await db.recipients.add(-1, None, "one")
    await publisher.run(DAY)
    await publisher.run(DAY)
    assert len(bot.sent) == 2


async def test_forbidden_deactivates_and_others_continue(db, bot, publisher):
    [item_id] = await add_ready(db, "a")
    kicked = await db.recipients.add(-1, None, "kicked")
    await db.recipients.add(-2, None, "ok")
    bot.errors[-1] = TelegramForbiddenError(METHOD, "Forbidden: bot was kicked")

    result = await publisher.run(DAY)
    assert result.deactivated == [kicked.id]
    assert bot.texts_to(-2)
    assert not (await db.recipients.get(kicked.id)).active
    assert "отключён" in bot.texts_to(1001)[0]
    assert (await db.news.get(item_id)).status == "published"


async def test_other_errors_retry_three_times_then_notify(db, bot, publisher):
    [item_id] = await add_ready(db, "a")
    await db.recipients.add(-1, None, "one")
    bot.errors[-1] = TelegramBadRequest(METHOD, "Bad Request: chat not found")

    for _ in range(2):
        await publisher.run(DAY)
        assert bot.texts_to(1001) == []
        assert (await db.news.get(item_id)).status == "ready"
    await publisher.run(DAY)
    assert "после 3 попыток" in bot.texts_to(1001)[0]
    assert (await db.news.get(item_id)).status == "published"


async def test_failure_keeps_order_for_that_chat(db, bot, publisher):
    await add_ready(db, "a", "b")
    await db.recipients.add(-1, None, "one")
    bot.errors[-1] = TelegramBadRequest(METHOD, "Bad Request: temporary")
    await publisher.run(DAY)
    del bot.errors[-1]
    await publisher.run(DAY)
    assert [t.split("</b>")[0] for t in bot.texts_to(-1)] == ["<b>RU a", "<b>RU b"]


async def test_retry_after_is_respected(db, bot, publisher, sleeps):
    await add_ready(db, "a")
    await db.recipients.add(-1, None, "one")
    original = bot.send_message
    calls = {"n": 0}

    async def flaky(chat_id, text, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TelegramRetryAfter(METHOD, "Too Many Requests", retry_after=17)
        return await original(chat_id, text, **kwargs)

    bot.send_message = flaky
    result = await publisher.run(DAY)
    assert sleeps.calls == [17]
    assert result.sent == 1


async def test_group_migration_updates_chat_id(db, bot, publisher):
    await add_ready(db, "a")
    rec = await db.recipients.add(-1, None, "group")
    bot.errors[-1] = TelegramMigrateToChat(METHOD, "migrated", migrate_to_chat_id=-100500)
    result = await publisher.run(DAY)
    assert result.sent == 1
    assert bot.texts_to(-100500)
    assert (await db.recipients.get(rec.id)).chat_id == -100500
