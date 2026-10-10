"""Run the MCP server: python -m server [--host 127.0.0.1] [--port 8000]."""

from __future__ import annotations

import argparse
import os

import uvicorn

from server.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Aawiz Voice Check-in MCP server")
    parser.add_argument("--host", default=os.environ.get("AAWIZ_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AAWIZ_PORT", "8000")))
    args = parser.parse_args()
    app = create_app(host=args.host, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
