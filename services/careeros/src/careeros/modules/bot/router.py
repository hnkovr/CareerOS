"""``POST /tg/webhook`` — the bot's only HTTP surface (ADR-012).

Two contracts live here and neither is optional:

*Gates before body.* The shared secret is verified before the payload is trusted
for anything, and a refusal returns 403 without parsing. A non-owner gets 200 and
no side effect, so probing the endpoint reveals nothing.

*Acknowledge, then work.* Telegram retries any update not acknowledged within
~60s. AI parsing can exceed that, and with ``task_runner=inline`` it would run
inside the request, so the handler returns 200 immediately and continues in the
background. The ``update_id`` gate is what makes the resulting retries free.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, Request, Response, status

from careeros.modules.bot.enums import UpdateVerdict

log = structlog.get_logger(__name__)

router = APIRouter(tags=["bot"])

#: Telegram's header carrying the secret configured at setWebhook time.
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@router.post("/tg/webhook", include_in_schema=False)
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    secret_token: str | None = Header(default=None, alias=SECRET_HEADER),
) -> Response:
    gate = request.app.state.bot_gate
    service = request.app.state.bot_service

    # Gate 1 runs before the body is read at all.
    if gate.check_secret(secret_token) is not UpdateVerdict.ACCEPTED:
        log.warning("bot.webhook.bad_secret", path=request.url.path)
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        payload = await request.json()
    except ValueError:
        # Authenticated but malformed: acknowledge so Telegram stops retrying it.
        log.warning("bot.webhook.bad_json")
        return Response(status_code=status.HTTP_200_OK)

    verdict = gate.evaluate(secret=secret_token, payload=payload)
    if verdict is UpdateVerdict.NOT_OWNER:
        # 200, never 403: a stranger must not learn whether this bot is live.
        log.info("bot.webhook.not_owner")
        return Response(status_code=status.HTTP_200_OK)
    if verdict is UpdateVerdict.DUPLICATE:
        log.info("bot.webhook.duplicate", update_id=payload.get("update_id"))
        return Response(status_code=status.HTTP_200_OK)

    # Acknowledge now; the handler runs after the response is sent.
    background.add_task(service.handle, payload)
    return Response(status_code=status.HTTP_200_OK)
