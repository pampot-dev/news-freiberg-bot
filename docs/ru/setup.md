# Установка и деплой

## Что нужно для запуска

1. **Токен бота.** Создайте бота у [@BotFather](https://t.me/BotFather) командой `/newbot` и скопируйте токен.
2. **Ключ DeepL API Free.** Зарегистрируйтесь на [deepl.com/pro-api](https://www.deepl.com/pro-api) (тариф Free, 500 000 символов в месяц). Ключ находится в разделе Account → API Keys и заканчивается на `:fx`.
3. **Ваш Telegram user ID.** Его можно узнать, например, у [@userinfobot](https://t.me/userinfobot).
4. **Docker с плагином Compose** на сервере.

## Настройка

```bash
cp .env.example .env
```

Заполните `.env`:

| Переменная | Описание | По умолчанию |
|---|---|---|
| `BOT_TOKEN` | токен от @BotFather | — |
| `DEEPL_API_KEY` | ключ DeepL API Free | — |
| `ADMIN_IDS` | ID админов через запятую | — |
| `POLL_INTERVAL_MIN` | интервал опроса, минуты | `30` |
| `QUIET_HOURS` | тихие часы, когда ничего не публикуется; пусто — выключено | `22-7` |
| `TIMEZONE` | часовой пояс для тихих часов | `Europe/Berlin` |
| `STALE_DAYS` | через сколько дней без новостей уведомлять админа | `7` |
| `DB_PATH` | путь к SQLite (в Docker задаётся в compose) | `data/bot.db` |
| `BATCH_LIMIT` | сколько постов подряд отправлять в один чат за цикл | `5` |
| `CONTACT` | e-mail или URL для User-Agent: так владельцы сайта смогут связаться | — |
| `GLOSSARY_PATH` | путь к глоссарию | `glossary.yaml` |
| `LOG_LEVEL` | уровень логов | `INFO` |

**Важно.** Каждый админ должен хотя бы один раз написать боту `/start`. Иначе Telegram не даст боту прислать ему уведомление.

## Запуск

```bash
docker compose up -d
docker compose logs -f
```

База данных хранится в Docker volume `bot-data` и не теряется при перезапуске или пересборке контейнера. Глоссарий `glossary.yaml` подключён с хоста, поэтому после его правки достаточно `docker compose restart`.

## Обновление

```bash
git pull
docker compose up -d --build
```

## Деплой на AWS EC2

1. Создайте инстанс, например `t3.micro` или `t4g.micro` с Amazon Linux 2023 или Ubuntu. Входящие порты, кроме SSH, не нужны: бот работает через long polling.
2. Установите Docker и плагин Compose.

   Amazon Linux 2023:

   ```bash
   sudo dnf install -y docker git
   sudo systemctl enable --now docker
   sudo usermod -aG docker $USER   # перелогиньтесь после этого
   sudo mkdir -p /usr/local/lib/docker/cli-plugins
   sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m) \
     -o /usr/local/lib/docker/cli-plugins/docker-compose
   sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
   ```

   Ubuntu: `sudo apt install -y docker.io docker-compose-v2 git`.
3. Склонируйте репозиторий, создайте `.env` и запустите:

   ```bash
   git clone https://github.com/pampot-dev/news-freiberg-bot.git && cd news-freiberg-bot
   cp .env.example .env && nano .env
   docker compose up -d
   ```

Контейнер перезапускается автоматически (`restart: unless-stopped`), в том числе после перезагрузки инстанса.

## Резервная копия базы

```bash
docker compose exec bot python -c "import sqlite3; s=sqlite3.connect('/app/data/bot.db'); d=sqlite3.connect('/app/data/backup.db'); s.backup(d)"
docker compose cp bot:/app/data/backup.db ./backup.db
```
