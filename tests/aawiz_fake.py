"""A fake Aawiz API for tests: an httpx MockTransport plus a scripted chat WebSocket.

Response shapes follow the Aawiz.API source (allauth headless envelopes, mood tracker and
companion serializers, chat consumer frames). No network is used.
"""

from __future__ import annotations

import base64
import json
import uuid

import httpx2 as httpx

from server.aawiz_client import (
    ENTRIES_PATH,
    MOODS_PATH,
    REFRESH_PATH,
    SESSION_PATH,
    SUGGESTION_PATH,
    TICKET_PATH,
    WEEKLY_PATH,
    AawizClient,
    save_session,
)

MOODS = [
    ("Bright", 9), ("Neutral", 5), ("Anxious", 3), ("Happy", 8), ("Worry", 3),
    ("Heavy", 2), ("Down", 2), ("Bored", 4), ("No Idea", 5),
]  # fmt: skip
MOOD_IDS = {name: str(uuid.uuid5(uuid.NAMESPACE_DNS, name)) for name, _ in MOODS}


def make_jwt(exp: float, n: int) -> str:
    def enc(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'HS256'})}.{enc({'exp': int(exp), 'n': n})}.sig{n}"


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeAawiz:
    def __init__(self, clock: Clock):
        self.clock = clock
        self.requests: list[tuple[str, str, dict | None]] = []
        self.access_ttl = 300
        self.rotate = True
        self.refresh_tokens = {"rt-1"}
        self.valid_access: set[str] = set()
        self.reject_next = 0  # answer this many authenticated calls with 401
        self.timeout_paths: set[str] = set()
        self.weekly = {
            "has_entries": True,
            "weekly_mood": [],
            "top_tags": [],
            "mood_trend": [
                {"day": "Monday", "value": 1.0, "label": "Down"},
                {"day": "Wednesday", "value": 2.86, "label": "Happy"},
            ],
            "streak": {"days": [], "current_streak": 3, "longest_streak": 5},
            "weekly_reflection": ["This week, you checked in 4 times.", "You mostly felt happy."],
        }
        self.suggestion: dict | None = {
            "id": str(uuid.uuid4()),
            "kind": "event",
            "title": "A free jazz evening at the library",
            "reason": "You said music helps you unwind.",
            "reasons": ["You said music helps you unwind."],
        }
        self.n = 0

    # helpers ---------------------------------------------------------------

    def issue_access(self) -> str:
        self.n += 1
        token = make_jwt(self.clock() + self.access_ttl, self.n)
        self.valid_access.add(token)
        return token

    def paths(self) -> list[str]:
        return [p for _, p, _ in self.requests]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # the API ---------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))
        if path in self.timeout_paths:
            raise httpx.ReadTimeout("timed out", request=request)

        if path == REFRESH_PATH:
            token = (body or {}).get("refresh_token")
            if token not in self.refresh_tokens:
                return httpx.Response(401, json={"status": 401, "errors": []})
            data = {"access_token": self.issue_access()}
            if self.rotate:
                self.refresh_tokens.discard(token)
                new = f"rt-{self.n + 1}"
                self.refresh_tokens.add(new)
                data["refresh_token"] = new
            return httpx.Response(200, json={"status": 200, "data": data})
        if path == SESSION_PATH:
            return httpx.Response(401, json={"status": 401, "data": {"flows": []}})

        auth = request.headers.get("Authorization", "")
        if self.reject_next > 0:
            self.reject_next -= 1
            return httpx.Response(401, json={"detail": "expired"})
        if not auth.startswith("Bearer ") or auth[7:] not in self.valid_access:
            return httpx.Response(401, json={"detail": "invalid"})

        if path == MOODS_PATH:
            rows = [{"id": MOOD_IDS[n], "name": n, "score": s} for n, s in MOODS]
            return httpx.Response(200, json=rows)
        if path == ENTRIES_PATH and request.method == "POST":
            name = next(n for n, i in MOOD_IDS.items() if i == body["mood_id"])
            return httpx.Response(201, json={"id": str(uuid.uuid4()), "mood": {"name": name}})
        if path == WEEKLY_PATH:
            return httpx.Response(200, json=self.weekly)
        if path == SUGGESTION_PATH:
            if self.suggestion is None:
                return httpx.Response(204)
            return httpx.Response(200, json=self.suggestion)
        if path == TICKET_PATH:
            return httpx.Response(201, json={"ticket": str(uuid.uuid4()), "expires_in": 30})
        return httpx.Response(404, json={"detail": "not found"})


class FakeChatSocket:
    """Plays the chat consumer: replays an old greeting, pings, then acks and streams a reply."""

    def __init__(self, reply: str = "Hi! Good to hear from you. How did the report go?"):
        self.reply = reply
        self.sent: list[dict] = []
        self.frames: list[dict] = []
        self.urls: list[str] = []

    def connect(self, url: str):
        self.urls.append(url)
        self.frames = [
            {
                "type": "server.message.final",
                "data": {"content": "Hey, I'm here.", "msg_id": "old-greeting"},
            },
            {"type": "server.ping"},
        ]
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, raw: str) -> None:
        frame = json.loads(raw)
        self.sent.append(frame)
        if frame["type"] == "client.message":
            temp_id = frame["data"]["temp_id"]
            reply_id = str(uuid.uuid4())
            half = len(self.reply) // 2
            self.frames += [
                {"type": "server.ack", "data": {"temp_id": temp_id, "msg_id": "u1", "seq": 1}},
                {"type": "server.thinking", "data": {"msg_id": reply_id}},
                {"type": "server.stream", "data": {"chunk": self.reply[:half], "msg_id": reply_id}},
                {"type": "server.stream", "data": {"chunk": self.reply[half:], "msg_id": reply_id}},
                {
                    "type": "server.message.final",
                    "data": {"content": self.reply, "msg_id": reply_id},
                },
            ]

    def recv(self, timeout: float | None = None) -> str:
        if not self.frames:
            raise TimeoutError("no frame")
        return json.dumps(self.frames.pop(0))


def make_client(tmp_path, fake: FakeAawiz, chat: FakeChatSocket | None = None) -> AawizClient:
    session_file = tmp_path / "aawiz_session.json"
    save_session(session_file, {"refresh_token": "rt-1", "session_token": "st-1"})
    chat = chat or FakeChatSocket()
    return AawizClient(
        "https://aawiz.example/",
        session_file,
        transport=fake.transport(),
        ws_connect=chat.connect,
        clock=fake.clock,
    )
