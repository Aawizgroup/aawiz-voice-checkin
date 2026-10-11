"""The Aawiz adapter against a mocked Aawiz API: payloads, token refresh, 401 retry, timeouts."""

from __future__ import annotations

import json
import os
import stat
import sys

import httpx2 as httpx
import pytest

from server.aawiz_client import (
    CHAT_WS_PATH,
    ENTRIES_PATH,
    REFRESH_PATH,
    SUGGESTION_PATH,
    TICKET_PATH,
    WEEKLY_PATH,
    AawizError,
    AawizSessionEnded,
    load_session,
)
from tests.aawiz_fake import MOOD_IDS, Clock, FakeAawiz, FakeChatSocket, make_client


@pytest.fixture
def fake():
    return FakeAawiz(Clock())


def test_mood_entry_payload(tmp_path, fake):
    client = make_client(tmp_path, fake)
    client.create_mood_entry("Down")
    method, path, body = fake.requests[-1]
    assert (method, path) == ("POST", ENTRIES_PATH)
    assert body == {"mood_id": MOOD_IDS["Down"], "source": "manual"}


def test_unknown_mood_name_is_an_error_not_a_guess(tmp_path, fake):
    client = make_client(tmp_path, fake)
    with pytest.raises(AawizError):
        client.create_mood_entry("Ecstatic")
    assert ENTRIES_PATH not in fake.paths()


def test_weekly_and_suggestion_requests(tmp_path, fake):
    client = make_client(tmp_path, fake)
    assert client.weekly_report()["has_entries"] is True
    assert client.suggestion_today("Europe/Amsterdam")["title"]
    assert WEEKLY_PATH in fake.paths() and SUGGESTION_PATH in fake.paths()
    fake.suggestion = None
    assert client.suggestion_today("Europe/Amsterdam") is None  # 204


def test_ask_elin_uses_ticket_and_skips_the_replayed_greeting(tmp_path, fake):
    chat = FakeChatSocket(reply="Nice work on the report.")
    client = make_client(tmp_path, fake, chat)
    assert client.ask_elin("I finished the report") == "Nice work on the report."
    assert TICKET_PATH in fake.paths()
    assert chat.urls[0].startswith(f"wss://aawiz.example/{CHAT_WS_PATH}?ticket=")
    types = [f["type"] for f in chat.sent]
    assert types == ["client.pong", "client.message"] or types == ["client.message", "client.pong"]
    sent = next(f for f in chat.sent if f["type"] == "client.message")
    assert sent["data"]["content"] == "I finished the report"
    assert sent["data"]["temp_id"]


def test_refresh_happens_before_expiry_and_saves_the_rotated_token(tmp_path, fake):
    client = make_client(tmp_path, fake)
    client.weekly_report()
    assert fake.paths().count(REFRESH_PATH) == 1
    assert load_session(client.session_file)["refresh_token"] != "rt-1"  # rotated and saved

    fake.clock.t += 200  # still 100 s left: no refresh
    client.weekly_report()
    assert fake.paths().count(REFRESH_PATH) == 1

    fake.clock.t += 80  # 20 s left, inside the 30 s margin: refresh before the call
    client.weekly_report()
    assert fake.paths().count(REFRESH_PATH) == 2
    assert fake.requests[-1][1] == WEEKLY_PATH
    assert fake.requests[-2][1] == REFRESH_PATH


def test_refresh_without_rotation_keeps_the_same_token(tmp_path, fake):
    fake.rotate = False
    client = make_client(tmp_path, fake)
    client.weekly_report()
    assert load_session(client.session_file)["refresh_token"] == "rt-1"


def test_a_401_refreshes_once_and_retries_once(tmp_path, fake):
    client = make_client(tmp_path, fake)
    client.weekly_report()
    before = len(fake.requests)
    fake.reject_next = 1
    assert client.weekly_report()["has_entries"] is True
    assert [p for _, p, _ in fake.requests[before:]] == [WEEKLY_PATH, REFRESH_PATH, WEEKLY_PATH]


def test_a_second_401_ends_the_session_without_looping(tmp_path, fake):
    client = make_client(tmp_path, fake)
    client.weekly_report()
    before = len(fake.requests)
    fake.reject_next = 5
    with pytest.raises(AawizSessionEnded):
        client.weekly_report()
    assert len(fake.requests) - before == 3  # call, refresh, one retry


def test_expired_refresh_token_means_session_ended(tmp_path, fake):
    fake.refresh_tokens = set()
    client = make_client(tmp_path, fake)
    with pytest.raises(AawizSessionEnded) as err:
        client.weekly_report()
    assert err.value.speech == "Your Aawiz demo session has ended. Please run make aawiz-login."


def test_timeouts_become_a_calm_error(tmp_path, fake):
    client = make_client(tmp_path, fake)
    fake.timeout_paths.add(WEEKLY_PATH)
    with pytest.raises(AawizError) as err:
        client.weekly_report()
    assert not isinstance(err.value, AawizSessionEnded)
    assert "couldn't reach Aawiz" in err.value.speech


def test_a_slow_read_is_tried_once_more_but_a_write_never(tmp_path, fake):
    client = make_client(tmp_path, fake)
    client.moods()
    slow = {WEEKLY_PATH: 1, ENTRIES_PATH: 1}
    original = fake.handle

    def handle_once_slow(request):
        path = request.url.path.lstrip("/")
        if slow.get(path):
            slow[path] -= 1
            fake.requests.append((request.method, path, None))
            raise httpx.ReadTimeout("slow", request=request)
        return original(request)

    fake.handle = handle_once_slow
    client._http._transport = httpx.MockTransport(handle_once_slow)
    assert client.weekly_report()["has_entries"] is True  # GET: retried
    with pytest.raises(AawizError):
        client.create_mood_entry("Down")  # POST: not repeated
    assert fake.paths().count(ENTRIES_PATH) == 1


def test_chat_timeout_is_handled(tmp_path, fake):
    class Silent(FakeChatSocket):
        def send(self, raw):
            self.sent.append(json.loads(raw))  # never answers

    client = make_client(tmp_path, fake, Silent())
    with pytest.raises(AawizError):
        client.ask_elin("hello")


def test_session_file_keeps_only_tokens_needed(tmp_path, fake):
    client = make_client(tmp_path, fake)
    client.weekly_report()
    saved = json.loads(client.session_file.read_text())
    assert set(saved) <= {"refresh_token", "session_token"}
    assert not any(t in client.session_file.read_text() for t in fake.valid_access)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_session_file_is_owner_only(tmp_path, fake):
    client = make_client(tmp_path, fake)
    client.weekly_report()
    mode = stat.S_IMODE(os.stat(client.session_file).st_mode)
    assert mode == 0o600
