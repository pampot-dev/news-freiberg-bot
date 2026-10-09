import pytest

from app.config import Settings
from app.db import Database


@pytest.fixture
async def db():
    database = await Database.open(":memory:")
    yield database
    await database.close()


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        bot_token="123:abc",
        deepl_api_key="key:fx",
        admin_ids="1001,1002",
    )


class FakeBot:
    """Records send_message calls; can be told to raise per chat."""

    def __init__(self):
        self.sent: list[tuple[int, str, dict]] = []
        self.errors: dict[int, Exception] = {}
        self._next_id = 100

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.errors:
            raise self.errors[chat_id]
        self.sent.append((chat_id, text, kwargs))
        self._next_id += 1

        class Message:
            message_id = self._next_id

        return Message()

    def texts_to(self, chat_id):
        return [text for cid, text, _ in self.sent if cid == chat_id]


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
def notifier(bot, db):
    from app.notify import Notifier

    return Notifier(bot, [1001], db)
