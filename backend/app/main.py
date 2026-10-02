"""FastAPI app: routers, error format, lifespan with process-wide services."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import admin, dev
from app.api.channels import instagram
from app.api.v1 import events, messages
from app.errors import AppError
from app.logging import get_logger, setup_logging
from app.services import Services
from app.settings import get_settings

log = get_logger(__name__)


def create_app(services: Services | None = None) -> FastAPI:
    """`services` is passed by tests (fake LLM, manual clock); the API process
    creates its own in the lifespan."""
    settings = services.settings if services is not None else get_settings()
    setup_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        own = services is None
        app.state.services = services if services is not None else Services.create(settings)
        try:
            yield
        finally:
            if own:
                await app.state.services.close()

    app = FastAPI(title="zama_chat", version="0.1.0", lifespan=lifespan)
    if services is not None:
        # test clients (httpx.ASGITransport) do not run the lifespan
        app.state.services = services

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status >= 500:
            log.error("api.app_error", code=exc.code, params=exc.params)
        return JSONResponse({"error": exc.to_dict()}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"loc": [str(p) for p in err.get("loc", ())], "type": err.get("type", "")}
            for err in exc.errors()
        ]
        return JSONResponse(
            {"error": {"code": "request.invalid", "params": {"fields": fields}}}, status_code=422
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(messages.router)
    app.include_router(events.router)
    app.include_router(instagram.router)
    for router in admin.routers:
        app.include_router(router)
    app.include_router(dev.router)
    return app


app = create_app()
