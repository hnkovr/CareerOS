"""``careeros bot`` — webhook ops from the CLI.

Mirrors scripts/prj-tools/tg-bot.sh so the same rules apply whether an operator
or the app itself is claiming: never take a webhook from a live owner unless the
takeover is explicit.
"""

from __future__ import annotations

import asyncio

import typer

from careeros.core.config import get_settings
from careeros.modules.bot.client import TelegramClient, TelegramError
from careeros.modules.bot.enums import WebhookClaim
from careeros.modules.bot.webhook import claim_webhook, webhook_url

app = typer.Typer(help="Telegram bot webhook operations.")


def _client() -> TelegramClient:
    settings = get_settings()
    if settings.tg_bot_token is None:
        raise typer.BadParameter("CAREEROS_TG_BOT_TOKEN is not set")
    return TelegramClient(settings)


@app.command("webhook-info")
def webhook_info() -> None:
    """Ask Telegram who currently owns the webhook (the authority)."""

    async def run() -> None:
        info = await _client().get_webhook_info()
        settings = get_settings()
        typer.echo(f"url:          {info.get('url') or '(unset)'}")
        typer.echo(f"pending:      {info.get('pending_update_count', 0)}")
        typer.echo(f"last error:   {info.get('last_error_message') or 'none'}")
        typer.echo(f"expected url: {webhook_url(settings) or '(no public url configured)'}")

    _run(run())


@app.command("webhook-set")
def webhook_set(
    force: bool = typer.Option(False, "--force", help="take it from a live owner"),
) -> None:
    """Claim the webhook. Refuses a foreign owner unless --force."""

    async def run() -> None:
        settings = get_settings().model_copy(update={"tg_webhook_force_claim": force})
        result = await claim_webhook(settings, _client())
        typer.echo(f"claim: {result.value}")
        if result is WebhookClaim.REFUSED_FOREIGN:
            raise typer.Exit(code=3)

    _run(run())


@app.command("webhook-delete")
def webhook_delete() -> None:
    """Release the webhook so a local run (or another host) can take it."""
    _run(_delete())


async def _delete() -> None:
    await _client().delete_webhook()
    typer.echo("webhook removed (pending updates kept)")


@app.command("check")
def check() -> None:
    """Verify the token lives and names the bot we expect."""

    async def run() -> None:
        me = await _client().get_me()
        typer.echo(f"bot: @{me.get('username')}")

    _run(run())


def _run(coro) -> None:
    try:
        asyncio.run(coro)
    except TelegramError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc
