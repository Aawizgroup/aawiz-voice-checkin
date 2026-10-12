"""Cost guard for the public demo: how many turns may go to Nebius."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

HOUR = 60 * 60
DAY = 24 * HOUR
PER_VISITOR_PER_HOUR = 20
TOTAL_PER_DAY = 300
MAX_VISITORS = 5000

LIMIT_NOTE = "Demo limit reached. The scripted brain is answering for now."


class DemoLimiter:
    """At most 20 Nebius turns per visitor per hour and 300 per day in total."""

    def __init__(
        self,
        per_visitor: int = PER_VISITOR_PER_HOUR,
        total: int = TOTAL_PER_DAY,
        clock: Callable[[], float] = time.time,
    ):
        self.per_visitor = per_visitor
        self.total = total
        self._clock = clock
        self._visitors: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def allow(self, visitor: str) -> bool:
        """True (and counted) when this visitor may use Nebius for one more turn."""
        now = self._clock()
        while self._all and now - self._all[0] >= DAY:
            self._all.popleft()
        mine = self._visitors.setdefault(visitor, deque())
        while mine and now - mine[0] >= HOUR:
            mine.popleft()
        if len(self._all) >= self.total or len(mine) >= self.per_visitor:
            return False
        mine.append(now)
        self._all.append(now)
        if len(self._visitors) > MAX_VISITORS:  # forget idle visitors
            for key in [k for k, v in self._visitors.items() if not v]:
                del self._visitors[key]
        return True
