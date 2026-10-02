"""Dev-only receiver of result JSON: point an endpoint at
`http://localhost:8090/dev/sink/<name>` and see what the bot sends, without an external
service. Off (404) when env = prod.

  POST   /dev/sink/{name}?respond_status=500   store the body; answer with that status
                                              (to try retries)
  GET    /dev/sink/{name}                      what came, newest first
  DELETE /dev/sink/{name}                      clear
"""

import json
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select

from app.api.deps import ServicesDep
from app.db.models import DevSinkItem
from app.errors import NotFound
from app.results.delivery import EVENT_HEADER, IDEMPOTENCY_HEADER, SIGNATURE_HEADER

router = APIRouter(prefix="/dev", tags=["dev"])

KEPT_HEADERS = (IDEMPOTENCY_HEADER, SIGNATURE_HEADER, EVENT_HEADER, "Content-Type")
LIST_LIMIT = 200


def _check_dev(services: ServicesDep) -> None:
    if not services.settings.is_dev:
        raise NotFound("not_found")


@router.post("/sink/{name}")
async def sink_post(
    name: str,
    request: Request,
    services: ServicesDep,
    respond_status: int = Query(200, ge=200, le=599),
) -> JSONResponse:
    _check_dev(services)
    raw = await request.body()
    try:
        body: Any = json.loads(raw) if raw else {}
    except ValueError:
        body = {"_raw": raw.decode("utf-8", "replace")}
    if not isinstance(body, dict):
        body = {"_value": body}
    headers = {h: request.headers[h] for h in KEPT_HEADERS if h in request.headers}
    async with services.sm() as session, session.begin():
        item = DevSinkItem(name=name[:100], body=body, headers=headers)
        session.add(item)
        await session.flush()
        item_id = str(item.id)
    return JSONResponse({"ok": respond_status < 300, "id": item_id}, status_code=respond_status)


@router.get("/sink/{name}")
async def sink_list(name: str, services: ServicesDep) -> dict[str, Any]:
    _check_dev(services)
    async with services.sm() as session:
        rows = (
            await session.execute(
                select(DevSinkItem)
                .where(DevSinkItem.name == name)
                .order_by(DevSinkItem.created_at.desc(), DevSinkItem.id.desc())
                .limit(LIST_LIMIT)
            )
        ).scalars()
        items = [
            {
                "id": str(r.id),
                "received_at": r.created_at.isoformat(),
                "headers": r.headers,
                "body": r.body,
            }
            for r in rows
        ]
    return {"name": name, "items": items}


@router.delete("/sink/{name}")
async def sink_clear(name: str, services: ServicesDep) -> dict[str, Any]:
    _check_dev(services)
    async with services.sm() as session, session.begin():
        result = await session.execute(delete(DevSinkItem).where(DevSinkItem.name == name))
    return {"deleted": result.rowcount or 0}
