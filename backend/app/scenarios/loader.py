"""Scenario files (YAML / JSON) → the `scenarios` table, with versions.

The same checks run here and in the admin editor: `parse_definition` (format) and
`check_definition` (references, start threshold, languages of the tenant). An error
stops the file; warnings (a text missing in one of the tenant's languages…) are returned.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_settings import TenantSettings
from app.db.models import Channel, Scenario, Tenant
from app.errors import AppError
from app.scenarios.schema import ScenarioDef, check_definition, parse_definition
from app.scenarios.store import enable_in_channel, save_scenario

SUFFIXES = (".yaml", ".yml", ".json")


class ScenarioYamlLoader(yaml.SafeLoader):
    """YAML 1.1 reads `on`, `off`, `yes`, `no` as booleans — and the scenario format has
    keys `on:` (actions) and `yes:` / `no:` (filters). Only true / false are booleans."""


ScenarioYamlLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
ScenarioYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def parse_yaml(text: str) -> Any:
    # a SafeLoader subclass: no arbitrary objects
    return yaml.load(text, Loader=ScenarioYamlLoader)


@dataclass
class LoadResult:
    path: str
    key: str | None = None
    version: int | None = None
    created: bool = False
    warnings: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None


def read_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text) if path.suffix == ".json" else parse_yaml(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise AppError("scenario.file_invalid", {"path": str(path), "error": str(exc)}) from exc


def prepare(data: Any, languages: list[str]) -> tuple[ScenarioDef, list[dict[str, Any]]]:
    defn = parse_definition(data)
    return defn, check_definition(defn, languages)


def scenario_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.iterdir() if p.is_file() and p.suffix in SUFFIXES)


def tenant_languages(tenant: Tenant) -> list[str]:
    return TenantSettings.model_validate(tenant.settings or {}).languages


async def load_path(
    session: AsyncSession,
    tenant: Tenant,
    path: Path,
    *,
    author: str | None = None,
    note: str | None = None,
) -> list[LoadResult]:
    """Every file in its own savepoint: a broken file does not stop the others."""
    languages = tenant_languages(tenant)
    out = []
    for file in scenario_files(path):
        result = LoadResult(path=str(file))
        try:
            defn, result.warnings = prepare(read_file(file), languages)
            async with session.begin_nested():
                row, result.created = await save_scenario(
                    session, tenant.id, defn, author=author, note=note
                )
            result.key, result.version = row.key, row.version
        except AppError as exc:
            result.error = exc.to_dict()
        out.append(result)
    return out


async def tenant_channels(session: AsyncSession, tenant_id: uuid.UUID) -> list[Channel]:
    return list(
        (
            await session.execute(
                select(Channel).where(Channel.tenant_id == tenant_id).order_by(Channel.created_at)
            )
        ).scalars()
    )


async def enable_loaded(
    session: AsyncSession,
    tenant: Tenant,
    results: list[LoadResult],
    *,
    channel_id: uuid.UUID | None = None,
) -> dict[str, list[str]]:
    """Switch the loaded scenarios on: in one channel, or in all the tenant's channels.
    Returns channel id → keys added."""
    keys = [r.key for r in results if r.key is not None]
    if channel_id is not None:
        channel = await session.get(Channel, channel_id)
        if channel is None or channel.tenant_id != tenant.id:
            raise AppError("channel.not_found", {"id": str(channel_id)}, status=404)
        channels = [channel]
    else:
        channels = await tenant_channels(session, tenant.id)
    added = {}
    for channel in channels:
        added[str(channel.id)] = await enable_in_channel(
            session, tenant_id=tenant.id, channel_id=channel.id, keys=keys
        )
    return added


async def active_scenarios(session: AsyncSession, tenant_id: uuid.UUID) -> list[Scenario]:
    return list(
        (
            await session.execute(
                select(Scenario)
                .where(Scenario.tenant_id == tenant_id, Scenario.is_active)
                .order_by(Scenario.key)
            )
        ).scalars()
    )
