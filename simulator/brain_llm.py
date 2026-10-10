"""Optional LLM brain (BRAIN_MODE=llm): Claude reads the MCP tool schemas and decides the calls.

Needs `pip install -e ".[llm]"` and ANTHROPIC_API_KEY. The user's words are sent to the
Anthropic API in this mode; the scripted mode sends nothing anywhere except our MCP server.
"""

from __future__ import annotations

import anthropic

from server.safety import banned_words_in, contains_crisis, safety_message
from simulator.brain import SessionState, Turn, strip_wake
from simulator.bridge import McpBridge, ToolCall

# Models that take output_config.effort and the server-side refusal fallback ("default" form).
CURRENT_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
MAX_STEPS = 6

SYSTEM = """You are the voice of Aawiz check-in, running as an Alexa+ skill. People use you \
for a short daily check-in about their mood, energy and stress.

How to talk:
- Reply in one or two short spoken sentences. No lists, no markdown, no emoji.
- When a tool result has a `speech` field, say that, shortened if needed.

What to do:
- Before logging a check-in, make sure the person has given consent with set_consent. If \
they have not, ask whether you may keep their scores and short notes.
- Scores are whole numbers from 1 to 5. Ask again if one is missing or out of range.
- Before delete_my_data, ask the person to confirm, and call it only after a clear yes.

Boundaries:
- This is not a medical service. Never diagnose, never name a condition, never give \
medication advice. Talk only about early signs of stress, energy and patterns in the \
person's own check-ins, and never claim certainty."""


class LlmBrain:
    mode = "llm"

    def __init__(self, bridge: McpBridge, region: str, model: str):
        self.bridge = bridge
        self.region = region
        self.model = model
        self.client = anthropic.AsyncAnthropic()
        self._tools: list[dict] | None = None

    async def _tool_defs(self) -> list[dict]:
        if self._tools is None:
            self._tools = [
                {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                for t in await self.bridge.list_tools()
            ]
        return self._tools

    async def _ask(self, messages: list) -> object:
        params = {
            "model": self.model,
            "max_tokens": 4096,
            "system": SYSTEM,
            "tools": await self._tool_defs(),
            "messages": messages,
        }
        if self.model in CURRENT_MODELS:
            params["output_config"] = {"effort": "low"}  # short spoken turns
            return await self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params
            )
        return await self.client.messages.create(**params)

    async def handle(self, text: str, state: SessionState) -> Turn:
        raw = strip_wake(text)
        if contains_crisis(raw):
            return Turn(safety_message(self.region), safety=True)

        state.history.append({"role": "user", "content": raw})
        calls: list[ToolCall] = []
        response = None
        for _ in range(MAX_STEPS):
            response = await self._ask(state.history)
            if response.stop_reason == "refusal":
                state.history.pop()
                return Turn("Sorry, I can't help with that one.", calls)
            # Keep every block (thinking included) so the next request continues cleanly.
            state.history.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                break
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                call = await self.bridge.call(block.name, dict(block.input))
                calls.append(call)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": call.text or "{}",
                        "is_error": not call.ok,
                    }
                )
            state.history.append({"role": "user", "content": results})

        reply = ""
        if response is not None:
            reply = " ".join(b.text for b in response.content if b.type == "text").strip()
        last_speech = next(
            (c.data.get("speech") for c in reversed(calls) if c.data and c.data.get("speech")),
            "",
        )
        if not reply or banned_words_in(reply):
            reply = last_speech or "Sorry, could you say that again?"
        return Turn(reply, calls, safety=any(c.data and c.data.get("safety") for c in calls))
