# news-freiberg-bot

**English** | [Русский](README_RU.md)

A Telegram bot that reads the news list on [freiberg.de](https://www.freiberg.de/stadt-und-buerger/aktuelles/neuigkeiten) every 30 minutes, translates titles and teasers from German to Russian with DeepL, and posts them to Telegram channels and groups.

- Posts only translated news, oldest first, never twice.
- At night (22:00–07:00 Berlin time) it doesn't touch the site and posts nothing.
- Managed through an inline menu in a private chat with the bot.
- Alerts admins about problems: bot removed from a chat, site down, page layout changed, DeepL quota exhausted.

The bot's own interface and posts are in Russian.

## Quick start

```bash
git clone https://github.com/pampot-dev/news-freiberg-bot.git && cd news-freiberg-bot
cp .env.example .env   # set BOT_TOKEN, DEEPL_API_KEY and ADMIN_IDS
docker compose up -d
```

Then send `/start` to the bot in a private chat and add it to a channel or group.

## Documentation

- [Setup and deployment](docs/en/setup.md): `.env` settings, running, updating, AWS EC2, backup.
- [Using the bot](docs/en/usage.md): menu, recipients, bot behavior, glossary.
- [Development](docs/en/development.md): tests, layout, adding a source.
- [Specification](docs/SPEC.md) (in Russian).
