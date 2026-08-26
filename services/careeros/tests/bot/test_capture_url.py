"""A forwarded LINK is read, a forwarded TEXT is parsed (ADR-015 in the bot, GH #3).

The bot owns no state and no HTTP: both paths go through a service (invariant 7), so the tests
replace those services and assert which one the message reached — and what came back to the chat.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import SecretStr

from careeros.core.config import Settings
from careeros.modules.bot.capture import looks_like_job_description
from careeros.modules.bot.service import BotService
from careeros.modules.platform.enums import FetchStrategy
from careeros.modules.platform.fetch.artifact import JobReadError
from careeros.modules.platform.schemas import FetchAttempt
from careeros.modules.vault.enums import Platform

OWNER = 4242
OID = uuid.UUID("01a03aef-b36f-792d-b2d6-adf41a6963b6")
JOB_URL = "https://careers.northwind.example/jobs/senior-data-engineer-4711"
JD = "We are hiring a senior data engineer to own our dbt and Dagster stack. " * 3


@dataclass
class FakeScore:
    overall: int = 80
    recommendation: Any = None
    dimensions: list[Any] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class FakeDetail:
    id: uuid.UUID = OID
    title: str = "Senior Data Engineer"
    company_name: str | None = "Northwind Commerce"
    url: str | None = JOB_URL
    possible_duplicate_of: uuid.UUID | None = None
    status: str = "new"
    score: FakeScore | None = field(default_factory=FakeScore)
    analysis: Any = None


@dataclass
class FakeRead:
    """Only the ``ReadOut`` fields the bot is allowed to read."""

    opportunity_id: uuid.UUID | None = OID
    created: bool = True
    duplicate_of: uuid.UUID | None = None
    snapshot_created: bool = True
    closed: bool = False


class FakeOpportunityService:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.ingested: list[Any] = []

    async def ingest(self, req: Any) -> FakeDetail:
        self.log.append("ingest")
        self.ingested.append(req)
        return FakeDetail()

    async def get(self, oid: uuid.UUID) -> FakeDetail:
        self.log.append("get")
        return FakeDetail(id=oid)


class SpyClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> dict:
        self.sent.append(text)
        return {}

    async def answer_callback_query(self, callback_id: str, text: str = "") -> None:
        return None


@pytest.fixture
def bot(monkeypatch: pytest.MonkeyPatch):
    log: list[str] = []
    settings = Settings(
        env="test",
        tg_enabled=True,
        tg_bot_token=SecretStr("1:a"),
        tg_webhook_secret=SecretStr("s"),
        tg_owner_chat_id=OWNER,
    )
    client = SpyClient()
    service = BotService(settings, client, sessionmaker=object())  # type: ignore[arg-type]
    opportunities = FakeOpportunityService(log)
    reads: list[str] = []
    outcome: dict[str, Any] = {"out": FakeRead()}

    async def _with_opportunities(fn):
        return await fn(opportunities)

    async def _read_job(url: str):
        log.append("read_job")
        reads.append(url)
        result = outcome["out"]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(service, "_with_opportunities", _with_opportunities)
    monkeypatch.setattr(service, "_read_job", _read_job)
    return service, client, log, reads, outcome, opportunities


def msg(text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": OWNER}, "text": text}}


async def test_a_forwarded_link_is_read_not_stored_as_a_link(bot) -> None:
    service, client, log, reads, _outcome, _opps = bot
    await service.handle(msg(JOB_URL))
    assert log.count("read_job") == 1 and reads == [JOB_URL]
    assert "ingest" not in log, "a recognised URL must not become a bare url capture"
    assert any("Senior Data Engineer" in m for m in client.sent), "the triage card still comes back"


async def test_a_forwarded_description_still_takes_the_paste_path(bot) -> None:
    service, _client, log, reads, _outcome, opportunities = bot
    await service.handle(msg(JD))
    assert log == ["ingest"] and reads == []
    assert opportunities.ingested[0].text == JD.strip() and opportunities.ingested[0].url is None


async def test_an_already_known_job_says_so_before_the_card(bot) -> None:
    service, client, _log, _reads, outcome, _opps = bot
    outcome["out"] = FakeRead(created=False, duplicate_of=OID, snapshot_created=True)
    await service.handle(msg(JOB_URL))
    assert any("already known" in m for m in client.sent)
    assert any("new snapshot" in m for m in client.sent)

    client.sent.clear()
    outcome["out"] = FakeRead(created=False, duplicate_of=OID, snapshot_created=False)
    await service.handle(msg(JOB_URL))
    assert any("no change" in m for m in client.sent)


async def test_a_closed_posting_is_called_out(bot) -> None:
    service, client, _log, _reads, outcome, _opps = bot
    outcome["out"] = FakeRead(created=False, duplicate_of=OID, closed=True)
    await service.handle(msg(JOB_URL))
    assert any("closed" in m for m in client.sent)


async def test_a_failed_read_answers_with_the_diagnostics_and_offers_the_paste_path(bot) -> None:
    service, client, log, _reads, outcome, _opps = bot
    outcome["out"] = JobReadError(
        Platform.website,
        [
            FetchAttempt(strategy=FetchStrategy.public_html, url=JOB_URL, error_type="captcha"),
            FetchAttempt(
                strategy=FetchStrategy.jina,
                url=JOB_URL,
                status_code=422,
                error_type="http_error",
            ),
        ],
    )
    await service.handle(msg(JOB_URL))
    reply = "\n".join(client.sent)
    assert "captcha" in reply and "jina" in reply, "the owner is told WHICH strategy failed"
    assert "forward it instead" in reply.lower() or "paste" in reply.lower()
    assert "ingest" not in log, "a failed read must not silently store the bare link"


def test_a_bare_link_is_still_what_reaches_capture() -> None:
    """The URL branch only exists because the capture predicate lets a bare link through."""
    assert looks_like_job_description(JOB_URL)
    assert not looks_like_job_description("ok")
