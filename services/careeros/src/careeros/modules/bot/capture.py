"""Deciding whether a message is a job description.

The bot has no "paste a JD" mode on purpose — forwarding something and getting a
scored card back is the whole point. That makes this predicate the boundary
between capture and chatter, so it errs toward ignoring: a wrongly ingested
"thanks!" is noise in the pipeline, a wrongly ignored JD is one retry.
"""

from __future__ import annotations

import re

#: Shorter than this is conversation, not a job description.
MIN_LENGTH = 80

_URL = re.compile(r"https?://\S+")


def looks_like_job_description(text: str) -> bool:
    """True when a message should be captured as an opportunity."""
    text = text.strip()
    if not text or text.startswith("/"):
        return False  # commands are never content
    if _URL.match(text):
        return True  # a bare link is an explicit "look at this"
    return len(text) >= MIN_LENGTH
