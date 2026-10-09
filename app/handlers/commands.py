import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

log = logging.getLogger(__name__)

ADMIN_COMMANDS = [
    BotCommand(command="menu", description="Меню администратора"),
    BotCommand(command="status", description="Состояние бота"),
    BotCommand(command="pause", description="Приостановить публикацию"),
    BotCommand(command="resume", description="Возобновить публикацию"),
    BotCommand(command="sources", description="Источники"),
    BotCommand(command="recipients", description="Получатели"),
    BotCommand(command="add_recipient", description="Добавить получателя: chat_id [thread_id]"),
    BotCommand(command="remove_recipient", description="Удалить получателя: chat_id"),
    BotCommand(command="preview", description="Предпросмотр последней новости"),
    BotCommand(command="run", description="Опросить источники сейчас"),
]


async def set_admin_commands(bot: Bot, admin_ids: list[int]) -> None:
    """Show the command list (Telegram's "Menu" button) only in admins' private chats.

    Fails with "chat not found" for an admin who hasn't started the bot yet, so /start
    calls this again for that admin.
    """
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as exc:
            log.warning("cannot set commands for admin %s: %s", admin_id, exc)
