"""One-time login to the Aawiz demo account (email code). Run with `make aawiz-login`.

Stores only the refresh token and the session token, in a git-ignored file with 0600
permissions. Never prints a token.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.aawiz_client import AawizClient, AawizError  # noqa: E402
from server.config import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    session_file = settings.aawiz_session_file
    client = AawizClient(settings.aawiz_api_base, session_file)
    print(f"Aawiz API: {client.base_url}")
    print("Use the demo account only, never a real user's account.")
    email = input("Demo account email: ").strip()
    if "@" not in email:
        print("That does not look like an email address.")
        return 1
    try:
        flow = client.request_code(email)
    except AawizError as exc:
        print(f"Could not request a code ({exc}).")
        return 1
    print("Aawiz has emailed a login code. It is valid for 5 minutes.")
    code = input("Code from the email: ").strip()
    try:
        info = client.confirm_code(flow, code)
    except AawizError as exc:
        print(f"Login failed ({exc}). Run make aawiz-login again for a new code.")
        return 1
    print(f"Logged in. Session saved to {session_file} (owner-only permissions).")
    print(f"Access tokens last about {info['access_expires_in']} seconds and renew automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
