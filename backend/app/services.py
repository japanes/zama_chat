"""Process-wide services: one engine, one httpx client, one LLM client.

Created once in the API lifespan, the worker and the CLI. Tests build it with a
ManualClock and fake backends.
"""

from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.channels.base import ChannelAdapter
from app.channels.http import HttpChannel
from app.core.clock import Clock, SystemClock
from app.crypto import Crypto
from app.db.models import ChannelKind
from app.db.session import make_engine, make_sessionmaker
from app.llm.gateway_catalog import GatewayCatalog
from app.llm.openai_client import ChatBackend, OpenAIGatewayBackend
from app.llm.service import LLMService
from app.settings import Settings, get_settings


@dataclass
class Services:
    settings: Settings
    clock: Clock
    engine: AsyncEngine
    sm: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    backend: ChatBackend
    llm: LLMService
    catalog: GatewayCatalog
    crypto: Crypto
    channels: dict[str, ChannelAdapter] = field(default_factory=dict)
    # filled by later stages (decider, search…); kept loose to avoid import cycles
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        settings: Settings | None = None,
        *,
        clock: Clock | None = None,
        backend: ChatBackend | None = None,
        http: httpx.AsyncClient | None = None,
        database_url: str | None = None,
        llm_retry_delay_s: float = 1.0,
    ) -> "Services":
        settings = settings or get_settings()
        clock = clock or SystemClock()
        engine = make_engine(settings, database_url)
        sm = make_sessionmaker(engine)
        http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        api_key = settings.ai_gateway_api_key.get_secret_value()
        backend = backend or OpenAIGatewayBackend(settings.ai_gateway_url, api_key)
        return cls(
            settings=settings,
            clock=clock,
            engine=engine,
            sm=sm,
            http=http,
            backend=backend,
            llm=LLMService(sm, backend, clock, retry_delay_s=llm_retry_delay_s),
            catalog=GatewayCatalog(http, settings.ai_gateway_url, api_key),
            crypto=Crypto(settings.secret_key.get_secret_value()),
            channels={ChannelKind.HTTP: HttpChannel()},
        )

    def channel_adapter(self, kind: str) -> ChannelAdapter | None:
        return self.channels.get(kind)

    async def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if close is not None:
            await close()
        await self.http.aclose()
        await self.engine.dispose()
