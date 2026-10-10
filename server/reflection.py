"""Weekly reflection: the last 7 days against the 7 before. Pure functions, easy to test."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from statistics import mean

from server.storage import Checkin

SCORES = ("mood", "energy", "stress")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_WORDS = 35

NOT_ENOUGH = (
    "Not enough data yet. A few more check-ins this week and I can reflect on how it went."
)


def day_score(c: Checkin) -> int:
    """Higher means a lighter day: good mood, good energy, low stress."""
    return c.mood + c.energy + (6 - c.stress)


def _avg(items: Sequence[Checkin], key: str) -> float:
    return round(mean(getattr(c, key) for c in items), 2)


def _direction(now: float, before: float | None, threshold: float) -> str:
    if before is None:
        return "steady"
    diff = now - before
    if diff >= threshold:
        return "up"
    if diff <= -threshold:
        return "down"
    return "steady"


_PHRASES = {
    "mood": {"up": "a brighter mood", "down": "a lower mood", "steady": "a steady mood"},
    "energy": {"up": "more energy", "down": "lower energy", "steady": "steady energy"},
    "stress": {"up": "more stress", "down": "less stress", "steady": "steady stress"},
}


def _sentence(direction: dict[str, str], best: date, hardest: date, compared: bool) -> str:
    opening = "Compared with last week, your check-ins suggest" if compared else (
        "There is no earlier week to compare yet, but your check-ins suggest"
    )
    parts = [_PHRASES[k][direction[k]] for k in SCORES]
    body = f"{opening} {parts[0]}, {parts[1]} and {parts[2]}"
    if best == hardest:
        text = f"{body}; {WEEKDAYS[best.weekday()]} was your only day so far."
    else:
        text = (
            f"{body}; {WEEKDAYS[best.weekday()]} felt lightest and "
            f"{WEEKDAYS[hardest.weekday()]} was the hardest."
        )
    if len(text.split()) > MAX_WORDS:  # keep the promise of a short spoken line
        text = f"{body}."
    return text


def weekly_reflection(
    checkins: Sequence[Checkin], today: date, threshold: float = 0.5, min_points: int = 3
) -> dict:
    start = today - timedelta(days=6)
    prev_start, prev_end = today - timedelta(days=13), today - timedelta(days=7)
    last = [c for c in checkins if start <= c.day <= today]
    prev = [c for c in checkins if prev_start <= c.day <= prev_end]

    result: dict = {
        "data_points": len(last),
        "period": {"from": start.isoformat(), "to": today.isoformat()},
    }
    if len(last) < min_points:
        result.update(enough_data=False, sentence=NOT_ENOUGH)
        return result

    averages = {k: _avg(last, k) for k in SCORES}
    previous = {k: _avg(prev, k) for k in SCORES} if prev else None
    direction = {
        k: _direction(averages[k], previous[k] if previous else None, threshold) for k in SCORES
    }

    by_day: dict[date, list[int]] = {}
    for c in last:
        by_day.setdefault(c.day, []).append(day_score(c))
    day_avg = {d: mean(v) for d, v in by_day.items()}
    best = max(day_avg, key=lambda d: (day_avg[d], -d.toordinal()))
    hardest = min(day_avg, key=lambda d: (day_avg[d], d.toordinal()))

    result.update(
        enough_data=True,
        averages=averages,
        previous_averages=previous,
        direction=direction,
        best_day={"date": best.isoformat(), "weekday": WEEKDAYS[best.weekday()]},
        hardest_day={"date": hardest.isoformat(), "weekday": WEEKDAYS[hardest.weekday()]},
        sentence=_sentence(direction, best, hardest, previous is not None),
    )
    return result
