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
