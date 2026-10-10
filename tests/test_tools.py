"""Tool behaviour: validation, consent, reflection math, not-enough-data, micro-actions."""

from datetime import date

import pytest

from server.actions import choose_action
from server.reflection import MAX_WORDS, _direction, weekly_reflection
from server.service import CheckinService
from server.storage import Checkin, Store
from tests.conftest import mcp_client, payload

TODAY = date(2026, 10, 10)  # a Saturday


def c(day: str, mood: int, energy: int, stress: int, sleep=None) -> Checkin:
    return Checkin(date.fromisoformat(day), mood, energy, stress, sleep, None)


# --- range validation, through the real MCP transport ----------------------


@pytest.mark.anyio
@pytest.mark.parametrize("bad", [0, 6, 2.5, "3", None])
async def test_out_of_range_or_non_integer_scores_are_rejected(live_server, bad):
    async with mcp_client(live_server["url"], "token-a") as client:
        await client.call_tool("set_consent", {"scores": True, "notes": True})
        result = await client.call_tool("log_checkin", {"mood": bad, "energy": 3, "stress": 3})
    assert result.is_error
    assert live_server["store"].count("user_a") == 0


@pytest.mark.anyio
async def test_sleep_and_note_limits_are_enforced(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        await client.call_tool("set_consent", {"scores": True, "notes": True})
        too_much_sleep = await client.call_tool(
            "log_checkin", {"mood": 3, "energy": 3, "stress": 3, "sleep_hours": 15}
        )
        long_note = await client.call_tool(
            "log_checkin", {"mood": 3, "energy": 3, "stress": 3, "note": "x" * 281}
        )
    assert too_much_sleep.is_error and long_note.is_error
    assert live_server["store"].count("user_a") == 0


# --- consent enforcement ----------------------------------------------------


@pytest.mark.anyio
async def test_without_scores_consent_nothing_is_stored(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        result = payload(
            await client.call_tool("log_checkin", {"mood": 3, "energy": 3, "stress": 3})
        )
    assert result["stored"] is False
    assert live_server["store"].count("user_a") == 0


@pytest.mark.anyio
async def test_without_notes_consent_the_note_is_dropped(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        await client.call_tool("set_consent", {"scores": True, "notes": False})
        result = payload(
            await client.call_tool(
                "log_checkin", {"mood": 3, "energy": 3, "stress": 3, "note": "a long day"}
            )
        )
    assert result["stored"] is True and result["note_kept"] is False
    rows = live_server["store"].checkins("user_a")
    assert len(rows) == 1 and rows[0].note is None


@pytest.mark.anyio
async def test_with_notes_consent_the_note_is_kept(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        await client.call_tool("set_consent", {"scores": True, "notes": True})
        await client.call_tool(
            "log_checkin", {"mood": 3, "energy": 3, "stress": 3, "note": "a long day"}
        )
    assert live_server["store"].checkins("user_a")[0].note == "a long day"


# --- weekly reflection math -------------------------------------------------

FIXTURE = [
    # previous week: 27 Sep to 3 Oct
    c("2026-09-28", 4, 4, 2),
    c("2026-09-30", 4, 4, 2),
    c("2026-10-02", 4, 4, 2),
    # last 7 days: 4 to 10 Oct
    c("2026-10-06", 3, 3, 4),  # Tuesday, day score 8
    c("2026-10-08", 2, 2, 5),  # Thursday, day score 5, the hardest
    c("2026-10-10", 4, 3, 3),  # Saturday, day score 10, the lightest
]


def test_reflection_averages_directions_and_days():
    r = weekly_reflection(FIXTURE, TODAY)
    assert r["enough_data"] is True and r["data_points"] == 3
    assert r["averages"] == {"mood": 3.0, "energy": 2.67, "stress": 4.0}
    assert r["previous_averages"] == {"mood": 4.0, "energy": 4.0, "stress": 2.0}
    assert r["direction"] == {"mood": "down", "energy": "down", "stress": "up"}
    assert r["best_day"] == {"date": "2026-10-10", "weekday": "Saturday"}
    assert r["hardest_day"] == {"date": "2026-10-08", "weekday": "Thursday"}
    assert r["sentence"] == (
        "Compared with last week, your check-ins suggest a lower mood, lower energy and more "
        "stress; Saturday felt lightest and Thursday was the hardest."
    )
    assert len(r["sentence"].split()) <= MAX_WORDS


def test_direction_threshold_is_half_a_point():
    assert _direction(3.5, 3.0, 0.5) == "up"
    assert _direction(2.5, 3.0, 0.5) == "down"
    assert _direction(3.49, 3.0, 0.5) == "steady"
    assert _direction(3.0, None, 0.5) == "steady"


def test_reflection_without_a_previous_week_still_reads_well():
    r = weekly_reflection(FIXTURE[3:], TODAY)
    assert r["previous_averages"] is None
    assert r["direction"] == {"mood": "steady", "energy": "steady", "stress": "steady"}
    assert len(r["sentence"].split()) <= MAX_WORDS


def test_not_enough_data_below_three_checkins():
    r = weekly_reflection(FIXTURE[3:5], TODAY)
    assert r["enough_data"] is False
    assert r["data_points"] == 2
    assert r["sentence"].startswith("Not enough data yet")


def test_service_reflection_uses_only_this_users_rows(tmp_path):
    store = Store(tmp_path / "x.sqlite3")
    for row in FIXTURE:
        store.add_checkin("user_a", row)
    store.add_checkin("user_b", c("2026-10-09", 1, 1, 5))
    result = CheckinService(store, "NL").weekly_reflection("user_a", today=TODAY)
    assert result["data_points"] == 3 and result["speech"] == result["sentence"]


# --- micro-actions ----------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (c("2026-10-10", 2, 3, 4), "breathing"),
        (c("2026-10-10", 3, 2, 3), "walk"),
        (c("2026-10-10", 2, 3, 3), "friend"),
        (c("2026-10-10", 3, 3, 3, sleep=5), "screen_break"),
        (c("2026-10-10", 4, 3, 2), "one_good_thing"),
        (c("2026-10-10", 3, 3, 3), "water"),
    ],
)
def test_micro_action_follows_the_latest_scores(row, expected):
    assert choose_action(row) == expected
    assert all(choose_action(row) == expected for _ in range(20))  # deterministic


def test_micro_action_without_scores_uses_context_or_water():
    assert choose_action(None, "I feel so tense") == "breathing"
    assert choose_action(None, None) == "water"
