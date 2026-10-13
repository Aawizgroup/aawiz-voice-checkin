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


VOICE_CHARS_PER_DAY = 3000


class VoiceBudget:
    """How many characters of reply text the public demo may send to ElevenLabs per day.

    Visitors are anonymous and the voice is paid for by the host, so above the budget the page
    simply uses the browser's own voice.
    """

    def __init__(
        self, chars_per_day: int = VOICE_CHARS_PER_DAY, clock: Callable[[], float] = time.time
    ):
        self.chars_per_day = chars_per_day
        self._clock = clock
        self._spent: deque[tuple[float, int]] = deque()

    def allow(self, chars: int) -> bool:
        """True (and counted) when a reply of this length still fits in today's budget."""
        now = self._clock()
        while self._spent and now - self._spent[0][0] >= DAY:
            self._spent.popleft()
        if sum(n for _, n in self._spent) + chars > self.chars_per_day:
            return False
        self._spent.append((now, chars))
        return True
