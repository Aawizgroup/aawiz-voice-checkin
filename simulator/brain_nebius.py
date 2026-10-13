"""Nebius brain (BRAIN_MODE=nebius): an NVIDIA open model on Nebius Token Factory decides the calls.

The model receives our MCP tools as function definitions (built from the live tools/list) and
chooses which to call. Every call still goes through the real MCP client, so the activity panel
shows it. Needs `pip install -e ".[nebius]"` and NEBIUS_API_KEY.

API used (Nebius Token Factory docs, docs.tokenfactory.nebius.com): the OpenAI-compatible
endpoint https://api.tokenfactory.nebius.com/v1/ with the official `openai` package;
`chat.completions.create(model, messages, tools, tool_choice)`; tool results go back as
`{"role": "tool", "tool_call_id", "name", "content"}`.

Order in every turn: crisis check first (no request to Nebius on crisis language), then the
model, then the banned-word guard on its reply. Three things are decided in code and never by
the model: consent (only the person's words grant it), deleting (only the person's yes runs
it) and the scores of a check-in (only numbers the person said in their current message).
Any Nebius error falls back to the scripted brain for that turn.
"""

from __future__ import annotations

import json
import re

import openai

from server.safety import banned_words_in, contains_crisis, safety_message
from simulator.brain import (
    DELETE_CONFIRM,
    GREETING,
    NO,
    NUM,
    SCORES_ONLY,
    YES,
    ScriptedBrain,
    SessionState,
    Turn,
    _number,
    _whole,
    parse_checkin,
    strip_wake,
)
from simulator.bridge import McpBridge, ToolCall

BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
TIMEOUT = 15.0
MAX_TOOL_CALLS = 4  # per turn; a fifth is refused
MAX_REQUESTS = 6  # model round trips per turn
MAX_TOKENS = 2048  # Nemotron reasons before it answers; 400 was all spent on reasoning
HISTORY_MESSAGES = 24
FALLBACK_LABEL = "Brain: scripted (fallback)"
CONSENT_REFUSAL = json.dumps(
    {
        "error": "not allowed: consent must come from the person, in their own words. If they "
        "already agreed earlier, go on and call log_checkin; it will say if consent is missing."
    }
)
CONSENT_WORDS = re.compile(r"\b(store|storing|keep|keeping|save|saving|consent|record)\b")
SCORE_KEYS = ("mood", "energy", "stress")
# "I'd like to do my check-in", "open Aawiz check-in", "let's check in": a wish to start.
CHECKIN_INTENT = re.compile(
    r"\b(do|start|open|begin|launch|log|record|make|have)\b.{0,30}\bcheck[\s-]?in\b"
    r"|\b(want|like|ready|time|let'?s|let us)\b.{0,12}\bcheck[\s-]?in\b"
    r"|\b(open|start|launch|begin)\b.{0,20}\baawiz\b"
)
LIMIT_REFUSAL = json.dumps(
    {"error": "tool call limit reached for this turn; answer with what you already have"}
)

SYSTEM = """You are the voice of Aawiz check-in. People use you for a short daily check-in \
about their mood, energy and stress, each a whole number from 1 to 5.

How to talk:
- Reply in one or two short spoken sentences. No lists, no markdown, no emoji.
- When a tool result has a `speech` field, say that, shortened if needed.

What to do:
- Use the tools for everything about the person's check-ins. Never invent scores or history.
- When the person asks about their week, what they can do now, or what is kept about them, \
call the matching tool every time, even if you answered before. Never answer those from memory.
- When the person gives their scores, call log_checkin straight away. The tool checks \
consent itself, so do not check or set consent first.
- Call log_checkin only when the person's latest message contains their mood, energy and \
stress. The app takes the numbers from their words, not from you. If any is missing, ask for it.
- If log_checkin says consent is missing, ask whether you may keep their scores and short \
notes. After they answer, call set_consent with their answer, then log the check-in again.
- Consent comes only from the person. Never call set_consent unless they have just answered \
that question or asked to change their consent.
- Ask again if a score is missing or out of range.
- When the person asks to delete their data, call delete_my_data. The app asks them to \
confirm before anything is deleted.

Boundaries:
- You are not a medical service. You never diagnose, never name a condition and never give \
medication advice.
- You only describe patterns in the person's own check-ins, in plain words, and you never \
claim certainty."""


def make_client(api_key: str, http_client=None) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        base_url=BASE_URL,
        api_key=api_key,
        timeout=TIMEOUT,
        max_retries=0,  # one try, then the scripted brain answers
        http_client=http_client,
    )


def _confirmed(raw: str, state: SessionState) -> bool:
    """True when the person just said yes to a delete question the assistant asked."""
    t = raw.lower()
    if not YES.search(t) or NO.search(t):
        return False
    last = next((m for m in reversed(state.history) if m.get("role") == "assistant"), None)
    return bool(last and "delet" in str(last.get("content", "")).lower())


def _consent_said(raw: str, state: SessionState) -> tuple[bool, bool] | None:
    """(scores, notes) the person just agreed to, or None when they said nothing about consent.

    Counts only when the person answers a question the assistant asked about storing, or
    talks about storing themselves. What they said decides the values, not the model.
    """
    t = raw.lower()
    last = next((m for m in reversed(state.history) if m.get("role") == "assistant"), None)
    asked = bool(last and CONSENT_WORDS.search(str(last.get("content", "")).lower()))
    # A short answer such as "yes, scores and notes" also counts; a check-in sentence does not.
    scores, notes = re.search(r"\bscores?\b", t), re.search(r"\bnotes?\b", t)
    short_answer = (
        len(t.split()) <= 8
        and re.match(r"\W*(yes|yeah|yep|sure|ok|okay|no|nope)\b", t)
        and (scores or notes)
    )
    if not (asked or CONSENT_WORDS.search(t) or SCORES_ONLY.search(t) or short_answer):
        return None
    if SCORES_ONLY.search(t):
        return True, False
    if NO.search(t) and not YES.search(t):
        return False, False
    if YES.search(t) or re.search(r"\b(you (may|can)|allow|agree)\b", t):
        return True, not (scores and not notes)  # naming only scores grants only scores
    return None


def _last_assistant(state: SessionState) -> str:
    last = next((m for m in reversed(state.history) if m.get("role") == "assistant"), None)
    return str(last.get("content", "")).lower() if last else ""


def _scores_said(raw: str, state: SessionState) -> dict:
    """The check-in the person stated in this message: scores, sleep and note, nothing else.

    Scores are read next to their names ("mood 2, energy 3, stress 4"). Three bare numbers
    ("2, 3 and 4") count as mood, energy, stress only as the answer to a question that asked
    for exactly those. Whatever is missing here is missing: the model's numbers are not used.
    """
    said = parse_checkin(raw)
    if not any(k in said for k in SCORE_KEYS):
        asked = _last_assistant(state)
        numbers = re.findall(rf"\b{NUM}\b", raw.lower())
        if len(numbers) == 3 and all(k in asked for k in SCORE_KEYS):
            said.update({k: _whole(_number(n)) for k, n in zip(SCORE_KEYS, numbers, strict=True)})
    return said


def _checkin_refusal(missing: list[str]) -> str:
    return json.dumps(
        {
            "stored": False,
            "error": "not allowed: scores must come from the person's latest message, and it "
            f"has no {' or '.join(missing)}. Nothing was stored. Ask the person to say their "
            "mood, energy and stress together, each from 1 to 5.",
        }
    )


def _trim(history: list) -> list:
    """Keep the recent turns, and never start in the middle of a tool exchange."""
    recent = history[-HISTORY_MESSAGES:]
    while recent and recent[0].get("role") != "user":
        recent = recent[1:]
    return recent


class NebiusBrain:
    mode = "nebius"

    def __init__(
        self,
        bridge: McpBridge,
        region: str,
        model: str,
        client: openai.AsyncOpenAI,
        elin: bool = False,
    ):
        self.bridge = bridge
        self.region = region
        self.model = model
        self.client = client
        self.scripted = ScriptedBrain(bridge, region, elin)
        self.label = f"Brain: Nebius · {model}"
        self.tokens_used = 0
        self._tools: list[dict] | None = None

    async def _tool_defs(self) -> list[dict]:
        if self._tools is None:
            self._tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.input_schema,
                    },
                }
                for t in await self.bridge.list_tools()
            ]
        return self._tools

    async def _ask(self, messages: list, allow_tools: bool):
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM}, *messages],
            tools=await self._tool_defs(),
            tool_choice="auto" if allow_tools else "none",
            max_tokens=MAX_TOKENS,
            temperature=0.2,
        )
        if response.usage is not None:
            self.tokens_used += response.usage.total_tokens or 0
        return response.choices[0].message

    async def handle(self, text: str, state: SessionState) -> Turn:
        raw = strip_wake(text)
        # Safety first: on crisis language nothing is sent to Nebius.
        if contains_crisis(raw):
            state.awaiting = None
            return Turn(safety_message(self.region), safety=True)

        # Deleting is confirmed in code, never left to the model: a yes deletes, anything else
        # keeps the data. No request goes to Nebius for this answer.
        if state.awaiting == "delete":
            turn = await self.scripted._delete_answer(raw.lower(), state)
            turn.brain = self.label
            state.history += [
                {"role": "user", "content": raw},
                {"role": "assistant", "content": turn.reply},
            ]
            return turn

        # Consent before scores, like the scripted brain: someone who asks to check in and has
        # not been asked in this session gets the consent question first, written in code.
        if (
            not state.consent_asked
            and CHECKIN_INTENT.search(raw.lower())
            and not all(k in parse_checkin(raw) for k in SCORE_KEYS)
        ):
            state.consent_asked = True
            state.awaiting = "consent"  # so a fallback turn reads the answer correctly
            state.history += [
                {"role": "user", "content": raw},
                {"role": "assistant", "content": GREETING},
            ]
            return Turn(GREETING, brain=self.label)

        messages = [*_trim(state.history), {"role": "user", "content": raw}]
        calls: list[ToolCall] = []
        reply = ""
        try:
            for _ in range(MAX_REQUESTS):
                message = await self._ask(messages, allow_tools=len(calls) < MAX_TOOL_CALLS)
                tool_calls = message.tool_calls or []
                if not tool_calls:
                    reply = (message.content or "").strip()
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )
                for tc in tool_calls:
                    if tc.function.name == "delete_my_data" and not _confirmed(raw, state):
                        # The model may ask for it, but only the person's yes runs it.
                        state.awaiting = "delete"
                        state.history += [
                            {"role": "user", "content": raw},
                            {"role": "assistant", "content": DELETE_CONFIRM},
                        ]
                        return Turn(DELETE_CONFIRM, calls, brain=self.label)
                    content = await self._run_tool(tc, calls, raw, state)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.function.name,
                            "content": content,
                        }
                    )
        except (openai.OpenAIError, ValueError, AttributeError, IndexError) as exc:
            print(f'{{"event": "nebius_fallback", "error": "{type(exc).__name__}"}}', flush=True)
            return await self._fallback(text, state, calls)

        if not reply or banned_words_in(reply):
            # No usable words from the model (empty, or refused by the guard): the scripted
            # answer for this turn instead.
            return await self._fallback(text, state, calls)

        state.history[:] = [*messages, {"role": "assistant", "content": reply}]
        state.awaiting = None  # the model led this turn; drop any stale scripted question
        safety = any(c.data and c.data.get("safety") for c in calls)
        return Turn(reply, calls, safety=safety, brain=self.label)

    async def _run_tool(self, tc, calls: list[ToolCall], raw: str, state: SessionState) -> str:
        if len(calls) >= MAX_TOOL_CALLS:
            return LIMIT_REFUSAL
        try:
            args = json.loads(tc.function.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except ValueError:
            return json.dumps({"error": "arguments were not valid JSON"})
        if tc.function.name == "set_consent":
            # Consent is the person's to give. The model may only pass on what they just said.
            said = _consent_said(raw, state)
            if said is None:
                return CONSENT_REFUSAL
            args = {"scores": said[0], "notes": said[1]}
            state.consent_asked = True
        if tc.function.name == "log_checkin":
            # The scores are the person's: only numbers from their current message are logged.
            said_now = _scores_said(raw, state)
            missing = [k for k in SCORE_KEYS if k not in said_now]
            if missing:
                return _checkin_refusal(missing)
            note = said_now.get("note") or str(args.get("note") or "").strip()
            if note and note.lower() in raw.lower():  # a note is kept only in their own words
                said_now["note"] = note[:280]
            args = said_now
        call = await self.bridge.call(tc.function.name, args)
        calls.append(call)
        return call.text or "{}"

    async def _fallback(self, text: str, state: SessionState, calls: list[ToolCall]) -> Turn:
        """The scripted brain's answer for this turn.

        If the model already ran tools this turn, their own spoken line is that answer: running
        the scripted brain again would repeat the side effect (for example log a check-in
        twice). The model chose those tools, so the label stays Nebius. Otherwise the scripted
        brain handles the whole turn and the label says so.
        """
        speech = next(
            (c.data["speech"] for c in reversed(calls) if c.data and c.data.get("speech")), ""
        )
        if speech:
            safety = any(c.data and c.data.get("safety") for c in calls)
            state.awaiting = None
            return Turn(speech, calls, safety=safety, brain=self.label)
        turn = await self.scripted.handle(text, state)
        turn.tools = [*calls, *turn.tools]
        turn.brain = FALLBACK_LABEL
        return turn
