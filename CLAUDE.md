# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

`docs/SPEC.md` (in Russian) is the authoritative requirements document; read it before making design decisions. User-facing bot text and admin messages are in Russian; code, comments and commits are in English. v1 is implemented.

Docs come in two languages that must be kept in sync: `README.md` (English) and `README_RU.md` (Russian) are short entry points linking to `docs/en/` and `docs/ru/` (`setup.md`, `usage.md`, `development.md`). When behavior or configuration changes, update both language versions.

## Repository rules (SPEC §16)

- Never credit Claude: no `Co-Authored-By: Claude` trailer in commits, no "Generated with Claude Code" in PR descriptions.
- Commit messages in English, short, imperative mood (e.g. `Add Freiberg news parser`).
- One commit = one logical piece of work (one plan stage).

## Decisions beyond the spec (agreed with the user)

- Delivery rows are snapshotted once per item (`news_items.deliveries_created_at`) in the first publish cycle that has at least one active recipient, for the recipients active at that moment; recipients added later don't receive it. Until a recipient exists the item waits in `ready` (so the first-run item isn't lost before the admin adds chats). An item becomes `published` when it has no `pending` deliveries left.
- Items are inserted oldest-first, so `ORDER BY published_date, id` is chronological (see `CHRONO` in `app/db/repo.py`).
- The first-run "freshest item" also respects quiet hours.
- `news_items.status = failed` only after repeated non-quota translation failures; delivery failures are tracked per recipient and don't change item status.
- `sources.first_error_at` (start of the current error streak) is added to support the ">24h unavailable" alert.
- Admin UI is an inline-keyboard menu only (SPEC §10): `/start` and `/menu` open it, any other admin message does too; the Telegram command list contains just `/menu`. Adding a recipient by `chat_id [thread_id]` is an FSM prompt (`AddRecipient` in `app/handlers/admin.py`).
- DB: aiosqlite with plain SQL migrations versioned via `PRAGMA user_version`. Scheduling: a plain asyncio loop woken by interval or an `asyncio.Event` (the "Опросить сейчас" button).

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"      # setup
.venv/bin/pytest -q                                             # all tests
.venv/bin/pytest tests/test_publisher.py::test_no_duplicates_on_rerun   # single test
.venv/bin/ruff check . && .venv/bin/ruff format --check .       # lint (line length 100)
.venv/bin/python -m app.main                                    # run locally (needs .env)
docker compose up -d                                            # production
```

`pytest` runs with `asyncio_mode = "auto"`, so async tests need no marker. Tests never touch the network: build `Settings` with `Settings(_env_file=None, ...)` (the `settings` fixture), use the in-memory `db` fixture, and `FakeBot` / `notifier` from `tests/conftest.py`. Handler tests feed real `Update`s through the dispatcher with a `MockedSession` (`tests/test_handlers.py`).

## Architecture

Stack: Python 3.12+, aiogram 3 (long polling), httpx, selectolax (`selectolax.lexbor`; the old `selectolax.parser` backend raises ImportError in 1.x), official `deepl` (sync, wrapped in `asyncio.to_thread`), aiosqlite, pydantic-settings.

`app/main.py` starts two things concurrently: the aiogram dispatcher (admin UI) and `Worker.run_forever()` (`app/worker.py`), which runs one cycle every `POLL_INTERVAL_MIN` or when the "Опросить сейчас" button sets `services.trigger`. A cycle runs these steps in order; each is wrapped so one failing step doesn't stop the others:

1. `poller.poll_all` (`app/poller.py`): fetch each enabled source, insert unknown URLs oldest-first as `pending_translation`, apply first-run logic, update the source's health counters.
2. `translate.translate_pending` (`app/translate/service.py`): `pending_translation` → `ready`.
3. `Publisher.run` (`app/publish/publisher.py`): check pause and quiet hours, snapshot deliveries, send round-robin, settle `published`.
4. `monitor.check_sources` (`app/monitor.py`): send one-shot admin alerts based on source health fields.

Shared state lives in the `Services` dataclass (`app/services.py`), injected into handlers as the `services` workflow-data key. Admin alerts go through `Notifier` (`app/notify.py`); `send_throttled` stores the last-sent time in `kv` (`notified_at:<key>`). Item status flow: `pending_translation` → `ready` → `published`, plus `skipped_initial` and `failed`.

Admin UI (`app/handlers/`): `views.py` builds the screens (text plus inline keyboard, `CallbackData` classes `Menu`/`SourceAction`/`RecipientAction`); `admin.py` is a router factory filtered to `ADMIN_IDS` in private chats; `commands.py` sets Telegram's command list (on startup and on every `/start`); `recipients.py` checks rights and sends the test message; `chat_member.py` handles `my_chat_member` join/leave. Routers are built by factories because an aiogram Router can only be attached to one dispatcher.

DB access goes through `Database` (`app/db/repo.py`) with per-table repos (`db.sources`, `db.news`, `db.recipients`, `db.deliveries`, `db.kv`). Timestamps are stored as UTC ISO strings and converted by `app/db/models.py`. To change the schema, append a new script to `MIGRATIONS` in `app/db/migrations.py`; never edit an applied one.

## Invariants that span modules

- **Sources are pluggable**: each source is a class implementing `fetch() -> list[NewsItem]` plus a row in the `sources` table. Adding one must not require changes to the core.
- **freiberg.de parsing** (no RSS; TYPO3 `?type=9818` returns HTML): first page only; title comes from the link's `title` attribute (visible text contains photo credits/dates); strip the trailing `mehr erfahren` from teasers but keep the `…`. Parser tests run against a saved real page in `tests/fixtures/freiberg_neuigkeiten.html`. One request per poll, descriptive `User-Agent` with contact.
- **Never publish untranslated German.** On DeepL quota exhaustion (HTTP 456) items remain `pending_translation` and admins are notified at most once per day. 429/5xx/network errors are retried with backoff by the `deepl` library itself (`max_network_retries`), then the item waits for the next cycle without counting an attempt. Translations are stored and never redone. Glossary: use a DeepL glossary if DE→RU is supported, otherwise post-replace from `glossary.yaml`.
- **No duplicate deliveries**: `deliveries` has a unique key on `(news_item_id, recipient_id)`, and per-recipient status ensures restarts or partial failures don't resend. On 403 → mark recipient inactive and notify admin. On 429 → honor `retry_after`. Other errors → retry next cycle, at most 3 attempts, then notify. Keep to about 20 msgs/min per chat.
- **Post format**: HTML parse mode with every interpolated string escaped, link preview disabled, truncate with `…` to stay ≤4096 chars (see SPEC §5 template).
- **Scheduling**: poll every 30 min. During quiet hours (22–07 Europe/Berlin) scheduled cycles skip polling and publishing (a manual run still polls and translates, but doesn't publish); the worker sleeps until quiet hours end so the morning cycle runs at 07:00 sharp. The backlog publishes oldest-first, with at most N (default 5) posts per chat per cycle. Pause stops publishing only, not polling.
- **First run of a source**: mark all found items `skipped_initial` except the freshest one (by date, ties broken by first in list), which gets published.
- **Monitoring** (DMs to all `ADMIN_IDS`): no new items for `STALE_DAYS` triggers one alert, re-armed only after a new item arrives. 0 parsed items for 3 consecutive polls triggers an immediate alert. Source HTTP errors lasting >24h trigger an alert. On `my_chat_member` (bot added to a chat), send admins the chat title and `chat_id`.
- **Admin menu** (SPEC §10) only works for `ADMIN_IDS` and only in private chat.

## Configuration

`app/config.py`; all keys are documented in `.env.example`. Beyond the spec it adds `BATCH_LIMIT`, `CONTACT` (goes into the User-Agent), `GLOSSARY_PATH` and `LOG_LEVEL`. `QUIET_HOURS` may be empty to disable quiet hours.

## Out of scope for v1

Full article text, images, hashtags, LLM summaries, a web admin UI, sources other than freiberg.de, and editing already-published posts.
