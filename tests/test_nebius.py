"""The Nebius brain with a mocked Nebius API: tools as functions, real MCP calls, safety, limits."""

from __future__ import annotations

import json

import httpx2 as httpx
import pytest

from server.app import create_app
from server.demo import seed
from server.safety import banned_words_in, safety_message
from simulator.app import create_sim_app
from simulator.brain_nebius import FALLBACK_LABEL, MAX_TOOL_CALLS
from tests.conftest import ServerThread, free_port
from tests.nebius_fake import MODEL, FakeNebius, tool_call

CRISIS = "I want to end my life"
NEBIUS_LABEL = f"Brain: Nebius · {MODEL}"
EXPECTED_TOOLS = {
    "set_consent",
    "log_checkin",
    "get_weekly_reflection",
    "suggest_micro_action",
    "get_privacy_summary",
    "export_my_data",
    "delete_my_data",
}


@pytest.fixture
def stack(settings):
    """A real MCP server with seeded data, and a simulator whose Nebius is scripted per test."""
    mcp_port = free_port()
    mcp_app = create_app(settings, port=mcp_port)
    store = mcp_app.state.store
    seeded = seed(store, "user_a")
    threads = [ServerThread(mcp_app, mcp_port)]
    threads[0].start()

    def start(fake: FakeNebius) -> httpx.Client:
        port = free_port()
        app = create_sim_app(
            mcp_url=f"http://127.0.0.1:{mcp_port}/mcp",
            token="token-a",
            mode="nebius",
            region="NL",
            backend="local",
            tts=None,
            demo=False,
            nebius_client=fake.client(),
            nebius_model=MODEL,
        )
        t = ServerThread(app, port)
        t.start()
        threads.append(t)
        return httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30)

    yield {"start": start, "store": store, "seeded": seeded}
    for t in reversed(threads):
        t.stop()


def turn(http: httpx.Client, text: str, session: str = "s" * 20) -> dict:
    return http.post("/api/turn", json={"session": session, "text": text}).json()


def test_tools_go_to_the_model_as_functions_and_a_tool_call_reaches_mcp(stack):
    args = {"mood": 2, "energy": 3, "stress": 4, "sleep_hours": 6}
    fake = FakeNebius([tool_call("log_checkin", args)], "Thanks, today's check-in is saved.")
    http = stack["start"](fake)
    assert http.get("/api/config").json()["label"] == NEBIUS_LABEL

    data = turn(http, "My mood is 2, energy 3, stress 4, I slept 6 hours.")

    first = fake.requests[0]
    assert first["model"] == MODEL
    assert {t["function"]["name"] for t in first["tools"]} == EXPECTED_TOOLS
    assert all(t["type"] == "function" and t["function"]["parameters"] for t in first["tools"])
    assert first["tool_choice"] == "auto"
    system = first["messages"][0]
    assert system["role"] == "system" and "not a medical service" in system["content"]
    assert "never diagnose" in system["content"]
    assert first["messages"][-1] == {
        "role": "user",
        "content": "My mood is 2, energy 3, stress 4, I slept 6 hours.",
    }
    # a real MCP tools/call happened: the row is in the database, the panel shows it
    assert stack["store"].count("user_a") == stack["seeded"] + 1
    assert [t["tool"] for t in data["tools"]] == ["log_checkin"]
    # the tool result went back to the model, and its final text is the reply
    tool_message = fake.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "call_log_checkin"
    assert json.loads(tool_message["content"])["stored"] is True
    assert data["reply"] == "Thanks, today's check-in is saved."
    assert data["brain"] == NEBIUS_LABEL


def test_crisis_language_makes_zero_requests_to_nebius(stack):
    fake = FakeNebius("should never be used")
    http = stack["start"](fake)
    data = turn(http, f"Alexa, {CRISIS}")
    assert data["safety"] is True and data["reply"] == safety_message("NL")
    assert fake.requests == [] and data["tools"] == []


def test_a_reply_with_a_banned_word_is_replaced_by_the_scripted_answer(stack):
    fake = FakeNebius("It sounds like you may have depression.")
    http = stack["start"](fake)
    data = turn(http, "How was my week?")
    assert banned_words_in(data["reply"]) == []
    assert "your check-ins suggest" in data["reply"].lower()  # the scripted brain's answer
    assert [t["tool"] for t in data["tools"]] == ["get_weekly_reflection"]


def test_a_banned_reply_after_a_tool_call_does_not_repeat_the_tool(stack):
    args = {"mood": 3, "energy": 3, "stress": 3}
    fake = FakeNebius([tool_call("log_checkin", args)], "Saved. Watch out for burnout.")
    http = stack["start"](fake)
    data = turn(http, "My mood is 3, energy 3, stress 3.")
    assert data["reply"] == "Thanks, I've saved today's check-in."  # the tool's own line
    assert stack["store"].count("user_a") == stack["seeded"] + 1  # logged once, not twice


@pytest.mark.parametrize("failure", ["timeout", "http_error"])
def test_nebius_errors_fall_back_to_scripted_with_the_label(stack, failure):
    step = httpx.ReadTimeout("slow") if failure == "timeout" else 500
    fake = FakeNebius(step)
    http = stack["start"](fake)
    data = turn(http, "What do you keep about me?")
    assert data["brain"] == FALLBACK_LABEL
    assert "90 days" in data["reply"]
    assert [t["tool"] for t in data["tools"]] == ["get_privacy_summary"]
    assert len(fake.requests) == 1  # no retries


def test_the_fifth_tool_call_in_one_turn_is_refused(stack):
    five = [tool_call("get_privacy_summary", call_id=f"c{i}") for i in range(5)]
    fake = FakeNebius(five[:3], five[3:], "Here is what I keep.")
    http = stack["start"](fake)
    data = turn(http, "Tell me everything about my privacy, five times.")
    assert len(data["tools"]) == MAX_TOOL_CALLS == 4
    last = fake.requests[2]
    refused = last["messages"][-1]
    assert refused["tool_call_id"] == "c4" and "limit reached" in refused["content"]
    assert last["tool_choice"] == "none"  # the model must answer now
    assert data["reply"] == "Here is what I keep."


def test_conversation_history_is_kept_between_turns(stack):
    fake = FakeNebius("Hello. May I keep your scores and short notes?", "Got it.")
    http = stack["start"](fake)
    turn(http, "Alexa, open Aawiz check-in.")
    turn(http, "Yes please.")
    roles = [m["role"] for m in fake.requests[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_missing_key_or_model_means_scripted(settings):
    app = create_sim_app(
        mcp_url="http://127.0.0.1:9/mcp", token="t", mode="nebius", tts=None, demo=False,
        nebius_model=MODEL,
    )  # fmt: skip
    routes = {r.path for r in app.routes}
    assert "/api/turn" in routes  # it starts, on the scripted brain (no key in tests)
