import pytest
from pydantic import ValidationError

from app.api.v1.messages import TEXT_MAX, MessageIn

CONTACT = {"external_id": "c1"}


def test_a_whole_story_fits_in_one_message():
    assert TEXT_MAX == 40_000
    assert len(MessageIn(contact=CONTACT, text="а" * TEXT_MAX).text) == TEXT_MAX


def test_a_longer_message_is_refused():
    with pytest.raises(ValidationError):
        MessageIn(contact=CONTACT, text="а" * (TEXT_MAX + 1))
