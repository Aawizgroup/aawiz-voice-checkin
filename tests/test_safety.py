"""Crisis language: safety message with regional resources, and nothing stored."""

import pytest

from server.config import policy
from server.safety import contains_crisis, safety_message
from tests.conftest import mcp_client, payload

CRISIS = [
    "I want to end my life",
    "honestly I could kill myself",
    "there is no reason to live",
    "I don't want to live anymore",
    "I've been thinking about suicide",
    "Ik denk aan zelfmoord",
]
NOT_CRISIS = [
    "today was a hard day",
    "I am so tired of this project",
    "work is killing me slowly but it's fine",
    "I want to diet before summer",
    "a die-hard fan of the weekend",
]
NL_RESOURCES = ["113", "0800-0113", "113.nl", "112"]


@pytest.mark.parametrize("text", CRISIS)
def test_crisis_phrases_are_detected(text):
    assert contains_crisis(text)


@pytest.mark.parametrize("text", NOT_CRISIS)
def test_ordinary_hard_days_do_not_trigger(text):
    assert not contains_crisis(text)


def test_every_phrase_in_the_config_is_detected():
    for phrase in policy()["crisis_phrases"]:
        assert contains_crisis(f"lately {phrase}, honestly"), phrase


def test_safety_message_has_nl_resources_by_default():
    msg = safety_message("NL")
    assert "You are not alone" in msg
    for r in NL_RESOURCES:
        assert r in msg


def test_resources_are_configurable_per_region():
    msg = safety_message("US")
    assert "988" in msg and "911" in msg


@pytest.mark.anyio
@pytest.mark.parametrize("text", CRISIS[:3])
async def test_crisis_note_returns_safety_and_stores_nothing(live_server, text):
    async with mcp_client(live_server["url"], "token-a") as client:
        await client.call_tool("set_consent", {"scores": True, "notes": True})
        result = payload(
            await client.call_tool(
                "log_checkin", {"mood": 1, "energy": 1, "stress": 5, "note": text}
            )
        )
    assert result["safety"] is True and result["stored"] is False
    for r in NL_RESOURCES:
        assert r in result["speech"]
    assert live_server["store"].count("user_a") == 0


@pytest.mark.anyio
async def test_a_hard_day_note_is_stored_normally(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        await client.call_tool("set_consent", {"scores": True, "notes": True})
        result = payload(
            await client.call_tool(
                "log_checkin", {"mood": 2, "energy": 2, "stress": 4, "note": "today was a hard day"}
            )
        )
    assert result["stored"] is True and "safety" not in result
    assert live_server["store"].checkins("user_a")[0].note == "today was a hard day"


@pytest.mark.anyio
async def test_crisis_context_in_micro_action_returns_safety(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        result = payload(
            await client.call_tool("suggest_micro_action", {"context": "no reason to live"})
        )
    assert result["safety"] is True and result["action"] is None
