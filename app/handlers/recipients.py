"""Adding a recipient: check the bot's rights in the chat and send a test message."""

import logging
from html import escape
from typing import Any, Protocol

from aiogram.exceptions import TelegramAPIError

from app.db import Database

log = logging.getLogger(__name__)

TEST_MESSAGE = "✅ Бот подключён. Здесь будут публиковаться новости Фрайберга на русском."


class ChatBot(Protocol):
    id: int

    async def get_chat(self, chat_id: int | str) -> Any: ...

    async def get_chat_member(self, chat_id: int | str, user_id: int) -> Any: ...

    async def send_message(self, chat_id: int | str, text: str, **kwargs: Any) -> Any: ...


def parse_chat_ref(value: str) -> int | str:
    """Numeric chat id, or @username of a public channel/group."""
    value = value.strip()
    if value.startswith("@"):
        return value
    return int(value)


def rights_problem(chat_type: str, member: Any) -> str | None:
    """Why the bot can't post in this chat, or None if it can."""
    status = getattr(member, "status", None)
    if status in ("left", "kicked"):
        return "бот не состоит в этом чате"
    if chat_type == "channel":
        if status == "creator":
            return None
        if status != "administrator" or not getattr(member, "can_post_messages", False):
            return "в канале бот должен быть администратором с правом публикации сообщений"
        return None
    if status == "restricted" and not getattr(member, "can_send_messages", False):
        return "у бота нет права писать в этой группе"
    return None


async def add_recipient(
    bot: ChatBot, db: Database, chat_ref: int | str, thread_id: int | None = None
) -> str:
    """Validate and add; returns an HTML message for the admin."""
    try:
        chat = await bot.get_chat(chat_ref)
    except TelegramAPIError as exc:
        return f"❌ Бот не видит чат <code>{escape(str(chat_ref))}</code>: {escape(exc.message)}"
    if chat.type == "private":
        return "❌ Получателем может быть только канал или группа."

    title = chat.title or chat.username or str(chat.id)
    try:
        member = await bot.get_chat_member(chat.id, bot.id)
    except TelegramAPIError as exc:
        return f"❌ Не удалось проверить права в «{escape(title)}»: {escape(exc.message)}"
    if problem := rights_problem(chat.type, member):
        return f"❌ «{escape(title)}»: {problem}."

    try:
        await bot.send_message(chat.id, TEST_MESSAGE, message_thread_id=thread_id)
    except TelegramAPIError as exc:
        return f"❌ Тестовое сообщение в «{escape(title)}» не отправлено: {escape(exc.message)}"

    recipient = await db.recipients.add(chat.id, thread_id, title)
    log.info("recipient added: %s %s", chat.id, thread_id)
    return (
        f"✅ Получатель «{escape(recipient.display_name)}» (<code>{chat.id}</code>) добавлен, "
        "тестовое сообщение отправлено."
    )
