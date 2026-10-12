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

from server.config import get_settings
from server.demo import DATA, LABEL, seed  # noqa: F401  (re-exported for tests and tools)
from server.storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user", help="pseudonym to seed (default: the SIM_TOKEN's user)")
    args = parser.parse_args()

    settings = get_settings()
    user = args.user or settings.tokens.get(os.environ.get("SIM_TOKEN", ""), "user_demo")
    seed(Store(settings.db_path), user)
    print(
        f"SYNTHETIC demo data: {len(DATA)} check-ins over 14 days for pseudonym "
        f"'{user}' in {settings.db_path}"
    )


if __name__ == "__main__":
    main()
