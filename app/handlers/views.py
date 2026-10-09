"""Admin menu screens: text + inline keyboard, built from DB state."""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db import Recipient, utcnow
from app.publish.publisher import PAUSED_KEY, in_quiet_hours
from app.services import Services

STATUS_NAMES = {
    "pending_translation": "ждут перевода",
    "ready": "готовы к публикации",
    "published": "опубликованы",
    "failed": "ошибка перевода",
    "skipped_initial": "пропущены при первом запуске",
}


class Menu(CallbackData, prefix="m"):
    action: str  # main / status / pause / resume / sources / recipients / preview / run


class SourceAction(CallbackData, prefix="s"):
    action: str  # toggle
    id: int


class RecipientAction(CallbackData, prefix="r"):
    action: str  # toggle / ask_remove / remove / add_chat
    id: int  # recipient id; chat id for add_chat


Screen = tuple[str, InlineKeyboardMarkup]


def fmt_dt(value: datetime | None, tz: ZoneInfo) -> str:
    return value.astimezone(tz).strftime("%d.%m.%Y %H:%M") if value else "—"


def _num(value: int) -> str:
    return f"{value:,}".replace(",", "\u202f")


def _back(builder: InlineKeyboardBuilder) -> None:
    builder.button(text="◀️ Меню", callback_data=Menu(action="main"))


async def main_menu(services: Services) -> Screen:
    paused = await services.db.kv.get_bool(PAUSED_KEY)
    b = InlineKeyboardBuilder()
    b.button(text="📊 Статус", callback_data=Menu(action="status"))
    if paused:
        b.button(text="▶️ Возобновить", callback_data=Menu(action="resume"))
    else:
        b.button(text="⏸ Пауза", callback_data=Menu(action="pause"))
    b.button(text="📰 Источники", callback_data=Menu(action="sources"))
    b.button(text="👥 Получатели", callback_data=Menu(action="recipients"))
    b.button(text="👁 Предпросмотр", callback_data=Menu(action="preview"))
    b.button(text="🔄 Опросить сейчас", callback_data=Menu(action="run"))
    b.adjust(2)
    state = "⏸ публикация на паузе" if paused else "▶️ работает"
    return f"<b>Меню администратора</b>\nСостояние: {state}", b.as_markup()


async def status_screen(services: Services, now: datetime | None = None) -> Screen:
    now = now or utcnow()
    db, settings = services.db, services.settings
    tz = settings.tz
    lines = ["<b>Статус</b>"]

    paused = await db.kv.get_bool(PAUSED_KEY)
    lines.append("Публикация: " + ("⏸ на паузе" if paused else "▶️ работает"))
    if settings.quiet_hours:
        start, end = settings.quiet_hours
        quiet = in_quiet_hours(now, tz, settings.quiet_hours)
        lines.append(
            f"Тихие часы: {start:02d}:00–{end:02d}:00" + (" (сейчас действуют)" if quiet else "")
        )

    lines.append("\n<b>Источники</b>")
    for src in await db.sources.list():
        mark = "✅" if src.enabled else "⛔️"
        lines.append(f"{mark} {escape(src.name)}")
        lines.append(f"   последний опрос: {fmt_dt(src.last_poll_at, tz)}")
        lines.append(f"   последняя новая новость: {fmt_dt(src.last_new_item_at, tz)}")
        if src.first_error_at:
            lines.append(
                f"   ⚠️ ошибки с {fmt_dt(src.first_error_at, tz)}: {escape(src.last_error or '')}"
            )
        if src.consecutive_empty_polls:
            lines.append(f"   ⚠️ пустых опросов подряд: {src.consecutive_empty_polls}")

    counts = await db.news.count_by_status()
    lines.append("\n<b>Очередь</b>")
    for status in ("pending_translation", "ready", "failed"):
        lines.append(f"{STATUS_NAMES[status]}: {counts.get(status, 0)}")
    lines.append(f"неотправленных доставок: {await db.deliveries.pending_count()}")
    active = len(await db.recipients.list(active_only=True))
    lines.append(f"активных получателей: {active}")

    lines.append("\n<b>DeepL</b>")
    try:
        usage = await services.translator.usage()
    except Exception as exc:  # status must render even if DeepL is down
        lines.append(f"квота: недоступно ({escape(str(exc)[:200])})")
    else:
        if usage:
            used, limit, left = (
                _num(n) for n in (usage.used, usage.limit, usage.limit - usage.used)
            )
            lines.append(f"квота: использовано {used} из {limit}, осталось {left}")
        else:
            lines.append("квота: нет данных")

    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить", callback_data=Menu(action="status"))
    _back(b)
    return "\n".join(lines), b.as_markup()


async def sources_screen(services: Services) -> Screen:
    sources = await services.db.sources.list()
    lines = ["<b>Источники</b>"]
    b = InlineKeyboardBuilder()
    for src in sources:
        mark = "✅ включён" if src.enabled else "⛔️ выключен"
        lines.append(f"• {escape(src.name)} — {mark}\n  {escape(src.url)}")
        b.button(
            text=("Выключить: " if src.enabled else "Включить: ") + src.name,
            callback_data=SourceAction(action="toggle", id=src.id),
        )
    _back(b)
    b.adjust(1)
    return "\n".join(lines), b.as_markup()


ADD_HINT = (
    "Чтобы добавить получателя, добавьте бота в канал (админом с правом публикации) "
    "или в группу — я пришлю кнопку «Добавить». Либо командой:\n"
    "<code>/add_recipient &lt;chat_id&gt; [thread_id]</code>"
)


async def recipients_screen(services: Services) -> Screen:
    recipients = await services.db.recipients.list()
    lines = ["<b>Получатели</b>"]
    b = InlineKeyboardBuilder()
    if not recipients:
        lines.append("Пока никого нет.")
    for r in recipients:
        mark = "✅" if r.active else "⏸"
        lines.append(f"{mark} {escape(r.display_name)} — <code>{r.chat_id}</code>")
        b.button(
            text=("⏸ " if r.active else "▶️ ") + r.display_name,
            callback_data=RecipientAction(action="toggle", id=r.id),
        )
        b.button(text="🗑", callback_data=RecipientAction(action="ask_remove", id=r.id))
    lines.append("\n" + ADD_HINT)
    _back(b)
    b.adjust(*([2] * len(recipients)), 1)
    return "\n".join(lines), b.as_markup()


def confirm_remove_screen(recipient: Recipient) -> Screen:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Удалить", callback_data=RecipientAction(action="remove", id=recipient.id))
    b.button(text="Отмена", callback_data=Menu(action="recipients"))
    name = escape(recipient.display_name)
    return f"Удалить получателя «{name}» (<code>{recipient.chat_id}</code>)?", b.as_markup()


def add_chat_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="➕ Добавить в получатели",
        callback_data=RecipientAction(action="add_chat", id=chat_id),
    )
    return b.as_markup()
