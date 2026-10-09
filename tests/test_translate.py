from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import deepl
import pytest

from app.sources import NewsItem
from app.translate import DeeplTranslator, Glossary, translate_pending
from app.translate.deepl_client import GLOSSARY_PREFIX

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


class FakeTranslator:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def translate(self, title, text):
        self.calls.append((title, text))
        if self.error:
            raise self.error
        return f"RU:{title}", f"RU:{text}"

    async def usage(self):
        return None


async def queue(db, *slugs):
    src = await db.sources.ensure("s", "S", "u")
    for slug in slugs:
        await db.news.insert(
            src.id,
            NewsItem(
                url=f"https://ex.org/{slug}",
                title=slug,
                published_date=date(2026, 10, 1),
                subtitle="Sub",
                teaser="Teaser…",
            ),
            "pending_translation",
        )
    await db.news.commit()


async def test_translates_and_stores(db, notifier, bot):
    await queue(db, "a", "b")
    translator = FakeTranslator()
    result = await translate_pending(db, translator, notifier, NOW)
    assert result.translated == 2
    assert translator.calls[0] == ("a", "Sub\n\nTeaser…")
    [first, _] = await db.news.by_status("ready")
    assert (first.title_ru, first.text_ru) == ("RU:a", "RU:Sub\n\nTeaser…")
    assert bot.sent == []

    # Already translated items are not sent to DeepL again.
    await translate_pending(db, translator, notifier, NOW)
    assert len(translator.calls) == 2


async def test_quota_keeps_items_pending_and_notifies_once_a_day(db, notifier, bot):
    await queue(db, "a", "b")
    translator = FakeTranslator(deepl.QuotaExceededException("quota", http_status_code=456))
    result = await translate_pending(db, translator, notifier, NOW)
    assert result.stopped == "quota"
    assert len(translator.calls) == 1  # stops after the first quota error
    assert len(await db.news.by_status("pending_translation")) == 2
    assert len(bot.texts_to(1001)) == 1

    await translate_pending(db, translator, notifier, NOW + timedelta(hours=5))
    assert len(bot.texts_to(1001)) == 1
    await translate_pending(db, translator, notifier, NOW + timedelta(days=1, minutes=1))
    assert len(bot.texts_to(1001)) == 2


async def test_transient_error_does_not_count_attempts(db, notifier, bot):
    await queue(db, "a")
    translator = FakeTranslator(deepl.DeepLException("503", should_retry=True))
    result = await translate_pending(db, translator, notifier, NOW)
    assert result.stopped == "unavailable"
    [item] = await db.news.by_status("pending_translation")
    assert item.translate_attempts == 0
    assert "DeepL недоступен" in bot.texts_to(1001)[0]


async def test_permanent_error_fails_item_after_attempts(db, notifier, bot):
    await queue(db, "a", "b")
    translator = FakeTranslator(deepl.DeepLException("Bad request"))
    for _ in range(3):
        await translate_pending(db, translator, notifier, NOW)
    assert len(await db.news.by_status("failed")) == 2
    assert len(bot.texts_to(1001)) == 2


def test_glossary_post_process(tmp_path):
    path = tmp_path / "g.yaml"
    path.write_text(
        "terms:\n  Kornhaus: Корнхаус\n  Oberbürgermeister: обер-бургомистр\n"
        "fixes:\n  Фрайбергский: фрайбергский\n",
        encoding="utf-8",
    )
    g = Glossary.load(path)
    text = "В Kornhaus выступил Oberbürgermeister. Фрайбергский музей, Kornhausplatz."
    assert g.post_process(text, replace_terms=True) == (
        "В Корнхаус выступил обер-бургомистр. фрайбергский музей, Kornhausplatz."
    )
    assert g.post_process(text, replace_terms=False).startswith("В Kornhaus")


def test_glossary_missing_file(tmp_path):
    assert Glossary.load(tmp_path / "none.yaml").terms == {}


def test_repo_glossary_is_valid():
    assert Glossary.load(Path(__file__).parent.parent / "glossary.yaml").terms


class FakeDeeplClient:
    def __init__(self, pairs, glossaries=()):
        self.pairs = pairs
        self.glossaries = list(glossaries)
        self.created, self.deleted = [], []
        self.translate_kwargs = None

    def get_glossary_languages(self):
        return [SimpleNamespace(source_lang=s, target_lang=t) for s, t in self.pairs]

    def list_glossaries(self):
        return self.glossaries

    def delete_glossary(self, glossary):
        self.deleted.append(glossary.name)

    def create_glossary(self, name, source, target, entries):
        self.created.append(name)
        return SimpleNamespace(name=name, glossary_id="gid-new")

    def translate_text(self, texts, **kwargs):
        self.translate_kwargs = kwargs
        return [SimpleNamespace(text=f"{t} Kornhaus") for t in texts]


def make_translator(client):
    translator = DeeplTranslator("key:fx", Glossary({"Kornhaus": "Корнхаус"}))
    translator.client = client
    return translator


async def test_deepl_glossary_created_and_old_ones_removed():
    old = SimpleNamespace(name=f"{GLOSSARY_PREFIX}-old", glossary_id="gid-old")
    client = FakeDeeplClient([("de", "ru")], [old])
    translator = make_translator(client)
    title, text = await translator.translate("T", "X")
    assert client.created == [f"{GLOSSARY_PREFIX}-{translator.glossary.digest}"]
    assert client.deleted == [old.name]
    assert client.translate_kwargs["glossary"] == "gid-new"
    # With a DeepL glossary the terms are not post-replaced.
    assert (title, text) == ("T Kornhaus", "X Kornhaus")


async def test_deepl_glossary_reused_when_current():
    client = FakeDeeplClient([("de", "ru")])
    translator = make_translator(client)
    name = f"{GLOSSARY_PREFIX}-{translator.glossary.digest}"
    client.glossaries = [SimpleNamespace(name=name, glossary_id="gid-cur")]
    await translator.translate("T", "")
    assert client.created == [] and translator.glossary_id == "gid-cur"


async def test_unsupported_pair_falls_back_to_post_replacement():
    client = FakeDeeplClient([("de", "en-us")])
    translator = make_translator(client)
    title, text = await translator.translate("T", "")
    assert translator.glossary_id is None
    assert (title, text) == ("T Корнхаус", "")


@pytest.mark.parametrize(
    ("exc", "prepared"),
    [(deepl.DeepLException("503", should_retry=True), False), (deepl.DeepLException("x"), True)],
)
async def test_glossary_setup_error(exc, prepared):
    client = FakeDeeplClient([("de", "ru")])

    def broken():
        raise exc

    client.get_glossary_languages = broken
    translator = make_translator(client)
    await translator.prepare()
    assert translator.glossary_id is None
    assert translator._prepared is prepared
