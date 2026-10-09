# Разработка

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q                       # тесты
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m app.main              # локальный запуск (нужен .env)
```

Тесты не обращаются к сети: парсер проверяется на сохранённой странице `tests/fixtures/freiberg_neuigkeiten.html`, а Telegram и DeepL заменены фейками.

## Структура

- `app/sources/`: источники новостей и парсер freiberg.de.
- `app/poller.py`: опрос источников и запись новых новостей в БД.
- `app/translate/`: перевод через DeepL и глоссарий.
- `app/publish/`: формат поста и рассылка по получателям.
- `app/monitor.py`: уведомления админам о проблемах с источниками.
- `app/handlers/`: админ-меню и обработка добавления бота в чаты.
- `app/worker.py`: цикл «опрос → перевод → публикация → мониторинг».
- `app/db/`: SQLite, миграции и доступ к таблицам.

## Новый источник

1. Создайте класс-наследник `app.sources.base.Source` с уникальным `key` и методом `fetch()`, который возвращает `list[NewsItem]`.
2. Импортируйте модуль в `app/sources/__init__.py` и добавьте источник в `DEFAULT_SOURCES`.

Ядро бота менять не нужно.
