"""Export, delete, isolation between users, and the 90-day retention purge."""

from datetime import date, timedelta

import pytest

from server.app import create_app
from server.storage import Checkin, Store
from tests.conftest import ServerThread, free_port, mcp_client, payload


async def _checkin(client, mood, note=None):
    args = {"mood": mood, "energy": 3, "stress": 3}
    if note:
        args["note"] = note
    return await client.call_tool("log_checkin", args)


@pytest.mark.anyio
async def test_export_returns_everything_for_that_user_only(live_server):
    url = live_server["url"]
    async with mcp_client(url, "token-a") as a, mcp_client(url, "token-b") as b:
        await a.call_tool("set_consent", {"scores": True, "notes": True})
        await b.call_tool("set_consent", {"scores": True, "notes": True})
        await _checkin(a, 4, "a's note")
        await _checkin(a, 2)
        await _checkin(b, 1, "b's private note")
        exported = payload(await a.call_tool("export_my_data", {}))["data"]
    assert exported["user"] == "user_a"
    assert len(exported["checkins"]) == 2
    assert len(exported["consent_records"]) == 1
    assert "b's private note" not in str(exported)
    assert {r["mood"] for r in exported["checkins"]} == {4, 2}


@pytest.mark.anyio
async def test_user_a_can_never_read_user_b(live_server):
    url = live_server["url"]
    async with mcp_client(url, "token-b") as b:
        await b.call_tool("set_consent", {"scores": True, "notes": True})
        await _checkin(b, 1, "only for b")
    async with mcp_client(url, "token-a") as a:
        mine = payload(await a.call_tool("export_my_data", {}))["data"]
        reflection = payload(await a.call_tool("get_weekly_reflection", {}))
    assert mine["checkins"] == [] and mine["consent_records"] == []
    assert reflection["data_points"] == 0


@pytest.mark.anyio
async def test_delete_leaves_zero_rows_for_that_user_and_keeps_others(live_server):
    url, store = live_server["url"], live_server["store"]
    async with mcp_client(url, "token-a") as a, mcp_client(url, "token-b") as b:
        for client in (a, b):
            await client.call_tool("set_consent", {"scores": True, "notes": True})
            await _checkin(client, 3)
        result = payload(await a.call_tool("delete_my_data", {}))
        after = payload(await a.call_tool("export_my_data", {}))["data"]
    assert result["deleted_rows"] == 2  # one check-in, one consent record
    assert store.count("user_a") == 0 and after["consent_records"] == []
    assert store.count("user_b") == 1


def test_retention_purge_removes_rows_older_than_90_days(tmp_path):
    store = Store(tmp_path / "ret.sqlite3")
    today = date(2026, 10, 10)
    for age in (120, 91, 90, 30, 0):
        store.add_checkin("u", Checkin(today - timedelta(days=age), 3, 3, 3, None, None))
    removed = store.purge_older_than(90, today=today)
    kept = {(today - r.day).days for r in store.checkins("u")}
    assert removed == 2
    assert kept == {90, 30, 0}


def test_retention_purge_runs_at_server_startup(settings):
    store = Store(settings.db_path)
    old = date.today() - timedelta(days=200)
    store.add_checkin("user_a", Checkin(old, 3, 3, 3, None, None))
    store.add_checkin("user_a", Checkin(date.today(), 3, 3, 3, None, None))
    port = free_port()
    thread = ServerThread(create_app(settings, port=port), port)
    thread.start()
    try:
        days = [r.day for r in store.checkins("user_a")]
    finally:
        thread.stop()
    assert days == [date.today()]
