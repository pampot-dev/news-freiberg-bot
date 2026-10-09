import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import JOIN_TRANSITION, LEAVE_TRANSITION, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated

from app.handlers.views import add_chat_keyboard
from app.services import Services

log = logging.getLogger(__name__)

CHAT_TYPES = {"channel": "канал", "group": "группу", "supergroup": "группу"}


def create_chat_member_router() -> Router:
    router = Router(name="chat_member")
    router.my_chat_member.filter(F.chat.type != "private")

    @router.my_chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
    async def bot_added(event: ChatMemberUpdated, services: Services) -> None:
        chat = event.chat
        log.info("bot added to %s %s", chat.type, chat.id)
        title = escape(chat.title or str(chat.id))
        lines = [
            f"Бота добавили в {CHAT_TYPES.get(chat.type, chat.type)} «{title}».",
            f"chat_id: <code>{chat.id}</code>",
        ]
        if chat.type == "channel":
            lines.append("Для публикации бот должен быть администратором с правом публикации.")
        if chat.is_forum:
            lines.append(
                "В группе включены темы. Чтобы публиковать в определённую тему, откройте "
                "«Получатели» → «➕ Добавить по chat_id» и пришлите "
                f"<code>{chat.id} &lt;thread_id&gt;</code>"
            )
        if event.from_user:
            lines.append(f"Добавил: {escape(event.from_user.full_name)}")
        await services.notifier.send(
            "\n".join(lines), is_html=True, reply_markup=add_chat_keyboard(chat.id)
        )

    @router.my_chat_member(ChatMemberUpdatedFilter(LEAVE_TRANSITION))
    async def bot_removed(event: ChatMemberUpdated, services: Services) -> None:
        chat = event.chat
        log.info("bot removed from %s %s", chat.type, chat.id)
        db = services.db
        affected = [r for r in await db.recipients.list(active_only=True) if r.chat_id == chat.id]
        for recipient in affected:
            await db.recipients.set_active(recipient.id, False)
        if affected:
            await services.notifier.send(
                f"Бота удалили из «{chat.title or chat.id}» ({chat.id}). "
                "Получатель отключён, публикация в остальные чаты продолжается."
            )

    return router
