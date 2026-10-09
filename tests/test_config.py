import pytest
from pydantic import ValidationError

from app.config import Settings


def make(**overrides):
    values = {"bot_token": "t", "deepl_api_key": "k", "admin_ids": "1"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_admin_ids_parsed_from_comma_list():
    assert make(admin_ids="111, 222,333").admin_ids == [111, 222, 333]


def test_quiet_hours_parsed():
    assert make(quiet_hours="22-7").quiet_hours == (22, 7)


def test_quiet_hours_can_be_disabled():
    assert make(quiet_hours="").quiet_hours is None


@pytest.mark.parametrize("value", ["22", "25-7", "a-b"])
def test_quiet_hours_invalid(value):
    with pytest.raises(ValidationError):
        make(quiet_hours=value)


def test_invalid_timezone():
    with pytest.raises(ValidationError):
        make(timezone="Mars/Olympus")


def test_user_agent_includes_contact():
    assert "(+bot@example.org)" in make(contact="bot@example.org").user_agent
