"""Splitting oversized messages.

Telegram rejects a sendMessage body over 4096 characters outright — no partial
send, no truncation, a 400. A CV diff or a variant listing reaches that easily, so
the split has to be correct rather than incidental: it must never land between a
MarkdownV2 backslash and the character it escapes, because the orphan backslash is
itself a 400.
"""

from __future__ import annotations

from careeros.modules.bot.formatting import MESSAGE_LIMIT, chunk_message, escape_md


def test_a_short_message_is_left_alone() -> None:
    assert chunk_message("hello") == ["hello"]


def test_a_message_exactly_at_the_limit_is_not_split() -> None:
    assert len(chunk_message("x" * MESSAGE_LIMIT)) == 1


def test_every_chunk_fits() -> None:
    text = "\n".join(f"line {i} " + "y" * 200 for i in range(200))
    assert all(len(c) <= MESSAGE_LIMIT for c in chunk_message(text))


def test_nothing_is_lost_in_the_split() -> None:
    text = "\n".join(f"line {i}" for i in range(3000))
    assert "\n".join(chunk_message(text)) == text


def test_the_split_prefers_line_boundaries() -> None:
    text = "\n".join("z" * 100 for _ in range(200))
    for chunk in chunk_message(text):
        assert not chunk.startswith("z" * 100 + "z"), "a line was cut mid-way unnecessarily"


def test_a_single_oversized_line_is_still_split() -> None:
    chunks = chunk_message("q" * (MESSAGE_LIMIT * 2 + 5))
    assert len(chunks) == 3
    assert "".join(chunks) == "q" * (MESSAGE_LIMIT * 2 + 5)


def test_a_cut_never_orphans_an_escape_backslash() -> None:
    """`\\` alone at the end of a MarkdownV2 message is a 400, not a stray character."""
    line = escape_md("a." * MESSAGE_LIMIT)  # every other character becomes an escape pair
    for chunk in chunk_message(line):
        trailing = len(chunk) - len(chunk.rstrip("\\"))
        assert trailing % 2 == 0, "a chunk ended on a backslash that escapes the next chunk"


def test_empty_chunks_are_dropped() -> None:
    assert chunk_message("a\n\n\nb") == ["a\n\n\nb"]
