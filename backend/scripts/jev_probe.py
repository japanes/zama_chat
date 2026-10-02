"""Jev probe (stage 0): how Jev answers our route questions on uk / ru / en.

    make jev-probe                     # = python -m scripts.jev_probe
    python -m scripts.jev_probe --llm openai/gpt-6-luna --no-checks

Every phrase is already "rewritten" (the form the route sees). Each question is
asked twice — instructions/criteria in Ukrainian and in English — to decide which
language Jev questions should be written in. The same set goes through an LLM in
the same `/v1/evaluate` call (if the Gateway accepts a language model there, the
LLM decider needs no prompt of its own).

Side checks: zeroDataRetention, the 32k / 64k limits, allowed question and choice
keys, the score answer shape, and whether `reasoning` effort passes through chat
completions.

Output: <project>/tmp/jev-probe/report.md (+ raw.jsonl). The whole probe costs a
fraction of a cent. The API key is only sent in the header, never printed.
"""

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.settings import PROJECT_ROOT, get_settings

OUT_DIR = PROJECT_ROOT / "tmp" / "jev-probe"
QUESTION_LANGS = ("uk", "en")

# --- questions -----------------------------------------------------------------------
# Wording follows the legacy context_manager_types (docs/legacy-prompts/_cm_types_table.md).
# TODO(review): check the wording of every question, both languages.

QUESTIONS: dict[str, dict[str, dict[str, Any]]] = {
    "service_type": {
        "en": {
            "type": "choice",
            "instructions": "Classify the client's latest message by its service type.",
            "criteria": {
                "none": "The message has a question, a request or any information beyond "
                "a bare greeting, farewell or identity question.",
                "greeting_only": "A standalone greeting without any questions or requests "
                "(e.g. 'hi', 'hello', 'good morning').",
                "goodbye_only": "A standalone farewell (e.g. 'bye', 'see you', 'take care').",
                "not_a_bot": "The client asks about your identity, name or job, or whether "
                "you are a real person or a bot.",
                "ethical_violation": "The message is about unethical, illegal or dangerous "
                "things (hacking, fraud, drugs, terrorism, extremism and the like).",
            },
        },
        "uk": {
            "type": "choice",
            "instructions": "Визнач службовий тип останнього повідомлення клієнта.",
            "criteria": {
                "none": "У повідомленні є питання, прохання або будь-яка інформація, крім "
                "простого привітання, прощання чи питання про особу.",
                "greeting_only": "Лише привітання без питань і прохань "
                "(наприклад «привіт», «добрий день»).",
                "goodbye_only": "Лише прощання (наприклад «бувай», «до побачення»).",
                "not_a_bot": "Клієнт питає, хто ти, як тебе звати, ким працюєш, або чи ти "
                "жива людина чи бот.",
                "ethical_violation": "Повідомлення про неетичне, незаконне або небезпечне "
                "(злам, шахрайство, наркотики, тероризм, екстремізм тощо).",
            },
        },
    },
    "wants_human": {
        "en": {
            "type": "boolean",
            "instructions": "Does the client ask to talk to a real person "
            "(a manager, an operator, an administrator)?",
            "criteria": {
                "true": "The client asks for a human, a manager or an operator.",
                "false": "The client does not ask for a human.",
            },
        },
        "uk": {
            "type": "boolean",
            "instructions": "Клієнт просить поговорити з живою людиною "
            "(менеджером, оператором, адміністратором)?",
            "criteria": {
                "true": "Клієнт просить людину, менеджера або оператора.",
                "false": "Клієнт не просить людину.",
            },
        },
    },
    "start_offer": {
        "en": {
            "type": "boolean",
            "instructions": "Does the client want help with their offer or positioning — "
            "what they sell and how to present it?",
            "criteria": {
                "true": "The client wants to work on their offer, positioning or how to "
                "explain their product.",
                "false": "The client wants something else or only asks a question.",
            },
        },
        "uk": {
            "type": "boolean",
            "instructions": "Клієнт хоче розібратися зі своєю пропозицією чи позиціонуванням "
            "— що він продає і як це подати?",
            "criteria": {
                "true": "Клієнт хоче попрацювати над пропозицією, позиціонуванням або тим, "
                "як пояснити свій продукт.",
                "false": "Клієнт хоче іншого або просто ставить питання.",
            },
        },
    },
    "start_booking": {
        "en": {
            "type": "boolean",
            "instructions": "Does the client want to book an appointment for a service "
            "(a time slot)?",
            "criteria": {
                "true": "The client wants to book or asks for a free slot.",
                "false": "The client does not want to book (e.g. only asks the price).",
            },
        },
        "uk": {
            "type": "boolean",
            "instructions": "Клієнт хоче записатися на послугу (на певний час)?",
            "criteria": {
                "true": "Клієнт хоче записатися або питає про вільний час.",
                "false": "Клієнт не хоче записуватися (наприклад, лише питає ціну).",
            },
        },
    },
    "field_niche": {
        "en": {
            "type": "boolean",
            "instructions": "Does the message say what the client's business does "
            "(their niche or field)?",
            "criteria": {
                "true": "The message names what the client's business does.",
                "false": "The message does not say what the business does.",
            },
        },
        "uk": {
            "type": "boolean",
            "instructions": "У повідомленні сказано, чим займається бізнес клієнта "
            "(його ніша чи сфера)?",
            "criteria": {
                "true": "Повідомлення називає, чим займається бізнес клієнта.",
                "false": "Повідомлення не каже, чим займається бізнес.",
            },
        },
    },
    # a field filter: asked only where the message names a niche (as at runtime)
    "filter_concrete_niche": {
        "en": {
            "type": "boolean",
            "instructions": "Did the client name a specific niche, not just a general field? "
            "('wedding dresses made to order' is specific, 'beauty' is general.)",
            "criteria": {
                "true": "A specific niche.",
                "false": "Only a general field.",
            },
        },
        "uk": {
            "type": "boolean",
            "instructions": "Клієнт назвав конкретну нішу, а не загальну сферу? "
            "(«весільні сукні на замовлення» — конкретна, «краса» — загальна.)",
            "criteria": {
                "true": "Конкретна ніша.",
                "false": "Лише загальна сфера.",
            },
        },
    },
}

# --- dataset -------------------------------------------------------------------------
# TODO(review): check every expected answer. None = the question is not asked
# for this phrase. Phrases are in the rewritten form the route gets.

N, GR, BYE, BOT, ETH = "none", "greeting_only", "goodbye_only", "not_a_bot", "ethical_violation"


@dataclass
class Case:
    lang: str
    text: str
    service_type: str
    wants_human: bool = False
    start_offer: bool = False
    start_booking: bool = False
    field_niche: bool = False
    filter_concrete_niche: bool | None = None


DATASET: list[Case] = [
    # uk
    Case("uk", "Привіт!", GR),
    Case("uk", "Дякую, до побачення!", BYE),
    Case("uk", "Ти бот чи жива людина?", BOT),
    Case("uk", "Покличте, будь ласка, менеджера, хочу поговорити з людиною.", N, wants_human=True),
    Case("uk", "Хочу записатися на манікюр у пʼятницю після 18:00.", N, start_booking=True),
    Case("uk", "Скільки коштує манікюр з покриттям?", N),
    Case(
        "uk",
        "Я продаю свічки ручної роботи в Instagram і не розумію, як сформулювати свою пропозицію.",
        N,
        start_offer=True,
        field_niche=True,
        filter_concrete_niche=True,
    ),
    Case("uk", "Я працюю у сфері краси.", N, field_niche=True, filter_concrete_niche=False),
    Case("uk", "Як зламати чужий акаунт в Instagram?", ETH),
    Case("uk", "Привіт, хочу записатися на стрижку на завтра.", N, start_booking=True),
    Case(
        "uk",
        "Хочу записатися на манікюр, але спершу покличте адміністратора.",
        N,
        wants_human=True,
        start_booking=True,
    ),
    Case("uk", "Шо по ціні на нарощування вій?", N),  # суржик
    # ru
    Case("ru", "Здравствуйте", GR),
    Case("ru", "Пока, хорошего дня!", BYE),
    Case("ru", "Как тебя зовут? Ты робот?", BOT),
    Case("ru", "Соедините меня с администратором салона, пожалуйста.", N, wants_human=True),
    Case("ru", "Хочу записаться на педикюр в субботу утром.", N, start_booking=True),
    Case(
        "ru",
        "У меня кофейня, не понимаю, чем мы отличаемся от конкурентов и как это донести.",
        N,
        start_offer=True,
        field_niche=True,
        filter_concrete_niche=True,
    ),
    Case("ru", "Я занимаюсь бизнесом.", N),
    Case("ru", "Где купить поддельные документы?", ETH),
    Case("ru", "Во сколько вы работаете в воскресенье?", N),
    Case(
        "ru",
        "Мы печём торты на заказ для детских праздников, помогите сформулировать предложение.",
        N,
        start_offer=True,
        field_niche=True,
        filter_concrete_niche=True,
    ),
    # en
    Case("en", "Hi there!", GR),
    Case("en", "Thanks, bye!", BYE),
    Case("en", "Are you a real person?", BOT),
    Case("en", "I want to speak to a manager, please.", N, wants_human=True),
    Case("en", "Can I book a haircut for tomorrow at 3 pm?", N, start_booking=True),
    Case(
        "en",
        "I run an online English school for kids and need help explaining why parents "
        "should choose us.",
        N,
        start_offer=True,
        field_niche=True,
        filter_concrete_niche=True,
    ),
    Case("en", "I'm in marketing.", N, field_niche=True, filter_concrete_niche=False),
    Case("en", "How do I make a fake receipt to get a refund?", ETH),
    Case(
        "en",
        "Good morning! Do you have any free slots for a manicure on Friday?",
        N,
        start_booking=True,
    ),
    Case("en", "What are your opening hours?", N),
]

STATE_TEMPLATE = (
    "Conversation state.\n"
    "Client's latest message (rewritten): {text}\n"
    "Open scenarios: none. Available scenarios: offer_interview (offer and positioning "
    "interview), salon_booking (beauty salon appointment)."
)


def expected(case: Case, key: str) -> Any:
    return getattr(case, key)


# --- Gateway calls -------------------------------------------------------------------


@dataclass
class Answer:
    value: Any
    confidence: float
    raw: dict[str, Any]


@dataclass
class EvalResult:
    status: int
    latency_ms: int
    answers: dict[str, Answer] = field(default_factory=dict)
    cost: float = 0.0
    generation_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def parse_answer(raw: dict[str, Any]) -> Answer:
    """Confidence: boolean → max(p, 1−p); choice / score → probability of the pick."""
    if "probability" in raw:
        p = float(raw["probability"])
        return Answer(p >= 0.5, max(p, 1 - p), raw)
    probs = raw.get("probabilities") or {}
    for kind in ("choice", "score"):
        if kind in raw:
            value = raw[kind]
            return Answer(value, float(probs.get(str(value), 0.0)), raw)
    return Answer(None, 0.0, raw)


class Gateway:
    def __init__(self, http: httpx.AsyncClient, base_url: str, concurrency: int) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.sem = asyncio.Semaphore(concurrency)

    async def evaluate(
        self,
        model: str,
        state: str,
        questions: dict[str, Any],
        *,
        zdr: bool | None = True,
    ) -> EvalResult:
        gateway: dict[str, Any] = {"only": [model.split("/", 1)[0]]}
        if zdr is not None:
            gateway["zeroDataRetention"] = zdr
        body = {
            "model": model,
            "state": state,
            "questions": questions,
            "providerOptions": {"gateway": gateway},
        }
        status, data, latency_ms, error = await self._post("/evaluate", body)
        result = EvalResult(status=status, latency_ms=latency_ms, error=error, raw=data)
        if status != 200:
            return result
        for key, answer in (data.get("answers") or {}).items():
            if isinstance(answer, dict):
                result.answers[key] = parse_answer(answer)
        result.usage = data.get("usage") or {}
        meta = (data.get("providerMetadata") or {}).get("gateway") or {}
        result.cost = float(meta.get("cost") or 0)
        result.generation_id = meta.get("generationId")
        return result

    async def chat(self, body: dict[str, Any]) -> tuple[int, dict[str, Any], int, str]:
        return await self._post("/chat/completions", body)

    async def _post(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any], int, str]:
        async with self.sem:
            started = time.monotonic()
            try:
                response = await self.http.post(self.base_url + path, json=body)
            except httpx.HTTPError as exc:
                return 0, {}, int((time.monotonic() - started) * 1000), type(exc).__name__
            latency_ms = int((time.monotonic() - started) * 1000)
        try:
            data = response.json()
        except ValueError:
            data = {}
        error = ""
        if response.status_code != 200:
            err = data.get("error") if isinstance(data, dict) else None
            message = err.get("message") if isinstance(err, dict) else response.text
            error = f"{response.status_code}: {str(message)[:300]}"
        return response.status_code, data if isinstance(data, dict) else {}, latency_ms, error


# --- main probe ----------------------------------------------------------------------


@dataclass
class Row:
    model: str
    qlang: str
    lang: str
    text: str
    key: str
    expected: Any
    got: Any
    confidence: float
    correct: bool
    latency_ms: int
    cost: float
    error: str = ""


def questions_for(case: Case, qlang: str) -> dict[str, Any]:
    return {
        key: variants[qlang]
        for key, variants in QUESTIONS.items()
        if expected(case, key) is not None
    }


async def probe_case(gw: Gateway, model: str, qlang: str, case: Case) -> list[Row]:
    questions = questions_for(case, qlang)
    result = await gw.evaluate(model, STATE_TEMPLATE.format(text=case.text), questions)
    # cost and latency belong to the whole request: put them on the first row only
    rows: list[Row] = []
    for i, key in enumerate(questions):
        want = expected(case, key)
        answer = result.answers.get(key)
        got = answer.value if answer else None
        rows.append(
            Row(
                model=model,
                qlang=qlang,
                lang=case.lang,
                text=case.text,
                key=key,
                expected=want,
                got=got,
                confidence=answer.confidence if answer else 0.0,
                correct=answer is not None and got == want,
                latency_ms=result.latency_ms if i == 0 else 0,
                cost=result.cost if i == 0 else 0.0,
                error=result.error or ("" if answer else "no answer"),
            )
        )
    return rows


async def run_main(gw: Gateway, models: list[str]) -> list[Row]:
    tasks = [
        probe_case(gw, model, qlang, case)
        for model in models
        for qlang in QUESTION_LANGS
        for case in DATASET
    ]
    return [row for rows in await asyncio.gather(*tasks) for row in rows]


# --- side checks ---------------------------------------------------------------------

TINY_Q = {
    "is_greeting": {
        "type": "boolean",
        "instructions": "Is the client's message a greeting?",
        "criteria": {"true": "A greeting.", "false": "Not a greeting."},
    }
}
TINY_STATE = "Client's latest message: Hello!"


async def check_zdr(gw: Gateway, model: str) -> list[str]:
    lines = []
    for zdr in (True, False, None):
        r = await gw.evaluate(model, TINY_STATE, TINY_Q, zdr=zdr)
        label = {True: "zeroDataRetention=true", False: "=false", None: "без флага"}[zdr]
        lines.append(f"| {label} | {r.status} | {r.error or 'ok'} |")
    return ["| Запрос | HTTP | Результат |", "|---|---|---|", *lines]


async def check_limits(gw: Gateway, model: str) -> list[str]:
    """Rough sizes: one 'note ' ≈ 1 token. usage in the answer gives the real count."""
    big_q = {
        f"q{i}": {
            "type": "boolean",
            "instructions": "Is the note about apples? " + "note " * 22_000,
            "criteria": {"true": "Yes.", "false": "No."},
        }
        for i in range(3)
    }
    cases = [
        ("state ≈ 30k токенов", "note " * 30_000, TINY_Q),
        ("state ≈ 34k токенов (> 32k)", "note " * 34_000, TINY_Q),
        ("3 вопроса по ≈ 22k (сумма > 64k)", TINY_STATE, big_q),
    ]
    lines = ["| Что | HTTP | usage | Результат |", "|---|---|---|---|"]
    for label, state, questions in cases:
        r = await gw.evaluate(model, state, questions)
        usage = json.dumps(r.usage, ensure_ascii=False)[:120]
        lines.append(f"| {label} | {r.status} | `{usage}` | {r.error or 'ok'} |")
    return lines


KEY_CANDIDATES = [
    "start:booking.wants_booking",
    "field:offer_interview.niche",
    "cancel:0192f5a0-7c1e-7d2a-9b11-3c4d5e6f7a8b",
    "with space",
    "так",
]


async def check_keys(gw: Gateway, model: str) -> list[str]:
    lines = ["| Ключ | как ключ вопроса | как вариант choice |", "|---|---|---|"]
    for key in KEY_CANDIDATES:
        as_question = {key: TINY_Q["is_greeting"]}
        as_choice = {
            "kind": {
                "type": "choice",
                "instructions": "What is the client's message?",
                "criteria": {key: "A greeting.", "none": "Something else."},
            }
        }
        q, c = await asyncio.gather(
            gw.evaluate(model, TINY_STATE, as_question), gw.evaluate(model, TINY_STATE, as_choice)
        )
        q_res = "ok" if q.status == 200 and key in q.answers else (q.error or "нет ответа")
        c_ok = c.status == 200 and "kind" in c.answers
        c_res = f"ok ({c.answers['kind'].value})" if c_ok else (c.error or "нет ответа")
        lines.append(f"| `{key}` | {q_res} | {c_res} |")
    return lines


async def check_score(gw: Gateway, model: str) -> list[str]:
    questions = {
        "urgency": {
            "type": "score",
            "instructions": "How urgent is the client's request?",
            "criteria": {
                "1": "Not urgent at all.",
                "2": "Slightly urgent.",
                "3": "Urgent.",
                "4": "Very urgent.",
                "5": "Emergency.",
            },
        }
    }
    r = await gw.evaluate(model, "Client's latest message: I need a manicure right now!", questions)
    shape = json.dumps(r.raw.get("answers", r.raw), ensure_ascii=False)[:600]
    return [f"HTTP {r.status} {r.error}", "", "```json", shape, "```"]


async def check_reasoning(gw: Gateway, llm: str) -> list[str]:
    prompt = "A bat and a ball cost $1.10 in total. The bat costs $1 more than the ball. "
    prompt += "How much is the ball? Answer with the number only."
    variants: list[tuple[str, dict[str, Any]]] = [
        ("без reasoning", {}),
        ('reasoning: {effort: "none"}', {"reasoning": {"effort": "none"}}),
        ('reasoning: {effort: "low"}', {"reasoning": {"effort": "low"}}),
        ('reasoning: {effort: "high"}', {"reasoning": {"effort": "high"}}),
        ('reasoning_effort: "low" (OpenAI)', {"reasoning_effort": "low"}),
    ]
    lines = [
        "| Вариант | HTTP | reasoning-токены | всего выход | время, мс | Результат |",
        "|---|---|---|---|---|---|",
    ]
    for label, extra in variants:
        body = {"model": llm, "messages": [{"role": "user", "content": prompt}], **extra}
        status, data, latency_ms, error = await gw.chat(body)
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        lines.append(
            f"| {label} | {status} | {details.get('reasoning_tokens', '—')} | "
            f"{usage.get('completion_tokens', '—')} | {latency_ms} | {error or 'ok'} |"
        )
    return lines


# --- report --------------------------------------------------------------------------


def pct(part: int, total: int) -> str:
    return f"{100 * part / total:.0f}% ({part}/{total})" if total else "—"


def accuracy_table(rows: list[Row], group: str, groups: list[str]) -> list[str]:
    combos = sorted({(r.model, r.qlang) for r in rows})
    head = "| Модель | Язык вопросов | " + " | ".join(groups) + " | всего |"
    lines = [head, "|---|---|" + "---|" * (len(groups) + 1)]
    for model, qlang in combos:
        sel = [r for r in rows if r.model == model and r.qlang == qlang]
        cells = []
        for g in groups:
            part = [r for r in sel if getattr(r, group) == g]
            cells.append(pct(sum(r.correct for r in part), len(part)))
        cells.append(pct(sum(r.correct for r in sel), len(sel)))
        lines.append(f"| {model} | {qlang} | " + " | ".join(cells) + " |")
    return lines


def confidence_table(rows: list[Row]) -> list[str]:
    lines = [
        "| Модель | Язык вопросов | ср. уверенность: верно | ошибки | "
        "ошибки с уверенностью ≥ 0.9 |",
        "|---|---|---|---|---|",
    ]
    for model, qlang in sorted({(r.model, r.qlang) for r in rows}):
        sel = [r for r in rows if r.model == model and r.qlang == qlang and not r.error]
        right = [r.confidence for r in sel if r.correct]
        wrong = [r.confidence for r in sel if not r.correct]
        sure_wrong = sum(c >= 0.9 for c in wrong)
        lines.append(
            f"| {model} | {qlang} | {statistics.mean(right) if right else 0:.2f} | "
            f"{len(wrong)} (ср. {statistics.mean(wrong) if wrong else 0:.2f}) | {sure_wrong} |"
        )
    return lines


def threshold_table(rows: list[Row]) -> list[str]:
    """What a threshold would give: accuracy of answers above it, and how many go to the LLM."""
    thresholds = (0.6, 0.7, 0.8, 0.9, 0.95)
    lines = [
        "| Модель | Язык вопросов | "
        + " | ".join(f"порог {t}: точность / ушло в LLM" for t in thresholds)
        + " |",
        "|---|---|" + "---|" * len(thresholds),
    ]
    for model, qlang in sorted({(r.model, r.qlang) for r in rows}):
        sel = [r for r in rows if r.model == model and r.qlang == qlang and not r.error]
        cells = []
        for t in thresholds:
            above = [r for r in sel if r.confidence >= t]
            acc = f"{100 * sum(r.correct for r in above) / len(above):.0f}%" if above else "—"
            below = f"{100 * (len(sel) - len(above)) / len(sel):.0f}%" if sel else "—"
            cells.append(f"{acc} / {below}")
        lines.append(f"| {model} | {qlang} | " + " | ".join(cells) + " |")
    return lines


def timing_table(rows: list[Row]) -> list[str]:
    lines = ["| Модель | запросов | p50, мс | p95, мс | цена, $ |", "|---|---|---|---|---|"]
    for model in sorted({r.model for r in rows}):
        lat = sorted(r.latency_ms for r in rows if r.model == model and r.latency_ms)
        cost = sum(r.cost for r in rows if r.model == model)
        p50 = lat[len(lat) // 2] if lat else 0
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0
        lines.append(f"| {model} | {len(lat)} | {p50} | {p95} | {cost:.6f} |")
    return lines


def mistakes(rows: list[Row]) -> list[str]:
    lines = [
        "| Модель | Вопросы | Язык | Фраза | Ключ | Ожидали | Получили | Уверенность |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r.correct:
            continue
        got = r.error if r.error else r.got
        lines.append(
            f"| {r.model} | {r.qlang} | {r.lang} | {r.text} | {r.key} | {r.expected} | "
            f"{got} | {r.confidence:.2f} |"
        )
    return lines


def write_report(
    out: Path, rows: list[Row], models: list[str], checks: dict[str, list[str]]
) -> Path:
    keys = list(QUESTIONS)
    parts = [
        "# Проба Jev",
        "",
        f"- Дата: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        f"- Модели: {', '.join(models)}",
        f"- Фраз: {len(DATASET)} (uk / ru / en), вопросов: {len(keys)}, "
        f"варианты вопросов: {' / '.join(QUESTION_LANGS)}",
        "- Уверенность: да/нет — max(p, 1−p); выбор — вероятность выбранного варианта.",
        "",
        "## Точность по языку фразы",
        *accuracy_table(rows, "lang", ["uk", "ru", "en"]),
        "",
        "## Точность по вопросу",
        *accuracy_table(rows, "key", keys),
        "",
        "## Уверенность (ошибается ли модель уверенно)",
        *confidence_table(rows),
        "",
        "## Что дал бы порог",
        "Точность ответов с уверенностью не ниже порога / доля, которая ушла бы в запасной LLM.",
        "",
        *threshold_table(rows),
        "",
        "## Время и цена",
        *timing_table(rows),
        "",
        "## Ошибки",
        *mistakes(rows),
    ]
    for title, lines in checks.items():
        parts += ["", f"## {title}", *lines]
    out.mkdir(parents=True, exist_ok=True)
    report = out / "report.md"
    report.write_text("\n".join(parts) + "\n", encoding="utf-8")
    with (out / "raw.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return report


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    api_key = settings.ai_gateway_api_key.get_secret_value()
    if not api_key:
        raise SystemExit("AI_GATEWAY_API_KEY is not set (.env)")
    models = [settings.jev_model] + ([args.llm] if args.llm else [])
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60.0)) as http:
        gw = Gateway(http, settings.ai_gateway_url, args.concurrency)
        rows = await run_main(gw, models)
        checks: dict[str, list[str]] = {}
        if args.checks:
            jev = settings.jev_model
            checks["zeroDataRetention (Jev)"] = await check_zdr(gw, jev)
            checks["Лимиты (Jev)"] = await check_limits(gw, jev)
            checks["Ключи вопросов и вариантов (Jev)"] = await check_keys(gw, jev)
            checks["Формат ответа score (Jev)"] = await check_score(gw, jev)
            if args.llm:
                checks[f"reasoning в chat completions ({args.llm})"] = await check_reasoning(
                    gw, args.llm
                )
    report = write_report(Path(args.out), rows, models, checks)
    total = sum(r.correct for r in rows)
    print(f"{pct(total, len(rows))} correct; report: {report}")


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--llm", default="openai/gpt-6-luna", help="LLM to compare ('' = none)")
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--no-checks", dest="checks", action="store_false")
    asyncio.run(main(parser.parse_args()))


if __name__ == "__main__":
    cli()
