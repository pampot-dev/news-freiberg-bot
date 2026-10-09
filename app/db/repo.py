from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

import aiosqlite

from app.db.migrations import MIGRATIONS
from app.db.models import Delivery, NewsRow, Recipient, SourceRow, to_db, utcnow
from app.sources.base import NewsItem

# Oldest first. Items are inserted oldest-first, so id breaks ties within a day.
CHRONO = "published_date IS NULL, published_date, id"

_SOURCE_COLUMNS = {
    "enabled",
    "initialized",
    "last_poll_at",
    "last_new_item_at",
    "consecutive_empty_polls",
    "empty_notified",
    "first_error_at",
    "last_error_at",
    "last_error",
    "down_notified",
    "stale_notified",
}


class Database:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn
        self.sources = SourcesRepo(conn)
        self.news = NewsRepo(conn)
        self.recipients = RecipientsRepo(conn)
        self.deliveries = DeliveriesRepo(conn)
        self.kv = KV(conn)

    @classmethod
    async def open(cls, path: Path | str) -> Database:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await migrate(conn)
        return cls(conn)

    async def close(self) -> None:
        await self.conn.close()


async def migrate(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA user_version") as cur:
        (version,) = await cur.fetchone()
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        await conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")


class _Repo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def _all(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        async with self.conn.execute(sql, [to_db(p) for p in params]) as cur:
            return list(await cur.fetchall())

    async def _one(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
        async with self.conn.execute(sql, [to_db(p) for p in params]) as cur:
            return await cur.fetchone()

    async def _exec(self, sql: str, params: Sequence[object] = ()) -> int:
        async with self.conn.execute(sql, [to_db(p) for p in params]) as cur:
            rowcount = cur.rowcount
        await self.conn.commit()
        return rowcount


class SourcesRepo(_Repo):
    async def ensure(self, key: str, name: str, url: str) -> SourceRow:
        """Create the source if missing; keeps state (enabled, counters) of an existing one."""
        await self._exec(
            "INSERT INTO sources (key, name, url) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET name = excluded.name, url = excluded.url",
            (key, name, url),
        )
        return await self.get_by_key(key)

    async def get(self, source_id: int) -> SourceRow | None:
        row = await self._one("SELECT * FROM sources WHERE id = ?", (source_id,))
        return SourceRow.from_row(row) if row else None

    async def get_by_key(self, key: str) -> SourceRow | None:
        row = await self._one("SELECT * FROM sources WHERE key = ?", (key,))
        return SourceRow.from_row(row) if row else None

    async def list(self, enabled_only: bool = False) -> list[SourceRow]:
        sql = (
            "SELECT * FROM sources"
            + (" WHERE enabled = 1" if enabled_only else "")
            + " ORDER BY id"
        )
        return [SourceRow.from_row(r) for r in await self._all(sql)]

    async def update(self, source_id: int, **values: object) -> None:
        unknown = set(values) - _SOURCE_COLUMNS
        if unknown:
            raise ValueError(f"unknown source columns: {unknown}")
        assignments = ", ".join(f"{name} = ?" for name in values)
        await self._exec(
            f"UPDATE sources SET {assignments} WHERE id = ?", (*values.values(), source_id)
        )


class NewsRepo(_Repo):
    async def known_urls(self, urls: Iterable[str]) -> set[str]:
        urls = list(urls)
        if not urls:
            return set()
        marks = ",".join("?" * len(urls))
        rows = await self._all(f"SELECT url FROM news_items WHERE url IN ({marks})", urls)
        return {r["url"] for r in rows}

    async def insert(
        self, source_id: int, item: NewsItem, status: str, now: datetime | None = None
    ) -> int | None:
        """Insert one item; returns its id, or None if the URL is already known."""
        async with self.conn.execute(
            "INSERT OR IGNORE INTO news_items (source_id, url, published_date, label, title_de,"
            " subtitle_de, teaser_de, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                to_db(v)
                for v in (
                    source_id,
                    item.url,
                    item.published_date,
                    item.label,
                    item.title,
                    item.subtitle,
                    item.teaser,
                    status,
                    now or utcnow(),
                )
            ],
        ) as cur:
            new_id = cur.lastrowid if cur.rowcount else None
        return new_id

    async def commit(self) -> None:
        await self.conn.commit()

    async def get(self, item_id: int) -> NewsRow | None:
        row = await self._one("SELECT * FROM news_items WHERE id = ?", (item_id,))
        return NewsRow.from_row(row) if row else None

    async def by_status(self, status: str, limit: int | None = None) -> list[NewsRow]:
        sql = f"SELECT * FROM news_items WHERE status = ? ORDER BY {CHRONO}"
        params: list[object] = [status]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [NewsRow.from_row(r) for r in await self._all(sql, params)]

    async def count_by_status(self) -> dict[str, int]:
        rows = await self._all("SELECT status, COUNT(*) AS n FROM news_items GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    async def latest(self, translated_only: bool = False) -> NewsRow | None:
        where = "WHERE title_ru IS NOT NULL" if translated_only else ""
        row = await self._one(
            f"SELECT * FROM news_items {where}"
            " ORDER BY published_date IS NULL, published_date DESC, id DESC LIMIT 1"
        )
        return NewsRow.from_row(row) if row else None

    async def set_status(self, item_id: int, status: str) -> None:
        await self._exec("UPDATE news_items SET status = ? WHERE id = ?", (status, item_id))

    async def set_translation(self, item_id: int, title_ru: str, text_ru: str) -> None:
        await self._exec(
            "UPDATE news_items SET title_ru = ?, text_ru = ?, translate_error = NULL,"
            " status = 'ready' WHERE id = ? AND status = 'pending_translation'",
            (title_ru, text_ru, item_id),
        )

    async def record_translate_failure(self, item_id: int, error: str, give_up: bool) -> None:
        await self._exec(
            "UPDATE news_items SET translate_attempts = translate_attempts + 1,"
            " translate_error = ?, status = CASE WHEN ? THEN 'failed' ELSE status END"
            " WHERE id = ?",
            (error, give_up, item_id),
        )

    async def without_deliveries(self) -> list[NewsRow]:
        rows = await self._all(
            "SELECT * FROM news_items WHERE status = 'ready' AND deliveries_created_at IS NULL"
            f" ORDER BY {CHRONO}"
        )
        return [NewsRow.from_row(r) for r in rows]

    async def create_deliveries(
        self, item_id: int, recipient_ids: Iterable[int], now: datetime | None = None
    ) -> None:
        """Snapshot the current recipients for an item; done once per item."""
        await self.conn.executemany(
            "INSERT OR IGNORE INTO deliveries (news_item_id, recipient_id) VALUES (?, ?)",
            [(item_id, rid) for rid in recipient_ids],
        )
        await self._exec(
            "UPDATE news_items SET deliveries_created_at = ? WHERE id = ?",
            (now or utcnow(), item_id),
        )

    async def finalize_published(self, now: datetime | None = None) -> int:
        """Mark ready items whose deliveries are all settled as published."""
        return await self._exec(
            "UPDATE news_items SET status = 'published', published_at = ?"
            " WHERE status = 'ready' AND deliveries_created_at IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM deliveries d"
            "   WHERE d.news_item_id = news_items.id AND d.status = 'pending')",
            (now or utcnow(),),
        )


class RecipientsRepo(_Repo):
    async def add(
        self, chat_id: int, thread_id: int | None, title: str | None, now: datetime | None = None
    ) -> Recipient:
        """Add a recipient, or reactivate and retitle an existing one."""
        existing = await self.get_by_chat(chat_id, thread_id)
        if existing:
            await self._exec(
                "UPDATE recipients SET active = 1, title = COALESCE(?, title) WHERE id = ?",
                (title, existing.id),
            )
            return await self.get(existing.id)
        await self._exec(
            "INSERT INTO recipients (chat_id, thread_id, title, added_at) VALUES (?, ?, ?, ?)",
            (chat_id, thread_id, title, now or utcnow()),
        )
        return await self.get_by_chat(chat_id, thread_id)

    async def get(self, recipient_id: int) -> Recipient | None:
        row = await self._one("SELECT * FROM recipients WHERE id = ?", (recipient_id,))
        return Recipient.from_row(row) if row else None

    async def get_by_chat(self, chat_id: int, thread_id: int | None) -> Recipient | None:
        row = await self._one(
            "SELECT * FROM recipients WHERE chat_id = ? AND COALESCE(thread_id, 0) = ?",
            (chat_id, thread_id or 0),
        )
        return Recipient.from_row(row) if row else None

    async def list(self, active_only: bool = False) -> list[Recipient]:
        sql = "SELECT * FROM recipients" + (" WHERE active = 1" if active_only else "")
        return [Recipient.from_row(r) for r in await self._all(sql + " ORDER BY id")]

    async def remove(self, recipient_id: int) -> bool:
        return await self._exec("DELETE FROM recipients WHERE id = ?", (recipient_id,)) > 0

    async def remove_chat(self, chat_id: int) -> int:
        return await self._exec("DELETE FROM recipients WHERE chat_id = ?", (chat_id,))

    async def set_active(self, recipient_id: int, active: bool) -> None:
        await self._exec("UPDATE recipients SET active = ? WHERE id = ?", (active, recipient_id))
        if not active:
            await self._exec(
                "UPDATE deliveries SET status = 'skipped', error = 'recipient inactive'"
                " WHERE recipient_id = ? AND status = 'pending'",
                (recipient_id,),
            )


class DeliveriesRepo(_Repo):
    async def pending_for(self, recipient_id: int, limit: int) -> list[tuple[Delivery, NewsRow]]:
        """Pending deliveries of ready items for one recipient, oldest item first."""
        rows = await self._all(
            "SELECT d.* FROM deliveries d JOIN news_items n ON n.id = d.news_item_id"
            " WHERE d.recipient_id = ? AND d.status = 'pending' AND n.status = 'ready'"
            " ORDER BY n.published_date IS NULL, n.published_date, n.id LIMIT ?",
            (recipient_id, limit),
        )
        news = NewsRepo(self.conn)
        result = []
        for row in rows:
            delivery = Delivery.from_row(row)
            result.append((delivery, await news.get(delivery.news_item_id)))
        return result

    async def get(self, delivery_id: int) -> Delivery | None:
        row = await self._one("SELECT * FROM deliveries WHERE id = ?", (delivery_id,))
        return Delivery.from_row(row) if row else None

    async def for_item(self, item_id: int) -> list[Delivery]:
        rows = await self._all("SELECT * FROM deliveries WHERE news_item_id = ?", (item_id,))
        return [Delivery.from_row(r) for r in rows]

    async def mark_sent(self, delivery_id: int, message_id: int, now: datetime | None = None):
        await self._exec(
            "UPDATE deliveries SET status = 'sent', message_id = ?, sent_at = ?,"
            " attempts = attempts + 1, error = NULL WHERE id = ?",
            (message_id, now or utcnow(), delivery_id),
        )

    async def mark_failed_attempt(self, delivery_id: int, error: str, max_attempts: int) -> int:
        """Count a failed attempt; gives up (status 'failed') at max_attempts. Returns attempts."""
        await self._exec(
            "UPDATE deliveries SET attempts = attempts + 1, error = ?,"
            " status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE status END WHERE id = ?",
            (error, max_attempts, delivery_id),
        )
        delivery = await self.get(delivery_id)
        return delivery.attempts

    async def pending_count(self) -> int:
        row = await self._one("SELECT COUNT(*) FROM deliveries WHERE status = 'pending'")
        return row[0]


class KV(_Repo):
    async def get(self, key: str, default: str | None = None) -> str | None:
        row = await self._one("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else default

    async def set(self, key: str, value: str | None) -> None:
        await self._exec(
            "INSERT INTO kv (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def get_bool(self, key: str) -> bool:
        return await self.get(key) == "1"

    async def set_bool(self, key: str, value: bool) -> None:
        await self.set(key, "1" if value else "0")

    async def get_datetime(self, key: str) -> datetime | None:
        value = await self.get(key)
        return datetime.fromisoformat(value) if value else None

    async def set_datetime(self, key: str, value: datetime) -> None:
        await self.set(key, str(to_db(value)))
