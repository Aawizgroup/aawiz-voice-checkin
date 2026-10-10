"""End to end: real MCP server, real simulator, headless Chromium, full demo flow by typing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from server.app import create_app
from server.config import Settings
from simulator.app import create_sim_app
from tests.conftest import ServerThread, free_port

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "test-results"
TOKEN = "e2e-token"

FLOW = [
    # (what the person types, expected tool or None, words the reply must contain)
    ("Alexa, open Aawiz check-in.", None, "may I store"),
    ("Yes, scores and notes.", "set_consent", "keep your check-in scores"),
    ("My mood is 2, energy 3, stress 4, I slept 6 hours.", "log_checkin", "saved today"),
    ("How was my week?", "get_weekly_reflection", "your check-ins suggest"),
    ("What can I do right now?", "suggest_micro_action", "breathing"),
    ("What do you keep about me?", "get_privacy_summary", "90 days"),
    ("Delete my data.", None, "Should I delete everything"),
    ("Yes, delete it.", "delete_my_data", "deleted all your check-ins"),
]


def _load_seed():
    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.seed


@pytest.fixture
def stack(tmp_path):
    settings = Settings(tokens={TOKEN: "user_demo"}, data_dir=tmp_path, region="NL")
    mcp_port, sim_port = free_port(), free_port()
    server_app = create_app(settings, port=mcp_port)
    store = server_app.state.store
    seeded = _load_seed()(store, "user_demo")
    sim_app = create_sim_app(
        mcp_url=f"http://127.0.0.1:{mcp_port}/mcp", token=TOKEN, mode="scripted", region="NL"
    )
    threads = [ServerThread(server_app, mcp_port), ServerThread(sim_app, sim_port)]
    for t in threads:
        t.start()
    yield {"url": f"http://127.0.0.1:{sim_port}", "store": store, "seeded": seeded}
    for t in reversed(threads):
        t.stop()


@pytest.mark.e2e
def test_full_demo_flow_by_typing(stack):
    store, seeded = stack["store"], stack["seeded"]
    expected_tools = [tool for _, tool, _ in FLOW if tool]
    SHOTS.mkdir(exist_ok=True)

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(stack["url"])
        assert page.title() == "Aawiz Voice Check-in: Alexa+ simulator"
        page.wait_for_selector("#brain-badge:has-text('scripted')")

        for i, (text, tool, words) in enumerate(FLOW):
            page.fill("#text-input", text)
            page.click("#send-button")
            reply = page.locator(".turn.assistant .text").nth(i)
            reply.wait_for(timeout=15_000)
            assert words.lower() in reply.inner_text().lower(), (text, reply.inner_text())
            assert page.locator(".turn.user .text").nth(i).inner_text() == text
            if tool == "log_checkin":
                assert store.count("user_demo") == seeded + 1
            if i == 5:
                page.screenshot(path=str(SHOTS / "simulator-desktop.png"), full_page=True)

        tools = page.locator("#mcp-log li").evaluate_all("els => els.map(e => e.dataset.tool)")
        assert tools == expected_tools
        assert page.locator("#mcp-log li[data-ok='false']").count() == 0
        assert store.count("user_demo") == 0
        assert store.get_consent("user_demo").at is None

        page.set_viewport_size({"width": 390, "height": 844})
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        page.screenshot(path=str(SHOTS / "simulator-mobile.png"), full_page=True)
        browser.close()
    assert not overflow, "the page must not scroll sideways on a phone"
