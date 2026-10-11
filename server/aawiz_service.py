"""Aawiz mode: the same tools, backed by the real Aawiz product and Elin.

Order inside every tool: validate, crisis check (no network on crisis language), consent, then
the local mirror, then Aawiz. Everything spoken passes the banned-word guard.
"""

from __future__ import annotations

from datetime import date

from server.aawiz_client import AawizClient, AawizError, AawizSessionEnded
from server.actions import choose_action
from server.config import policy
from server.safety import banned_words_in, contains_crisis, safety_message
from server.service import CheckinService, _clean

# Our 1-5 mood score -> the name of a mood that Aawiz seeds
# (Aawiz.API apps/mood_tracker/migrations/0004_seed_moods_and_activities.py: name, score).
# Aawiz has no energy, stress, sleep or note field on a mood entry; those stay in the local mirror.
MOOD_MAP = {1: "Heavy", 2: "Down", 3: "Neutral", 4: "Happy", 5: "Bright"}

APP_MANAGED = (
    " Your Aawiz account and everything Elin knows is managed in the Aawiz app, where you can "
    "delete your account at any time."
)
ELIN_FILTERED = (
    "Elin answered, but her reply used words this voice skill does not speak. You can read it "
    "in the Aawiz app."
)
ASK_ELIN_MAX = 500


def _direction(points: list[dict], threshold: float) -> str:
    """Up, down or steady across the week's mood trend (values 0 to 4 in Aawiz)."""
    if len(points) < 2:
        return "steady"
    delta = float(points[-1]["value"]) - float(points[0]["value"])
    if delta >= threshold:
        return "up"
    if delta <= -threshold:
        return "down"
    return "steady"


class AawizCheckinService(CheckinService):
    def __init__(self, store, region: str, client: AawizClient, tz: str = "Europe/Amsterdam"):
        super().__init__(store, region)
        self.client = client
        self.tz = tz

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
        # Validation, crisis check and consent all happen locally, before any network call.
        local = super().log_checkin(user, mood, energy, stress, sleep_hours, note, today)
        if not local.get("stored"):
            return local
        try:
            self.client.create_mood_entry(MOOD_MAP[mood])
        except AawizError as exc:
            local["aawiz"] = False
            local["summary"] = "stored here, not in Aawiz"
            local["speech"] = _clean(f"I saved it here, but not in Aawiz. {exc.speech}")
            return local
        local["aawiz"] = True
        local["aawiz_mood"] = MOOD_MAP[mood]
        local["summary"] = f"stored in Aawiz as {MOOD_MAP[mood]}"
        local["speech"] = _clean(local["speech"].replace("check-in.", "check-in in Aawiz.", 1))
        return local

    # 3 ------------------------------------------------------------------
    def weekly_reflection(self, user: str, today: date | None = None) -> dict:
        try:
            report = self.client.weekly_report()
        except AawizError as exc:
            return {"source": "aawiz", "summary": "Aawiz unreachable", "speech": exc.speech}
        if not report.get("has_entries"):
            speech = "There are no Aawiz check-ins in the last weeks yet."
            return {"source": "aawiz", "has_entries": False, "summary": "no entries yet",
                    "speech": _clean(speech)}  # fmt: skip
        trend = _direction(report.get("mood_trend") or [], policy()["direction_threshold"])
        streak = (report.get("streak") or {}).get("current_streak", 0)
        # Drop any Aawiz sentence the guard would refuse, keep the rest.
        parts = [s for s in report.get("weekly_reflection") or [] if not banned_words_in(s)]
        if trend != "steady":
            parts.append(f"Your mood this week is going {trend}.")
        if streak:
            parts.append(f"You've checked in {streak} days in a row.")
        speech = " ".join(parts) or "You have Aawiz check-ins, but none this week yet."
        return {
            "source": "aawiz",
            "has_entries": True,
            "mood_trend": trend,
            "current_streak": streak,
            "summary": f"trend {trend}, streak {streak}",
            "speech": _clean(speech),
        }

    # 4 ------------------------------------------------------------------
    def suggest_micro_action(self, user: str, context: str | None = None) -> dict:
        if contains_crisis(context):
            return {"action": None, "safety": True, "speech": safety_message(self.region)}
        try:
            row = self.client.suggestion_today(self.tz)
        except AawizSessionEnded as exc:  # say so clearly instead of hiding it
            return {"action": None, "summary": "session ended", "speech": exc.speech}
        except AawizError:
            row = None
        if row and row.get("title") and not banned_words_in(row["title"]):
            reason = str(row.get("reason") or "").strip()
            speech = f"Elin suggests: {row['title'].rstrip('.')}."
            if reason and not banned_words_in(reason):
                speech += f" {reason}"
            return {
                "action": "elin_suggests",
                "kind": row.get("kind"),
                "summary": f"Elin suggests: {row['title']}",
                "speech": _clean(speech),
            }
        rows = self.store.checkins(user)
        key = choose_action(rows[-1] if rows else None, context)
        speech = "Elin has no suggestion for today, so here is one small thing. "
        speech += policy()["micro_actions"][key]
        return {"action": key, "summary": f"{key} (curated)", "speech": _clean(speech)}

    # 5 ------------------------------------------------------------------
    def privacy_summary(self) -> dict:
        result = super().privacy_summary()
        result["speech"] = _clean(result["speech"] + APP_MANAGED)
        return result

    # 7 ------------------------------------------------------------------
    def delete(self, user: str) -> dict:
        # Only the local mirror. The real account is never deleted from here.
        result = super().delete(user)
        result["speech"] = _clean(result["speech"] + APP_MANAGED)
        return result

    # 8 ------------------------------------------------------------------
    def ask_elin(self, user: str, message: str) -> dict:
        message = (message or "").strip()
        if not message:
            return {"sent": False, "speech": "What would you like to tell Elin?"}
        if len(message) > ASK_ELIN_MAX:
            raise ValueError(f"message must be {ASK_ELIN_MAX} characters or fewer")
        if contains_crisis(message):
            return {"sent": False, "safety": True, "speech": safety_message(self.region)}
        try:
            reply = self.client.ask_elin(message)
        except AawizError as exc:
            return {"sent": False, "summary": "Aawiz unreachable", "speech": exc.speech}
        if not reply:
            return {"sent": True, "summary": "empty reply", "speech": ELIN_FILTERED}
        if banned_words_in(reply):
            return {"sent": True, "summary": "reply filtered", "speech": ELIN_FILTERED}
        return {"sent": True, "summary": f"Elin replied ({len(reply)} chars)", "speech": reply}
