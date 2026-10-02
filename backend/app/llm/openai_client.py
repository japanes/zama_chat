"""Thin client over the official `openai` SDK pointed at the Vercel AI Gateway
(base_url is a setting: switching to OpenAI directly needs no code change).

`ChatBackend` is the seam tests replace with a fake: it takes a plain request and
returns the raw response dict.
"""

from typing import Any, Protocol

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI


class LLMTransportError(Exception):
    """Network / timeout / 5xx / 429 — worth a retry or a fallback model."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LLMRequestError(Exception):
    """4xx (bad request, unknown model…) — a retry will not help."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ChatBackend(Protocol):
    async def chat(self, request: dict[str, Any], *, timeout: float) -> dict[str, Any]: ...

    async def embed(self, model: str, inputs: list[str], *, timeout: float) -> dict[str, Any]: ...


class OpenAIGatewayBackend:
    def __init__(self, base_url: str, api_key: str, http: httpx.AsyncClient | None = None) -> None:
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "missing",
            max_retries=0,  # retries are ours (LLMService), with logging per attempt
            http_client=http,
        )

    async def chat(self, request: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        request = dict(request)
        extra_body: dict[str, Any] = {}
        # Gateway-only fields go through extra_body (not in the SDK types)
        for key in ("reasoning", "providerOptions"):
            if key in request:
                extra_body[key] = request.pop(key)
        try:
            completion = await self.client.chat.completions.create(
                **request, extra_body=extra_body or None, timeout=timeout
            )
        except (APITimeoutError, APIConnectionError) as exc:
            raise LLMTransportError(type(exc).__name__) from exc
        except APIStatusError as exc:
            message = _error_message(exc)
            if exc.status_code in (408, 409, 429) or exc.status_code >= 500:
                raise LLMTransportError(message, status=exc.status_code) from exc
            raise LLMRequestError(message, status=exc.status_code) from exc
        return completion.model_dump(mode="json", exclude_none=False)

    async def embed(self, model: str, inputs: list[str], *, timeout: float) -> dict[str, Any]:
        try:
            result = await self.client.embeddings.create(model=model, input=inputs, timeout=timeout)
        except (APITimeoutError, APIConnectionError) as exc:
            raise LLMTransportError(type(exc).__name__) from exc
        except APIStatusError as exc:
            message = _error_message(exc)
            if exc.status_code in (408, 409, 429) or exc.status_code >= 500:
                raise LLMTransportError(message, status=exc.status_code) from exc
            raise LLMRequestError(message, status=exc.status_code) from exc
        return result.model_dump(mode="json")

    async def close(self) -> None:
        await self.client.close()


def _error_message(exc: APIStatusError) -> str:
    try:
        body = exc.response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return f"{exc.status_code}: {err['message']}"[:500]
    except Exception:
        pass
    return f"{exc.status_code}: {exc.message}"[:500]
