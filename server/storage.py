"""SQLite storage. One file, minimal columns, rows always scoped to one pseudonymous user."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS consent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    scores INTEGER NOT NULL,
    notes INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    day TEXT NOT NULL,
    mood INTEGER NOT NULL,
    energy INTEGER NOT NULL,
    stress INTEGER NOT NULL,
    sleep_hours REAL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_checkins_user_day ON checkins (user_id, day);
CREATE INDEX IF NOT EXISTS idx_consent_user ON consent (user_id);
"""


@dataclass(frozen=True)
class Consent:
    scores: bool
    notes: bool
    at: str | None


@dataclass(frozen=True)
class Checkin:
    day: date
    mood: int
    energy: int
    stress: int
    sleep_hours: float | None
    note: str | None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- consent -----------------------------------------------------------

    def set_consent(self, user_id: str, scores: bool, notes: bool) -> Consent:
        at = now_iso()
        with self._conn() as c:
            c.execute(
                "INSERT INTO consent (user_id, scores, notes, at) VALUES (?, ?, ?, ?)",
                (user_id, int(scores), int(notes), at),
            )
        return Consent(scores, notes, at)

    def get_consent(self, user_id: str) -> Consent:
        with self._conn() as c:
            row = c.execute(
                "SELECT scores, notes, at FROM consent WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        if row is None:
            return Consent(False, False, None)
        return Consent(bool(row[0]), bool(row[1]), row[2])

    # --- check-ins ---------------------------------------------------------

    def add_checkin(self, user_id: str, item: Checkin) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO checkins (user_id, day, mood, energy, stress, sleep_hours, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    item.day.isoformat(),
                    item.mood,
                    item.energy,
                    item.stress,
                    item.sleep_hours,
                    item.note,
                ),
            )

    def checkins(self, user_id: str, since: date | None = None) -> list[Checkin]:
        sql = "SELECT day, mood, energy, stress, sleep_hours, note FROM checkins WHERE user_id = ?"
        args: list = [user_id]
        if since is not None:
            sql += " AND day >= ?"
            args.append(since.isoformat())
        sql += " ORDER BY day, id"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [Checkin(date.fromisoformat(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in rows]

    # --- rights: export, delete, retention --------------------------------

    def export(self, user_id: str) -> dict:
        with self._conn() as c:
            consents = c.execute(
                "SELECT scores, notes, at FROM consent WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
        return {
            "user": user_id,
            "consent_records": [
                {"scores": bool(s), "notes": bool(n), "at": at} for s, n, at in consents
            ],
            "checkins": [
                {
                    "date": x.day.isoformat(),
                    "mood": x.mood,
                    "energy": x.energy,
                    "stress": x.stress,
                    "sleep_hours": x.sleep_hours,
                    "note": x.note,
                }
                for x in self.checkins(user_id)
            ],
        }

    def delete_user(self, user_id: str) -> int:
        with self._conn() as c:
            n = c.execute("DELETE FROM checkins WHERE user_id = ?", (user_id,)).rowcount
            n += c.execute("DELETE FROM consent WHERE user_id = ?", (user_id,)).rowcount
        return n

    def purge_older_than(self, days: int, today: date | None = None) -> int:
        cutoff = (today or date.today()) - timedelta(days=days)
        with self._conn() as c:
            return c.execute("DELETE FROM checkins WHERE day < ?", (cutoff.isoformat(),)).rowcount

    def count(self, user_id: str) -> int:
        with self._conn() as c:
            return c.execute(
                "SELECT COUNT(*) FROM checkins WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
