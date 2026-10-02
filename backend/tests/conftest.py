"""Shared fixtures.

DB tests run on a real PostgreSQL (`TEST_DATABASE_URL`, default zama_test on :5442):
the lease and SKIP LOCKED need separate connections, so there is no rollback fixture —
tables are truncated before every test instead. Without Postgres those tests skip.

The LLM is a FakeChatBackend: scripted answers, no network. Settings never read `.env`
(`_env_file=None`), so the real Gateway key is never used by tests.
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.clock import ManualClock
from app.core.simple_pipeline import simple_pipeline
from app.db.base import Base
from app.db.session import make_engine, make_sessionmaker
from app.main import create_app
from app.search.base import SearchClients
from app.services import Services
from app.settings import PROJECT_ROOT, Settings


def pytest_configure(config: pytest.Config) -> None:
    # tmp_path lives in the project's tmp/, never in the system /tmp
    if not config.option.basetemp:
        config.option.basetemp = str(PROJECT_ROOT / "tmp" / "pytest")


class FakeChatBackend:
    """ChatBackend for tests. Each chat() call takes the next scripted item:
    a raw response dict, an exception (raised) or a callable(request) → dict.
    With the script empty, `default` answers; without it the call fails the test."""

    def __init__(self) -> None:
        self.script: deque[Any] = deque()
        self.default: Any = None
        self.requests: list[dict[str, Any]] = []
        self.timeouts: list[float | None] = []
        self.embed_calls: list[tuple[str, list[str]]] = []
        self.embed_dim = 8

    def push(self, *items: Any) -> "FakeChatBackend":
        self.script.extend(items)
        return self

    async def chat(self, request: dict[str, Any], *, timeout: float | None) -> dict[str, Any]:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.script:
            item = self.script.popleft()
        elif self.default is not None:
            item = self.default
        else:
            raise AssertionError(f"unexpected LLM call to {request.get('model')}")
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(request)
            if asyncio.iscoroutine(item):
                item = await item
        return item

    async def embed(self, model: str, inputs: list[str], *, timeout: float | None) -> dict:
        self.embed_calls.append((model, list(inputs)))
        return {
            "data": [
                {"index": i, "embedding": [float(len(t) % 7)] * self.embed_dim}
                for i, t in enumerate(inputs)
            ],
            "usage": {"prompt_tokens": sum(len(t.split()) for t in inputs)},
        }

    async def close(self) -> None:
        return None

    @property
    def calls(self) -> int:
        return len(self.requests)

    def models_called(self) -> list[str]:
        return [r["model"] for r in self.requests]


def chat_reply(
    content: str,
    *,
    tokens_in: int = 100,
    tokens_out: int = 20,
    cached: int = 0,
    cost: float | None = None,
    gen_id: str = "gen-test",
) -> dict[str, Any]:
    """Raw chat-completions response as the Gateway returns it."""
    raw: dict[str, Any] = {
        "id": gen_id,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }
    if cost is not None:
        raw["usage"]["cost"] = cost
    return raw


# --- settings / clock -----------------------------------------------------------------


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        secret_key=Fernet.generate_key().decode(),
        ai_gateway_api_key="test-key",
        http_lease_wait_seconds=2.0,
        lease_seconds=90,
    )


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def fake_llm() -> FakeChatBackend:
    return FakeChatBackend()


# --- database ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def db_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(settings, settings.test_database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # no Postgres → DB tests are skipped, unit tests still run
        await engine.dispose()
        pytest.skip(f"test database unavailable: {type(exc).__name__}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def clean_db(db_engine: AsyncEngine) -> AsyncEngine:
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with db_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return db_engine


@pytest.fixture
def sm(clean_db: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(clean_db)


@pytest.fixture
async def services(
    clean_db: AsyncEngine,
    settings: Settings,
    clock: ManualClock,
    fake_llm: FakeChatBackend,
) -> AsyncIterator[Services]:
    svc = Services.create(
        settings,
        clock=clock,
        backend=fake_llm,
        database_url=settings.test_database_url,
        llm_retry_delay_s=0,
    )
    # stage 0 pipeline; tests of the full pipeline set their own
    svc.extra["pipeline"] = simple_pipeline
    # no Elasticsearch / Qdrant in tests: search tests put fakes here (search_fakes.py)
    svc.extra["search"] = SearchClients()
    try:
        yield svc
    finally:
        await svc.close()


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def run_sql(sm: async_sessionmaker[AsyncSession]) -> Callable[..., Any]:
    """`await run_sql("SELECT …", key=value)` → list of row mappings."""

    async def go(sql: str, **params: Any) -> list[dict[str, Any]]:
        async with sm() as session, session.begin():
            result = await session.execute(text(sql), params)
            return [dict(r) for r in result.mappings().all()] if result.returns_rows else []

    return go
