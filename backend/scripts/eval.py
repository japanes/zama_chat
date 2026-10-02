"""`make eval`: replay recorded dialogs on the real Jev and LLMs, write a report to read.

    make eval                                   # every eval/dialogs/*.yaml
    ONLY=interview make eval                    # files whose name contains "interview"
    MODEL_REPLY=openai/gpt-6-luna EFFORT=low make eval
    SHADOW=1 make eval                          # Jev + LLM on every question → agreement
    DECIDER=llm make eval                       # the LLM answers every question alone

Environment (only for this run; the settings in the DB stay as they are):
    MODEL=<name>            every LLM role (the model must be added: `zama models add`)
    MODEL_<ROLE>=<name>     one role: REPLY, REWRITE, EXTRACT, DECIDE, VISION
                            (not EMBED: another embedding model needs a reindex)
    EFFORT=<level>|off      reasoning effort of every role; EFFORT_<ROLE> for one
    SHADOW=1                = DECIDER=shadow
    DECIDER=jev|llm|shadow  who answers the decision questions (thresholds of the rules stay)
    ONLY=<text>             only the dialog files whose name contains it

Every dialog gets its own tenant `eval-<file name>` (created on the first run, settings
from the file every run) and a fresh conversation per run, marked sandbox: nothing is sent
anywhere, no actions or timers. Needs `make seed` (prompts) and models on the roles.

Dialog file (eval/dialogs/*.yaml):
    name: …
    settings: {languages: [uk, en], reply_language: {ru: uk, "*": en}, business_description: …}
    scenarios: [offer_interview]              # <project>/scenarios/<key>.yaml
    messages:
      - text: Привіт!
        expect:                               # all optional
          decisions: {service_type: greeting_only}     # the used value of a question
          scenario: {key: offer_interview, status: focus, asking: niche,
                     fields: {niche: filled}}
          no_scenario: true                   # no open scenario run
          handover: true
          reply_language: uk

Output: <project>/tmp/eval/<time>/report.md and report.json — per turn: the client's text,
the rewritten text, every decision (value, confidence, who answered; Jev vs the LLM when
both did), the scenario state, the reply, time and cost per role, ✓/✗ of the expectations.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select, update

from app.admin.tenants import create_tenant
from app.core.ingest import save_client_message
from app.core.tenant_settings import TenantSettings
from app.core.turn import run_turn
from app.db.models import (
    OPEN_STATUSES,
    ChannelScenario,
    Conversation,
    Decision,
    DecisionProvider,
    LlmCall,
    LlmRole,
    PolicyMode,
    ScenarioRun,
    Tenant,
    Turn,
)
from app.llm.service import Overrides
from app.logging import setup_logging
from app.scenarios.loader import load_path, tenant_channels
from app.scenarios.store import enable_in_channel
from app.services import Services
from app.settings import PROJECT_ROOT, get_settings

DIALOGS = PROJECT_ROOT / "eval" / "dialogs"
SCENARIOS = PROJECT_ROOT / "scenarios"
OUT = PROJECT_ROOT / "tmp" / "eval"
TRUE = {"1", "true", "yes", "on"}


# --- settings from the environment ---------------------------------------------------------


def _effort(value: str | None) -> str | None:
    if value is None:
        return None
    return "" if value.strip().lower() in {"off", "none", ""} else value.strip()


def overrides_from_env(env: dict[str, str]) -> Overrides | None:
    roles = [r for r in LlmRole if r != LlmRole.EMBED]
    models = {r.value: env[f"MODEL_{r.name}"] for r in roles if env.get(f"MODEL_{r.name}")}
    efforts = {r.value: e for r in roles if (e := _effort(env.get(f"EFFORT_{r.name}"))) is not None}
    mode = env.get("DECIDER") or None
    if env.get("SHADOW", "").lower() in TRUE:
        mode = PolicyMode.SHADOW
    if mode is not None and mode not in set(PolicyMode):
        raise SystemExit(f"DECIDER must be one of {', '.join(PolicyMode)}, not {mode!r}")
    overrides = Overrides(
        model=env.get("MODEL") or None,
        reasoning_effort=_effort(env.get("EFFORT")),
        models=models,
        efforts=efforts,
        decision_mode=str(mode) if mode else None,
    )
    if overrides == Overrides():
        return None
    return overrides


# --- dialogs -------------------------------------------------------------------------------


@dataclass
class Dialog:
    path: Path
    name: str
    settings: dict[str, Any]
    scenarios: list[str]
    messages: list[dict[str, Any]]

    @property
    def slug(self) -> str:
        stem = "".join(c if c.isalnum() else "-" for c in self.path.stem.lower()).strip("-")
        return f"eval-{stem}"[:64].rstrip("-")


def read_dialogs(folder: Path, only: str | None) -> list[Dialog]:
    out = []
    for path in sorted(folder.glob("*.yaml")):
        if only and only not in path.name:
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        messages = data.get("messages") or []
        if not messages:
            raise SystemExit(f"{path}: no messages")
        for m in messages:
            if not isinstance(m, dict) or not str(m.get("text") or "").strip():
                raise SystemExit(f"{path}: every message needs `text`")
        out.append(
            Dialog(
                path=path,
                name=str(data.get("name") or path.stem),
                settings=dict(data.get("settings") or {}),
                scenarios=list(data.get("scenarios") or []),
                messages=messages,
            )
        )
    return out


async def prepare_tenant(services: Services, dialog: Dialog) -> tuple[uuid.UUID, uuid.UUID]:
    """The dialog's tenant (settings from the file) and its HTTP channel with exactly the
    dialog's scenarios switched on, in the file's order."""
    settings = TenantSettings(**{"company_title": dialog.name, **dialog.settings}).validate_rules()
    async with services.sm() as session, session.begin():
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == dialog.slug))
        ).scalar_one_or_none()
        if tenant is None:
            tenant, channel = await create_tenant(
                session, slug=dialog.slug, title=f"Eval · {dialog.name}"[:200], settings=settings
            )
        else:
            tenant.settings = settings.model_dump(mode="json")
            channel = (await tenant_channels(session, tenant.id))[0]
        await session.flush()
        for key in dialog.scenarios:
            file = SCENARIOS / f"{key}.yaml"
            if not file.exists():
                raise SystemExit(f"{dialog.path}: no scenario file {file}")
            for result in await load_path(session, tenant, file, author="eval"):
                if result.error is not None:
                    raise SystemExit(f"{file}: {result.error}")
        await session.execute(
            delete(ChannelScenario).where(ChannelScenario.channel_id == channel.id)
        )
        await enable_in_channel(
            session, tenant_id=tenant.id, channel_id=channel.id, keys=dialog.scenarios
        )
        return tenant.id, channel.id


# --- one turn ------------------------------------------------------------------------------


@dataclass
class TurnReport:
    text: str
    rewritten: str | None = None
    reply: str | None = None
    reply_language: str | None = None
    handover: bool = False
    error: str | None = None
    duration_ms: int | None = None
    cost: Decimal = Decimal(0)
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)


async def play_turn(
    services: Services,
    tenant_id: uuid.UUID,
    channel_id: uuid.UUID,
    contact: str,
    text: str,
    overrides: Overrides | None,
) -> tuple[TurnReport, uuid.UUID]:
    report = TurnReport(text=text)
    async with services.sm() as session, session.begin():
        ingested = await save_client_message(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            contact_external_id=contact,
            contact_name="Eval",
            text=text,
            now=services.clock.now(),
        )
        await session.execute(
            update(Conversation)
            .where(Conversation.id == ingested.conversation_id)
            .values(sandbox=True)
        )
    started = time.monotonic()
    outcome = None
    try:
        outcome = await run_turn(
            services,
            ingested.conversation_id,
            owner=f"eval:{uuid.uuid4().hex}",
            wait_seconds=services.settings.http_lease_wait_seconds,
            only_message_id=ingested.message_id,
            sandbox=True,
            overrides=overrides,
        )
    except Exception as exc:  # the report shows it; the next dialogs still run
        report.error = f"{type(exc).__name__}: {exc}"[:500]
    report.duration_ms = int((time.monotonic() - started) * 1000)
    if outcome is not None:
        report.reply = outcome.reply_text
        report.handover = outcome.handover
    await fill_turn(services, report, ingested.conversation_id, outcome)
    return report, ingested.conversation_id


async def fill_turn(
    services: Services, report: TurnReport, conversation_id: uuid.UUID, outcome: Any
) -> None:
    async with services.sm() as session:
        if outcome is not None:
            turn = await session.get(Turn, outcome.turn_id)
        else:  # the turn failed: its trace is still worth reading
            turn = (
                await session.execute(
                    select(Turn)
                    .where(Turn.conversation_id == conversation_id)
                    .order_by(Turn.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if turn is not None:
            report.rewritten = turn.rewritten
            report.reply_language = turn.reply_language
            report.error = report.error or turn.error
            calls = (
                await session.execute(select(LlmCall).where(LlmCall.turn_id == turn.id))
            ).scalars()
            roles: dict[str, dict[str, Any]] = defaultdict(
                lambda: {"calls": 0, "ms": 0, "cost": Decimal(0), "models": set()}
            )
            for c in calls:
                r = roles[c.role]
                r["calls"] += 1
                r["ms"] += c.latency_ms
                r["cost"] += c.cost or Decimal(0)
                r["models"].add(c.model)
            decisions = list(
                (
                    await session.execute(
                        select(Decision)
                        .where(Decision.turn_id == turn.id)
                        .order_by(Decision.created_at)
                    )
                ).scalars()
            )
            batches: dict[uuid.UUID, int] = {}
            for d in decisions:
                if d.provider == DecisionProvider.JEV:
                    r = roles["jev"]
                    r["cost"] += d.cost or Decimal(0)
                    r["models"].add(d.model)
                    batches[d.batch_id] = max(batches.get(d.batch_id, 0), d.latency_ms or 0)
            if batches:
                roles["jev"]["calls"] = len(batches)
                roles["jev"]["ms"] = sum(batches.values())
            report.roles = {
                k: {**v, "models": sorted(v["models"])} for k, v in sorted(roles.items())
            }
            report.cost = sum((v["cost"] for v in roles.values()), Decimal(0))
            report.decisions = decision_view(decisions)
        runs = (
            await session.execute(
                select(ScenarioRun)
                .where(ScenarioRun.conversation_id == conversation_id)
                .order_by(ScenarioRun.started_at)
            )
        ).scalars()
        report.scenarios = [
            {
                "key": r.scenario_key,
                "status": r.status,
                "asking": r.asking,
                "fields": {
                    k: {"state": (v or {}).get("state"), "value": (v or {}).get("value")}
                    for k, v in (r.fields or {}).items()
                },
            }
            for r in runs
        ]


def decision_view(rows: list[Decision]) -> list[dict[str, Any]]:
    """Per question: the used answer and, when both answered, Jev's and the LLM's."""
    by_key: dict[str, dict[str, Any]] = {}
    for d in rows:
        item = by_key.setdefault(d.key, {"key": d.key})
        side = {
            "value": (d.answer or {}).get("value"),
            "confidence": round(d.confidence, 3) if d.confidence is not None else None,
            "reason": d.reason,
            "used": d.used,
        }
        item[d.provider] = side
        if d.used:
            item["value"] = side["value"]
            item["by"] = d.provider
        if d.reason == "unsure":
            item["unsure"] = True
    for item in by_key.values():
        jev, llm = item.get("jev"), item.get("llm")
        if jev and llm and jev.get("value") is not None and llm.get("value") is not None:
            item["agree"] = jev["value"] == llm["value"]
    return list(by_key.values())


# --- expectations --------------------------------------------------------------------------


def check(expect: dict[str, Any], report: TurnReport) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(what: str, want: Any, got: Any) -> None:
        out.append({"what": what, "want": want, "got": got, "ok": want == got})

    used = {d["key"]: d.get("value") for d in report.decisions}
    for key, want in (expect.get("decisions") or {}).items():
        add(f"decision {key}", want, used.get(key))
    if "handover" in expect:
        add("handover", bool(expect["handover"]), report.handover)
    if "reply_language" in expect:
        add("reply_language", expect["reply_language"], report.reply_language)
    open_runs = [s for s in report.scenarios if s["status"] in OPEN_STATUSES]
    if expect.get("no_scenario"):
        add("no open scenario", [], [s["key"] for s in open_runs])
    want_run = expect.get("scenario")
    if want_run:
        key = want_run.get("key")
        run = next((s for s in reversed(report.scenarios) if s["key"] == key), None)
        if run is None:
            add(f"scenario {key}", "started", None)
        else:
            for name in ("status", "asking"):
                if name in want_run:
                    add(f"{key}.{name}", want_run[name], run[name])
            for fk, state in (want_run.get("fields") or {}).items():
                add(f"{key}.{fk}", state, (run["fields"].get(fk) or {}).get("state"))
    return out


# --- report --------------------------------------------------------------------------------


def _v(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _side(side: dict[str, Any] | None) -> str:
    if not side:
        return "—"
    mark = "" if side.get("used") else " (not used)"
    return f"{_v(side.get('value'))} · {side.get('confidence')}{mark}"


def markdown(run: dict[str, Any]) -> str:
    lines = [f"# Eval {run['started_at']}", ""]
    lines.append(f"Overrides: `{json.dumps(run['overrides'], ensure_ascii=False)}`")
    lines.append("")
    lines.append("| Dialog | Turns | Checks | Errors | Time | Cost | Jev vs LLM |")
    lines.append("|---|---|---|---|---|---|---|")
    for d in run["dialogs"]:
        lines.append(
            f"| {d['name']} | {len(d['turns'])} | {d['passed']}/{d['checks']} | "
            f"{d['errors']} | {d['ms'] / 1000:.1f} s | ${d['cost']} | "
            f"{d['agree']}/{d['pairs']} |"
        )
    lines.append("")
    disagreements = []
    for d in run["dialogs"]:
        lines += ["", f"## {d['name']}", "", f"`{d['file']}`", ""]
        for n, t in enumerate(d["turns"], start=1):
            ok = all(c["ok"] for c in t["checks"]) if t["checks"] else None
            mark = "" if ok is None else (" ✓" if ok else " ✗")
            lines.append(f"### {n}. {t['text']}{mark}")
            lines.append("")
            if t["error"]:
                lines.append(f"**Error:** `{t['error']}`")
                lines.append("")
            lines.append(f"- rewritten: {t['rewritten'] or '—'}")
            lines.append(f"- reply ({t['reply_language'] or '—'}): {t['reply'] or '—'}")
            if t["handover"]:
                lines.append("- **handover** to a person")
            roles = ", ".join(f"{k} {v['ms']} ms ${v['cost']}" for k, v in t["roles"].items())
            lines.append(f"- time {t['duration_ms']} ms, cost ${t['cost']} ({roles or '—'})")
            for s in t["scenarios"]:
                states = ", ".join(f"{k}={v['state']}" for k, v in s["fields"].items())
                lines.append(
                    f"- scenario `{s['key']}` {s['status']}, asking {s['asking'] or '—'}"
                    f"{': ' + states if states else ''}"
                )
            if t["decisions"]:
                lines += ["", "| Question | Used | Jev | LLM |", "|---|---|---|---|"]
                for q in t["decisions"]:
                    flag = " ⚠" if q.get("agree") is False else ""
                    unsure = " (unsure)" if q.get("unsure") else ""
                    lines.append(
                        f"| `{q['key']}`{flag} | {_v(q.get('value'))} by {q.get('by', '—')}"
                        f"{unsure} | {_side(q.get('jev'))} | {_side(q.get('llm'))} |"
                    )
                    if q.get("agree") is False:
                        disagreements.append((d["name"], n, t["rewritten"] or t["text"], q))
            if t["checks"]:
                lines.append("")
                for c in t["checks"]:
                    sign = "✓" if c["ok"] else "✗"
                    lines.append(
                        f"- {sign} {c['what']}: want `{_v(c['want'])}`, got `{_v(c['got'])}`"
                    )
            lines.append("")
    if disagreements:
        lines += ["", "## Jev vs LLM: disagreements", ""]
        lines += ["| Dialog | Turn | Text | Question | Jev | LLM |", "|---|---|---|---|---|---|"]
        for name, n, text, q in disagreements:
            lines.append(
                f"| {name} | {n} | {text} | `{q['key']}` | {_side(q.get('jev'))} | "
                f"{_side(q.get('llm'))} |"
            )
    return "\n".join(lines) + "\n"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(type(value).__name__)


# --- main ----------------------------------------------------------------------------------


async def play(services: Services, dialog: Dialog, overrides: Overrides | None) -> dict:
    tenant_id, channel_id = await prepare_tenant(services, dialog)
    contact = f"eval-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    turns = []
    for message in dialog.messages:
        report, _ = await play_turn(
            services, tenant_id, channel_id, contact, str(message["text"]).strip(), overrides
        )
        report.checks = check(message.get("expect") or {}, report)
        turns.append(report)
        print(f"  {'✗' if report.error else '·'} {report.text[:60]}", flush=True)
    pairs = [q for t in turns for q in t.decisions if "agree" in q]
    checks = [c for t in turns for c in t.checks]
    return {
        "name": dialog.name,
        "file": str(dialog.path.relative_to(PROJECT_ROOT)),
        "turns": [asdict(t) for t in turns],
        "checks": len(checks),
        "passed": sum(c["ok"] for c in checks),
        "errors": sum(1 for t in turns if t.error),
        "ms": sum(t.duration_ms or 0 for t in turns),
        "cost": sum((t.cost for t in turns), Decimal(0)),
        "pairs": len(pairs),
        "agree": sum(1 for q in pairs if q["agree"]),
    }


async def main() -> int:
    settings = get_settings()
    # only warnings and errors: the progress lines must stay readable
    setup_logging(os.environ.get("LOG_LEVEL") or "WARNING", json=settings.log_json)
    env = dict(os.environ)
    overrides = overrides_from_env(env)
    dialogs = read_dialogs(DIALOGS, env.get("ONLY") or None)
    if not dialogs:
        print(f"no dialogs in {DIALOGS}", file=sys.stderr)
        return 1
    started = datetime.now(UTC)
    out_dir = OUT / f"{started:%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    services = Services.create(settings)
    run: dict[str, Any] = {
        "started_at": started.isoformat(timespec="seconds"),
        "overrides": asdict(overrides) if overrides else None,
        "dialogs": [],
    }
    try:
        for dialog in dialogs:
            print(f"{dialog.name} ({dialog.path.name})", flush=True)
            run["dialogs"].append(await play(services, dialog, overrides))
    finally:
        await services.close()
    (out_dir / "report.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=1, default=_jsonable), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(markdown(run), encoding="utf-8")
    passed = sum(d["passed"] for d in run["dialogs"])
    total = sum(d["checks"] for d in run["dialogs"])
    print(f"checks {passed}/{total} → {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
