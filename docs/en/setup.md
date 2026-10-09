# Setup and deployment

## Prerequisites

1. **Bot token.** Create a bot with [@BotFather](https://t.me/BotFather) using `/newbot` and copy the token.
2. **DeepL API Free key.** Sign up at [deepl.com/pro-api](https://www.deepl.com/pro-api) (Free plan, 500,000 characters per month). The key is under Account → API Keys and ends with `:fx`.
3. **Your Telegram user ID.** You can get it from [@userinfobot](https://t.me/userinfobot), for example.
4. **Docker with the Compose plugin** on the server.

## Configuration

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Description | Default |
|---|---|---|
| `BOT_TOKEN` | token from @BotFather | — |
| `DEEPL_API_KEY` | DeepL API Free key | — |
| `ADMIN_IDS` | comma-separated admin user IDs | — |
| `POLL_INTERVAL_MIN` | polling interval, minutes | `30` |
| `QUIET_HOURS` | hours when nothing is posted; empty disables them | `22-7` |
| `TIMEZONE` | time zone for quiet hours | `Europe/Berlin` |
| `STALE_DAYS` | days without news before admins are alerted | `7` |
| `DB_PATH` | SQLite path (set by compose in Docker) | `data/bot.db` |
| `BATCH_LIMIT` | max posts per chat per cycle | `5` |
| `CONTACT` | e-mail or URL for the User-Agent, so site owners can reach you | — |
| `GLOSSARY_PATH` | glossary path | `glossary.yaml` |
| `LOG_LEVEL` | log level | `INFO` |

**Important.** Every admin must send `/start` to the bot at least once. Otherwise Telegram won't let the bot message them.

## Running

```bash
docker compose up -d
docker compose logs -f
```

The database lives in the `bot-data` Docker volume and survives restarts and rebuilds. `glossary.yaml` is mounted from the host, so after editing it `docker compose restart` is enough.

## Updating

```bash
git pull
docker compose up -d --build
```

## Deploying to AWS EC2

1. Launch an instance, e.g. `t3.micro` or `t4g.micro` with Amazon Linux 2023 or Ubuntu. No inbound ports besides SSH are needed: the bot uses long polling.
2. Install Docker and the Compose plugin.

   Amazon Linux 2023:

   ```bash
   sudo dnf install -y docker git
   sudo systemctl enable --now docker
   sudo usermod -aG docker $USER   # log out and back in afterwards
   sudo mkdir -p /usr/local/lib/docker/cli-plugins
   sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m) \
     -o /usr/local/lib/docker/cli-plugins/docker-compose
   sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
   ```

   Ubuntu: `sudo apt install -y docker.io docker-compose-v2 git`.
3. Clone the repository, create `.env` and start:

   ```bash
   git clone https://github.com/pampot-dev/news-freiberg-bot.git && cd news-freiberg-bot
   cp .env.example .env && nano .env
   docker compose up -d
   ```

The container restarts automatically (`restart: unless-stopped`), including after an instance reboot.

## Database backup

```bash
docker compose exec bot python -c "import sqlite3; s=sqlite3.connect('/app/data/bot.db'); d=sqlite3.connect('/app/data/backup.db'); s.backup(d)"
docker compose cp bot:/app/data/backup.db ./backup.db
```
