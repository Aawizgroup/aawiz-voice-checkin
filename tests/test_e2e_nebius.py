"""Browser test for the hosted demo page: the Nebius label, the fallback label, the notice."""

from __future__ import annotations

import pytest

from server.app import create_app
from server.config import Settings
from simulator.app import DEMO_NOTICE, create_sim_app
from simulator.brain_nebius import FALLBACK_LABEL
from tests.conftest import ServerThread, free_port
from tests.nebius_fake import MODEL, FakeNebius, tool_call

playwright = pytest.importorskip("playwright.sync_api")

TOKEN = "e2e-token"


@pytest.mark.e2e
def test_demo_page_shows_the_brain_labels_and_the_notice(tmp_path):
    settings = Settings(tokens={TOKEN: "x"}, data_dir=tmp_path, region="NL", demo_public=True)
    fake = FakeNebius(
        [tool_call("get_weekly_reflection")],
        "Your check-ins suggest a heavier week than the one before.",
        500,  # the second turn: Nebius fails, the scripted brain answers
    )
    mcp_port, sim_port = free_port(), free_port()
    threads = [
        ServerThread(create_app(settings, port=mcp_port), mcp_port),
        ServerThread(
            create_sim_app(
                mcp_url=f"http://127.0.0.1:{mcp_port}/mcp",
                token=TOKEN,
                mode="nebius",
                region="NL",
                demo=True,
                nebius_client=fake.client(),
                nebius_model=MODEL,
            ),
            sim_port,
        ),
    ]
    for t in threads:
        t.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{sim_port}")
            assert page.title() == "Aawiz: a private personal AI for daily check-ins"
            assert "Alexa" not in page.inner_text("body")
            page.wait_for_selector(f"#brain-badge:has-text('Brain: Nebius · {MODEL}')")
            assert page.inner_text("#demo-notice") == DEMO_NOTICE
            assert page.is_visible("#demo-notice")
            page.wait_for_selector("#voice-badge:has-text('Voice: browser')")

            page.fill("#text-input", "How was my week?")
            page.click("#send-button")
            first = page.locator(".turn.assistant .text").nth(0)
            first.wait_for(timeout=15_000)
            assert "heavier week" in first.inner_text()
            assert page.locator("#mcp-log li").evaluate_all(
                "els => els.map(e => e.dataset.tool)"
            ) == ["get_weekly_reflection"]

            page.fill("#text-input", "What do you keep about me?")
            page.click("#send-button")
            second = page.locator(".turn.assistant .text").nth(1)
            second.wait_for(timeout=15_000)
            assert "90 days" in second.inner_text()
            page.wait_for_selector(f"#brain-badge:has-text('{FALLBACK_LABEL}')")

            page.set_viewport_size({"width": 390, "height": 844})
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            browser.close()
    finally:
        for t in reversed(threads):
            t.stop()
    assert not overflow, "the page must not scroll sideways on a phone"
