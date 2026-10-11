"""A small client for the real Aawiz API, used only when BACKEND_MODE=aawiz.

Endpoints and payloads come from the Aawiz.API source (paths relative to its repo root):

- Login and tokens: django-allauth headless, app client (`aawiz/settings.py`, `aawiz/urls.py`
  mounts `allauth.headless.urls` at `api/`). Refresh: `POST api/app/v1/tokens/refresh` with
  `{"refresh_token"}`; allauth rotates refresh tokens by default (`JWT_ROTATE_REFRESH_TOKEN`).
- Mood check-in: `POST api/v1/mood-tracker/entries/` with `{"mood_id", "source": "manual"}`
  (`apps/mood_tracker/api/serializers.py`, `MoodEntryWriteSerializer`); moods from
  `GET api/v1/mood-tracker/moods/`.
- Weekly report: `GET api/v1/mood-tracker/reports/weekly/` (`WeeklyReportSerializer`).
- Elin suggests: `GET api/v1/companion/suggestions/today/`, 204 when there is none
  (`apps/companion/views.py`, `SuggestionTodayView`).
- Chat with Elin: `POST api/v1/chat/ticket/`, then a WebSocket to `ws/chat/?ticket=...`
  (`apps/chat/views.py`, `apps/chat/middleware.py`, `apps/chat/consumers.py`).

Tokens and message text are never logged.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx2 as httpx

TIMEOUT = 10.0
CHAT_REPLY_LIMIT = 90.0  # Elin streams her reply; the whole answer may take longer than one read
REFRESH_MARGIN = 30  # refresh this many seconds before the access token expires
DEFAULT_ACCESS_LIFETIME = 300  # JWT_ACCESS_TOKEN_EXPIRES_IN default in Aawiz.API

SESSION_ENDED = "Your Aawiz demo session has ended. Please run make aawiz-login."
UNAVAILABLE = "Sorry, I couldn't reach Aawiz just now. Please try again in a moment."

REFRESH_PATH = "api/app/v1/tokens/refresh"
SESSION_PATH = "api/app/v1/auth/session"
CODE_REQUEST_PATH = "api/app/v1/auth/code/request"
CODE_CONFIRM_PATH = "api/app/v1/auth/code/confirm"
MOODS_PATH = "api/v1/mood-tracker/moods/"
ENTRIES_PATH = "api/v1/mood-tracker/entries/"
WEEKLY_PATH = "api/v1/mood-tracker/reports/weekly/"
SUGGESTION_PATH = "api/v1/companion/suggestions/today/"
TICKET_PATH = "api/v1/chat/ticket/"
CHAT_WS_PATH = "ws/chat/"


class AawizError(Exception):
    """Aawiz could not be reached or answered with an error. `speech` is safe to say aloud."""

    speech = UNAVAILABLE


class AawizSessionEnded(AawizError):
    speech = SESSION_ENDED


# --- session file -----------------------------------------------------------


def load_session(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_session(path: Path, data: dict) -> None:
    """Write the session file readable by the owner only (0600)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = {k: v for k, v in data.items() if k in ("refresh_token", "session_token") and v}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(kept, fh)
    os.chmod(path, 0o600)


def token_expiry(token: str, now: float) -> float:
    """The `exp` claim of a JWT, read without verifying it (the server verifies)."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return now + DEFAULT_ACCESS_LIFETIME


def ws_url(base_url: str, ticket: str) -> str:
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{parts.netloc}/{CHAT_WS_PATH}?ticket={ticket}"


def _ws_connect(url: str):
    from websockets.sync.client import connect  # optional dependency: pip install -e ".[aawiz]"

    return connect(url, open_timeout=TIMEOUT, close_timeout=2)


# --- client -------------------------------------------------------------------


class AawizClient:
    def __init__(
        self,
        base_url: str,
        session_file: Path,
        transport: httpx.BaseTransport | None = None,
        ws_connect: Callable[[str], object] = _ws_connect,
        clock: Callable[[], float] = time.time,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.session_file = Path(session_file)
        self._http = httpx.Client(base_url=self.base_url, timeout=TIMEOUT, transport=transport)
        self._ws_connect = ws_connect
        self._clock = clock
        self._access: str | None = None
        self._access_exp = 0.0
        self._moods: dict[str, str] | None = None
        # Refresh tokens rotate: two threads must never spend the same one.
        self._lock = threading.Lock()

    # tokens ----------------------------------------------------------------

    def _post_json(self, path: str, body: dict, headers: dict | None = None) -> httpx.Response:
        try:
            return self._http.post(path, json=body, headers=headers or {})
        except httpx.TimeoutException as exc:
            raise AawizError("timeout") from exc
        except httpx.HTTPError as exc:
            raise AawizError("network") from exc

    def refresh(self) -> None:
        """Get a new access token. Saves the rotated refresh token when Aawiz sends one."""
        session = load_session(self.session_file)
        refresh_token = session.get("refresh_token")
        if refresh_token:
            resp = self._post_json(REFRESH_PATH, {"refresh_token": refresh_token})
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                access = data.get("access_token")
                if access:
                    if data.get("refresh_token"):  # rotation on: the old one is now invalid
                        session["refresh_token"] = data["refresh_token"]
                        save_session(self.session_file, session)
                    self._set_access(access)
                    return
            elif resp.status_code >= 500:
                raise AawizError(f"refresh {resp.status_code}")
        # Fallback: the durable session token (what the Android app uses).
        session_token = session.get("session_token")
        if session_token:
            try:
                resp = self._http.get(SESSION_PATH, headers={"X-Session-Token": session_token})
            except httpx.HTTPError as exc:
                raise AawizError("network") from exc
            if resp.status_code == 200:
                meta = resp.json().get("meta", {})
                if meta.get("access_token"):
                    if meta.get("refresh_token"):
                        session["refresh_token"] = meta["refresh_token"]
                        save_session(self.session_file, session)
                    self._set_access(meta["access_token"])
                    return
        self._access = None
        raise AawizSessionEnded("refresh failed")

    def _set_access(self, token: str) -> None:
        self._access = token
        self._access_exp = token_expiry(token, self._clock())

    def _token(self) -> str:
        with self._lock:
            if self._access is None or self._clock() >= self._access_exp - REFRESH_MARGIN:
                self.refresh()
            assert self._access is not None
            return self._access

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One authenticated call. On a 401: refresh once, retry once."""
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._token()}"}
            try:
                resp = self._http.request(method, path, headers=headers, **kwargs)
            except httpx.TimeoutException as exc:
                raise AawizError("timeout") from exc
            except httpx.HTTPError as exc:
                raise AawizError("network") from exc
            if resp.status_code != 401:
                break
            if attempt == 1:
                with self._lock:
                    self.refresh()
            else:
                raise AawizSessionEnded("401 after refresh")
        if resp.status_code >= 400:
            raise AawizError(f"{method} {path} {resp.status_code}")
        return resp

    # API -------------------------------------------------------------------

    def moods(self) -> dict[str, str]:
        """Mood name -> id, fetched once."""
        if self._moods is None:
            rows = self.request("GET", MOODS_PATH).json()
            self._moods = {row["name"]: row["id"] for row in rows}
        return self._moods

    def create_mood_entry(self, mood_name: str) -> dict:
        mood_id = self.moods().get(mood_name)
        if mood_id is None:
            raise AawizError(f"mood {mood_name!r} not offered by Aawiz")
        body = {"mood_id": mood_id, "source": "manual"}
        return self.request("POST", ENTRIES_PATH, json=body).json()

    def weekly_report(self) -> dict:
        return self.request("GET", WEEKLY_PATH).json()

    def suggestion_today(self, tz: str, lang: str = "en") -> dict | None:
        resp = self.request("GET", SUGGESTION_PATH, params={"tz": tz, "lang": lang})
        return None if resp.status_code == 204 else resp.json()

    def ask_elin(self, message: str) -> str:
        """Send one message to Elin over the chat WebSocket and return her final reply."""
        ticket = self.request("POST", TICKET_PATH).json()["ticket"]
        temp_id = str(uuid.uuid4())
        try:
            with self._ws_connect(ws_url(self.base_url, ticket)) as ws:
                ws.send(
                    json.dumps(
                        {"type": "client.message", "data": {"content": message, "temp_id": temp_id}}
                    )
                )
                return self._read_reply(ws, temp_id)
        except AawizError:
            raise
        except TimeoutError as exc:
            raise AawizError("chat timeout") from exc
        except Exception as exc:  # connection refused, closed early, bad frame
            raise AawizError(f"chat {type(exc).__name__}") from exc

    def _read_reply(self, ws, temp_id: str) -> str:
        """Our reply is the final message whose id came in `server.thinking` after our ack.

        A fresh session can first replay a greeting as `server.message.final`; that one is skipped.
        """
        deadline = self._clock() + CHAT_REPLY_LIMIT
        acked = False
        reply_id = None
        while self._clock() < deadline:
            frame = json.loads(ws.recv(timeout=TIMEOUT))
            kind = frame.get("type")
            data = frame.get("data") or {}
            if kind == "server.ping":
                ws.send(json.dumps({"type": "client.pong"}))
            elif kind == "server.error":
                raise AawizError(f"chat error {data.get('code')}")
            elif kind == "server.ack" and data.get("temp_id") == temp_id:
                acked = True
            elif kind == "server.thinking" and acked:
                reply_id = data.get("msg_id")
            elif kind == "server.message.final" and reply_id and data.get("msg_id") == reply_id:
                return str(data.get("content", "")).strip()
        raise AawizError("chat timeout")

    # login (used by scripts/aawiz_login.py) ---------------------------------

    def request_code(self, email: str) -> str:
        """Ask Aawiz to email a login code. Returns the pending flow's session token."""
        resp = self._post_json(CODE_REQUEST_PATH, {"email": email})
        token = (resp.json().get("meta") or {}).get("session_token")
        if not token:
            raise AawizError(f"code request {resp.status_code}")
        return token

    def confirm_code(self, flow_token: str, code: str) -> dict:
        resp = self._post_json(
            CODE_CONFIRM_PATH, {"code": code}, headers={"X-Session-Token": flow_token}
        )
        meta = resp.json().get("meta") or {}
        if resp.status_code != 200 or not meta.get("refresh_token"):
            raise AawizError(f"code confirm {resp.status_code}")
        save_session(
            self.session_file,
            {"refresh_token": meta["refresh_token"], "session_token": meta.get("session_token")},
        )
        self._set_access(meta["access_token"])
        return {"access_expires_in": round(self._access_exp - self._clock())}
