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

#: Telegram refuses a bot upload above this size. Checked before the request so a
#: too-large file fails as a sentence the owner can read, not as a timeout.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024

#: A caption longer than this is rejected outright, taking the document with it.
MAX_CAPTION_CHARS = 1024


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

    async def answer_callback_query(self, callback_id: str, text: str = "") -> None:
        """Close the spinner on a tapped inline button.

        Telegram requires this for every callback_query and refuses it once the query
        has aged out, so it is sent BEFORE the work the button asked for. `text` shows
        as a toast; keep it short — Telegram caps it at 200 characters.
        """
        await self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> dict:
        return await self._call("sendMessage", chat_id=chat_id, text=text, **kw)

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        *,
        caption: str | None = None,
    ) -> dict:
        """Upload one file as a document.

        Takes bytes rather than a path on purpose: the client's job is the wire, and
        keeping the filesystem out of it means a test can exercise the upload without
        one. Multipart, so it cannot share `_call`'s JSON body.
        """
        if len(content) > MAX_DOCUMENT_BYTES:
            raise TelegramError(
                f"{filename} is {len(content) // (1024 * 1024)} MB; "
                f"Telegram caps bot uploads at {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB"
            )
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:MAX_CAPTION_CHARS]
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.post(
                    f"{self._base}/sendDocument",
                    data=data,
                    files={"document": (filename, content)},
                )
            except httpx.HTTPError as exc:
                raise TelegramError(f"cannot reach Telegram for sendDocument: {exc}") from exc
        body = resp.json()
        if not body.get("ok"):
            raise TelegramError(f"sendDocument failed: {body.get('description')}")
        return body.get("result")
