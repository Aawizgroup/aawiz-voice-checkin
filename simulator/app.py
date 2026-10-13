"""The simulator web app: a static voice page and one JSON endpoint that runs the brain."""

from __future__ import annotations

import hashlib
import os
import secrets
from collections import OrderedDict
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from server.config import env_flag, load_dotenv
from simulator.brain import ScriptedBrain, SessionState
from simulator.bridge import McpBridge
from simulator.limits import LIMIT_NOTE, DemoLimiter
from simulator.tts import ElevenLabsTTS, from_env

STATIC = Path(__file__).resolve().parent / "static"
MAX_SESSIONS = 500
MAX_TEXT = 500
MAX_PENDING_AUDIO = 200
MIN_DEMO_SESSION_ID = 16
_FROM_ENV = object()
DEMO_NOTICE = "Public demo with synthetic data. Please do not enter real personal information."
DEMO_TITLE = "Aawiz: a private personal AI for daily check-ins"
# The public demo is not an Alexa+ simulator: the same page, without the Alexa+ wording.
DEMO_PAGE_TEXT = (
    ("<title>Aawiz Voice Check-in: Alexa+ simulator</title>", f"<title>{DEMO_TITLE}</title>"),
    ("<h1>Aawiz Voice Check-in</h1>", f"<h1>{DEMO_TITLE}</h1>"),
    ('<p class="sub">Alexa+ simulator</p>', '<p class="sub">Public demo</p>'),
    ("Say “Alexa, open Aawiz check-in”.", "Say “Open Aawiz check-in”."),
    ("<li>Alexa, open Aawiz check-in.</li>", "<li>Open Aawiz check-in.</li>"),
)


def demo_page() -> str:
    """The simulator page for the public demo. Fails loudly if the page text has drifted."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for old, new in DEMO_PAGE_TEXT:
        if old not in html:
            raise RuntimeError(f"demo page text not found: {old}")
        html = html.replace(old, new)
    return html


def _make_brain(
    mode: str,
    bridge: McpBridge,
    region: str,
    model: str,
    elin: bool = False,
    nebius_client=None,
    nebius_model: str = "",
):
    """Returns (brain, note). Falls back to scripted when the chosen mode cannot run."""
    if mode == "llm":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return (
                ScriptedBrain(bridge, region, elin),
                "llm requested but ANTHROPIC_API_KEY is not set",
            )
        try:
            from simulator.brain_llm import LlmBrain
        except ImportError:
            return ScriptedBrain(bridge, region, elin), 'llm needs: pip install -e ".[llm]"'
        return LlmBrain(bridge, region, model), ""
    if mode == "nebius":
        if not nebius_model:
            return ScriptedBrain(
                bridge, region, elin
            ), "nebius requested but NEBIUS_MODEL is not set"
        try:
            from simulator.brain_nebius import NebiusBrain, make_client
        except ImportError:
            return ScriptedBrain(bridge, region, elin), 'nebius needs: pip install -e ".[nebius]"'
        if nebius_client is None:
            key = os.environ.get("NEBIUS_API_KEY", "").strip()
            if not key:
                return (
                    ScriptedBrain(bridge, region, elin),
                    "nebius requested but NEBIUS_API_KEY is not set",
                )
            nebius_client = make_client(key)
        return NebiusBrain(bridge, region, nebius_model, nebius_client, elin), ""
    return ScriptedBrain(bridge, region, elin), ""


def create_sim_app(
    mcp_url: str | None = None,
    token: str | None = None,
    mode: str | None = None,
    region: str | None = None,
    model: str | None = None,
    backend: str | None = None,
    tts: ElevenLabsTTS | None | object = _FROM_ENV,
    demo: bool | None = None,
    nebius_client=None,
    nebius_model: str | None = None,
    limiter: DemoLimiter | None = None,
) -> Starlette:
    load_dotenv()
    mcp_url = mcp_url or os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
    token = token or os.environ.get("SIM_TOKEN", "")
    mode = (mode or os.environ.get("BRAIN_MODE", "scripted")).lower()
    region = (region or os.environ.get("AAWIZ_REGION", "NL")).upper()
    model = model or os.environ.get("LLM_MODEL", "claude-sonnet-5-5")
    backend = (backend or os.environ.get("BACKEND_MODE", "local")).lower()
    demo = env_flag("DEMO_PUBLIC") if demo is None else demo
    if nebius_model is None:
        nebius_model = os.environ.get("NEBIUS_MODEL", "").strip()
    if tts is _FROM_ENV:
        tts = from_env()
    if demo:  # the hosted demo: local backend, browser speech, per-visitor synthetic users
        backend, tts = "local", None
        limiter = limiter or DemoLimiter()
    if nebius_client is None and mode == "nebius" and os.environ.get("NEBIUS_API_KEY", "").strip():
        try:  # one client (one connection pool) shared by every visitor's brain
            from simulator.brain_nebius import make_client

            nebius_client = make_client(os.environ["NEBIUS_API_KEY"].strip())
        except ImportError:
            pass

    def brain_for(visitor_token: str):
        return _make_brain(
            mode,
            McpBridge(mcp_url, visitor_token),
            region,
            model,
            elin=backend == "aawiz",
            nebius_client=nebius_client,
            nebius_model=nebius_model,
        )

    brain, note = brain_for(token)  # the shared brain outside demo mode; also names the mode
    sessions: OrderedDict[str, tuple[SessionState, object]] = OrderedDict()
    # Reply text waiting to be spoken in Elin's voice, keyed by a one-time id. Only assistant
    # replies are ever put here, so only they can reach ElevenLabs.
    pending_audio: OrderedDict[str, str] = OrderedDict()

    demo_html = demo_page() if demo else ""

    async def index(_: Request) -> Response:
        if demo:
            return HTMLResponse(demo_html)
        return FileResponse(STATIC / "index.html")

    async def config(_: Request) -> JSONResponse:
        body = {
            "brain": brain.mode,
            "model": model if brain.mode == "llm" else None,
            "note": note,
            "mcp_url": mcp_url,
            "backend": backend,
            "voice": "elin" if tts else "browser",
        }
        if brain.mode == "nebius":
            body["label"] = brain.label
        if demo:
            body["demo"] = DEMO_NOTICE
            body["mcp_url"] = "in-container MCP server"
        return JSONResponse(body)

    def session_for(session_id: str) -> tuple[SessionState, object, str]:
        """This visitor's conversation state and brain. In demo mode each visitor has their own."""
        visitor = hashlib.sha256(session_id.encode()).hexdigest()[:32]
        found = sessions.pop(session_id, None)
        if found is None:
            found = (SessionState(), brain_for(f"{token}.{visitor}")[0] if demo else brain)
        sessions[session_id] = found
        while len(sessions) > MAX_SESSIONS:
            sessions.popitem(last=False)
        return found[0], found[1], visitor

    async def turn(request: Request) -> JSONResponse:
        body = await request.json()
        text = str(body.get("text", "")).strip()[:MAX_TEXT]
        session_id = str(body.get("session", "default"))[:64]
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        if demo and len(session_id) < MIN_DEMO_SESSION_ID:
            return JSONResponse({"error": "session id required"}, status_code=400)
        state, my_brain, visitor = session_for(session_id)
        limited = bool(
            limiter and getattr(my_brain, "mode", "") == "nebius" and not limiter.allow(visitor)
        )
        try:
            if limited:  # cost guard: the scripted brain answers, and the page says so
                from simulator.brain_nebius import FALLBACK_LABEL

                result = await my_brain.scripted.handle(text, state)
                result.brain = FALLBACK_LABEL
            else:
                result = await my_brain.handle(text, state)
        except Exception as exc:  # the page shows a calm message instead of a stack trace
            print(f'{{"event": "turn_error", "error": "{type(exc).__name__}"}}', flush=True)
            return JSONResponse(
                {"reply": "Sorry, I couldn't reach the check-in service just now.", "tools": []},
                status_code=502,
            )
        body = {
            "reply": result.reply,
            "safety": result.safety,
            "awaiting": state.awaiting,
            "tools": [{"tool": c.tool, "ok": c.ok, "summary": c.summary} for c in result.tools],
        }
        if result.brain:
            body["brain"] = result.brain
        if limited:
            body["note"] = LIMIT_NOTE
        if tts:
            audio_id = secrets.token_urlsafe(12)
            pending_audio[audio_id] = result.reply
            while len(pending_audio) > MAX_PENDING_AUDIO:
                pending_audio.popitem(last=False)
            body["audio"] = f"/api/audio/{audio_id}"
        return JSONResponse(body)

    async def audio(request: Request) -> Response:
        text = pending_audio.pop(request.path_params["audio_id"], None)
        if not tts or text is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        mp3 = await run_in_threadpool(tts.synthesize, text)
        if mp3 is None:  # the page falls back to browser speech
            return JSONResponse({"error": "voice unavailable"}, status_code=503)
        return Response(mp3, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/config", config),
            Route("/api/turn", turn, methods=["POST"]),
            Route("/api/audio/{audio_id}", audio),
            Route("/health", health),
            Mount("/static", StaticFiles(directory=STATIC), name="static"),
        ]
    )
