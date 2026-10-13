"""The public demo (DEMO_PUBLIC=true): per-visitor synthetic users, 2-hour purge, cost caps."""

from __future__ import annotations

from dataclasses import replace

import httpx2 as httpx
import pytest

from server.app import create_app
from server.demo import DATA, DEMO_TTL_SECONDS, LABEL, DemoTokenVerifier, DemoUsers
from server.storage import Store
from simulator.app import DEMO_NOTICE, create_sim_app
from simulator.brain_nebius import FALLBACK_LABEL
from simulator.limits import DAY, HOUR, LIMIT_NOTE, DemoLimiter
from tests.conftest import ServerThread, free_port, mcp_client, payload
from tests.nebius_fake import MODEL, FakeNebius, tool_call

SHARED = "token-a"
ALICE, BOB = "a" * 32, "b" * 32


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# --- per-visitor users on the MCP server ---------------------------------------


@pytest.fixture
def demo_server(settings):
    port = free_port()
    app = create_app(replace(settings, demo_public=True), port=port)
    thread = ServerThread(app, port)
    thread.start()
    yield {"url": f"http://127.0.0.1:{port}", "store": app.state.store, "port": port}
    thread.stop()


async def call(url: str, visitor: str, tool: str, args: dict | None = None) -> dict:
    async with mcp_client(url, f"{SHARED}.{visitor}") as client:
        return payload(await client.call_tool(tool, args or {}))


@pytest.mark.anyio
async def test_each_visitor_gets_a_fresh_seeded_history(demo_server):
    data = (await call(demo_server["url"], ALICE, "export_my_data"))["data"]
    assert len(data["checkins"]) == len(DATA) == 14
    assert {c["note"] for c in data["checkins"]} == {LABEL}
    assert demo_server["store"].count("demo_" + ALICE) == 14


@pytest.mark.anyio
async def test_two_visitors_never_see_each_others_data(demo_server):
    url = demo_server["url"]
    await call(url, ALICE, "log_checkin", {"mood": 1, "energy": 1, "stress": 5, "note": "alice"})
    bob = (await call(url, BOB, "export_my_data"))["data"]
    assert len(bob["checkins"]) == 14
    assert "alice" not in str(bob)
    await call(url, BOB, "delete_my_data")
    alice = (await call(url, ALICE, "export_my_data"))["data"]
    assert len(alice["checkins"]) == 15  # untouched by Bob's delete


@pytest.mark.anyio
@pytest.mark.parametrize(
    "token", [SHARED, f"{SHARED}.short", f"wrong.{ALICE}", f"{SHARED}.{'Z' * 32}"]
)
async def test_only_well_formed_visitor_tokens_are_accepted(demo_server, token):
    with pytest.raises(Exception):  # noqa: B017  (the transport refuses with 401)
        async with mcp_client(demo_server["url"], token) as client:
            await client.list_tools()


def test_rows_older_than_two_hours_are_purged(tmp_path):
    store, clock = Store(tmp_path / "d.sqlite3"), Clock()
    users = DemoUsers(store, clock)
    old = users.user_for(ALICE)
    clock.t += DEMO_TTL_SECONDS - 60
    young = users.user_for(BOB)
    assert users.purge_expired() == 0
    clock.t += 60  # Alice is now exactly two hours old
    assert users.purge_expired() == 1
    assert store.count(old) == 0 and store.get_consent(old).at is None
    assert store.count(young) == 14


def test_an_expired_visitor_who_returns_starts_fresh(tmp_path):
    store, clock = Store(tmp_path / "d.sqlite3"), Clock()
    users = DemoUsers(store, clock)
    user = users.user_for(ALICE)
    store.delete_user(user)  # the visitor deleted their data
    assert users.user_for(ALICE) == user and store.count(user) == 0  # not re-seeded behind them
    clock.t += DEMO_TTL_SECONDS
    users.user_for(ALICE)
    assert store.count(user) == 14


def test_demo_rows_do_not_survive_a_restart(tmp_path):
    store = Store(tmp_path / "d.sqlite3")
    DemoUsers(store).user_for(ALICE)
    assert store.count("demo_" + ALICE) == 14
    DemoUsers(store)  # a new process
    assert store.count("demo_" + ALICE) == 0


@pytest.mark.anyio
async def test_verifier_maps_a_visitor_to_their_own_pseudonym(tmp_path):
    verifier = DemoTokenVerifier({SHARED: "x"}, DemoUsers(Store(tmp_path / "d.sqlite3")))
    assert (await verifier.verify_token(f"{SHARED}.{ALICE}")).client_id == "demo_" + ALICE
    assert await verifier.verify_token(SHARED) is None


# --- the cost guard --------------------------------------------------------------


def test_per_visitor_hourly_cap():
    clock = Clock()
    limiter = DemoLimiter(clock=clock)
    assert all(limiter.allow("v1") for _ in range(20))
    assert limiter.allow("v1") is False  # the 21st turn in the hour
    assert limiter.allow("v2") is True  # another visitor is not affected
    clock.t += HOUR
    assert limiter.allow("v1") is True


def test_daily_total_cap():
    clock = Clock()
    limiter = DemoLimiter(clock=clock)
    for i in range(300):
        assert limiter.allow(f"v{i}") is True
    assert limiter.allow("someone-new") is False  # the 301st turn of the day
    clock.t += DAY
    assert limiter.allow("someone-new") is True


# --- the simulator in demo mode --------------------------------------------------


@pytest.fixture
def demo_stack(demo_server):
    threads = []

    def start(fake: FakeNebius, limiter: DemoLimiter | None = None) -> httpx.Client:
        port = free_port()
        app = create_sim_app(
            mcp_url=f"{demo_server['url']}/mcp",
            token=SHARED,
            mode="nebius",
            region="NL",
            backend="aawiz",  # ignored: the public demo is always local
            tts=object(),  # ignored: the public demo never uses ElevenLabs
            demo=True,
            nebius_client=fake.client(),
            nebius_model=MODEL,
            limiter=limiter,
        )
        t = ServerThread(app, port)
        t.start()
        threads.append(t)
        return httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30)

    yield {"start": start, "store": demo_server["store"]}
    for t in reversed(threads):
        t.stop()


def say(http: httpx.Client, session: str, text: str) -> httpx.Response:
    return http.post("/api/turn", json={"session": session, "text": text})


def test_demo_config_is_local_browser_voice_with_the_notice(demo_stack):
    http = demo_stack["start"](FakeNebius())
    config = http.get("/api/config").json()
    assert config["demo"] == DEMO_NOTICE
    assert config["backend"] == "local" and config["voice"] == "browser"
    assert "do not enter real personal information" in DEMO_NOTICE


def test_two_browser_sessions_are_two_separate_users(demo_stack):
    args = {"mood": 2, "energy": 2, "stress": 4}
    fake = FakeNebius([tool_call("log_checkin", args)], "Saved.", [tool_call("export_my_data")])
    http = demo_stack["start"](fake)
    first = say(http, "session-one-0123456789", "mood 2, energy 2, stress 4").json()
    assert "audio" not in first
    say(http, "session-two-0123456789", "export my data")
    store = demo_stack["store"]
    counts = sorted(store.count(u) for u in _demo_users(store))
    assert counts == [14, 15]  # one visitor logged a check-in, the other did not see it


def test_demo_needs_a_real_session_id(demo_stack):
    http = demo_stack["start"](FakeNebius())
    assert say(http, "default", "hello").status_code == 400


def test_above_the_cap_the_scripted_brain_answers_and_the_page_is_told(demo_stack):
    fake = FakeNebius("One.", "Two.")
    http = demo_stack["start"](fake, DemoLimiter(per_visitor=2))
    session = "session-cap-0123456789"
    assert say(http, session, "hello").json()["reply"] == "One."
    assert say(http, session, "hello again").json()["reply"] == "Two."
    third = say(http, session, "What do you keep about me?").json()
    assert third["brain"] == FALLBACK_LABEL and third["note"] == LIMIT_NOTE
    assert "90 days" in third["reply"]
    assert len(fake.requests) == 2  # the third turn never reached Nebius


def _demo_users(store: Store) -> list[str]:
    with store._conn() as c:
        rows = c.execute("SELECT DISTINCT user_id FROM consent WHERE user_id LIKE 'demo%'")
        return [r[0] for r in rows.fetchall()]


def test_the_demo_page_has_the_personal_ai_title_and_no_alexa_wording(demo_stack):
    http = demo_stack["start"](FakeNebius())
    html = http.get("/").text
    assert "<title>Aawiz: a private personal AI for daily check-ins</title>" in html
    assert "<h1>Aawiz: a private personal AI for daily check-ins</h1>" in html
    assert "alexa" not in html.lower()


def test_outside_demo_mode_the_page_keeps_its_alexa_wording(tmp_path):
    from simulator.app import STATIC

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "<title>Aawiz Voice Check-in: Alexa+ simulator</title>" in html
