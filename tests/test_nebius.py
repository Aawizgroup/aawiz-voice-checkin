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
    assert data["brain"] == FALLBACK_LABEL  # the label never claims Nebius for scripted words
    assert [t["tool"] for t in data["tools"]] == ["get_weekly_reflection"]


def test_a_banned_reply_after_a_tool_call_does_not_repeat_the_tool(stack):
    args = {"mood": 3, "energy": 3, "stress": 3}
    fake = FakeNebius([tool_call("log_checkin", args)], "Saved. Watch out for burnout.")
    http = stack["start"](fake)
    data = turn(http, "My mood is 3, energy 3, stress 3.")
    assert data["reply"] == "Thanks, I've saved today's check-in."  # the tool's own line
    assert stack["store"].count("user_a") == stack["seeded"] + 1  # logged once, not twice
    assert data["brain"] == NEBIUS_LABEL  # the model chose the tool


def test_an_empty_reply_is_answered_by_the_scripted_brain(stack):
    # Seen live: a reasoning model that runs out of output tokens returns no content at all.
    fake = FakeNebius("")
    http = stack["start"](fake)
    data = turn(http, "What do you keep about me?")
    assert "90 days" in data["reply"] and data["brain"] == FALLBACK_LABEL


def test_a_nebius_turn_clears_a_stale_scripted_question(stack):
    # Turn 1 asks for consent in code; turn 2 is handled by the model; turn 3 falls back and
    # must not be read as an answer to the consent question.
    args = {"scores": True, "notes": True}
    fake = FakeNebius([tool_call("set_consent", args)], "Got it, I will keep them.", 500)
    http = stack["start"](fake)
    assert "may I store" in turn(http, "Alexa, open Aawiz check-in.")["reply"]
    assert turn(http, "Yes, scores and notes.")["brain"] == NEBIUS_LABEL
    third = turn(http, "What do you keep about me?")
    assert "90 days" in third["reply"], third["reply"]


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
    fake = FakeNebius("Hello. What would you like to know?", "Got it.")
    http = stack["start"](fake)
    turn(http, "Alexa, hello.")
    turn(http, "Tell me more.")
    roles = [m["role"] for m in fake.requests[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_missing_key_or_model_means_scripted(settings):
    app = create_sim_app(
        mcp_url="http://127.0.0.1:9/mcp", token="t", mode="nebius", tts=None, demo=False,
        nebius_model=MODEL,
    )  # fmt: skip
    routes = {r.path for r in app.routes}
    assert "/api/turn" in routes  # it starts, on the scripted brain (no key in tests)


@pytest.mark.parametrize("answer, deleted", [("Yes, delete it.", True), ("No, keep it.", False)])
def test_delete_is_confirmed_in_code_not_by_the_model(stack, answer, deleted):
    # Seen live: the model called delete_my_data straight away, without asking.
    fake = FakeNebius([tool_call("delete_my_data")])
    http = stack["start"](fake)
    first = turn(http, "Delete my data.")
    assert "Should I delete everything" in first["reply"] and first["tools"] == []
    assert stack["store"].count("user_a") == stack["seeded"]  # nothing deleted yet
    second = turn(http, answer)
    assert len(fake.requests) == 1  # the yes or no is not sent to Nebius
    assert (stack["store"].count("user_a") == 0) is deleted
    assert [t["tool"] for t in second["tools"]] == (["delete_my_data"] if deleted else [])


def test_when_the_model_asked_first_a_yes_deletes_without_a_second_question(stack):
    # Seen live with two models: they ask "are you sure you want to delete?" in their own
    # words, then call the tool after the yes. That yes is the confirmation.
    fake = FakeNebius(
        "Are you sure you want to permanently delete all your check-ins?",
        [tool_call("delete_my_data")],
        "Done, everything is deleted.",
    )
    http = stack["start"](fake)
    assert "delete" in turn(http, "Delete my data.")["reply"]
    assert stack["store"].count("user_a") == stack["seeded"]
    second = turn(http, "Yes, delete it.")
    assert [t["tool"] for t in second["tools"]] == ["delete_my_data"]
    assert stack["store"].count("user_a") == 0


def test_a_yes_to_something_else_never_deletes(stack):
    fake = FakeNebius([tool_call("delete_my_data")])  # the consent question comes from code
    http = stack["start"](fake)
    turn(http, "Alexa, open Aawiz check-in.")
    data = turn(http, "Yes.")
    assert "Should I delete everything" in data["reply"]
    assert stack["store"].count("user_a") == stack["seeded"]


def test_the_model_cannot_grant_consent_on_its_own(stack):
    # Seen live: the model called set_consent before logging, without the person agreeing.
    stack["store"].delete_user("user_a")  # a new person: no consent yet
    args = {"mood": 3, "energy": 3, "stress": 3}
    fake = FakeNebius(
        [tool_call("set_consent", {"scores": True, "notes": True}), tool_call("log_checkin", args)],
        "May I keep your scores and short notes?",
    )
    http = stack["start"](fake)
    data = turn(http, "My mood is 3, energy 3, stress 3.")
    assert stack["store"].get_consent("user_a").at is None  # no consent was recorded
    assert stack["store"].count("user_a") == 0  # so nothing was stored
    assert [t["tool"] for t in data["tools"]] == ["log_checkin"]  # set_consent never ran
    refused = fake.requests[1]["messages"][-2]
    assert "consent must come from the person" in refused["content"]


@pytest.mark.parametrize(
    "answer, scores, notes",
    [
        ("Yes, scores and notes.", True, True),
        ("Scores only please.", True, False),
        ("No.", False, False),
    ],
)
def test_what_the_person_said_decides_the_consent_not_the_model(stack, answer, scores, notes):
    stack["store"].delete_user("user_a")
    everything = {"scores": True, "notes": True}  # the model always asks for everything
    fake = FakeNebius([tool_call("set_consent", everything)], "Okay.")
    http = stack["start"](fake)
    turn(http, "Alexa, open Aawiz check-in.")
    turn(http, answer)
    consent = stack["store"].get_consent("user_a")
    assert (consent.scores, consent.notes) == (scores, notes)


@pytest.mark.parametrize(
    "said, asked, expected",
    [
        ("Yes, scores and notes.", False, (True, True)),
        ("Yes, scores only.", False, (True, False)),
        ("you may keep my scores", False, (True, False)),
        ("Yes.", True, (True, True)),
        ("No thanks", True, (False, False)),
        ("Yes.", False, None),  # a yes to nothing
        ("note: I said yes to the job", False, None),
        ("My mood is 3, energy 3, stress 3, note: ok", False, None),
        ("My mood is 3", True, None),
    ],
)
def test_what_counts_as_the_person_giving_consent(said, asked, expected):
    from simulator.brain import SessionState
    from simulator.brain_nebius import _consent_said

    state = SessionState()
    if asked:
        state.history = [{"role": "assistant", "content": "May I keep your scores and notes?"}]
    assert _consent_said(said, state) == expected


# --- the scores of a check-in come from the person, never from the model ---------


def last_checkin(store):
    return store.checkins("user_a")[-1]


def test_a_model_that_logs_a_message_without_numbers_stores_nothing(stack):
    # Seen live: after "Yes, you can keep my scores and notes." the model called set_consent
    # and log_checkin with scores it made up, and the check-in was stored.
    invented = {"mood": 3, "energy": 3, "stress": 3}
    fake = FakeNebius(
        [
            tool_call("set_consent", {"scores": True, "notes": True}),
            tool_call("log_checkin", invented),
        ],
        "Thanks. What are your mood, energy and stress today, each from 1 to 5?",
    )
    http = stack["start"](fake)
    turn(http, "Hi, I'd like to do my check-in.")
    data = turn(http, "Yes, you can keep my scores and notes.")
    assert stack["store"].count("user_a") == stack["seeded"]  # nothing new was stored
    assert [t["tool"] for t in data["tools"]] == ["set_consent"]  # log_checkin never ran
    refused = json.loads(fake.requests[1]["messages"][-1]["content"])
    assert refused["stored"] is False and "no mood or energy or stress" in refused["error"]
    assert "mood, energy and stress" in data["reply"]


def test_model_scores_are_replaced_by_what_the_person_said(stack):
    wrong = {"mood": 5, "energy": 5, "stress": 1, "sleep_hours": 9, "note": "feeling great"}
    fake = FakeNebius([tool_call("log_checkin", wrong)], "Saved.")
    http = stack["start"](fake)
    turn(http, "My mood is 2, energy 3 and stress 4, I slept 6 hours.")
    row = last_checkin(stack["store"])
    assert (row.mood, row.energy, row.stress, row.sleep_hours) == (2, 3, 4, 6)
    assert row.note is None  # the model's note was not the person's words


def test_one_missing_score_means_nothing_is_stored(stack):
    args = {"mood": 2, "energy": 3, "stress": 3}
    fake = FakeNebius([tool_call("log_checkin", args)], "And your stress, from 1 to 5?")
    http = stack["start"](fake)
    turn(http, "My mood is 2 and my energy is 3.")
    assert stack["store"].count("user_a") == stack["seeded"]
    assert "no stress" in fake.requests[1]["messages"][-1]["content"]


def test_three_bare_numbers_count_only_as_the_answer_to_the_scores_question(stack):
    args = {"mood": 1, "energy": 1, "stress": 1}
    fake = FakeNebius(
        "How are your mood, energy and stress today, each from 1 to 5?",
        [tool_call("log_checkin", args)],
        "Saved.",
        [tool_call("log_checkin", args)],
        "Tell me your mood, energy and stress.",
    )
    http = stack["start"](fake)
    turn(http, "Hello there.")
    turn(http, "4, 2 and 5.")
    row = last_checkin(stack["store"])
    assert (row.mood, row.energy, row.stress) == (4, 2, 5)
    turn(http, "I walked 3 km 2 times in 4 days.")  # numbers, but nobody asked for scores
    assert stack["store"].count("user_a") == stack["seeded"] + 1


def test_the_first_check_in_turn_asks_for_consent_before_scores(stack):
    stack["store"].delete_user("user_a")  # a new person: no consent yet
    fake = FakeNebius("should not be used")
    http = stack["start"](fake)
    data = turn(http, "Hi, I'd like to do my check-in.")
    assert "may I store your daily scores and short notes" in data["reply"]
    assert "mood" not in data["reply"]  # consent first, scores after
    assert fake.requests == [] and data["tools"] == []
    assert data["brain"] == NEBIUS_LABEL


def test_scores_in_the_first_message_go_to_the_tool_which_checks_consent(stack):
    stack["store"].delete_user("user_a")
    args = {"mood": 2, "energy": 3, "stress": 4}
    fake = FakeNebius([tool_call("log_checkin", args)], "May I keep your scores first?")
    http = stack["start"](fake)
    data = turn(http, "I want to check in: mood 2, energy 3, stress 4.")
    assert [t["tool"] for t in data["tools"]] == ["log_checkin"]
    assert stack["store"].count("user_a") == 0  # the server refused: no consent
