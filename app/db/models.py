from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from typing import Self


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_db(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


def _from_db(name: str, value: object) -> object:
    if value is None:
        return None
    if name.endswith("_at"):
        return datetime.fromisoformat(str(value))
    if name == "published_date":
        return date.fromisoformat(str(value))
    return value


class Row:
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        keys = set(row.keys())
        values = {f.name: _from_db(f.name, row[f.name]) for f in fields(cls) if f.name in keys}
        return cls(**values)


@dataclass(slots=True)
class SourceRow(Row):
    id: int
    key: str
    name: str
    url: str
    enabled: bool
    initialized: bool
    last_poll_at: datetime | None
    last_new_item_at: datetime | None
    consecutive_empty_polls: int
    empty_notified: bool
    first_error_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    down_notified: bool
    stale_notified: bool

    def __post_init__(self) -> None:
        for name in ("enabled", "initialized", "empty_notified", "down_notified", "stale_notified"):
            setattr(self, name, bool(getattr(self, name)))


@dataclass(slots=True)
class NewsRow(Row):
    id: int
    source_id: int
    url: str
    published_date: date | None
    label: str | None
    title_de: str
    subtitle_de: str | None
    teaser_de: str | None
    title_ru: str | None
    text_ru: str | None
    status: str
    translate_attempts: int
    translate_error: str | None
    deliveries_created_at: datetime | None
    created_at: datetime
    published_at: datetime | None

    @property
    def text_de(self) -> str:
        """Subtitle and teaser joined: the unit that is translated as one text."""
        return "\n\n".join(part for part in (self.subtitle_de, self.teaser_de) if part)


@dataclass(slots=True)
class Recipient(Row):
    id: int
    chat_id: int
    thread_id: int | None
    title: str | None
    active: bool
    added_at: datetime

    def __post_init__(self) -> None:
        self.active = bool(self.active)

    @property
    def display_name(self) -> str:
        name = self.title or str(self.chat_id)
        return f"{name} (тема {self.thread_id})" if self.thread_id else name


@dataclass(slots=True)
class Delivery(Row):
    id: int
    news_item_id: int
    recipient_id: int
    status: str
    message_id: int | None
    attempts: int
    error: str | None
    sent_at: datetime | None
