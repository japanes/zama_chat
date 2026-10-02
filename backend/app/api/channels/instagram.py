"""Instagram webhook: GET — Meta's subscription check, POST — events.

One webhook URL for every connected account: the channel is found by the account id
(`recipient.id` of a client message). The POST only saves and queues; the turn runs
later in the worker (after the merge pause), so Meta gets its 200 at once.
"""

import hmac
import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.api.deps import ServicesDep
from app.channels.instagram.webhook import (
    SIGNATURE_HEADER,
    IgEvent,
    attachment_note,
    parse_webhook,
    verify_signature,
)
from app.core.ingest import save_client_message
from app.core.merge import schedule_turn
from app.db.models import Channel, ChannelKind, ChannelStatus, Contact
from app.errors import AppError
from app.jobs.queue import enqueue
from app.logging import get_logger

router = APIRouter(prefix="/v1/channels/instagram", tags=["instagram"])
log = get_logger(__name__)


@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    services: ServicesDep,
    mode: str = Query(default="", alias="hub.mode"),
    token: str = Query(default="", alias="hub.verify_token"),
    challenge: str = Query(default="", alias="hub.challenge"),
) -> str:
    expected = services.settings.ig_webhook_verify_token.get_secret_value()
    if not expected:
        raise AppError("instagram.not_configured", status=503)
    if mode != "subscribe" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise AppError("instagram.bad_verify_token", status=403)
    return challenge


def event_text(event: IgEvent) -> str:
    return event.text or attachment_note(event.attachments)


def event_meta(event: IgEvent) -> dict[str, Any]:
    meta: dict[str, Any] = {"source": "instagram"}
    if event.kind != "message":
        meta["kind"] = event.kind
    if event.reply_to:
        meta["reply_to"] = event.reply_to
    if event.quick_reply:
        meta["quick_reply"] = event.quick_reply
    if event.timestamp:
        meta["sent_at"] = event.timestamp.isoformat()
    if event.text and event.attachments:
        meta["attachment_note"] = attachment_note(event.attachments)
    return meta


@router.post("/webhook")
async def receive_webhook(request: Request, services: ServicesDep) -> dict[str, Any]:
    settings = services.settings
    secret = settings.meta_app_secret.get_secret_value()
    if not secret:
        raise AppError("instagram.not_configured", status=503)
    body = await request.body()
    # TODO(review): Instagram Login apps sign with the Instagram app secret; if Meta
    # shows a separate one for the Instagram product, META_APP_SECRET must be that one
    if not verify_signature(secret, body, request.headers.get(SIGNATURE_HEADER)):
        log.warning("instagram.bad_signature")
        raise AppError("instagram.bad_signature", status=401)
    try:
        payload = json.loads(body)
    except ValueError:
        raise AppError("request.bad_json", status=400) from None

    parsed = parse_webhook(payload)
    now = services.clock.now()
    saved = echoes = unknown = 0
    async with services.sm() as session, session.begin():
        channels: dict[str, Channel | None] = {}
        for event in parsed.events:
            if event.account_id not in channels:
                channels[event.account_id] = (
                    await session.execute(
                        select(Channel).where(
                            Channel.kind == ChannelKind.INSTAGRAM,
                            Channel.account_id == event.account_id,
                        )
                    )
                ).scalar_one_or_none()
            channel = channels[event.account_id]
            if channel is None or channel.status == ChannelStatus.DISABLED:
                unknown += 1
                continue

            if event.echo:
                await enqueue(
                    session,
                    "ig_echo",
                    {
                        "channel_id": str(channel.id),
                        "client_id": event.client_id,
                        "mid": event.mid,
                        "text": event_text(event),
                        "attachments": event.attachments,
                        "at": (event.timestamp or now).isoformat(),
                    },
                    run_at=now + timedelta(seconds=settings.ig_echo_delay_s),
                    tenant_id=channel.tenant_id,
                    dedupe_key=f"igecho:{event.mid}",
                    on_conflict="keep",
                    max_attempts=6,
                )
                echoes += 1
                continue

            ingested = await save_client_message(
                session,
                tenant_id=channel.tenant_id,
                channel_id=channel.id,
                contact_external_id=event.client_id,
                text=event_text(event),
                attachments=event.attachments,
                external_id=event.mid,
                meta=event_meta(event),
                now=now,
            )
            if ingested.duplicate:
                continue
            saved += 1
            # a handed-over conversation still gets a (quiet) turn: it marks the
            # message processed and shows it in the operator's task
            await schedule_turn(
                session,
                tenant_id=channel.tenant_id,
                conversation_id=ingested.conversation_id,
                channel_settings=channel.settings or {},
                now=now,
            )
            contact = await session.get(Contact, ingested.contact_id)
            if (
                contact is not None
                and not contact.username
                and not (contact.profile or {}).get("ig_checked_at")
            ):
                await enqueue(
                    session,
                    "ig_profile",
                    {"channel_id": str(channel.id), "contact_id": str(contact.id)},
                    run_at=now,
                    tenant_id=channel.tenant_id,
                    dedupe_key=f"igprofile:{contact.id}",
                    on_conflict="keep",
                    max_attempts=2,
                )
    if unknown or parsed.skipped:
        log.info("instagram.webhook", saved=saved, unknown=unknown, skipped=parsed.skipped)
    return {"ok": True, "saved": saved, "echoes": echoes}
