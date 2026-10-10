"""No tool ever speaks a clinical word, across many fixtures."""

import itertools
import json
from datetime import date, timedelta

import pytest

from server.config import policy
from server.safety import banned_words_in, safety_message
from server.service import CheckinService
from server.storage import Checkin, Store

TODAY = date(2026, 10, 10)
NOTES = [None, "long day at work", "felt good after lunch", "today was a hard day", "tired"]


def test_the_guard_itself_catches_clinical_words():
    for word in (
        "diagnosis",
        "Diagnosed",
        "a disorder",
        "depression",
        "burnout",
        "therapy",
        "anxiety disorder",
        "symptoms",
        "medication",
    ):
        assert banned_words_in(f"your {word} today"), word
    assert banned_words_in("early signs of stress and lower energy than usual") == []


def test_policy_texts_are_clean():
    p = policy()
    texts = [p["privacy_summary"], *p["micro_actions"].values()]
    texts += [safety_message(r) for r in p["resources"]]
    for t in texts:
        assert banned_words_in(t) == [], t


@pytest.mark.parametrize("seed_offset", range(4))
def test_every_tool_output_is_clean_over_many_fixtures(tmp_path, seed_offset):
    store = Store(tmp_path / f"lang{seed_offset}.sqlite3")
    svc = CheckinService(store, "NL")
    outputs = []
    user = f"user_{seed_offset}"

    outputs.append(svc.log_checkin(user, 3, 3, 3, today=TODAY))  # before consent
    outputs.append(svc.set_consent(user, False, False))
    outputs.append(svc.set_consent(user, True, False))
    outputs.append(svc.set_consent(user, True, True))

    combos = list(itertools.product(range(1, 6), repeat=3))
    for i, (mood, energy, stress) in enumerate(combos[seed_offset::4]):
        day = TODAY - timedelta(days=i % 14)
        note = NOTES[i % len(NOTES)]
        sleep = [None, 4.5, 7.0, 9.0][i % 4]
        outputs.append(svc.log_checkin(user, mood, energy, stress, sleep, note, today=day))
        if i % 5 == 0:
            outputs.append(svc.weekly_reflection(user, today=day))
            outputs.append(svc.suggest_micro_action(user, ["tense", None, "tired"][i % 3]))

    outputs.append(svc.weekly_reflection(user, today=TODAY))
    outputs.append(svc.privacy_summary())
    outputs.append(svc.delete(user))

    for out in outputs:
        text = json.dumps(out)
        assert banned_words_in(text) == [], text[:200]


def test_reflections_over_random_weeks_stay_short_and_clean(tmp_path):
    import random

    rng = random.Random(42)
    store = Store(tmp_path / "r.sqlite3")
    svc = CheckinService(store, "NL")
    for _ in range(200):
        store.delete_user("u")
        for d in range(14):
            if rng.random() < 0.7:
                store.add_checkin(
                    "u",
                    Checkin(
                        TODAY - timedelta(days=d),
                        rng.randint(1, 5),
                        rng.randint(1, 5),
                        rng.randint(1, 5),
                        None,
                        None,
                    ),
                )
        r = svc.weekly_reflection("u", today=TODAY)
        assert banned_words_in(json.dumps(r)) == []
        assert len(r["speech"].split()) <= 35
