# Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q                       # tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m app.main              # run locally (needs .env)
```

Tests never touch the network: the parser is tested against a saved page in `tests/fixtures/freiberg_neuigkeiten.html`, and Telegram and DeepL are replaced with fakes.

## Layout

- `app/sources/`: news sources and the freiberg.de parser.
- `app/poller.py`: polls sources and stores new items.
- `app/translate/`: DeepL translation and the glossary.
- `app/publish/`: post format and delivery to recipients.
- `app/monitor.py`: admin alerts about source health.
- `app/handlers/`: admin menu and handling of the bot being added to chats.
- `app/worker.py`: the poll → translate → publish → monitor loop.
- `app/db/`: SQLite, migrations and table access.

## Adding a source

1. Create a subclass of `app.sources.base.Source` with a unique `key` and a `fetch()` method returning `list[NewsItem]`.
2. Import the module in `app/sources/__init__.py` and add the source to `DEFAULT_SOURCES`.

No changes to the core are needed.
