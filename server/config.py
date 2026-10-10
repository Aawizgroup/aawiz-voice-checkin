"""Settings from the environment (and an optional .env file) plus the policy file."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = Path(__file__).resolve().parent / "policy.json"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines into os.environ without overriding real env vars."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@lru_cache(maxsize=1)
def policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def parse_tokens(raw: str) -> dict[str, str]:
    """'tokenA:user_a,tokenB:user_b' -> {'tokenA': 'user_a', ...}."""
    tokens: dict[str, str] = {}
    for pair in raw.split(","):
        token, sep, user = pair.strip().partition(":")
        if sep and token and user:
            tokens[token] = user
    return tokens


@dataclass(frozen=True)
class Settings:
    tokens: dict[str, str]
    data_dir: Path
    region: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "checkins.sqlite3"


def _resolve(path: str) -> Path:
    """Relative paths are taken from the project root, not from wherever the process started."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def get_settings() -> Settings:
    load_dotenv()
    region = os.environ.get("AAWIZ_REGION", policy()["default_region"]).upper()
    if region not in policy()["resources"]:
        region = policy()["default_region"]
    return Settings(
        tokens=parse_tokens(os.environ.get("AAWIZ_TOKENS", "")),
        data_dir=_resolve(os.environ.get("AAWIZ_DATA_DIR", "data")),
        region=region,
    )
