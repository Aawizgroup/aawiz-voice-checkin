"""What each tool does, independent of MCP. Every result carries a short `speech` line."""

from __future__ import annotations

from datetime import date

from server.actions import choose_action
from server.config import policy
from server.reflection import weekly_reflection
from server.safety import banned_words_in, contains_crisis, safety_message
from server.storage import Checkin, Store

FALLBACK = "Thanks. I have noted that."


def _clean(speech: str) -> str:
    """Last line of defence: never speak a clinical word, even by accident."""
    return FALLBACK if banned_words_in(speech) else speech


class CheckinService:
    def __init__(self, store: Store, region: str):
        self.store = store
        self.region = region

    # 1 ------------------------------------------------------------------
    def set_consent(self, user: str, scores: bool, notes: bool) -> dict:
        c = self.store.set_consent(user, scores, notes)
        if scores and notes:
            speech = "Got it. I will keep your check-in scores and short notes."
        elif scores:
            speech = "Got it. I will keep your check-in scores, but not your notes."
        else:
            speech = "Understood. I will not keep anything from your check-ins."
        return {"scores": c.scores, "notes": c.notes, "at": c.at, "speech": _clean(speech)}

    # 2 ------------------------------------------------------------------
    def log_checkin(
        self,
        user: str,
        mood: int,
        energy: int,
        stress: int,
        sleep_hours: float | None = None,
        note: str | None = None,
        today: date | None = None,
    ) -> dict:
        for name, value in (("mood", mood), ("energy", energy), ("stress", stress)):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
                raise ValueError(f"{name} must be a whole number from 1 to 5")
        if sleep_hours is not None and not 0 <= sleep_hours <= 14:
            raise ValueError("sleep_hours must be between 0 and 14")
        if note is not None and len(note) > policy()["note_max_chars"]:
            raise ValueError("note must be 280 characters or fewer")

        # Safety first: crisis language means nothing from this check-in is stored or reflected on.
        if contains_crisis(note):
            return {"stored": False, "safety": True, "speech": safety_message(self.region)}

        consent = self.store.get_consent(user)
        if not consent.scores:
            return {
                "stored": False,
                "speech": "I can't save check-ins yet, because you haven't allowed me to keep "
                "scores. Say yes to consent first.",
            }
        kept_note = note if (note and consent.notes) else None
        self.store.add_checkin(
            user, Checkin(today or date.today(), mood, energy, stress, sleep_hours, kept_note)
        )
        speech = "Thanks, I've saved today's check-in."
        if note and not consent.notes:
            speech += " I didn't keep your note, because notes are switched off."
        return {"stored": True, "note_kept": kept_note is not None, "speech": _clean(speech)}

    # 3 ------------------------------------------------------------------
    def weekly_reflection(self, user: str, today: date | None = None) -> dict:
        p = policy()
        result = weekly_reflection(
            self.store.checkins(user),
            today or date.today(),
            threshold=p["direction_threshold"],
            min_points=p["min_checkins_for_reflection"],
        )
        result["speech"] = _clean(result["sentence"])
        return result

    # 4 ------------------------------------------------------------------
    def suggest_micro_action(self, user: str, context: str | None = None) -> dict:
        if contains_crisis(context):
            return {"action": None, "safety": True, "speech": safety_message(self.region)}
        rows = self.store.checkins(user)
        key = choose_action(rows[-1] if rows else None, context)
        return {"action": key, "speech": _clean(policy()["micro_actions"][key])}

    # 5 ------------------------------------------------------------------
    def privacy_summary(self) -> dict:
        p = policy()
        return {
            "stored": ["date", "mood", "energy", "stress", "sleep_hours (optional)",
                       "note (optional, only with notes consent)"],
            "retention_days": p["retention_days"],
            "speech": _clean(p["privacy_summary"]),
        }

    # 6 ------------------------------------------------------------------
    def export(self, user: str) -> dict:
        data = self.store.export(user)
        n = len(data["checkins"])
        return {"data": data, "speech": f"Here is everything I keep about you: {n} check-ins."}

    # 7 ------------------------------------------------------------------
    def delete(self, user: str) -> dict:
        removed = self.store.delete_user(user)
        return {
            "deleted_rows": removed,
            "speech": "Done. I've deleted all your check-ins and consent records.",
        }
