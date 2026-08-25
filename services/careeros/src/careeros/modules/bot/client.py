"""Thin Telegram Bot API client.

Deliberately not aiogram's Bot: claiming a webhook and sending a message are two
calls, and this keeps the token handling in one place where it is never logged.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from careeros.core.config import Settings

log = structlog.get_logger(__name__)


class TelegramError(RuntimeError):
    """Telegram accepted the request and refused it, or could not be reached."""


class TelegramClient:
    def __init__(self, settings: Settings) -> None:
        if settings.tg_bot_token is None:
            raise ValueError("tg_bot_token is not configured")
        self._base = f"{settings.tg_api_base}/bot{settings.tg_bot_token.get_secret_value()}"
        self._timeout = settings.tg_http_timeout_s

    async def _call(self, method: str, **params: Any) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.post(f"{self._base}/{method}", json=params)
            except httpx.HTTPError as exc:  # unreachable ≠ rejected; keep them distinct
                raise TelegramError(f"cannot reach Telegram for {method}: {exc}") from exc
        body = resp.json()
        if not body.get("ok"):
            # The description is safe to surface; the token is only ever in the URL.
            raise TelegramError(f"{method} failed: {body.get('description')}")
        return body.get("result")

    async def get_me(self) -> dict:
        return await self._call("getMe")

    async def get_webhook_info(self) -> dict:
        return await self._call("getWebhookInfo")

    async def set_webhook(self, url: str, secret: str) -> None:
        await self._call("setWebhook", url=url, secret_token=secret, drop_pending_updates=False)

    async def delete_webhook(self) -> None:
        await self._call("deleteWebhook", drop_pending_updates=False)

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> dict:
        return await self._call("sendMessage", chat_id=chat_id, text=text, **kw)
