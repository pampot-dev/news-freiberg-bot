# Using the bot

The bot's interface is in Russian; button labels below are given in Russian with a translation.

## Menu

Send `/start` to the bot in a private chat, or tap the "Menu" button left of the input field. The admin menu opens with these buttons:

- **📊 Статус** (Status): publishing state, last poll time, queue, remaining DeepL quota.
- **⏸ Пауза / ▶️ Возобновить** (Pause / Resume): stops and resumes publishing. Polling and translation continue.
- **📰 Источники** (Sources): enable and disable sources.
- **👥 Получатели** (Recipients): list of recipients, disable (⏸/▶️), delete (🗑) and add by `chat_id`.
- **👁 Предпросмотр** (Preview): the latest news item rendered as a post, without publishing.
- **🔄 Опросить сейчас** (Poll now): an extra cycle, with a report when it finishes.

There are no text commands besides `/start` and `/menu`: any other message opens the menu too. The bot only responds to users in `ADMIN_IDS` and only in a private chat.

## Adding a channel or group

1. Add the bot to a channel **as an administrator with the right to post messages**. In a group, add it as a member allowed to send messages.
2. The bot sends admins the chat title, `chat_id` and an **"➕ Добавить в получатели"** (Add to recipients) button.
3. On tap, the bot checks its rights and sends a test message to the chat.

You can also add a chat manually: "👥 Получатели" → "➕ Добавить по chat_id", then send `<chat_id> [thread_id]`. `thread_id` is needed to post into a specific topic of a forum group.

## How the bot behaves

- **First run.** All news on the page count as already seen; only the freshest one is posted. If there are no recipients yet, it waits for the first one.
- **Quiet hours.** From 22:00 to 07:00 the bot doesn't touch the site and posts nothing. At exactly 07:00 it polls and posts the backlog oldest first, at most `BATCH_LIMIT` per chat per cycle. "Poll now" works at night too, but publishing waits for the morning.
- **Nothing is posted untranslated.** If DeepL is unavailable or the quota is exhausted, the item waits and admins are notified (at most once a day).
- **No duplicates.** Every delivery is recorded in the database, so restarts never repost.
- **Admin alerts** are sent when:
  - the bot was removed from a chat or lacks rights;
  - the site has had no news for `STALE_DAYS` days;
  - the parser found nothing three times in a row (the layout probably changed);
  - the site has been unavailable for more than a day;
  - a message couldn't be delivered after 3 attempts.

## Glossary

`glossary.yaml` has two sections:

```yaml
terms:            # German term → how to translate it
  Silberstadt: Серебряный город

fixes:            # fix the finished Russian text: before → after
  обер-мэр: обер-бургомистр
```

- `terms`: local DE → RU terms. If DeepL supports glossaries for DE→RU, the bot uploads them to DeepL and DeepL inflects the terms itself. Otherwise the bot replaces German words DeepL left untranslated.
- `fixes`: RU → RU replacements for wording DeepL consistently gets wrong.

Replacements match whole words only and are case-sensitive, so inflected forms in `fixes` need their own lines. Quote a term that contains a colon or `#`.

To update the glossary on the server:

```bash
git pull
docker compose restart
```

The bot reads the glossary on startup. Changes apply only to news translated after the restart; existing translations are never redone. The logs show which mode is used: `created DeepL glossary …` or `DeepL has no DE->RU glossaries, using post-replacement`.
