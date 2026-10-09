from datetime import UTC, date, datetime
from itertools import count

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    GetChat,
    GetChatMember,
    SendMessage,
    SetMyCommands,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatFullInfo,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    Update,
    User,
)

from app.handlers import create_dispatcher
from app.handlers.commands import ADMIN_COMMANDS
from app.handlers.recipients import TEST_MESSAGE, rights_problem
from app.handlers.views import Menu, RecipientAction, SourceAction
from app.notify import Notifier
from app.publish.publisher import PAUSED_KEY
from app.services import Services
from app.sources import NewsItem
from app.translate import Usage

ADMIN = 1001
STRANGER = 5555
BOT_ID = 42
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
_ids = count(1)


class MockedSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.chats: dict[int | str, ChatFullInfo] = {}
        self.members: dict[int, object] = {}

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage | EditMessageText):
            return Message(
                message_id=next(_ids),
                date=NOW,
                chat=Chat(id=int(method.chat_id or 0), type="private"),
                text=method.text,
            )
        if isinstance(method, GetChat):
            if method.chat_id not in self.chats:
                raise TelegramBadRequest(method, "Bad Request: chat not found")
            return self.chats[method.chat_id]
        if isinstance(method, GetChatMember):
            return self.members[method.chat_id]
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    async def close(self):
        pass

    def sent(self, chat_id=None):
        return [
            r
            for r in self.requests
            if isinstance(r, SendMessage) and (chat_id is None or r.chat_id == chat_id)
        ]

    def of_type(self, cls):
        return [r for r in self.requests if isinstance(r, cls)]


class FakeTranslator:
    async def translate(self, title, text):
        return title, text

    async def usage(self):
        return Usage(used=120_000, limit=500_000)


@pytest.fixture
async def env(db, settings):
    session = MockedSession()
    bot = Bot(f"{BOT_ID}:TEST", session=session, default=DefaultBotProperties(parse_mode="HTML"))
    settings.admin_ids = [ADMIN]
    services = Services(db, settings, FakeTranslator(), Notifier(bot, [ADMIN], db))
    dp = create_dispatcher(services)

    async def feed(**update):
        await dp.feed_update(bot, Update(update_id=next(_ids), **update))

    return session, services, feed


def user(uid):
    return User(id=uid, is_bot=False, first_name=f"U{uid}")


def private_message(text, uid=ADMIN):
    return Message(
        message_id=next(_ids),
        date=NOW,
        chat=Chat(id=uid, type="private"),
        from_user=user(uid),
        text=text,
    )


def callback(data, uid=ADMIN):
    return CallbackQuery(
        id=str(next(_ids)),
        from_user=user(uid),
        chat_instance="x",
        data=data.pack(),
        message=Message(
            message_id=next(_ids), date=NOW, chat=Chat(id=uid, type="private"), text="menu"
        ),
    )


def buttons(request):
    return [b.callback_data for row in request.reply_markup.inline_keyboard for b in row]


async def test_menu_for_admin(env):
    session, _, feed = env
    await feed(message=private_message("/start"))
    [reply] = session.sent(ADMIN)
    assert "Меню администратора" in reply.text
    assert Menu(action="status").pack() in buttons(reply)
    assert Menu(action="pause").pack() in buttons(reply)
    # /start (re)installs Telegram's "Menu" button: commands fail to set for an admin
    # who hadn't started the bot at launch time.
    [commands] = session.of_type(SetMyCommands)
    assert commands.scope.chat_id == ADMIN
    assert commands.commands == ADMIN_COMMANDS


@pytest.mark.parametrize("text", ["/start", "/menu", "/status", "-100", "hello"])
async def test_non_admin_is_ignored(env, text):
    session, services, feed = env
    await feed(message=private_message(text, uid=STRANGER))
    assert session.requests == []
    assert not await services.db.kv.get_bool(PAUSED_KEY)


async def test_commands_ignored_in_groups(env):
    session, _, feed = env
    msg = Message(
        message_id=1,
        date=NOW,
        chat=Chat(id=-100, type="supergroup"),
        from_user=user(ADMIN),
        text="/menu",
    )
    await feed(message=msg)
    assert session.requests == []


async def test_non_admin_callback_ignored(env):
    session, services, feed = env
    await feed(callback_query=callback(Menu(action="pause"), uid=STRANGER))
    assert not await services.db.kv.get_bool(PAUSED_KEY)
    assert session.of_type(EditMessageText) == []


async def test_pause_and_resume_buttons(env):
    session, services, feed = env
    await feed(callback_query=callback(Menu(action="pause")))
    assert await services.db.kv.get_bool(PAUSED_KEY)
    [edit] = session.of_type(EditMessageText)
    assert Menu(action="resume").pack() in buttons(edit)
    await feed(callback_query=callback(Menu(action="resume")))
    assert not await services.db.kv.get_bool(PAUSED_KEY)


async def test_status_screen(env):
    session, services, feed = env
    src = await services.db.sources.ensure("s", "Freiberg", "https://ex.org")
    await services.db.sources.update(src.id, last_poll_at=NOW, last_error="HTTP 503")
    await feed(callback_query=callback(Menu(action="status")))
    [edit] = session.of_type(EditMessageText)
    assert "Freiberg" in edit.text
    assert "ждут перевода: 0" in edit.text
    assert "осталось 380 000" in edit.text


async def test_source_toggle(env):
    session, services, feed = env
    src = await services.db.sources.ensure("s", "Freiberg", "https://ex.org")
    await feed(callback_query=callback(SourceAction(action="toggle", id=src.id)))
    assert not (await services.db.sources.get(src.id)).enabled
    [edit] = session.of_type(EditMessageText)
    assert "выключен" in edit.text


async def test_recipient_toggle_and_remove(env):
    session, services, feed = env
    r = await services.db.recipients.add(-100, None, "Канал")
    await feed(callback_query=callback(RecipientAction(action="toggle", id=r.id)))
    assert not (await services.db.recipients.get(r.id)).active
    await feed(callback_query=callback(RecipientAction(action="ask_remove", id=r.id)))
    assert "Удалить получателя «Канал»" in session.of_type(EditMessageText)[-1].text
    await feed(callback_query=callback(RecipientAction(action="remove", id=r.id)))
    assert await services.db.recipients.list() == []


def channel(chat_id=-100, title="Новости"):
    # model_construct: ChatFullInfo has many required fields irrelevant here.
    return ChatFullInfo.model_construct(id=chat_id, type="channel", title=title, username=None)


def admin_member(can_post=True):
    # Required permission flags differ between Bot API versions: default them all to False.
    flags = {
        name: False
        for name, field in ChatMemberAdministrator.model_fields.items()
        if field.is_required() and field.annotation is bool
    }
    return ChatMemberAdministrator(
        **flags,
        user=User(id=BOT_ID, is_bot=True, first_name="bot"),
        can_post_messages=can_post,
    )


async def start_add(feed):
    await feed(callback_query=callback(Menu(action="add_recipient")))


async def test_add_recipient_checks_rights_and_sends_test(env):
    session, services, feed = env
    session.chats[-100] = channel()
    session.members[-100] = admin_member()
    await start_add(feed)
    [prompt] = session.of_type(EditMessageText)
    assert "chat_id" in prompt.text
    await feed(message=private_message("-100"))
    [test] = session.sent(-100)
    assert test.text == TEST_MESSAGE
    assert "добавлен" in session.sent(ADMIN)[-2].text
    assert "Получатели" in session.sent(ADMIN)[-1].text
    [r] = await services.db.recipients.list()
    assert (r.chat_id, r.thread_id, r.title) == (-100, None, "Новости")


async def test_add_recipient_with_thread(env):
    session, services, feed = env
    session.chats[-100] = channel()
    session.members[-100] = admin_member()
    await start_add(feed)
    await feed(message=private_message("-100 42"))
    [test] = session.sent(-100)
    assert test.message_thread_id == 42
    [r] = await services.db.recipients.list()
    assert r.thread_id == 42


async def test_add_recipient_without_post_rights(env):
    session, services, feed = env
    session.chats[-100] = channel()
    session.members[-100] = admin_member(can_post=False)
    await start_add(feed)
    await feed(message=private_message("-100"))
    assert session.sent(-100) == []
    assert "правом публикации" in session.sent(ADMIN)[-2].text
    assert await services.db.recipients.list() == []


async def test_add_recipient_unknown_chat_and_bad_input(env):
    session, services, feed = env
    await start_add(feed)
    await feed(message=private_message("abc"))
    assert session.sent(ADMIN)[-1].text.startswith("Не понял")
    await feed(message=private_message("-999"))  # still waiting after bad input
    assert "не видит чат" in session.sent(ADMIN)[-2].text


async def test_add_recipient_cancel(env):
    session, services, feed = env
    await start_add(feed)
    await feed(callback_query=callback(Menu(action="recipients")))
    await feed(message=private_message("-100"))
    # Prompt cancelled: the text just opens the menu instead of adding a recipient.
    assert "Меню администратора" in session.sent(ADMIN)[-1].text
    assert session.of_type(GetChat) == []


async def test_any_text_opens_menu(env):
    session, _, feed = env
    await feed(message=private_message("/status"))
    [reply] = session.sent(ADMIN)
    assert "Меню администратора" in reply.text


async def test_preview(env):
    session, services, feed = env
    await feed(callback_query=callback(Menu(action="preview")))
    assert session.sent(ADMIN)[-1].text == "Новостей пока нет."

    src = await services.db.sources.ensure("s", "S", "u")
    item_id = await services.db.news.insert(
        src.id,
        NewsItem(url="https://ex.org/a", title="Titel", published_date=date(2026, 10, 1)),
        "pending_translation",
    )
    await services.db.news.commit()
    await services.db.news.set_translation(item_id, "Заголовок", "Текст")
    await feed(callback_query=callback(Menu(action="preview")))
    post = session.sent(ADMIN)[-1]
    assert post.text.startswith("<b>Заголовок</b>")
    assert post.link_preview_options.is_disabled


async def test_run_triggers_poll(env):
    session, services, feed = env
    await feed(callback_query=callback(Menu(action="run")))
    assert services.trigger.event.is_set()
    assert services.trigger.take_requesters() == {ADMIN}


def member_update(old, new, chat):
    return ChatMemberUpdated(
        chat=chat, from_user=user(777), date=NOW, old_chat_member=old, new_chat_member=new
    )


async def test_bot_added_to_channel_notifies_admin_with_button(env):
    session, services, feed = env
    bot_user = User(id=BOT_ID, is_bot=True, first_name="bot")
    chat = Chat(id=-100, type="channel", title="Новости <Фрайберг>")
    await feed(my_chat_member=member_update(ChatMemberLeft(user=bot_user), admin_member(), chat))
    [note] = session.sent(ADMIN)
    assert "<code>-100</code>" in note.text
    assert "Новости &lt;Фрайберг&gt;" in note.text
    assert buttons(note) == [RecipientAction(action="add_chat", id=-100).pack()]

    # The button runs the same checks as /add_recipient.
    session.chats[-100] = channel()
    session.members[-100] = admin_member()
    await feed(callback_query=callback(RecipientAction(action="add_chat", id=-100)))
    assert len(await services.db.recipients.list()) == 1
    assert session.of_type(EditMessageReplyMarkup)


async def test_bot_removed_deactivates_recipient(env):
    session, services, feed = env
    r = await services.db.recipients.add(-100, None, "Группа")
    bot_user = User(id=BOT_ID, is_bot=True, first_name="bot")
    chat = Chat(id=-100, type="supergroup", title="Группа")
    await feed(
        my_chat_member=member_update(
            ChatMemberMember(user=bot_user), ChatMemberLeft(user=bot_user), chat
        )
    )
    assert not (await services.db.recipients.get(r.id)).active
    assert "отключён" in session.sent(ADMIN)[-1].text


def test_rights_problem_for_groups():
    assert rights_problem("supergroup", ChatMemberMember(user=user(1))) is None
    assert rights_problem("group", ChatMemberLeft(user=user(1)))
    assert rights_problem("channel", ChatMemberMember(user=user(1)))


async def test_callbacks_are_answered(env):
    session, _, feed = env
    await feed(callback_query=callback(Menu(action="sources")))
    assert session.of_type(AnswerCallbackQuery)
