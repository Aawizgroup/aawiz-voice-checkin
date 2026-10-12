"""A fake Nebius Token Factory for tests: scripted chat completions over a mock transport.

The real `openai` client is used, so request building and response parsing are the real code.
No network is used.
"""

from __future__ import annotations

import json

import httpx2 as httpx

from simulator.brain_nebius import make_client

MODEL = "nvidia/test-nemotron"


def tool_call(name: str, args: dict | None = None, call_id: str | None = None) -> dict:
    return {
        "id": call_id or f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    }


class FakeNebius:
    """Answers each chat completion request with the next scripted step.

    A step is a string (final text), a list of tool calls, an int (HTTP error status) or an
    exception to raise.
    """

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        step = self.steps.pop(0) if self.steps else "Okay."
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return httpx.Response(step, json={"error": {"message": "upstream problem"}})
        message = {"role": "assistant", "content": step if isinstance(step, str) else None}
        if isinstance(step, list):
            message["tool_calls"] = step
        return httpx.Response(
            200,
            json={
                "id": "cmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if isinstance(step, list) else "stop",
                        "message": message,
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    def client(self):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        return make_client("test-key", http_client=http)
