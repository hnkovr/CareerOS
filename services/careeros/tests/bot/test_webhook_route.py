"""End-to-end behaviour of POST /api/tg/webhook.

Asserts the two contracts that cannot be verified from the gate unit tests: the
status codes an attacker sees, and that work is deferred rather than run inside
the request.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from careeros.api.app import create_app
from careeros.core.config import Settings
from careeros.modules.bot.security import UpdateGate

OWNER = 4242
SECRET = "s3cret"
URL = "/api/tg/webhook"
HEADER = "X-Telegram-Bot-Api-Secret-Token"


class SpyService:
    def __init__(self) -> None:
        self.handled: list[dict] = []

    async def handle(self, payload: dict) -> None:
        self.handled.append(payload)


@pytest.fixture
def app_and_spy():
    settings = Settings(
        env="test",
        task_runner="inline",
        tg_enabled=True,
        tg_bot_token="123:abc",  # type: ignore[arg-type]  # pydantic coerces str -> SecretStr
        tg_webhook_secret=SECRET,  # type: ignore[arg-type]
        tg_owner_chat_id=OWNER,
    )
    app = create_app(settings)
    spy = SpyService()
    # Bypass lifespan wiring: these tests are about the route, not about startup.
    app.state.bot_gate = UpdateGate(settings)
    app.state.bot_service = spy
    return app, spy


@pytest.fixture
async def client(app_and_spy):
    app, _ = app_and_spy
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


def msg(chat_id: int = OWNER, update_id: int = 1, text: str = "/help") -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


async def test_missing_secret_header_is_forbidden(client, app_and_spy) -> None:
    _, spy = app_and_spy
    r = await client.post(URL, json=msg())
    assert r.status_code == 403
    assert spy.handled == []


async def test_wrong_secret_is_forbidden(client, app_and_spy) -> None:
    _, spy = app_and_spy
    r = await client.post(URL, json=msg(), headers={HEADER: "nope"})
    assert r.status_code == 403
    assert spy.handled == []


async def test_owner_update_is_accepted_and_dispatched(client, app_and_spy) -> None:
    _, spy = app_and_spy
    r = await client.post(URL, json=msg(), headers={HEADER: SECRET})
    assert r.status_code == 200
    assert len(spy.handled) == 1


async def test_non_owner_gets_200_and_no_side_effect(client, app_and_spy) -> None:
    """403 here would tell a stranger the endpoint is a live bot."""
    _, spy = app_and_spy
    r = await client.post(URL, json=msg(chat_id=9999), headers={HEADER: SECRET})
    assert r.status_code == 200
    assert spy.handled == []


async def test_a_retried_update_is_handled_once(client, app_and_spy) -> None:
    """Telegram retries anything unacknowledged in ~60s; retries must be free."""
    _, spy = app_and_spy
    for _ in range(3):
        r = await client.post(URL, json=msg(update_id=77), headers={HEADER: SECRET})
        assert r.status_code == 200
    assert len(spy.handled) == 1


async def test_distinct_updates_are_each_handled(client, app_and_spy) -> None:
    _, spy = app_and_spy
    for i in (1, 2, 3):
        await client.post(URL, json=msg(update_id=i), headers={HEADER: SECRET})
    assert len(spy.handled) == 3


async def test_malformed_json_is_acknowledged_not_retried(client, app_and_spy) -> None:
    """Authenticated but broken: 200 stops Telegram retrying it forever."""
    _, spy = app_and_spy
    r = await client.post(
        URL, content=b"{not json", headers={HEADER: SECRET, "content-type": "application/json"}
    )
    assert r.status_code == 200
    assert spy.handled == []


async def test_bad_secret_does_not_consume_the_update_id(client, app_and_spy) -> None:
    """Otherwise an attacker could make us drop a real update as a duplicate."""
    _, spy = app_and_spy
    await client.post(URL, json=msg(update_id=55), headers={HEADER: "wrong"})
    r = await client.post(URL, json=msg(update_id=55), headers={HEADER: SECRET})
    assert r.status_code == 200
    assert len(spy.handled) == 1


async def test_callback_query_from_owner_is_accepted(client, app_and_spy) -> None:
    _, spy = app_and_spy
    payload = {
        "update_id": 9,
        "callback_query": {"message": {"chat": {"id": OWNER}}, "data": "skip"},
    }
    r = await client.post(URL, json=payload, headers={HEADER: SECRET})
    assert r.status_code == 200
    assert len(spy.handled) == 1
