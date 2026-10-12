"""Synthetic demo data, and the per-visitor users of the public demo (DEMO_PUBLIC=true).

All data here is invented. It is not from any real person. Every row carries the note
"Synthetic demo data". In the public demo each visitor gets a random pseudonym with a fresh
copy of this history, and everything a visitor's pseudonym holds is deleted after two hours.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from datetime import date, timedelta

from mcp.server.auth.provider import AccessToken

from server.storage import Checkin, Store

LABEL = "Synthetic demo data"

# days ago -> (mood, energy, stress, sleep hours). Today is left empty so the live check-in in
# the demo is today's. The last week is heavier than the week before, so the weekly reflection
# has something to say.
DATA = {
    14: (4, 4, 2, 7.5),
    13: (4, 3, 2, 7.0),
    12: (3, 4, 3, 7.0),
    11: (4, 4, 2, 8.0),
    10: (4, 3, 3, 7.0),
    9: (3, 4, 2, 7.5),
    8: (4, 4, 2, 8.0),
    7: (4, 3, 2, 7.0),
    6: (3, 3, 3, 7.0),
    5: (3, 2, 4, 6.0),
    4: (2, 2, 4, 5.5),
    3: (4, 3, 2, 7.5),
    2: (3, 2, 4, 6.0),
    1: (2, 3, 4, 6.0),
}

DEMO_PREFIX = "demo_"
DEMO_TTL_SECONDS = 2 * 60 * 60
VISITOR_ID = re.compile(r"^[a-f0-9]{16,64}$")


def seed(store: Store, user: str, today: date | None = None) -> int:
    """Replace this user's data with the synthetic set. Returns the number of check-ins."""
    today = today or date.today()
    store.delete_user(user)
    store.set_consent(user, scores=True, notes=True)
    for days_ago in sorted(DATA, reverse=True):
        mood, energy, stress, sleep = DATA[days_ago]
        day = today - timedelta(days=days_ago)
        store.add_checkin(user, Checkin(day, mood, energy, stress, sleep, LABEL))
    return len(DATA)


class DemoUsers:
    """Creates a seeded pseudonym per visitor and deletes it two hours later."""

    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self.store = store
        self._clock = clock
        self._created: dict[str, float] = {}
        self._lock = threading.Lock()
        store.delete_prefix(DEMO_PREFIX)  # nothing survives a restart

    def user_for(self, visitor_id: str) -> str:
        """The visitor's pseudonym, seeded on first use (and again after it expired)."""
        user = DEMO_PREFIX + visitor_id
        with self._lock:
            born = self._created.get(user)
            now = self._clock()
            if born is None or now - born >= DEMO_TTL_SECONDS:
                seed(self.store, user)
                self._created[user] = now
        return user

    def purge_expired(self) -> int:
        """Delete every visitor's rows that are older than two hours. Returns users removed."""
        now = self._clock()
        with self._lock:
            old = [u for u, born in self._created.items() if now - born >= DEMO_TTL_SECONDS]
            for user in old:
                self.store.delete_user(user)
                del self._created[user]
        return len(old)


class DemoTokenVerifier:
    """Public demo auth: `<shared token>.<visitor id>` maps to that visitor's own pseudonym.

    The shared token is known only to the simulator in the same container. The visitor id is
    random per browser session, so one visitor can never address another visitor's data.
    """

    def __init__(self, tokens: dict[str, str], users: DemoUsers):
        self._tokens = dict(tokens)
        self._users = users

    async def verify_token(self, token: str) -> AccessToken | None:
        shared, sep, visitor = token.rpartition(".")
        if not sep or shared not in self._tokens or not VISITOR_ID.match(visitor):
            return None
        return AccessToken(token=token, client_id=self._users.user_for(visitor), scopes=[])
