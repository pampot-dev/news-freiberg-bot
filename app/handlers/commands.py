import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

log = logging.getLogger(__name__)

# Everything else is in the inline menu; the "Menu" button only needs to open it.
ADMIN_COMMANDS = [BotCommand(command="menu", description="Меню администратора")]


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
