"""/cv, /cv update and /cv improve (GH #29, #30).

Two properties carry the weight here. First, `meta` means the CORE cv variant, not
a metadata field — the owner's word, the repo's concept. Second, invariant 2: a
generated bullet is a claim about the facts it derives from, so `derived_from[]`
survives the trip into a chat message, and a diff that shows fewer bullets than it
holds says so instead of reading as "that is all that changed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import SecretStr

from careeros.core.config import Settings
from careeros.modules.bot.cv import (
    DIFF_BULLETS,
    artifact_card,
    diff_card,
    parse_cv_command,
    resolve_variant,
    variants_card,
)
from careeros.modules.bot.service import BotService

OWNER = 4242


# ── fakes ─────────────────────────────────────────────────────────────────────


@dataclass
class FakeVariant:
    id: str
    name: str


class FakeMeta:
    def __init__(self, cv_variant: str = "general-core") -> None:
        self.default_cv_variant = cv_variant
        self.default_positioning = "senior_de"


class FakeVaultData:
    def __init__(self, variants: list[FakeVariant], core: str = "general-core") -> None:
        self.cv_variants = variants
        self.positioning: list[object] = []
        self.meta = FakeMeta(core)


@dataclass
class FakeFiles:
    pdf: str | None = None
    md: str | None = None


@dataclass
class FakeArtifact:
    variant_id: str = "general-core"
    status: str = "ready"
    ai_used: bool = False
    provider: str | None = None
    model: str | None = None
    bullet_count: int = 12
    vault_sha: str | None = "abc123def456789"
    warnings: list[str] = field(default_factory=list)
    files: FakeFiles = field(default_factory=FakeFiles)


@dataclass
class FakeDiff:
    group: str = "exp_a"
    text_a: str | None = None
    text_b: str | None = None
    derived_from: list[str] = field(default_factory=list)


@dataclass
class FakeComparison:
    rewritten: list[FakeDiff] = field(default_factory=list)
    added: list[FakeDiff] = field(default_factory=list)
    removed: list[FakeDiff] = field(default_factory=list)
    unchanged: int = 0
    keywords_only_b: list[str] = field(default_factory=list)


@dataclass
class FakeImprovement:
    artifact: FakeArtifact
    comparison: FakeComparison


def data() -> FakeVaultData:
    return FakeVaultData(
        [FakeVariant("general-core", "Core"), FakeVariant("remote-us", "Remote US")]
    )


# ── parsing ───────────────────────────────────────────────────────────────────


def test_bare_cv_lists_variants() -> None:
    assert parse_cv_command("/cv").action == "show"


def test_update_without_a_variant_means_the_core_one() -> None:
    cmd = parse_cv_command("/cv update")
    assert cmd.action == "update" and cmd.variant is None


@pytest.mark.parametrize("alias", ["meta", "core", "master", "MAIN", "default"])
def test_core_aliases_all_resolve_to_the_core_variant(alias: str) -> None:
    """`meta` was the owner's word for it; the others are the words they may reach for."""
    assert parse_cv_command(f"/cv update {alias}").variant is None


def test_the_preposition_the_owner_wrote_is_accepted() -> None:
    assert parse_cv_command("/cv improve in remote-us").variant == "remote-us"


def test_a_variant_without_the_preposition_works_too() -> None:
    assert parse_cv_command("/cv update remote-us").variant == "remote-us"


def test_improve_is_a_distinct_action_from_update() -> None:
    assert parse_cv_command("/cv improve").action == "improve"


def test_an_unknown_action_is_refused_with_the_usage() -> None:
    with pytest.raises(ValueError, match="unknown"):
        parse_cv_command("/cv delete")


def test_two_variants_at_once_are_refused_rather_than_silently_halved() -> None:
    with pytest.raises(ValueError, match="one variant"):
        parse_cv_command("/cv update remote-us poland-eu")


# ── resolution ────────────────────────────────────────────────────────────────


def test_none_resolves_to_the_vaults_core_variant() -> None:
    assert resolve_variant(data(), None) == "general-core"


def test_a_known_variant_resolves_to_itself() -> None:
    assert resolve_variant(data(), "remote-us") == "remote-us"


def test_an_unknown_variant_names_what_exists() -> None:
    with pytest.raises(ValueError) as exc:
        resolve_variant(data(), "nope")
    assert "remote-us" in str(exc.value) and "general-core" in str(exc.value)


def test_a_vault_with_no_core_variant_says_which_key_is_missing() -> None:
    with pytest.raises(ValueError, match="default_cv_variant"):
        resolve_variant(FakeVaultData([FakeVariant("a", "A")], core=""), None)


# ── rendering ─────────────────────────────────────────────────────────────────


def test_the_variant_listing_marks_the_core_one() -> None:
    card = variants_card(list(data().cv_variants), "general-core")
    assert "core" in card and "remote" in card


def test_the_artifact_card_states_when_ai_did_not_run() -> None:
    """ "ai: —" would read as a formatting gap; the absence is the point."""
    assert "not used" in artifact_card(FakeArtifact(), header="CV updated")


def test_the_artifact_card_names_the_model_that_ran() -> None:
    card = artifact_card(
        FakeArtifact(ai_used=True, provider="anthropic", model="claude"), header="CV improved"
    )
    assert "anthropic" in card and "claude" in card


def test_warnings_are_printed_not_swallowed() -> None:
    """The provenance guard reports here; a rejected claim must not look like a clean run."""
    card = artifact_card(
        FakeArtifact(warnings=["dropped a bullet citing an unknown fact id"]), header="x"
    )
    assert "unknown fact id" in card


def test_a_rewritten_bullet_keeps_its_provenance() -> None:
    card = diff_card(
        FakeComparison(
            rewritten=[FakeDiff(text_a="old", text_b="new wording", derived_from=["ach_one"])]
        )
    )
    assert "new wording" in card
    assert "ach" in card and "one" in card, "derived_from must survive formatting"


def test_a_bullet_with_no_provenance_says_so_rather_than_showing_nothing() -> None:
    card = diff_card(FakeComparison(added=[FakeDiff(text_b="unsourced", derived_from=[])]))
    assert "none" in card


def test_an_over_long_diff_says_how_many_it_left_out() -> None:
    """Truncating in silence reads as "that is all that changed"."""
    many = [
        FakeDiff(text_a="a", text_b=f"b{i}", derived_from=["ach_x"])
        for i in range(DIFF_BULLETS + 3)
    ]
    card = diff_card(FakeComparison(rewritten=many))
    assert "3 more" in card


def test_an_empty_diff_is_stated_in_words() -> None:
    card = diff_card(FakeComparison(unchanged=9))
    assert "nothing changed" in card


def test_the_diff_header_counts_every_bucket() -> None:
    card = diff_card(
        FakeComparison(
            rewritten=[FakeDiff(text_b="r")],
            added=[FakeDiff(text_b="a")],
            removed=[FakeDiff(text_a="d")],
            unchanged=4,
        )
    )
    assert "rewritten 1" in card and "added 1" in card and "removed 1" in card


# ── dispatch ──────────────────────────────────────────────────────────────────


class SpyClient:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.documents: list[tuple[str, bytes]] = []

    async def send_message(self, chat_id: int, text: str, **kw) -> dict:
        self.sent.append(text)
        return {}

    async def send_document(self, chat_id: int, filename: str, content: bytes, **kw) -> dict:
        self.documents.append((filename, content))
        return {}


class RecordingCVService:
    """Stands in for CVService — only the two calls the bot is allowed to make."""

    def __init__(self, artifact: FakeArtifact, improvement: FakeImprovement | None = None) -> None:
        self.artifact = artifact
        self.improvement = improvement
        self.generated: list[Any] = []
        self.improved: list[str] = []

    async def generate(self, req):
        self.generated.append(req)
        return self.artifact

    async def improve(self, variant_id: str, **kw):
        self.improved.append(variant_id)
        assert self.improvement is not None
        return self.improvement


@pytest.fixture
def bot(monkeypatch):
    settings = Settings(
        env="test",
        tg_enabled=True,
        tg_bot_token=SecretStr("1:a"),
        tg_webhook_secret=SecretStr("s"),
        tg_owner_chat_id=OWNER,
    )
    client = SpyClient()
    service = BotService(settings, client, sessionmaker=object())  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_vault_data", data)
    return service, client


def wire(service, monkeypatch, cv_service) -> None:
    async def _with_cv_service(fn):
        return await fn(cv_service)

    monkeypatch.setattr(service, "_with_cv_service", _with_cv_service)


def msg(text: str) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": OWNER}, "text": text}}


async def test_cv_lists_variants_without_touching_the_database(bot) -> None:
    service, client = bot
    service._sessionmaker = None
    await service.handle(msg("/cv"))
    assert "remote" in client.sent[-1]


async def test_update_generates_deterministically(bot, monkeypatch) -> None:
    """#29 is explicitly not an AI command; asking a provider here would be a cost bug."""
    service, _client = bot
    cv = RecordingCVService(FakeArtifact())
    wire(service, monkeypatch, cv)
    await service.handle(msg("/cv update"))
    assert len(cv.generated) == 1
    assert cv.generated[0].use_ai is False
    assert cv.generated[0].variant_id == "general-core"


async def test_update_in_a_channel_variant_targets_that_variant(bot, monkeypatch) -> None:
    service, _client = bot
    cv = RecordingCVService(FakeArtifact(variant_id="remote-us"))
    wire(service, monkeypatch, cv)
    await service.handle(msg("/cv update in remote-us"))
    assert cv.generated[0].variant_id == "remote-us"


async def test_an_unknown_variant_never_reaches_the_cv_service(bot, monkeypatch) -> None:
    service, client = bot
    cv = RecordingCVService(FakeArtifact())
    wire(service, monkeypatch, cv)
    await service.handle(msg("/cv update in nope"))
    assert cv.generated == []
    assert "nope" in client.sent[-1]


async def test_improve_shows_the_diff_after_the_card(bot, monkeypatch) -> None:
    service, client = bot
    improvement = FakeImprovement(
        artifact=FakeArtifact(ai_used=True, provider="anthropic", model="claude"),
        comparison=FakeComparison(
            rewritten=[FakeDiff(text_a="old", text_b="tightened", derived_from=["ach_one"])],
            unchanged=3,
        ),
    )
    cv = RecordingCVService(FakeArtifact(), improvement)
    wire(service, monkeypatch, cv)
    await service.handle(msg("/cv improve"))
    assert cv.improved == ["general-core"]
    assert any("tightened" in m for m in client.sent)
    assert cv.generated == [], "improve must not also run a separate generate from the bot"


async def test_improve_without_a_provider_says_so_instead_of_showing_an_empty_diff(
    bot, monkeypatch
) -> None:
    """An empty diff reads as "AI had nothing to add" — a different claim entirely."""
    service, client = bot
    improvement = FakeImprovement(
        artifact=FakeArtifact(ai_used=False, warnings=["AI requested but no configured provider"]),
        comparison=FakeComparison(unchanged=12),
    )
    wire(service, monkeypatch, RecordingCVService(FakeArtifact(), improvement))
    await service.handle(msg("/cv improve"))
    assert any("no AI provider ran" in m for m in client.sent)


async def test_the_rendered_pdf_is_uploaded(bot, monkeypatch, tmp_path) -> None:
    service, client = bot
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")
    wire(service, monkeypatch, RecordingCVService(FakeArtifact(files=FakeFiles(pdf=str(pdf)))))
    await service.handle(msg("/cv update"))
    assert client.documents and client.documents[0][0].endswith(".pdf")


async def test_markdown_is_uploaded_when_the_pdf_render_failed(bot, monkeypatch, tmp_path) -> None:
    service, client = bot
    md = tmp_path / "cv.md"
    md.write_text("# cv", encoding="utf-8")
    artifact = FakeArtifact(status="failed", files=FakeFiles(md=str(md)))
    wire(service, monkeypatch, RecordingCVService(artifact))
    await service.handle(msg("/cv update"))
    assert client.documents and client.documents[0][0].endswith(".md")


async def test_nothing_rendered_is_reported_rather_than_leaving_silence(bot, monkeypatch) -> None:
    service, client = bot
    wire(service, monkeypatch, RecordingCVService(FakeArtifact(status="failed")))
    await service.handle(msg("/cv update"))
    assert "no document was rendered" in client.sent[-1]


async def test_a_second_run_while_one_is_going_is_refused(bot, monkeypatch) -> None:
    """Telegram delivers a double-tap as two real updates; the update_id gate cannot help."""
    service, client = bot
    cv = RecordingCVService(FakeArtifact())
    wire(service, monkeypatch, cv)
    service._inflight.add((OWNER, "cv"))
    await service.handle(msg("/cv improve"))
    assert cv.improved == []
    assert "already going" in client.sent[-1]


async def test_a_failing_generation_answers_instead_of_going_quiet(bot, monkeypatch) -> None:
    service, client = bot

    async def _boom(fn):
        raise RuntimeError("rendercv exploded")

    monkeypatch.setattr(service, "_with_cv_service", _boom)
    await service.handle(msg("/cv update"))
    assert "rendercv exploded" in client.sent[-1]
    assert (OWNER, "cv") not in service._inflight, "the guard must not survive a failure"
