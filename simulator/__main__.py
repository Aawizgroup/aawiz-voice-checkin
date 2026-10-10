"""Run the simulator: python -m simulator [--host 127.0.0.1] [--port 8001]."""

from __future__ import annotations

import argparse
import os

import uvicorn

from simulator.app import create_sim_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Aawiz Voice Check-in: Alexa+ simulator")
    parser.add_argument("--host", default=os.environ.get("SIM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SIM_PORT", "8001")))
    args = parser.parse_args()
    uvicorn.run(create_sim_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
