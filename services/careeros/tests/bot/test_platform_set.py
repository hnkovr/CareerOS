"""Parsing and validating a platform set (GH #25).

The expensive failure is a silently accepted typo: `/services set hh,upwrok`
storing a set that makes every later command return nothing, with no error at the
point the mistake was made. So the parser names what it rejected and what exists.
"""

from __future__ import annotations

import pytest

from careeros.modules.bot.platforms import (
    UnknownPlatforms,
    format_platform_set,
    known_platforms,
    parse_platform_set,
)


def test_the_seven_connectors_are_known() -> None:
    assert {
        "hh",
        "upwork",
        "linkedin",
        "wellfound",
        "indeed",
        "getmatch",
        "toptal",
    } <= known_platforms()


def test_a_plain_comma_list_parses() -> None:
    assert parse_platform_set("hh,upwork") == ["hh", "upwork"]


@pytest.mark.parametrize("raw", ["hh, upwork", " hh ,upwork ", "hh,  upwork", "hh , upwork"])
def test_whitespace_around_names_is_tolerated(raw: str) -> None:
    assert parse_platform_set(raw) == ["hh", "upwork"]


def test_case_is_normalised() -> None:
    assert parse_platform_set("HH,UpWork") == ["hh", "upwork"]


def test_duplicates_collapse_but_order_is_kept() -> None:
    """Order is the user's stated preference; it survives dedup."""
    assert parse_platform_set("upwork,hh,upwork") == ["upwork", "hh"]


def test_spaces_and_semicolons_also_separate() -> None:
    """People type what they type; a separator guess must not become a typo."""
    assert parse_platform_set("hh upwork") == ["hh", "upwork"]
    assert parse_platform_set("hh; upwork") == ["hh", "upwork"]


def test_an_unknown_platform_is_rejected_by_name() -> None:
    with pytest.raises(UnknownPlatforms) as exc:
        parse_platform_set("hh,upwrok")
    assert "upwrok" in str(exc.value)
    assert "hh" not in str(exc.value).split("known")[0], "only the bad name is blamed"


def test_the_error_lists_what_does_exist() -> None:
    """A rejection that doesn't say what is valid just makes the user guess again."""
    with pytest.raises(UnknownPlatforms) as exc:
        parse_platform_set("nope")
    message = str(exc.value)
    assert "upwork" in message and "toptal" in message


def test_every_unknown_name_is_reported_not_just_the_first() -> None:
    with pytest.raises(UnknownPlatforms) as exc:
        parse_platform_set("nope,hh,alsobad")
    assert "nope" in str(exc.value) and "alsobad" in str(exc.value)


def test_a_partly_valid_set_is_rejected_entirely() -> None:
    """Storing the good half of a typo'd command is a surprise, not a kindness."""
    with pytest.raises(UnknownPlatforms):
        parse_platform_set("hh,garbage")


@pytest.mark.parametrize("raw", ["", "   ", ",", " , , "])
def test_an_empty_selection_is_rejected(raw: str) -> None:
    """Empty would mean 'act on nothing', which no command wants."""
    with pytest.raises(ValueError):
        parse_platform_set(raw)


def test_the_word_all_selects_every_known_platform() -> None:
    assert set(parse_platform_set("all")) == known_platforms()


def test_formatting_round_trips() -> None:
    assert parse_platform_set(format_platform_set(["hh", "upwork"])) == ["hh", "upwork"]


def test_formatting_an_empty_set_says_so_rather_than_printing_nothing() -> None:
    assert "none" in format_platform_set([]).lower()
