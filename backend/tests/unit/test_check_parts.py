"""Checks of answers longer than Jev's window, without a database: the answers go in
several requests, an answer too long for a request alone goes in parts, a filter passes
when a part passes, the show_if questions get a request with the run's card whole."""

import uuid
from types import SimpleNamespace

import pytest

from app.decisions.engine import DecideContext
from app.decisions.fake import FakeDecider
from app.decisions.limits import REQUEST_TOKENS, request_tokens
from app.decisions.policy import DecisionPolicy
from app.scenarios.checks import CheckItem, ask_filters
from app.scenarios.fields import FILLED, PARTIAL, FieldState
from app.scenarios.show import ShowItem
from tests.scenario_helpers import make_def

# 16 paragraphs, about 48 000 Cyrillic characters: more than a request holds
STORY = "\n\n".join(f"Абзац {i}. " + "слово " * 500 for i in range(16))


def facts_def():
    return make_def(
        {
            "key": "facts",
            "title": {"uk": "Фактаж", "en": "Facts"},
            "language": "uk",
            "start": {"questions": [{"key": "t", "ask": {"uk": "Розповідає?", "en": "Tells?"}}]},
            "fields": [
                {
                    "key": "story",
                    "question": {"uk": "Розкажіть історію", "en": "Tell the story"},
                    "filters": [{"key": "event", "ask": {"uk": "Є подія?", "en": "An event?"}}],
                },
                {"key": "age", "question": {"uk": "Скільки вам було?", "en": "How old?"}},
            ],
        }
    )


def booking_def():
    return make_def(
        {
            "key": "booking",
            "title": {"uk": "Запис", "en": "Booking"},
            "language": "uk",
            "start": {"questions": [{"key": "t", "ask": {"uk": "Запис?", "en": "Booking?"}}]},
            "fields": [
                {"key": "service", "question": {"uk": "Яка послуга?", "en": "Which service?"}},
                {
                    "key": "master",
                    "question": {"uk": "До якого майстра?", "en": "Which master?"},
                    "show_if": {
                        "questions": [{"key": "many", "ask": {"uk": "Кілька?", "en": "Many?"}}]
                    },
                },
            ],
        }
    )


@pytest.fixture
def saved(monkeypatch):
    rows = []

    async def save(_sm, new):
        rows.extend(new)

    monkeypatch.setattr("app.decisions.log.save_decisions", save)
    return rows


def _services(rules):
    fake = FakeDecider(rules)
    return SimpleNamespace(extra={"decider": fake}, sm=None), fake


CTX = DecideContext(tenant_id=uuid.uuid4(), policy=DecisionPolicy())


async def test_an_answer_longer_than_a_request_is_checked_in_parts(saved):
    defn = facts_def()
    states = {f.key: FieldState() for f in defn.fields}
    states["age"] = FieldState(state=FILLED, value="27")
    # only the last paragraph shows the event: the answer passes
    services, fake = _services({"filter:*": lambda q, state: "Абзац 15." in state})
    item = CheckItem(defn, defn.field("story"), STORY, states)
    outcome = await ask_filters(services, CTX, [item], "uk")
    assert outcome.failed == {("facts", "story"): []}
    assert len(fake.calls) > 1
    assert all(request_tokens(s, qs) <= REQUEST_TOKENS for s, qs in fake.calls)
    total = len(fake.calls)
    for n, (state, questions) in enumerate(fake.calls, start=1):
        assert [q.key for q in questions] == ["filter:facts.story.event"]
        assert f"client's answer, part {n} of {total}: «" in state
        # the accepted answers go with every part
        assert "Answers already accepted in «Фактаж»:" in state and "«27»" in state
    # nothing of the answer is lost between the parts
    assert all(f"Абзац {i}." in "".join(s for s, _ in fake.calls) for i in range(16))
    assert len(saved) == total and sum(r.used for r in saved) == 1


async def test_no_part_passes_the_answer_fails(saved):
    defn = facts_def()
    states = {f.key: FieldState() for f in defn.fields}
    services, _ = _services({"filter:*": False})
    item = CheckItem(defn, defn.field("story"), STORY, states)
    outcome = await ask_filters(services, CTX, [item], "uk")
    assert outcome.failed == {("facts", "story"): ["event"]}


async def test_the_show_questions_get_their_own_request_with_the_card_whole(saved):
    facts, booking = facts_def(), booking_def()
    fstates = {f.key: FieldState() for f in facts.fields}
    bstates = {f.key: FieldState() for f in booking.fields}
    bstates["service"] = FieldState(state=PARTIAL, value="манікюр")
    services, fake = _services({"filter:*": True, "show:*": True})
    outcome = await ask_filters(
        services,
        CTX,
        [CheckItem(facts, facts.field("story"), STORY, fstates)],
        "uk",
        shows=[ShowItem(booking, booking.field("master"), bstates)],
    )
    assert outcome.shows_dropped == []
    shows = [(s, qs) for s, qs in fake.calls if qs[0].key.startswith("show:")]
    assert len(shows) == 1
    state, questions = shows[0]
    assert [q.key for q in questions] == ["show:booking.master.many"]
    assert "Client's answers in «Запис»:" in state and "«манікюр» (incomplete)" in state
    assert "Абзац" not in state
    assert outcome.decisions is not None and outcome.decisions.value("show:booking.master.many")


async def test_a_card_longer_than_a_request_is_not_asked_about(saved):
    defn = booking_def()
    states = {f.key: FieldState() for f in defn.fields}
    states["service"] = FieldState(state=FILLED, value=STORY)
    services, fake = _services({"filter:*": True, "show:*": True})
    outcome = await ask_filters(
        services,
        CTX,
        [CheckItem(defn, defn.field("service"), STORY, states)],
        "uk",
        shows=[ShowItem(defn, defn.field("master"), states)],
    )
    assert outcome.shows_dropped == ["booking.master"]
    assert fake.calls == []
