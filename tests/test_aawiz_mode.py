"""Aawiz mode through a real MCP server, with the Aawiz API mocked: mapping, safety, guard."""

from __future__ import annotations

from dataclasses import replace

import pytest

from server.aawiz_client import ENTRIES_PATH, SESSION_ENDED, UNAVAILABLE, WEEKLY_PATH
from server.aawiz_service import APP_MANAGED, ELIN_FILTERED, MOOD_MAP
from server.app import create_app
from server.safety import banned_words_in, safety_message
from tests.aawiz_fake import MOOD_IDS, Clock, FakeAawiz, FakeChatSocket, make_client
from tests.conftest import ServerThread, free_port, mcp_client, payload

CRISIS = "I want to end my life"


@pytest.fixture
def fake():
    return FakeAawiz(Clock())


@pytest.fixture
def chat():
    return FakeChatSocket()


@pytest.fixture
def aawiz_server(settings, tmp_path, fake, chat):
    client = make_client(tmp_path, fake, chat)
    port = free_port()
    app = create_app(replace(settings, backend_mode="aawiz"), port=port, client=client)
    thread = ServerThread(app, port)
    thread.start()
    yield {"url": f"http://127.0.0.1:{port}", "store": app.state.store}
    thread.stop()


async def call(url: str, tool: str, args: dict | None = None) -> dict:
    async with mcp_client(url, "token-a") as client:
        return payload(await client.call_tool(tool, args or {}))


async def consent(url: str) -> None:
    await call(url, "set_consent", {"scores": True, "notes": True})


@pytest.mark.anyio
async def test_eight_tools_in_aawiz_mode(aawiz_server):
    async with mcp_client(aawiz_server["url"], "token-a") as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert len(names) == 8 and "ask_elin" in names


@pytest.mark.anyio
async def test_seven_tools_in_local_mode(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert len(names) == 7 and "ask_elin" not in names


@pytest.mark.anyio
@pytest.mark.parametrize("mood", [1, 2, 3, 4, 5])
async def test_log_checkin_maps_the_mood_score(aawiz_server, fake, mood):
    url = aawiz_server["url"]
    await consent(url)
    data = await call(url, "log_checkin", {"mood": mood, "energy": 3, "stress": 4, "note": "ok"})
    assert data["stored"] is True and data["aawiz"] is True
    method, path, body = fake.requests[-1]
    assert (method, path) == ("POST", ENTRIES_PATH)
    assert body == {"mood_id": MOOD_IDS[MOOD_MAP[mood]], "source": "manual"}
    assert "in Aawiz" in data["speech"]
    # energy, stress and the note stay in the local mirror only
    assert aawiz_server["store"].checkins("user_a")[-1].stress == 4


@pytest.mark.anyio
async def test_without_consent_nothing_is_sent_to_aawiz(aawiz_server, fake):
    data = await call(aawiz_server["url"], "log_checkin", {"mood": 2, "energy": 2, "stress": 2})
    assert data["stored"] is False
    assert fake.requests == []


@pytest.mark.anyio
async def test_weekly_reflection_speaks_the_aawiz_report(aawiz_server):
    data = await call(aawiz_server["url"], "get_weekly_reflection")
    assert data["source"] == "aawiz"
    assert "You mostly felt happy." in data["speech"]
    assert "going up" in data["speech"] and "3 days in a row" in data["speech"]


@pytest.mark.anyio
async def test_suggestion_from_elin_and_curated_fallback(aawiz_server, fake):
    data = await call(aawiz_server["url"], "suggest_micro_action")
    assert data["action"] == "elin_suggests"
    assert data["speech"].startswith("Elin suggests: A free jazz evening")
    assert "music helps you unwind" in data["speech"]

    fake.suggestion = None
    data = await call(aawiz_server["url"], "suggest_micro_action")
    assert data["action"] != "elin_suggests"
    assert data["speech"].startswith("Elin has no suggestion for today")


@pytest.mark.anyio
async def test_ask_elin_returns_her_reply(aawiz_server, chat):
    chat.reply = "Good to hear from you. How did the report go?"
    data = await call(aawiz_server["url"], "ask_elin", {"message": "Hi Elin, quick test."})
    assert data["sent"] is True
    assert data["speech"] == "Good to hear from you. How did the report go?"


@pytest.mark.anyio
async def test_privacy_and_delete_point_to_the_app_and_never_touch_the_account(aawiz_server, fake):
    url = aawiz_server["url"]
    privacy = await call(url, "get_privacy_summary")
    assert privacy["speech"].endswith(APP_MANAGED.strip())
    await consent(url)
    await call(url, "log_checkin", {"mood": 3, "energy": 3, "stress": 3})
    deleted = await call(url, "delete_my_data")
    assert deleted["deleted_rows"] >= 1
    assert deleted["speech"].endswith(APP_MANAGED.strip())
    assert aawiz_server["store"].checkins("user_a") == []
    # only the entry POST above reached Aawiz: no account, profile or delete endpoint
    assert all(m in ("GET", "POST") for m, _, _ in fake.requests)
    assert not any("account" in p or "profile" in p or "me/" in p for p in fake.paths())


@pytest.mark.anyio
async def test_expired_session_is_spoken_clearly(aawiz_server, fake):
    fake.refresh_tokens = set()
    url = aawiz_server["url"]
    assert (await call(url, "get_weekly_reflection"))["speech"] == SESSION_ENDED
    assert (await call(url, "suggest_micro_action"))["speech"] == SESSION_ENDED
    assert (await call(url, "ask_elin", {"message": "hi"}))["speech"] == SESSION_ENDED
    await consent(url)
    data = await call(url, "log_checkin", {"mood": 3, "energy": 3, "stress": 3})
    assert data["stored"] is True and data["aawiz"] is False
    assert SESSION_ENDED in data["speech"]


@pytest.mark.anyio
async def test_timeout_is_spoken_calmly(aawiz_server, fake):
    fake.timeout_paths.add(WEEKLY_PATH)
    data = await call(aawiz_server["url"], "get_weekly_reflection")
    assert data["speech"] == UNAVAILABLE


# --- safety first: crisis language never leaves the machine ---------------


@pytest.mark.anyio
async def test_crisis_note_sends_nothing_to_aawiz(aawiz_server, fake):
    url = aawiz_server["url"]
    await consent(url)
    data = await call(url, "log_checkin", {"mood": 1, "energy": 1, "stress": 5, "note": CRISIS})
    assert data["safety"] is True and data["speech"] == safety_message("NL")
    assert fake.requests == []
    assert aawiz_server["store"].checkins("user_a") == []


@pytest.mark.anyio
async def test_crisis_context_and_message_send_nothing_to_aawiz(aawiz_server, fake, chat):
    url = aawiz_server["url"]
    s1 = await call(url, "suggest_micro_action", {"context": CRISIS})
    s2 = await call(url, "ask_elin", {"message": f"Elin, {CRISIS}"})
    assert s1["speech"] == s2["speech"] == safety_message("NL")
    assert fake.requests == [] and chat.sent == []


# --- the banned-word guard covers everything Aawiz sends back --------------


@pytest.mark.anyio
async def test_guard_filters_banned_words_from_aawiz_responses(aawiz_server, fake, chat):
    url = aawiz_server["url"]
    fake.weekly["weekly_reflection"] = ["You mostly felt depressed this week."]
    fake.suggestion["reason"] = "It may ease your anxiety symptoms."
    chat.reply = "That sounds like burnout; a therapist could help."
    outputs = [
        await call(url, "get_weekly_reflection"),
        await call(url, "suggest_micro_action"),
        await call(url, "ask_elin", {"message": "I'm tired"}),
    ]
    for data in outputs:
        assert banned_words_in(data["speech"]) == [], data["speech"]
    assert outputs[2]["speech"] == ELIN_FILTERED
    # only the offending parts are dropped, the useful rest is still spoken
    assert "going up" in outputs[0]["speech"]
    assert outputs[1]["speech"] == "Elin suggests: A free jazz evening at the library."
