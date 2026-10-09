from datetime import UTC, datetime

import pytest

from app.db import NewsRow
from app.publish.formatter import TELEGRAM_LIMIT, _length, format_item, format_post

URL = "https://www.freiberg.de/stadt-und-buerger/aktuelles/neuigkeiten/a?x=1&y=2"


def test_layout_matches_spec():
    post = format_post("Заголовок", "Titel", "Текст…", URL)
    assert post == (
        "<b>Заголовок</b>\n<i>Titel</i>\n\nТекст…\n\n"
        '<a href="https://www.freiberg.de/stadt-und-buerger/aktuelles/neuigkeiten/a?x=1&amp;y=2">'
        "Читать оригинал</a>"
    )


def test_everything_is_escaped():
    post = format_post("<b>&", 'Ti"tel <x>', "a < b & c > d", URL)
    assert "<b>&lt;b&gt;&amp;</b>" in post
    assert "<i>Ti&quot;tel &lt;x&gt;</i>" in post
    assert "a &lt; b &amp; c &gt; d" in post


def test_empty_text_has_no_blank_body():
    post = format_post("З", "T", "", URL)
    assert (
        post == f'<b>З</b>\n<i>T</i>\n\n<a href="{URL.replace("&", "&amp;")}">Читать оригинал</a>'
    )


def test_long_text_is_truncated_with_ellipsis():
    text = "слово " * 2000
    post = format_post("Заголовок", "Titel", text, URL)
    assert _length(post) <= TELEGRAM_LIMIT
    assert _length(post) > TELEGRAM_LIMIT - 50
    body = post.split("\n\n")[1]
    assert body.endswith("…")
    assert "слово" in body


def test_truncation_accounts_for_escaping():
    post = format_post("З", "T", "&" * 5000, URL)
    assert _length(post) <= TELEGRAM_LIMIT
    assert "&amp;…" in post


def test_truncation_counts_utf16_units():
    post = format_post("З", "T", "😀" * 3000, URL)
    assert _length(post) <= TELEGRAM_LIMIT
    assert len(post) < TELEGRAM_LIMIT


@pytest.mark.parametrize("limit", [200, 500])
def test_pathological_titles(limit):
    post = format_post("Я" * 1000, "T" * 1000, "текст", URL, limit=limit)
    assert _length(post) <= limit
    assert post.endswith("Читать оригинал</a>")


def test_format_item_requires_translation():
    row = NewsRow(
        id=1,
        source_id=1,
        url=URL,
        published_date=None,
        label=None,
        title_de="T",
        subtitle_de=None,
        teaser_de=None,
        title_ru=None,
        text_ru=None,
        status="pending_translation",
        translate_attempts=0,
        translate_error=None,
        deliveries_created_at=None,
        created_at=datetime.now(UTC),
        published_at=None,
    )
    with pytest.raises(ValueError):
        format_item(row)
