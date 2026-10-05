from app.db.models import RunStatus
from app.scenarios.events import PAUSED
from app.scenarios.fields import FILLED, PARTIAL, FieldState
from app.scenarios.plan import AskStep, CatalogAsk, ClarifyStep, RespondPlan, build_plan
from app.scenarios.schema import ActionEvent
from app.scenarios.work import WorkRun
from tests.scenario_helpers import booking_data, interview_data, loaded, make_def, open_run

INTERVIEW = loaded(make_def())
BOOKING = loaded(make_def(booking_data()), position=1)


def work(scenario, status=RunStatus.FOCUS, asking=None):
    run = WorkRun.from_open(open_run(scenario, status))
    run.asking = asking
    return run


def test_no_focus_and_nothing_happened_is_no_plan():
    assert (
        build_plan([work(BOOKING, RunStatus.PAUSED)], language="uk", client_question=True) is None
    )
    assert build_plan([], language="uk", client_question=False) is None


def test_the_focus_asks_its_field_in_the_reply_language():
    plan = build_plan([work(INTERVIEW, asking="niche")], language="en", client_question=False)
    assert plan.scenario == "interview"
    assert plan.ask == AskStep("niche", "What is your niche?", "text", None, [])
    assert plan.clarify is None
    assert plan.asks
    assert plan.texts_language is None


def test_side_question_gives_both_the_answer_and_the_next_question():
    plan = build_plan([work(INTERVIEW, asking="offer")], language="uk", client_question=True)
    assert plan.answer_client_question_first is True
    assert plan.ask.field == "offer"
    prompt = plan.to_prompt(["rule"])
    assert prompt["answer_client_question_first"] is True
    assert prompt["ask_next"] == {"question": "Що ви продаєте?"}


def test_a_partial_answer_is_clarified_with_the_failed_filters():
    run = work(INTERVIEW, asking="niche")
    run.states["niche"] = FieldState(state=PARTIAL, value="краса", failed_filters=["concrete"])
    plan = build_plan([run], language="uk", client_question=False)
    assert plan.ask is None
    assert plan.clarify == ClarifyStep("niche", "Яка у вас ніша?", "краса", ["Ніша конкретна?"])
    assert plan.to_prompt([])["clarify"] == {
        "question": "Яка у вас ніша?",
        "client_answer": "краса",
        "missing": ["Ніша конкретна?"],
    }


def test_missing_language_is_reported_for_translation():
    data = interview_data()
    data["fields"][0]["question"] = "Яка у вас ніша?"
    run = work(loaded(make_def(data)), asking="niche")
    plan = build_plan([run], language="en", client_question=False)
    assert plan.ask.question == "Яка у вас ніша?"
    assert plan.texts_language == "uk"
    assert plan.to_prompt([])["texts_language"] == "uk"


def test_choice_options_go_to_the_plan():
    data = interview_data()
    data["fields"][1] = {
        "key": "offer",
        "type": "choice",
        "question": {"uk": "Формат?", "en": "Format?"},
        "options": [
            {"key": "online", "label": {"uk": "Онлайн", "en": "Online"}},
            {"key": "offline", "label": {"uk": "Офлайн", "en": "Offline"}},
        ],
    }
    run = work(loaded(make_def(data)), asking="offer")
    plan = build_plan([run], language="en", client_question=False)
    assert plan.ask.options == ["Online", "Offline"]
    assert plan.to_prompt([])["ask_next"]["options"] == ["Online", "Offline"]


def _catalog_run():
    data = interview_data()
    data["fields"][0] = {
        "key": "service",
        "type": "catalog",
        "question": {"uk": "Яка послуга?", "en": "Which service?"},
    }
    return work(loaded(make_def(data)), asking="service")


def _ask_catalog(shown: CatalogAsk | None) -> dict:
    options = {"interview.service": shown} if shown is not None else None
    plan = build_plan(
        [_catalog_run()], language="uk", client_question=False, catalog_options=options
    )
    return plan.to_prompt([])["ask_next"]


def test_catalog_items_go_to_the_plan():
    ask = _ask_catalog(CatalogAsk(["1. Манікюр — 300.00 UAH", "2. Педикюр — 500.00 UAH"]))
    assert ask == {
        "question": "Яка послуга?",
        "options": ["1. Манікюр — 300.00 UAH", "2. Педикюр — 500.00 UAH"],
        "from_catalog": True,
    }


def test_more_catalog_items_than_listed_are_told():
    ask = _ask_catalog(CatalogAsk(["1. Манікюр"], more=True))
    assert ask["options"] == ["1. Манікюр"]
    assert ask["more_options"] is True


def test_catalog_categories_go_to_the_plan_instead_of_items():
    ask = _ask_catalog(CatalogAsk(categories=["Волосся", "Нігті"]))
    assert ask == {
        "question": "Яка послуга?",
        "from_catalog": True,
        "categories": ["Волосся", "Нігті"],
    }


def test_a_catalog_question_with_nothing_to_show_stays_open():
    # still marked: the reply must not make up services from the business description
    assert _ask_catalog(None) == {"question": "Яка послуга?", "from_catalog": True}


def test_other_fields_are_not_marked_as_catalog():
    plan = build_plan([work(INTERVIEW, asking="niche")], language="uk", client_question=False)
    assert plan.to_prompt([])["ask_next"] == {"question": "Яка у вас ніша?"}


def test_events_go_to_the_plan():
    done = work(INTERVIEW)
    done.status = RunStatus.COMPLETED
    done.states["niche"] = FieldState(state=FILLED, value="торти")
    done.filled = ["niche"]
    done.add(ActionEvent.FIELD_FILLED, "niche")
    done.add(ActionEvent.ALL_FIELDS)
    done.add(ActionEvent.COMPLETED)
    started = work(BOOKING, asking="service")
    started.add(ActionEvent.STARTED)
    plan = build_plan([done, started], language="uk", client_question=False)
    assert plan.completed == ["Інтерв'ю"]
    assert plan.started == ["Запис"]
    assert plan.accepted == [
        {"field": "interview.niche", "question": "Яка у вас ніша?", "answer": "торти"}
    ]
    assert plan.scenario == "booking"
    prompt = plan.to_prompt([])
    assert prompt["accepted_answers"] == [{"question": "Яка у вас ніша?", "answer": "торти"}]
    assert prompt["scenario_completed"] == "Інтерв'ю"
    assert prompt["scenario_started"] == "Запис"
    assert prompt["ask_next"]["question"] == "Яка послуга?"


def test_waiting_after_all_fields_is_told_to_the_client_once():
    run = work(INTERVIEW)
    run.status = RunStatus.WAITING
    run.add(ActionEvent.ALL_FIELDS)
    run.add(ActionEvent.WAITING)
    plan = build_plan([run], language="uk", client_question=False)
    assert plan is not None
    assert plan.completed == []
    assert plan.waiting == [{"title": "Інтерв'ю", "for": "event", "new": True}]
    # later turns: still waiting, nothing new to say
    later = work(INTERVIEW)
    later.status = RunStatus.WAITING
    assert build_plan([later], language="uk", client_question=False) is None


def test_cancelled_and_paused():
    cancelled = work(INTERVIEW)
    cancelled.status = RunStatus.CANCELLED
    cancelled.add(ActionEvent.CANCELLED)
    paused = work(BOOKING, RunStatus.PAUSED)
    paused.add(PAUSED)
    plan = build_plan([cancelled, paused], language="uk", client_question=False)
    assert plan.cancelled == ["Інтерв'ю"]
    assert plan.paused == ["Запис"]
    assert plan.scenario is None
    assert not plan.asks


def test_wants_human_keeps_the_scenario_but_asks_nothing():
    plan = build_plan(
        [work(INTERVIEW, asking="niche")], language="uk", client_question=False, wants_human=True
    )
    assert plan.scenario == "interview"
    assert not plan.asks


def test_summary_for_the_trace():
    plan = RespondPlan(scenario="interview", ask=AskStep("offer", "?"))
    assert plan.summary() == {
        "scenario": "interview",
        "answer_client_question_first": False,
        "accepted": [],
        "ask": "offer",
        "clarify": None,
        "confirm": None,
        "waiting": [],
        "completed": [],
        "cancelled": [],
    }
