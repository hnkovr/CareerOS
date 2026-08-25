"""Score-card rendering and capture triage (GH #3).

Rendering is tested because MarkdownV2 fails loudly and unhelpfully: an unescaped
reserved character returns a 400 naming a byte offset, not the character, and job
titles are full of them — "Senior Engineer (Remote, EU) — $120k+".
"""

from __future__ import annotations

import pytest

from careeros.modules.bot.capture import looks_like_job_description
from careeros.modules.bot.formatting import escape_md, score_card
from careeros.modules.bot.keyboards import triage_keyboard


class D:
    """Minimal stand-in for OpportunityDetail — only what the card reads."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def dim(name, score, weight, explanation):
    return D(name=name, score=score, weight=weight, explanation=explanation, signals=[])


def detail(**kw):
    base = dict(
        id="abc-123",
        title="Senior Data Engineer (Remote, EU)",
        company_name="Acme Corp.",
        url="https://example.com/jobs/1",
        possible_duplicate_of=None,
        score=D(
            overall=83,
            recommendation=D(value="high_priority"),
            reasons=["strong stack overlap"],
            dimensions=[
                dim("stack_fit", 92, 0.30, "Python, dbt, ClickHouse all present"),
                dim("comp", 70, 0.25, "band below target"),
                dim("remote", 88, 0.20, "EU-wide remote"),
                dim("seniority", 40, 0.05, "listed as mid-level"),
            ],
        ),
    )
    base.update(kw)
    return D(**base)


# ── escaping ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ch", list(r"_*[]()~`>#+-=|{}.!"))
def test_every_reserved_character_is_escaped(ch: str) -> None:
    assert escape_md(f"a{ch}b") == f"a\\{ch}b"


def test_ordinary_text_is_untouched() -> None:
    assert escape_md("plain text 123") == "plain text 123"


def test_a_realistic_title_survives() -> None:
    out = escape_md("Senior Engineer (Remote, EU) - $120k+, Sr. level")
    for ch in "().+-":
        assert f"\\{ch}" in out


# ── score card ────────────────────────────────────────────────────────────────


def test_card_shows_title_company_and_overall_score() -> None:
    card = score_card(detail())
    assert "Senior Data Engineer" in card
    assert "Acme Corp" in card
    assert "83" in card


def test_card_shows_only_the_top_three_dimensions() -> None:
    """A phone card must fit on a phone; the full breakdown is a tap away."""
    card = score_card(detail())
    # Dimension names carry underscores, which MarkdownV2 reserves — the card must
    # show them escaped, or Telegram answers with a 400 naming a byte offset.
    assert "stack\\_fit" in card and "comp" in card and "remote" in card
    assert "seniority" not in card, "the 4th dimension must be omitted"


def test_dimensions_are_ordered_by_contribution_not_raw_score() -> None:
    """What moved the number is score x weight, not the biggest percentage.

    remote (88 x 0.20 = 17.6) outranks comp (70 x 0.25 = 17.5) despite the lower
    weight, and seniority (40 x 0.05 = 2.0) is last despite not being the lowest
    raw score. Ordering by raw score alone would get both of those wrong.
    """
    card = score_card(detail())
    assert card.index("stack\\_fit") < card.index("remote") < card.index("comp")


def test_card_is_fully_escaped() -> None:
    card = score_card(detail())
    assert "Acme Corp\\." in card, "the period in a company name must be escaped"
    assert "\\(Remote, EU\\)" in card


def test_duplicate_is_called_out() -> None:
    card = score_card(detail(possible_duplicate_of="dup-9"))
    assert "duplicate" in card.lower()


def test_card_without_a_score_still_renders() -> None:
    """Scoring can be unavailable (no vault); the capture must not be lost."""
    card = score_card(detail(score=None))
    assert "Senior Data Engineer" in card
    assert "not scored" in card.lower()


def test_card_without_a_company_renders() -> None:
    assert "Senior Data Engineer" in score_card(detail(company_name=None))


# ── triage keyboard ───────────────────────────────────────────────────────────


def test_keyboard_offers_the_four_triage_actions() -> None:
    kb = triage_keyboard("abc-123")
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert {"Skip", "Save", "Analyze", "Prompt"} <= set(labels)


def test_callback_data_carries_the_opportunity_id() -> None:
    kb = triage_keyboard("abc-123")
    for row in kb["inline_keyboard"]:
        for button in row:
            assert "abc-123" in button["callback_data"]


def test_callback_data_stays_within_telegrams_64_byte_limit() -> None:
    """Telegram silently rejects a longer payload; uuids are 36 chars already."""
    kb = triage_keyboard("01a03aef-b36f-792d-b2d6-adf41a6963b6")
    for row in kb["inline_keyboard"]:
        for button in row:
            assert len(button["callback_data"].encode()) <= 64


# ── what counts as a job description ──────────────────────────────────────────


def test_a_long_paste_is_a_job_description() -> None:
    assert looks_like_job_description("We are hiring a senior data engineer. " * 5)


def test_a_bare_url_is_a_job_description() -> None:
    assert looks_like_job_description("https://boards.greenhouse.io/acme/jobs/1")


def test_a_short_chat_message_is_not() -> None:
    assert not looks_like_job_description("ok")
    assert not looks_like_job_description("thanks!")


def test_a_command_is_never_a_job_description() -> None:
    assert not looks_like_job_description("/status")
    assert not looks_like_job_description("/help me find a job in data engineering " * 3)
