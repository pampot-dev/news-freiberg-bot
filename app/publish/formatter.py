from html import escape

from app.db import NewsRow

TELEGRAM_LIMIT = 4096
ELLIPSIS = "…"


def _length(text: str) -> int:
    """Telegram counts UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return ""
    cut = text[: max_chars - 1].rstrip()
    # Prefer a word boundary if it doesn't cost too much text.
    space = cut.rfind(" ")
    if space > max_chars * 0.8:
        cut = cut[:space]
    return cut.rstrip(" ,;:-–—") + ELLIPSIS


def _render(title_ru: str, title_de: str, text_ru: str, url: str) -> str:
    parts = [f"<b>{escape(title_ru)}</b>", f"<i>{escape(title_de)}</i>"]
    body = f"{parts[0]}\n{parts[1]}\n\n"
    if text_ru:
        body += f"{escape(text_ru)}\n\n"
    return body + f'<a href="{escape(url, quote=True)}">Читать оригинал</a>'


def _fit(render, text: str, limit: int) -> str | None:
    """Longest truncation of `text` for which render(text) fits; None if even "" doesn't."""
    if _length(render(text)) <= limit:
        return text
    if _length(render("")) > limit:
        return None
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _length(render(_truncate(text, mid))) <= limit:
            low = mid
        else:
            high = mid - 1
    return _truncate(text, low)


def format_post(
    title_ru: str, title_de: str, text_ru: str, url: str, limit: int = TELEGRAM_LIMIT
) -> str:
    """HTML post per SPEC §5, shortened with "…" to fit Telegram's message limit."""
    text = _fit(lambda t: _render(title_ru, title_de, t, url), text_ru, limit)
    if text is None:
        # Pathological titles: drop the body and shorten the titles too.
        half = (limit - _length(_render("", "", "", url))) // 4
        title_ru, title_de, text = _truncate(title_ru, half), _truncate(title_de, half), ""
    return _render(title_ru, title_de, text, url)


def format_item(item: NewsRow, limit: int = TELEGRAM_LIMIT) -> str:
    if item.title_ru is None:
        raise ValueError(f"item {item.id} is not translated")
    return format_post(item.title_ru, item.title_de, item.text_ru or "", item.url, limit)
