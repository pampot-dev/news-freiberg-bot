"""Schema migrations. Each entry upgrades the DB by one version (tracked in PRAGMA user_version).

Append new migrations to the end; never edit an applied one.
"""

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE sources (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        initialized INTEGER NOT NULL DEFAULT 0,
        last_poll_at TEXT,
        last_new_item_at TEXT,
        consecutive_empty_polls INTEGER NOT NULL DEFAULT 0,
        empty_notified INTEGER NOT NULL DEFAULT 0,
        first_error_at TEXT,
        last_error_at TEXT,
        last_error TEXT,
        down_notified INTEGER NOT NULL DEFAULT 0,
        stale_notified INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE news_items (
        id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES sources(id),
        url TEXT NOT NULL UNIQUE,
        published_date TEXT,
        label TEXT,
        title_de TEXT NOT NULL,
        subtitle_de TEXT,
        teaser_de TEXT,
        title_ru TEXT,
        text_ru TEXT,
        status TEXT NOT NULL CHECK (status IN
            ('skipped_initial', 'pending_translation', 'ready', 'published', 'failed')),
        translate_attempts INTEGER NOT NULL DEFAULT 0,
        translate_error TEXT,
        deliveries_created_at TEXT,
        created_at TEXT NOT NULL,
        published_at TEXT
    );
    CREATE INDEX news_items_status ON news_items(status);

    CREATE TABLE recipients (
        id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        thread_id INTEGER,
        title TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        added_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX recipients_chat_thread ON recipients(chat_id, COALESCE(thread_id, 0));

    CREATE TABLE deliveries (
        id INTEGER PRIMARY KEY,
        news_item_id INTEGER NOT NULL REFERENCES news_items(id) ON DELETE CASCADE,
        recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
        message_id INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        sent_at TEXT,
        UNIQUE (news_item_id, recipient_id)
    );

    CREATE TABLE kv (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
]
