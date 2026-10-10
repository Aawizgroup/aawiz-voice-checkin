"""Crisis language detection and the non-clinical language guard."""

from __future__ import annotations

import re
from functools import lru_cache

from server.config import policy


def _normalise(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def _crisis_pattern() -> re.Pattern[str]:
    phrases = sorted(policy()["crisis_phrases"], key=len, reverse=True)
    body = "|".join(re.escape(p.lower()) for p in phrases)
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)")


def contains_crisis(text: str | None) -> bool:
    """True when the text contains any listed crisis phrase as whole words."""
    if not text:
        return False
    return _crisis_pattern().search(_normalise(text)) is not None


def safety_message(region: str) -> str:
    p = policy()
    resources = p["resources"].get(region, p["resources"][p["default_region"]])
    return p["safety_message"].format(resources=resources)


@lru_cache(maxsize=1)
def _banned_pattern() -> re.Pattern[str]:
    stems = "|".join(re.escape(s) for s in policy()["banned_word_stems"])
    return re.compile(rf"\b(?:{stems})\w*", re.IGNORECASE)


def banned_words_in(text: str) -> list[str]:
    """Every banned clinical word found in the text (empty list means clean)."""
    return _banned_pattern().findall(text or "")
