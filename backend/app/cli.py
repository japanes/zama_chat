"""`zama` command line: tenants, API keys, models and the worker.

Writes to the same tables the admin edits later.
"""

import asyncio
import signal
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Annotated, Any

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import API_KEY_PREFIX
from app.core.tenant_settings import TenantSettings
from app.crypto import new_token, sha256_hex
from app.db.models import ApiKey, Channel, ChannelKind, LlmModel, LlmRole, ModelRole, Tenant
from app.errors import AppError
from app.llm.registry import assign_role, get_model
from app.logging import setup_logging
from app.services import Services
from app.settings import get_settings

app = typer.Typer(no_args_is_help=True, help="zama_chat admin commands")
models_app = typer.Typer(no_args_is_help=True, help="LLM models and role assignments")
app.add_typer(models_app, name="models")


def _run(fn: Callable[[Services], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        services = Services.create()
        try:
            return await fn(services)
        except AppError as exc:
            typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        finally:
            await services.close()

    return asyncio.run(main())


def _parse_rules(value: str) -> dict[str, str]:
    """'uk=uk,ru=uk,*=en' → {"uk": "uk", "ru": "uk", "*": "en"}"""
    rules: dict[str, str] = {}
    for part in filter(None, (p.strip() for p in value.split(","))):
        source, sep, target = part.partition("=")
        if not sep:
            raise typer.BadParameter(f"expected source=target, got {part!r}")
        rules[source.strip()] = target.strip()
    return rules


async def _tenant(session: AsyncSession, slug: str) -> Tenant:
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        raise AppError("tenant.not_found", {"slug": slug})
    return tenant


# --- tenants and keys ------------------------------------------------------------------


@app.command("create-tenant")
def create_tenant(
    slug: str,
    title: str,
    languages: Annotated[str, typer.Option(help="comma separated, first = main")] = "en",
    reply_language: Annotated[
        str, typer.Option(help="client → reply, e.g. 'uk=uk,ru=uk,*=en'")
    ] = "",
    business: Annotated[str, typer.Option(help="business description for the prompt")] = "",
    bot_name: str = "",
) -> None:
    """Create a tenant and its HTTP channel."""
    settings = TenantSettings(
        languages=[x.strip() for x in languages.split(",") if x.strip()],
        reply_language=_parse_rules(reply_language),
        business_description=business,
        company_title=title,
        bot_name=bot_name,
    )

    async def go(services: Services) -> None:
        settings.validate_rules()
        async with services.sm() as session, session.begin():
            exists = (
                await session.execute(select(Tenant.id).where(Tenant.slug == slug))
            ).scalar_one_or_none()
            if exists is not None:
                raise AppError("tenant.slug_taken", {"slug": slug})
            tenant = Tenant(slug=slug, title=title, settings=settings.model_dump(mode="json"))
            session.add(tenant)
            await session.flush()
            channel = Channel(tenant_id=tenant.id, kind=ChannelKind.HTTP, title="HTTP API")
            session.add(channel)
            await session.flush()
        typer.echo(f"tenant {slug}: {tenant.id}")
        typer.echo(f"http channel: {channel.id}")

    _run(go)


@app.command("create-api-key")
def create_api_key(
    tenant: Annotated[str, typer.Option(help="tenant slug")],
    channel: Annotated[
        str | None, typer.Option(help="http channel id (default: the tenant's first)")
    ] = None,
    title: str = "",
) -> None:
    """Create an API key for an HTTP channel. The key is printed once."""

    async def go(services: Services) -> None:
        async with services.sm() as session, session.begin():
            t = await _tenant(session, tenant)
            query = select(Channel).where(
                Channel.tenant_id == t.id, Channel.kind == ChannelKind.HTTP
            )
            if channel:
                query = query.where(Channel.id == uuid.UUID(channel))
            ch = (await session.execute(query.order_by(Channel.created_at))).scalars().first()
            if ch is None:
                raise AppError("channel.not_found", {"tenant": tenant})
            key = API_KEY_PREFIX + new_token(32)
            session.add(
                ApiKey(
                    tenant_id=t.id,
                    channel_id=ch.id,
                    title=title,
                    key_hash=sha256_hex(key),
                    prefix=key[:10],
                )
            )
        typer.echo(key)
        typer.secho("save it now: the key is not stored and cannot be shown again", err=True)

    _run(go)


@app.command("seed-prompts")
def seed_prompts_cmd(
    directory: Annotated[
        str | None, typer.Option(help="legacy prompts folder (default: docs/legacy-prompts)")
    ] = None,
) -> None:
    """Load the shared prompts from docs/legacy-prompts. A new version is added only for
    prompts whose text changed."""
    from pathlib import Path

    from app.prompts.seed import LEGACY_DIR, seed_prompts

    async def go(services: Services) -> None:
        async with services.sm() as session, session.begin():
            result = await seed_prompts(session, Path(directory) if directory else LEGACY_DIR)
        for key in result.created:
            typer.echo(f"new version  {key}")
        typer.echo(f"{len(result.created)} updated, {len(result.unchanged)} unchanged")

    _run(go)


# --- models ---------------------------------------------------------------------------


@models_app.command("add")
def models_add(
    name: Annotated[str, typer.Argument(help="Gateway id, e.g. openai/gpt-6-luna")],
    from_gateway: Annotated[bool, typer.Option(help="fill prices from the Gateway")] = False,
    label: str = "",
    price_input: Annotated[Decimal | None, typer.Option(help="USD per 1M tokens")] = None,
    price_output: Annotated[Decimal | None, typer.Option(help="USD per 1M tokens")] = None,
    kind: str = "language",
) -> None:
    """Add (or update) a model. Prices are USD per 1M tokens."""

    async def go(services: Services) -> None:
        data: dict[str, Any] = {"name": name, "label": label or name, "kind": kind}
        if from_gateway:
            entry = await services.catalog.get(name)
            data.update(
                label=label or entry.label,
                kind=entry.kind if entry.kind == "embedding" else "language",
                price_input=entry.price_input,
                price_cached_input=entry.price_cached_input,
                price_output=entry.price_output,
                context_window=entry.context_window,
                max_output=entry.max_output,
                supports_json_schema=entry.supports_json_schema,
                supports_reasoning=entry.supports_reasoning,
                supports_vision=entry.supports_vision,
                reasoning_efforts=entry.reasoning_efforts,
            )
        if price_input is not None:
            data["price_input"] = price_input
        if price_output is not None:
            data["price_output"] = price_output
        async with services.sm() as session, session.begin():
            model = (
                await session.execute(select(LlmModel).where(LlmModel.name == name))
            ).scalar_one_or_none()
            if model is None:
                model = LlmModel(**data)
                session.add(model)
            else:
                for key, value in data.items():
                    setattr(model, key, value)
        typer.echo(
            f"{name}: in ${data.get('price_input', 0)}/1M, out ${data.get('price_output', 0)}/1M, "
            f"json_schema={data.get('supports_json_schema', True)}, "
            f"reasoning={data.get('supports_reasoning', False)}"
        )

    _run(go)


def _roles_for(model: LlmModel) -> list[LlmRole]:
    """Roles '*' expands to: every role the model can serve."""
    if model.kind == "embedding":
        return [LlmRole.EMBED]
    roles = [LlmRole.REPLY]
    if model.supports_json_schema:
        roles += [LlmRole.REWRITE, LlmRole.EXTRACT, LlmRole.DECIDE]
    if model.supports_vision:
        roles.append(LlmRole.VISION)
    return roles


@models_app.command("assign")
def models_assign(
    role: Annotated[
        str, typer.Argument(help="reply | rewrite | extract | decide | vision | embed | *")
    ],
    model: str,
    tenant: Annotated[str | None, typer.Option(help="tenant slug (default: shared)")] = None,
    effort: Annotated[str | None, typer.Option(help="reasoning effort")] = None,
    timeout: Annotated[float | None, typer.Option(help="seconds")] = None,
    fallback: Annotated[str | None, typer.Option(help="fallback model name")] = None,
) -> None:
    """Assign a model to a role ('*' = every role the model can serve)."""

    async def go(services: Services) -> None:
        async with services.sm() as session, session.begin():
            m = await get_model(session, model)
            fb = await get_model(session, fallback) if fallback else None
            tenant_id = (await _tenant(session, tenant)).id if tenant else None
            roles = _roles_for(m) if role == "*" else [LlmRole(role)]
            for r in roles:
                await assign_role(
                    session,
                    role=r,
                    model=m,
                    tenant_id=tenant_id,
                    reasoning_effort=effort,
                    timeout_s=timeout,
                    fallback=fb,
                )
                typer.echo(f"{r.value} → {m.name}" + (f" (tenant {tenant})" if tenant else ""))

    _run(go)


@models_app.command("list")
def models_list() -> None:
    """Models with prices and the roles they serve."""

    async def go(services: Services) -> None:
        async with services.sm() as session:
            models = (await session.execute(select(LlmModel).order_by(LlmModel.name))).scalars()
            names = {}
            for m in models:
                names[m.id] = m.name
                flags = ",".join(
                    f
                    for f, on in (
                        ("json", m.supports_json_schema),
                        ("reasoning", m.supports_reasoning),
                        ("vision", m.supports_vision),
                    )
                    if on
                )
                state = "" if m.is_active else " [off]"
                typer.echo(
                    f"{m.name}{state}  in ${m.price_input} out ${m.price_output} per 1M  [{flags}]"
                )
            rows = (
                await session.execute(
                    select(ModelRole, Tenant.slug)
                    .outerjoin(Tenant, Tenant.id == ModelRole.tenant_id)
                    .order_by(ModelRole.role, Tenant.slug.nulls_first())
                )
            ).all()
        typer.echo("")
        assigned = {r.role for r, _ in rows if r.tenant_id is None}
        for r, slug in rows:
            extra = []
            if r.reasoning_effort:
                extra.append(f"effort={r.reasoning_effort}")
            if r.fallback_model_id:
                extra.append(f"fallback={names.get(r.fallback_model_id)}")
            scope = slug or "shared"
            typer.echo(f"{r.role:8} {scope:12} → {names.get(r.model_id)}  {' '.join(extra)}")
        for missing in sorted({x.value for x in LlmRole} - assigned):
            typer.secho(f"{missing:8} shared       → (no model)", fg=typer.colors.YELLOW)

    _run(go)


# --- worker ---------------------------------------------------------------------------


@app.command("worker")
def worker(concurrency: int = 8) -> None:
    """Run the jobs worker until SIGINT / SIGTERM."""
    from app.jobs.tasks import HANDLERS
    from app.jobs.worker import Worker

    settings = get_settings()
    setup_logging(settings.log_level, json=settings.log_json)

    async def main() -> None:
        services = Services.create(settings)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            await Worker(
                services.sm,
                services.clock,
                HANDLERS,
                services=services,
                concurrency=concurrency,
            ).run_forever(stop, settings.database_url)
        finally:
            await services.close()

    asyncio.run(main())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
