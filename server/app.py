"""The MCP server: seven tools over Streamable HTTP at /mcp, bearer auth, /health."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from server.config import Settings, get_settings, policy
from server.service import CheckinService
from server.storage import Store

DAY_SECONDS = 24 * 60 * 60

INSTRUCTIONS = (
    "Aawiz Voice Check-in helps a person notice early signs of stress and lower energy "
    "through short daily check-ins. It is not a medical service. Ask for consent before "
    "logging anything. Speak the `speech` field of each result to the user, briefly."
)


def log_event(event: str, **fields) -> None:
    """One JSON line per event on stdout. Never pass note text here."""
    line = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "event": event}
    line.update(fields)
    print(json.dumps(line), file=sys.stdout, flush=True)


class StaticTokenVerifier:
    """Bearer tokens from local config, each mapped to a pseudonymous user id."""

    def __init__(self, tokens: dict[str, str]):
        self._tokens = dict(tokens)

    async def verify_token(self, token: str) -> AccessToken | None:
        user = self._tokens.get(token)
        if user is None:
            return None
        return AccessToken(token=token, client_id=user, scopes=[])


def _user() -> str:
    tok = get_access_token()
    if tok is None:  # the transport already refuses unauthenticated requests
        raise PermissionError("not authenticated")
    return tok.client_id


def build_mcp(settings: Settings, service: CheckinService, base_url: str) -> MCPServer:
    mcp = MCPServer(
        "aawiz-voice-checkin",
        title="Aawiz Voice Check-in",
        instructions=INSTRUCTIONS,
        version="0.1.0",
        token_verifier=StaticTokenVerifier(settings.tokens),
        auth=AuthSettings(
            issuer_url=base_url,
            resource_server_url=f"{base_url}/mcp",
            validate_token_resource=False,
        ),
    )

    def done(tool: str, user: str, result: dict) -> dict:
        log_event("tool_call", tool=tool, user=user)
        return result

    @mcp.tool(
        description="Record the user's consent. `scores` allows storing the three 1-5 scores "
        "and sleep hours; `notes` allows storing a short free-text note. Call this before "
        "logging a check-in. Returns the consent state in plain language.",
        annotations=ToolAnnotations(idempotent_hint=True),
    )
    def set_consent(
        scores: Annotated[bool, Field(description="Allow storing check-in scores.")],
        notes: Annotated[bool, Field(description="Allow storing a short free-text note.")],
    ) -> dict:
        user = _user()
        return done("set_consent", user, service.set_consent(user, scores, notes))

    @mcp.tool(
        description="Log today's check-in. Scores are whole numbers from 1 to 5. Requires "
        "`scores` consent; the note is dropped unless `notes` consent exists. Returns a short "
        "spoken confirmation, or a safety message if the note contains crisis language.",
    )
    def log_checkin(
        mood: Annotated[
            int, Field(ge=1, le=5, strict=True, description="Mood: 1 very low, 5 very good.")
        ],
        energy: Annotated[
            int, Field(ge=1, le=5, strict=True, description="Energy: 1 drained, 5 full of energy.")
        ],
        stress: Annotated[
            int, Field(ge=1, le=5, strict=True, description="Stress: 1 calm, 5 very stressed.")
        ],
        sleep_hours: Annotated[
            float | None, Field(ge=0, le=14, description="Hours slept last night, 0 to 14.")
        ] = None,
        note: Annotated[
            str | None, Field(max_length=280, description="Optional short note, 280 chars max.")
        ] = None,
    ) -> dict:
        user = _user()
        result = service.log_checkin(user, mood, energy, stress, sleep_hours, note)
        return done("log_checkin", user, result)

    @mcp.tool(
        description="Reflect on the last 7 days compared with the 7 before: averages, direction "
        "(up, down or steady; for stress, up means more stress), best and hardest day, and one "
        "short plain-language sentence. Needs at least 3 check-ins.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def get_weekly_reflection() -> dict:
        user = _user()
        return done("get_weekly_reflection", user, service.weekly_reflection(user))

    @mcp.tool(
        description="Suggest one small, low-risk action from a fixed curated list (breathing, "
        "a short walk, water, a screen break, writing one good thing, messaging a friend), "
        "chosen from the latest scores. Never medical advice.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def suggest_micro_action(
        context: Annotated[
            str | None, Field(max_length=280, description="Optional words about the moment.")
        ] = None,
    ) -> dict:
        user = _user()
        return done("suggest_micro_action", user, service.suggest_micro_action(user, context))

    @mcp.tool(
        description="Explain what is stored, why, for how long, and how to delete it.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def get_privacy_summary() -> dict:
        user = _user()
        return done("get_privacy_summary", user, service.privacy_summary())

    @mcp.tool(
        description="Return all of this user's check-ins and consent records as JSON.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def export_my_data() -> dict:
        user = _user()
        return done("export_my_data", user, service.export(user))

    @mcp.tool(
        description="Permanently delete all of this user's check-ins and consent records. "
        "Confirm with the user before calling.",
        annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True),
    )
    def delete_my_data() -> dict:
        user = _user()
        return done("delete_my_data", user, service.delete(user))

    if hasattr(service, "ask_elin"):  # aawiz mode only; local mode keeps exactly seven tools

        @mcp.tool(
            description="Say one thing to Elin, the companion in the Aawiz app, and hear her "
            "reply. Elin remembers earlier conversations and check-ins. Not for emergencies.",
        )
        def ask_elin(
            message: Annotated[
                str, Field(min_length=1, max_length=500, description="What to tell Elin.")
            ],
        ) -> dict:
            user = _user()
            return done("ask_elin", user, service.ask_elin(user, message))

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        if settings.backend_mode == "aawiz":
            return JSONResponse({"status": "ok", "backend": "aawiz"})
        return JSONResponse({"status": "ok"})

    return mcp


def make_service(settings: Settings, store: Store, client=None) -> CheckinService:
    """Local mode: the SQLite service. Aawiz mode: the same tools backed by the Aawiz API."""
    if settings.backend_mode != "aawiz":
        return CheckinService(store, settings.region)
    from server.aawiz_client import AawizClient
    from server.aawiz_service import AawizCheckinService

    if client is None:
        session_file = settings.aawiz_session_file or settings.data_dir / "aawiz_session.json"
        client = AawizClient(settings.aawiz_api_base, session_file)
        client.warm_up()
    return AawizCheckinService(store, settings.region, client, tz=settings.timezone)


def create_app(
    settings: Settings | None = None, host: str = "127.0.0.1", port: int = 8000, client=None
) -> Starlette:
    settings = settings or get_settings()
    store = Store(settings.db_path)
    service = make_service(settings, store, client)
    mcp = build_mcp(settings, service, f"http://{host}:{port}")
    app = mcp.streamable_http_app(host=host)
    retention = policy()["retention_days"]

    def purge() -> None:
        log_event("retention_purge", removed=store.purge_older_than(retention))

    async def daily() -> None:
        while True:
            await asyncio.sleep(DAY_SECONDS)
            purge()

    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: Starlette):
        purge()  # at startup, then once a day
        task = asyncio.create_task(daily())
        try:
            async with inner(a) as state:
                yield state
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app.router.lifespan_context = lifespan
    app.state.store = store
    app.state.service = service
    return app
