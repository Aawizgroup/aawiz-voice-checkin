"""Picks one low-risk micro-action from the curated list. Deterministic on purpose."""

from __future__ import annotations

from server.storage import Checkin

_CONTEXT_HINTS = (
    (("stress", "tense", "overwhelm", "pressure", "busy"), "breathing"),
    (("tired", "exhausted", "sleepy", "drained", "low energy"), "walk"),
    (("lonely", "alone", "isolated"), "friend"),
    (("screen", "eyes", "headache"), "screen_break"),
    (("good", "great", "happy", "proud"), "one_good_thing"),
)


def choose_action(latest: Checkin | None, context: str | None = None) -> str:
    """Return a key from policy['micro_actions'].

    The latest scores decide first, in a fixed order, so the same check-in
    always gives the same suggestion. Without scores, a few plain words in
    the context are used; otherwise water.
    """
    if latest is not None:
        if latest.stress >= 4:
            return "breathing"
        if latest.energy <= 2:
            return "walk"
        if latest.mood <= 2:
            return "friend"
        if latest.sleep_hours is not None and latest.sleep_hours < 6:
            return "screen_break"
        if latest.mood >= 4:
            return "one_good_thing"
        return "water"
    text = (context or "").lower()
    for words, key in _CONTEXT_HINTS:
        if any(w in text for w in words):
            return key
    return "water"
