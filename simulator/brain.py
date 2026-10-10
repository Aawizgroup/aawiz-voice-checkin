"""The scripted brain: a deterministic intent router that needs no API key."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from server.safety import contains_crisis, safety_message
from simulator.bridge import McpBridge, ToolCall

WAKE = re.compile(r"^\s*(?:hey\s+|ok\s+)?alexa[\s,.!:]*", re.IGNORECASE)
NUM = r"(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip

YES = re.compile(r"\b(yes|yeah|yep|sure|ok|okay|both|agree|go ahead|please do|do it)\b")
NO = re.compile(r"\b(no|nope|don't|do not|nothing|neither)\b")
SCORES_ONLY = re.compile(
    r"(scores? only|only (the )?scores?|no notes|not (the )?notes|without notes)"
)

GREETING = (
    "Hi, welcome to Aawiz check-in. Before I keep anything: may I store your daily scores "
    "and short notes? Say yes, scores only, or no."
)
ASK_SCORES = " How are you today? Tell me your mood, energy and stress, each from 1 to 5."
HELP = (
    "You can tell me your mood, energy and stress from 1 to 5, ask how your week was, "
    "ask what you can do right now, or ask what I keep about you."
)


@dataclass
class SessionState:
    awaiting: str | None = None  # "consent" | "delete" | None
    history: list = field(default_factory=list)  # used by the llm brain


@dataclass
class Turn:
    reply: str
    tools: list[ToolCall] = field(default_factory=list)
    safety: bool = False


def _number(token: str) -> float:
    return float(WORDS.get(token, token))


def _whole(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def parse_checkin(text: str) -> dict:
    """Pull mood, energy, stress, sleep and an optional note out of one utterance."""
    t = text.lower()
    args: dict = {}
    for key in ("mood", "energy", "stress"):
        m = re.search(rf"\b{key}\b\D{{0,12}}?\b{NUM}\b", t)
        if m:
            args[key] = _whole(_number(m.group(1)))
    sleep = re.search(rf"\bslept\D{{0,12}}?\b{NUM}\b", t) or re.search(
        rf"\b{NUM}\s*(?:hours?|hrs?)\b(?:\s+of)?\s+sleep", t
    )
    if sleep:
        args["sleep_hours"] = _number(sleep.group(1))
    note = re.search(r"\bnote\b\s*[:,-]?\s+(.+)$", text, re.IGNORECASE)
    if note:
        args["note"] = note.group(1).strip()[:280]
    return args


def strip_wake(text: str) -> str:
    return WAKE.sub("", text).strip()


class ScriptedBrain:
    mode = "scripted"

    def __init__(self, bridge: McpBridge, region: str = "NL"):
        self.bridge = bridge
        self.region = region

    async def handle(self, text: str, state: SessionState) -> Turn:
        raw = strip_wake(text)
        t = raw.lower()

        # Safety first: crisis language is never routed to any tool or reflection.
        if contains_crisis(raw):
            state.awaiting = None
            return Turn(safety_message(self.region), safety=True)

        if state.awaiting == "consent":
            return await self._consent_answer(t, state)
        if state.awaiting == "delete":
            return await self._delete_answer(t, state)

        if re.search(r"\b(open|start|launch|begin)\b", t) and re.search(r"aawiz|check.?in", t):
            state.awaiting = "consent"
            return Turn(GREETING)
        if re.search(r"\b(change|update|withdraw)\b.*\bconsent\b", t):
            state.awaiting = "consent"
            return Turn(
                "Sure. May I store your daily scores and short notes? Say yes, scores only, or no."
            )
        if any(re.search(rf"\b{k}\b", t) for k in ("mood", "energy", "stress")):
            return await self._checkin(raw, state)
        if re.search(r"\b(delete|erase|remove|wipe)\b.*\b(data|everything|me)\b|forget me", t):
            state.awaiting = "delete"
            return Turn(
                "This removes all your check-ins and consent records, and it cannot be "
                "undone. Should I delete everything? Say yes or no."
            )
        if re.search(r"how (was|is|has been) my week|\bmy week\b|\bweekly\b|\breflect", t):
            return await self._call("get_weekly_reflection", {})
        if re.search(r"what can i do|\bsuggest|right now|something to do|what should i do", t):
            return await self._call("suggest_micro_action", {"context": raw[:280]})
        if re.search(r"what do you (keep|store|save|know|have)|\bprivacy\b|data about me", t):
            return await self._call("get_privacy_summary", {})
        if re.search(r"\bexport\b|download (my|all)|(give|send) me my data", t):
            return await self._call("export_my_data", {})
        return Turn(HELP)

    async def _call(self, tool: str, args: dict) -> Turn:
        call = await self.bridge.call(tool, args)
        speech = (call.data or {}).get("speech") or "Sorry, that didn't work. Please try again."
        return Turn(speech, [call], safety=bool((call.data or {}).get("safety")))

    async def _consent_answer(self, t: str, state: SessionState) -> Turn:
        if SCORES_ONLY.search(t):
            scores, notes = True, False
        elif YES.search(t):
            scores, notes = True, True
        elif NO.search(t):
            scores, notes = False, False
        else:
            return Turn("Sorry, I didn't catch that. Say yes, scores only, or no.")
        state.awaiting = None
        turn = await self._call("set_consent", {"scores": scores, "notes": notes})
        turn.reply += ASK_SCORES if scores else " You can still ask what I keep about you."
        return turn

    async def _delete_answer(self, t: str, state: SessionState) -> Turn:
        state.awaiting = None
        if YES.search(t) and not NO.search(t):
            return await self._call("delete_my_data", {})
        return Turn("Okay, I kept everything as it is.")

    async def _checkin(self, raw: str, state: SessionState) -> Turn:
        args = parse_checkin(raw)
        missing = [k for k in ("mood", "energy", "stress") if k not in args]
        if missing:
            return Turn(f"I still need your {' and '.join(missing)}, each from 1 to 5.")
        call = await self.bridge.call("log_checkin", args)
        if not call.ok:
            return Turn(
                "Scores go from 1 to 5, in whole numbers. Could you say them again?", [call]
            )
        data = call.data or {}
        turn = Turn(data.get("speech", "Saved."), [call], safety=bool(data.get("safety")))
        if not data.get("stored") and not data.get("safety"):
            state.awaiting = "consent"
        return turn
