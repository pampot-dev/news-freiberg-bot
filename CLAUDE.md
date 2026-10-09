# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

`SPEC.md` (in Russian) is the authoritative requirements document; read it before making design decisions. User-facing bot text and admin messages must be in Russian. Implementation goes in stages (skeleton → parser → DB → polling → translation → formatter → publisher → monitoring → handlers → main wiring → Docker), one commit per stage.

## Repository rules (SPEC §16)

- Never credit Claude: no `Co-Authored-By: Claude` trailer in commits, no "Generated with Claude Code" in PR descriptions.
- Commit messages in English, short, imperative mood (e.g. `Add Freiberg news parser`).
- One commit = one logical piece of work (one plan stage).

## Decisions beyond the spec (agreed with the user)

- Delivery rows are created when an item becomes publishable, only for recipients active at that moment; recipients added later don't receive old items.
- The first-run "freshest item" also respects quiet hours.
- `news_items.status = failed` only after repeated non-quota translation failures; delivery failures are tracked per recipient and don't change item status.
- `sources.first_error_at` (start of the current error streak) is added to support the ">24h unavailable" alert.
- Admin UI is an inline-keyboard menu (plus the slash commands from SPEC §10).
- DB: aiosqlite with plain SQL migrations versioned via `PRAGMA user_version`. Scheduling: a plain asyncio loop woken by interval or an `asyncio.Event` (`/run`).

## What the bot does

A Telegram bot that polls news listing pages (first source: freiberg.de Neuigkeiten), translates DE→RU with DeepL, and auto-publishes to multiple Telegram channels/groups.

Pipeline: `sources` (fetch/parse HTML) → `news_items` (dedupe by article URL) → translate (status `pending_translation` → `ready`) → publish to every active recipient (one `deliveries` row per item×recipient) → `published`.

## Planned stack

Python 3.12+, aiogram 3 (long polling, no webhook), httpx, selectolax/BeautifulSoup, official `deepl` lib, SQLite (aiosqlite or SQLAlchemy 2 async), APScheduler or asyncio tasks, pydantic-settings, stdlib `logging` to stdout. Deployed via Docker / docker-compose on AWS EC2 with the DB file in a volume.

Commands:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # setup
.venv/bin/pytest -q                                          # all tests
.venv/bin/pytest tests/test_config.py::test_quiet_hours_parsed   # single test
.venv/bin/ruff check . && .venv/bin/ruff format --check .    # lint
docker compose up -d                                         # production run
```

`pytest` runs in `asyncio_mode = "auto"`, so async tests need no marker. Build `Settings` in tests with `Settings(_env_file=None, ...)` so a local `.env` doesn't leak in. Target layout is in SPEC.md §13 (`app/sources`, `app/translate`, `app/publish`, `app/handlers`, `app/db`, `app/monitor.py`).

## Invariants that span modules

- **Sources are pluggable**: each source is a class implementing `fetch() -> list[NewsItem]` plus a row in the `sources` table. Adding one must not require changes to the core.
- **freiberg.de parsing** (no RSS; TYPO3 `?type=9818` returns HTML): first page only; title comes from the link's `title` attribute (visible text contains photo credits/dates); strip the trailing `mehr erfahren` from teasers but keep the `…`. Parser tests run against a saved real page in `tests/fixtures/freiberg_neuigkeiten.html`. One request per poll, descriptive `User-Agent` with contact.
- **Never publish untranslated German.** On DeepL quota exhaustion (HTTP 456) items remain `pending_translation` and admins are notified at most once per day. 429/5xx/network errors use exponential backoff, then retry on the next cycle. Translations are stored and never redone. Glossary: use a DeepL glossary if DE→RU is supported, otherwise post-replace from `glossary.yaml`.
- **No duplicate deliveries**: `deliveries` has a unique key on `(news_item_id, recipient_id)`, and per-recipient status ensures restarts or partial failures don't resend. On 403 → mark recipient inactive and notify admin. On 429 → honor `retry_after`. Other errors → retry next cycle, at most 3 attempts, then notify. Keep to about 20 msgs/min per chat.
- **Post format**: HTML parse mode with every interpolated string escaped, link preview disabled, truncate with `…` to stay ≤4096 chars (see SPEC §5 template).
- **Scheduling**: poll every 30 min. During quiet hours (22–07 Europe/Berlin) polling and translation continue but publishing pauses. The backlog publishes oldest-first, with at most N (default 5) posts per chat per cycle. `/pause` stops publishing only, not polling.
- **First run of a source**: mark all found items `skipped_initial` except the freshest one (by date, ties broken by first in list), which gets published.
- **Monitoring** (DMs to all `ADMIN_IDS`): no new items for `STALE_DAYS` triggers one alert, re-armed only after a new item arrives. 0 parsed items for 3 consecutive polls triggers an immediate alert. Source HTTP errors lasting >24h trigger an alert. On `my_chat_member` (bot added to a chat), send admins the chat title and `chat_id`.
- **Admin commands** (SPEC §10) only work for `ADMIN_IDS` and only in private chat.

## Configuration

`.env` keys: `BOT_TOKEN`, `DEEPL_API_KEY`, `ADMIN_IDS`, `POLL_INTERVAL_MIN`, `QUIET_HOURS`, `TIMEZONE`, `STALE_DAYS`, `DB_PATH`. Ship a `.env.example`.

## Out of scope for v1

Full article text, images, hashtags, LLM summaries, a web admin UI, sources other than freiberg.de, and editing already-published posts.
