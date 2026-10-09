import re
from datetime import date, datetime
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.sources.base import NewsItem, Source

_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
_MORE_RE = re.compile(r"\s*mehr erfahren\s*$", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _parse_date_line(text: str) -> tuple[date | None, str | None]:
    """'08.10.2026 | Pressemitteilung' -> (date, label)."""
    published = None
    if match := _DATE_RE.search(text):
        published = datetime.strptime(match.group(1), "%d.%m.%Y").date()
    _, sep, label = text.partition("|")
    return published, _clean(label) if sep else None


def _parse_item(node: LexborNode, base_url: str) -> NewsItem | None:
    link = node.css_first("a.news-list-item-link") or node.css_first("a[href]")
    if link is None or not link.attributes.get("href"):
        return None
    url = urljoin(base_url, link.attributes["href"])

    content = node.css_first(".news-list-item-content") or node
    title = _clean(link.attributes.get("title"))
    if not title:
        heading = content.css_first(".news-list-item-title")
        title = _clean(heading.text()) if heading else None
    if not title:
        return None

    published, label = None, None
    if date_node := content.css_first(".news-list-item-date"):
        published, label = _parse_date_line(date_node.text())

    subtitle_node = content.css_first(".news-list-item-teaser")
    subtitle = _clean(subtitle_node.text()) if subtitle_node else None

    teaser = None
    for paragraph in content.css("p"):
        classes = paragraph.attributes.get("class") or ""
        if "news-list-item-date" in classes or "news-list-item-teaser" in classes:
            continue
        text = _clean(paragraph.text())
        if text:
            teaser = _clean(_MORE_RE.sub("", text))
            break

    return NewsItem(
        url=url,
        title=title,
        published_date=published,
        label=label,
        subtitle=subtitle,
        teaser=teaser,
    )


def parse_listing(html: str, base_url: str) -> list[NewsItem]:
    tree = LexborHTMLParser(html)
    items: list[NewsItem] = []
    seen: set[str] = set()
    for node in tree.css("div.news-list-item"):
        item = _parse_item(node, base_url)
        if item and item.url not in seen:
            seen.add(item.url)
            items.append(item)
    return items


class FreibergNeuigkeiten(Source):
    key = "freiberg_neuigkeiten"

    async def fetch(self) -> list[NewsItem]:
        return parse_listing(await self.get_html(), self.url)
