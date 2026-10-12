#!/bin/sh
# Container entry point: the MCP server in the background, the simulator in the foreground.
#
# DEMO_PUBLIC=true (the hosted demo): a random internal token per start, the MCP server
# reachable only inside the container, no shared seed (each visitor gets their own history).
# Otherwise: the local demo, seeded with synthetic data for the one demo user.
set -e

# Hosting platforms (Render and others) say which port to listen on through PORT.
if [ -n "$PORT" ]; then
  export SIM_PORT="$PORT"
fi

if [ "$DEMO_PUBLIC" = "true" ]; then
  TOKEN=$(python -c "import secrets; print(secrets.token_hex(24))")
  export AAWIZ_TOKENS="$TOKEN:demo" SIM_TOKEN="$TOKEN"
  export AAWIZ_HOST=127.0.0.1 BACKEND_MODE=local
else
  python scripts/seed_demo.py
fi

python -m server &
sleep 2
exec python -m simulator
