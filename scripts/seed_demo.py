"""Create 14 days of clearly labelled SYNTHETIC demo data for the demo user.

The data is invented for the demo video. It is not from any real person.
Every row carries the note "Synthetic demo data". Today is left empty so the live
check-in in the demo is today's. The pattern is chosen so the weekly reflection has
something to say: the last week is heavier than the week before.

Usage: python scripts/seed_demo.py [--user user_demo]
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from server.config import get_settings
from server.storage import Checkin, Store

LABEL = "Synthetic demo data"

# days ago -> (mood, energy, stress, sleep hours)
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user", help="pseudonym to seed (default: the SIM_TOKEN's user)")
    args = parser.parse_args()

    settings = get_settings()
    user = args.user or settings.tokens.get(os.environ.get("SIM_TOKEN", ""), "user_demo")
    store = Store(settings.db_path)
    store.delete_user(user)
    store.set_consent(user, scores=True, notes=True)
    today = date.today()
    for days_ago in sorted(DATA, reverse=True):
        mood, energy, stress, sleep = DATA[days_ago]
        day = today - timedelta(days=days_ago)
        store.add_checkin(user, Checkin(day, mood, energy, stress, sleep, LABEL))
    print(
        f"SYNTHETIC demo data: {len(DATA)} check-ins over 14 days for pseudonym "
        f"'{user}' in {settings.db_path}"
    )


if __name__ == "__main__":
    main()
