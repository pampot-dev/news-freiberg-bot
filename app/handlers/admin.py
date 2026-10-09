import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LinkPreviewOptions, Message

from app.handlers.recipients import add_recipient, parse_chat_ref
from app.handlers.views import (
    Menu,
    RecipientAction,
    SourceAction,
    confirm_remove_screen,
    main_menu,
    recipients_screen,
    sources_screen,
    status_screen,
)
from app.publish.formatter import format_item
from app.publish.publisher import PAUSED_KEY
from app.services import Services

log = logging.getLogger(__name__)

ADD_USAGE = (
    "Использование: <code>/add_recipient &lt;chat_id&gt; [thread_id]</code>\n"
    "chat_id — число (например, <code>-1001234567890</code>) или @username канала."
)


def create_admin_router(admin_ids: list[int]) -> Router:
    """Everything here is reachable only by ADMIN_IDS in a private chat with the bot."""
    admins = set(admin_ids)
    router = Router(name="admin")
    router.message.filter(F.chat.type == "private", F.from_user.id.in_(admins))
    router.callback_query.filter(F.message.chat.type == "private", F.from_user.id.in_(admins))

    # --- slash commands ---------------------------------------------------------------

    @router.message(Command("start", "menu", "help"))
    async def cmd_menu(message: Message, services: Services) -> None:
        text, markup = await main_menu(services)
        await message.answer(text, reply_markup=markup)

    @router.message(Command("status"))
    async def cmd_status(message: Message, services: Services) -> None:
        text, markup = await status_screen(services)
        await message.answer(text, reply_markup=markup)

    @router.message(Command("pause", "resume"))
    async def cmd_pause(message: Message, command: CommandObject, services: Services) -> None:
        await message.answer(await set_paused(services, command.command == "pause"))

    @router.message(Command("sources"))
    async def cmd_sources(message: Message, services: Services) -> None:
        text, markup = await sources_screen(services)
        await message.answer(text, reply_markup=markup)

    @router.message(Command("recipients"))
    async def cmd_recipients(message: Message, services: Services) -> None:
        text, markup = await recipients_screen(services)
        await message.answer(text, reply_markup=markup)

    @router.message(Command("add_recipient"))
    async def cmd_add_recipient(
        message: Message, command: CommandObject, bot: Bot, services: Services
    ) -> None:
        args = (command.args or "").split()
        try:
            if not 1 <= len(args) <= 2:
                raise ValueError
            chat_ref = parse_chat_ref(args[0])
            thread_id = int(args[1]) if len(args) == 2 else None
        except ValueError:
            await message.answer(ADD_USAGE)
            return
        await message.answer(await add_recipient(bot, services.db, chat_ref, thread_id))

    @router.message(Command("remove_recipient"))
    async def cmd_remove_recipient(
        message: Message, command: CommandObject, services: Services
    ) -> None:
        try:
            chat_id = int((command.args or "").strip())
        except ValueError:
            await message.answer("Использование: <code>/remove_recipient &lt;chat_id&gt;</code>")
            return
        removed = await services.db.recipients.remove_chat(chat_id)
        if removed:
            await message.answer(f"🗑 Удалено получателей: {removed}.")
        else:
            await message.answer(f"Получатель <code>{chat_id}</code> не найден.")

    @router.message(Command("preview"))
    async def cmd_preview(message: Message, services: Services) -> None:
        await send_preview(message, services)

    @router.message(Command("run"))
    async def cmd_run(message: Message, services: Services) -> None:
        services.trigger.request(message.chat.id)
        await message.answer("🔄 Опрос запущен, пришлю результат.")

    # --- inline menu --------------------------------------------------------------

    @router.callback_query(Menu.filter())
    async def on_menu(query: CallbackQuery, callback_data: Menu, services: Services) -> None:
        action = callback_data.action
        if action in ("pause", "resume"):
            await query.answer(await set_paused(services, action == "pause"))
            await show(query, *await main_menu(services))
        elif action == "status":
            await show(query, *await status_screen(services))
            await query.answer()
        elif action == "sources":
            await show(query, *await sources_screen(services))
            await query.answer()
        elif action == "recipients":
            await show(query, *await recipients_screen(services))
            await query.answer()
        elif action == "preview":
            await query.answer()
            await send_preview(query.message, services)
        elif action == "run":
            services.trigger.request(query.message.chat.id)
            await query.answer("Опрос запущен, пришлю результат.")
        else:
            await show(query, *await main_menu(services))
            await query.answer()

    @router.callback_query(SourceAction.filter(F.action == "toggle"))
    async def on_source_toggle(
        query: CallbackQuery, callback_data: SourceAction, services: Services
    ) -> None:
        src = await services.db.sources.get(callback_data.id)
        if src is None:
            await query.answer("Источник не найден")
            return
        await services.db.sources.update(src.id, enabled=not src.enabled)
        await query.answer("Источник выключен" if src.enabled else "Источник включён")
        await show(query, *await sources_screen(services))

    @router.callback_query(RecipientAction.filter())
    async def on_recipient(
        query: CallbackQuery, callback_data: RecipientAction, bot: Bot, services: Services
    ) -> None:
        db = services.db
        if callback_data.action == "add_chat":
            await query.answer("Проверяю права…")
            result = await add_recipient(bot, db, callback_data.id)
            await query.message.answer(result)
            if result.startswith("✅"):
                await query.message.edit_reply_markup(reply_markup=None)
            return

        recipient = await db.recipients.get(callback_data.id)
        if recipient is None:
            await query.answer("Получатель уже удалён")
            await show(query, *await recipients_screen(services))
            return
        if callback_data.action == "toggle":
            await db.recipients.set_active(recipient.id, not recipient.active)
            await query.answer("Отключён" if recipient.active else "Включён")
            await show(query, *await recipients_screen(services))
        elif callback_data.action == "ask_remove":
            await show(query, *confirm_remove_screen(recipient))
            await query.answer()
        elif callback_data.action == "remove":
            await db.recipients.remove(recipient.id)
            await query.answer("Удалён")
            await show(query, *await recipients_screen(services))

    return router


async def set_paused(services: Services, paused: bool) -> str:
    await services.db.kv.set_bool(PAUSED_KEY, paused)
    log.info("publishing %s", "paused" if paused else "resumed")
    if paused:
        return "⏸ Публикация приостановлена. Опрос и перевод продолжаются."
    return "▶️ Публикация возобновлена."


async def show(query: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Replace the menu message in place."""
    try:
        await query.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in exc.message:
            raise


async def send_preview(message: Message, services: Services) -> None:
    item = await services.db.news.latest(translated_only=True)
    if item is None:
        latest = await services.db.news.latest()
        if latest is None:
            await message.answer("Новостей пока нет.")
        else:
            await message.answer(
                f"Последняя новость ещё не переведена:\n<i>{escape(latest.title_de)}</i>"
            )
        return
    await message.answer("👁 Предпросмотр последней новости (не опубликовано):")
    await message.answer(
        format_item(item), link_preview_options=LinkPreviewOptions(is_disabled=True)
    )
