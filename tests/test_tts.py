"""Elin's voice: only reply text reaches text-to-speech; every failure falls back to the browser."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx2 as httpx
import pytest

from server.app import create_app
from server.safety import safety_message
from simulator.app import create_sim_app
from simulator.tts import DEFAULT_MODEL_ID, DEFAULT_VOICE_ID, ElevenLabsTTS
from tests.aawiz_fake import Clock, FakeAawiz, FakeChatSocket, make_client
from tests.conftest import ServerThread, free_port

CRISIS = "I want to end my life"


class FakeTTS:
    def __init__(self, fail: bool = False):
        self.texts: list[str] = []
        self.fail = fail

    def synthesize(self, text: str) -> bytes | None:
        self.texts.append(text)
        return None if self.fail else b"ID3fake-mp3"


# --- the ElevenLabs client ---------------------------------------------------


def test_request_carries_only_the_text_model_and_key():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ID3audio")

    tts = ElevenLabsTTS("test-key", transport=httpx.MockTransport(handler))
    assert tts.synthesize("Thanks, I've saved today's check-in.") == b"ID3audio"
    req = seen[0]
    assert req.url.path == f"/v1/text-to-speech/{DEFAULT_VOICE_ID}"
    assert req.url.params["output_format"] == "mp3_44100_128"
    assert req.headers["xi-api-key"] == "test-key"
    assert json.loads(req.content) == {
        "text": "Thanks, I've saved today's check-in.",
        "model_id": DEFAULT_MODEL_ID,
    }


@pytest.mark.parametrize("failure", ["status", "network"])
def test_failures_return_none(failure):
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "network":
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(500, json={"detail": "busy"})

    tts = ElevenLabsTTS("test-key", transport=httpx.MockTransport(handler))
    assert tts.synthesize("Hello") is None


# --- the simulator -----------------------------------------------------------


@pytest.fixture
def stack(settings, tmp_path):
    """MCP server in aawiz mode (Aawiz API mocked) plus a simulator; `tts` is set per test."""
    fake, chat = FakeAawiz(Clock()), FakeChatSocket()
    mcp_port = free_port()
    mcp_app = create_app(
        replace(settings, backend_mode="aawiz"),
        port=mcp_port,
        client=make_client(tmp_path, fake, chat),
    )
    threads = [ServerThread(mcp_app, mcp_port)]
    threads[0].start()

    def start_sim(tts):
        port = free_port()
        app = create_sim_app(
            mcp_url=f"http://127.0.0.1:{mcp_port}/mcp",
            token="token-a",
            mode="scripted",
            region="NL",
            backend="aawiz",
            tts=tts,
        )
        t = ServerThread(app, port)
        t.start()
        threads.append(t)
        return httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20)

    yield {"start_sim": start_sim, "fake": fake, "chat": chat}
    for t in reversed(threads):
        t.stop()


def turn(http: httpx.Client, text: str) -> dict:
    return http.post("/api/turn", json={"session": "t", "text": text}).json()


def test_reply_text_only_goes_to_tts(stack):
    tts = FakeTTS()
    http = stack["start_sim"](tts)
    assert http.get("/api/config").json()["voice"] == "elin"
    turn(http, "Alexa, open Aawiz check-in.")
    turn(http, "Yes, scores and notes.")
    said = "My mood is 2, energy 3, stress 4, note: secret words about my boss"
    data = turn(http, said)
    audio = http.get(data["audio"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"
    assert tts.texts == [data["reply"]]
    assert "secret words" not in " ".join(tts.texts)
    assert http.get(data["audio"]).status_code == 404  # one-time id


def test_tts_failure_means_503_so_the_page_uses_browser_speech(stack):
    http = stack["start_sim"](FakeTTS(fail=True))
    data = turn(http, "What do you keep about me?")
    assert http.get(data["audio"]).status_code == 503
    assert data["reply"]  # the caption is there regardless


def test_missing_key_means_browser_speech(stack):
    http = stack["start_sim"](None)
    assert http.get("/api/config").json()["voice"] == "browser"
    assert "audio" not in turn(http, "What do you keep about me?")


def test_crisis_sends_nothing_to_aawiz_and_only_the_safety_text_to_tts(stack):
    tts = FakeTTS()
    http = stack["start_sim"](tts)
    for text in (f"Alexa, tell Elin {CRISIS}", f"My mood is 1, energy 1, stress 5, note {CRISIS}"):
        data = turn(http, text)
        assert data["safety"] is True
        http.get(data["audio"])
    assert stack["fake"].requests == [] and stack["chat"].sent == []
    assert tts.texts == [safety_message("NL")] * 2


def test_ask_elin_through_the_simulator(stack):
    http = stack["start_sim"](FakeTTS())
    stack["chat"].reply = "Nice. How did it feel to finish it?"
    data = turn(http, "Alexa, tell Elin I finished the report today.")
    assert data["reply"] == "Nice. How did it feel to finish it?"
    assert [t["tool"] for t in data["tools"]] == ["ask_elin"]


def test_a_refused_call_records_the_status_and_code_but_no_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": {"status": "invalid_api_key", "message": "x"}})

    tts = ElevenLabsTTS("test-key", transport=httpx.MockTransport(handler))
    assert tts.synthesize("Hello") is None
    assert tts.last_error == "401 invalid_api_key" and "test-key" not in tts.last_error
