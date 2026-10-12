"""Nebius brain (BRAIN_MODE=nebius): an NVIDIA open model on Nebius Token Factory decides the calls.

The model receives our MCP tools as function definitions (built from the live tools/list) and
chooses which to call. Every call still goes through the real MCP client, so the activity panel
shows it. Needs `pip install -e ".[nebius]"` and NEBIUS_API_KEY.

API used (Nebius Token Factory docs, docs.tokenfactory.nebius.com): the OpenAI-compatible
endpoint https://api.tokenfactory.nebius.com/v1/ with the official `openai` package;
`chat.completions.create(model, messages, tools, tool_choice)`; tool results go back as
`{"role": "tool", "tool_call_id", "name", "content"}`.

Order in every turn: crisis check first (no request to Nebius on crisis language), then the
model, then the banned-word guard on its reply. Any Nebius error falls back to the scripted
brain for that turn.
"""

from __future__ import annotations

import json

import openai

from server.safety import banned_words_in, contains_crisis, safety_message
from simulator.brain import ScriptedBrain, SessionState, Turn, strip_wake
from simulator.bridge import McpBridge, ToolCall

BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
TIMEOUT = 15.0
MAX_TOOL_CALLS = 4  # per turn; a fifth is refused
MAX_REQUESTS = 6  # model round trips per turn
MAX_TOKENS = 400
HISTORY_MESSAGES = 24
FALLBACK_LABEL = "Brain: scripted (fallback)"
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
- Before logging a check-in, make sure the person has given consent with set_consent. If they \
have not, ask whether you may keep their scores and short notes.
- Ask again if a score is missing or out of range.
- Before delete_my_data, ask the person to confirm, and call it only after a clear yes.

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
                    content = await self._run_tool(tc, calls)
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
            # The guard refused the model's words: the scripted answer for this turn instead.
            return await self._fallback(text, state, calls, label=self.label)

        state.history[:] = [*messages, {"role": "assistant", "content": reply}]
        safety = any(c.data and c.data.get("safety") for c in calls)
        return Turn(reply, calls, safety=safety, brain=self.label)

    async def _run_tool(self, tc, calls: list[ToolCall]) -> str:
        if len(calls) >= MAX_TOOL_CALLS:
            return LIMIT_REFUSAL
        try:
            args = json.loads(tc.function.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except ValueError:
            return json.dumps({"error": "arguments were not valid JSON"})
        call = await self.bridge.call(tc.function.name, args)
        calls.append(call)
        return call.text or "{}"

    async def _fallback(
        self, text: str, state: SessionState, calls: list[ToolCall], label: str = FALLBACK_LABEL
    ) -> Turn:
        """The scripted brain's answer for this turn.

        If tools already ran this turn, their own spoken line is that answer; running the
        scripted brain again would repeat the side effect (for example log a check-in twice).
        """
        speech = next(
            (c.data["speech"] for c in reversed(calls) if c.data and c.data.get("speech")), ""
        )
        if speech:
            safety = any(c.data and c.data.get("safety") for c in calls)
            return Turn(speech, calls, safety=safety, brain=label)
        turn = await self.scripted.handle(text, state)
        turn.tools = [*calls, *turn.tools]
        turn.brain = label
        return turn
