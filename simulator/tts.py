"""Elin's voice: ElevenLabs text-to-speech for the assistant's replies, nothing else.

Only reply text is ever sent. If the key is missing or a call fails, the page falls back to the
browser's own speech, so the conversation never breaks on a voice problem.
"""

from __future__ import annotations

import os

import httpx2 as httpx

API_BASE = "https://api.elevenlabs.io"
# From the ElevenLabs share link of "Lauren B, Friendly & Comforting Voice for AI Companion &
# Assistants" (a public library voice, not a clone). `make aawiz-smoke` prints its resolved name.
DEFAULT_VOICE_ID = "DODLEQrClDo8wCz460ld"
# Flash v2.5: about 75 ms model latency and multilingual (ElevenLabs models page, October 2026).
DEFAULT_MODEL_ID = "eleven_flash_v2_5"
OUTPUT_FORMAT = "mp3_44100_128"
TIMEOUT = 10.0
MAX_CHARS = 1500


class ElevenLabsTTS:
    def __init__(
        self,
        api_key: str,
        voice_id: str = DEFAULT_VOICE_ID,
        model_id: str = DEFAULT_MODEL_ID,
        transport: httpx.BaseTransport | None = None,
    ):
        self.voice_id = voice_id
        self.model_id = model_id
        self._http = httpx.Client(
            base_url=API_BASE,
            timeout=TIMEOUT,
            transport=transport,
            headers={"xi-api-key": api_key},
        )

    def synthesize(self, text: str) -> bytes | None:
        """MP3 bytes for `text`, or None when anything goes wrong."""
        text = (text or "").strip()[:MAX_CHARS]
        if not text:
            return None
        try:
            resp = self._http.post(
                f"/v1/text-to-speech/{self.voice_id}",
                params={"output_format": OUTPUT_FORMAT},
                json={"text": text, "model_id": self.model_id},
            )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200 or not resp.content:
            return None
        return resp.content

    def voice_name(self) -> str | None:
        try:
            resp = self._http.get(f"/v1/voices/{self.voice_id}")
        except httpx.HTTPError:
            return None
        return resp.json().get("name") if resp.status_code == 200 else None


def from_env() -> ElevenLabsTTS | None:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        return None
    return ElevenLabsTTS(
        key,
        os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or DEFAULT_VOICE_ID,
        os.environ.get("ELEVENLABS_MODEL_ID", "").strip() or DEFAULT_MODEL_ID,
    )
