"""Browser tests for Elin's voice and Aawiz mode.

The voice fallback test always runs (local mode, no network). The Aawiz-mode flow runs only
when BACKEND_MODE=aawiz is set and a demo session file exists; it is skipped otherwise and in CI.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from server.app import create_app
from server.config import Settings, get_settings
from simulator.app import create_sim_app
from tests.conftest import ServerThread, free_port

playwright = pytest.importorskip("playwright.sync_api")

TOKEN = "e2e-token"


class FailingTTS:
    def synthesize(self, text: str) -> None:
        return None


def _stack(settings: Settings, tts):
    mcp_port, sim_port = free_port(), free_port()
    mcp_app = create_app(settings, port=mcp_port)
    sim_app = create_sim_app(
        mcp_url=f"http://127.0.0.1:{mcp_port}/mcp",
        token=TOKEN,
        mode="scripted",
        region="NL",
        backend=settings.backend_mode,
        tts=tts,
    )
    threads = [ServerThread(mcp_app, mcp_port), ServerThread(sim_app, sim_port)]
    for t in threads:
        t.start()
    return threads, f"http://127.0.0.1:{sim_port}"


def _say(page, i: int, text: str, timeout: int = 15_000) -> str:
    page.fill("#text-input", text)
    page.click("#send-button")
    reply = page.locator(".turn.assistant .text").nth(i)
    reply.wait_for(timeout=timeout)
    return reply.inner_text()


@pytest.mark.e2e
def test_page_falls_back_to_browser_voice_when_elin_fails(tmp_path):
    settings = Settings(tokens={TOKEN: "user_demo"}, data_dir=tmp_path, region="NL")
    threads, url = _stack(settings, FailingTTS())
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#voice-badge:has-text('Voice: Elin')")
            assert "sent to Google" in page.inner_text("#mic-note")
            assert "90 days" in _say(page, 0, "What do you keep about me?")
            page.wait_for_selector("#voice-badge:has-text('Voice: browser')", timeout=10_000)
            browser.close()
    finally:
        for t in reversed(threads):
            t.stop()


def _aawiz_ready() -> bool:
    if os.environ.get("BACKEND_MODE", "").lower() != "aawiz":
        return False
    session = get_settings().aawiz_session_file
    return bool(session and session.is_file())


@pytest.mark.e2e
@pytest.mark.skipif(not _aawiz_ready(), reason="needs BACKEND_MODE=aawiz and make aawiz-login")
def test_aawiz_mode_flow_against_production(tmp_path):
    settings = replace(get_settings(), tokens={TOKEN: "user_demo"}, data_dir=tmp_path)
    threads, url = _stack(settings, None)
    flow = [
        ("Alexa, open Aawiz check-in.", None),
        ("Yes, scores only.", "set_consent"),
        ("My mood is 4, energy 3, stress 2.", "log_checkin"),
        ("How was my week?", "get_weekly_reflection"),
        ("What can I do right now?", "suggest_micro_action"),
        ("Alexa, tell Elin this is a short end-to-end test.", "ask_elin"),
    ]
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url)
            for i, (text, _tool) in enumerate(flow):
                reply = _say(page, i, text, timeout=90_000)
                assert "session has ended" not in reply, reply
                assert "couldn't reach Aawiz" not in reply, reply
            assert "in Aawiz" in page.locator(".turn.assistant .text").nth(2).inner_text()
            tools = page.locator("#mcp-log li").evaluate_all("els => els.map(e => e.dataset.tool)")
            assert tools == [t for _, t in flow if t]
            assert page.locator("#mcp-log li[data-ok='false']").count() == 0
            browser.close()
    finally:
        for t in reversed(threads):
            t.stop()
