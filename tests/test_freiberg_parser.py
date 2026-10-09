from datetime import date
from pathlib import Path

import httpx
import pytest

from app.sources import SourceError, create_source
from app.sources.freiberg import FreibergNeuigkeiten, parse_listing

BASE = "https://www.freiberg.de/stadt-und-buerger/aktuelles/neuigkeiten"
FIXTURE = Path(__file__).parent / "fixtures" / "freiberg_neuigkeiten.html"


@pytest.fixture(scope="module")
def items():
    return parse_listing(FIXTURE.read_text(encoding="utf-8"), BASE)


def test_parses_all_items(items):
    assert len(items) == 10
    assert len({i.url for i in items}) == 10


def test_urls_are_absolute_article_links(items):
    for item in items:
        assert item.url.startswith(BASE + "/")
    assert items[0].url == f"{BASE}/stadtbibliothek-programm-im-oktober"


def test_first_item_fields(items):
    first = items[0]
    assert first.published_date == date(2026, 10, 8)
    assert first.label == "Pressemitteilung"
    assert first.title == "Stadtbibliothek: Programm im Oktober"
    assert first.subtitle == (
        "Festival, Ausstellung, Zine-Workshop und Ferienangebote laden ins Kornhaus ein"
    )
    assert first.teaser.startswith("Neben dem großen Lese- und Lauschfestival")
    assert first.teaser.endswith("ein Zine-Workshop und…")


def test_titles_are_clean(items):
    for item in items:
        assert item.title == item.title.strip()
        assert "  " not in item.title
        assert "Adobe Stock" not in item.title
        assert "Pressemitteilung" not in item.title
        assert not item.title[:2].isdigit()


def test_teasers_have_no_more_link_and_keep_ellipsis(items):
    for item in items:
        assert item.teaser
        assert "mehr erfahren" not in item.teaser
        assert item.teaser.endswith("…")


def test_missing_subtitle(items):
    by_slug = {i.url.rsplit("/", 1)[1]: i for i in items}
    assert by_slug["stadtrat-hat-entschieden-freiberg-bekommt-einen-amtsverweser"].subtitle is None
    assert by_slug["drei-termine-ein-festival-freiberg-feiert-ein-fest-der-stimmen"].subtitle


def test_dates_are_parsed(items):
    assert all(i.published_date for i in items)
    assert items[-1].published_date == date(2026, 10, 2)


SNIPPET = """
<div class="news-list-item">
  <a class="news-list-item-link" title="  Ein   Titel "
     href="/stadt-und-buerger/aktuelles/neuigkeiten/ein-titel">
    <span>Adobe Stock</span>
    <div class="news-list-item-content">
      <p class="news-list-item-date">05.09.2026</p>
      <h4 class="news-list-item-title">Ein Titel</h4>
      <p>Kurzer Text…
         mehr erfahren</p>
    </div>
  </a>
</div>
<div class="news-list-item">
  <a class="news-list-item-link" title="Ein Titel"
     href="/stadt-und-buerger/aktuelles/neuigkeiten/ein-titel"></a>
</div>
"""


def test_item_without_label_and_inline_more_link():
    [item] = parse_listing(SNIPPET, BASE)
    assert item.title == "Ein Titel"
    assert item.label is None
    assert item.subtitle is None
    assert item.published_date == date(2026, 9, 5)
    assert item.teaser == "Kurzer Text…"


def test_empty_page_yields_no_items():
    assert parse_listing("<html><body>Wartungsarbeiten</body></html>", BASE) == []


async def test_fetch_uses_http_client():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == BASE
        return httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = create_source("freiberg_neuigkeiten", BASE, client)
        assert isinstance(source, FreibergNeuigkeiten)
        assert len(await source.fetch()) == 10


async def test_fetch_http_error_raises_source_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceError):
            await FreibergNeuigkeiten(BASE, client).fetch()
