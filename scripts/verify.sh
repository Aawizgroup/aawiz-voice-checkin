#!/usr/bin/env bash
# One command that proves the project works: install, lint, all tests, then a live
# server with /health, a real MCP tools/call over Streamable HTTP, and a 401 check.
# Prints PASS or FAIL per check and exits non-zero on any failure.
set -u
cd "$(dirname "$0")/.."

if [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else PY=$(command -v python3 || command -v python); fi

FAILED=0
LOG=$(mktemp)

check() {
  local name=$1; shift
  if "$@" >"$LOG" 2>&1; then
    printf 'PASS  %s\n' "$name"
  else
    printf 'FAIL  %s\n' "$name"
    tail -n 25 "$LOG" | sed 's/^/      /'
    FAILED=1
  fi
}

check "install dependencies"            "$PY" -m pip install -q -e ".[dev]"
check "install Chromium for the e2e test" "$PY" -m playwright install chromium
check "lint (ruff check)"               "$PY" -m ruff check .
check "format (ruff format --check)"    "$PY" -m ruff format --check .
check "tests (pytest, incl. e2e)"       "$PY" -m pytest -q
grep -E "passed|failed" "$LOG" | tail -n 1 | sed 's/^/      /'

PORT=${VERIFY_PORT:-8765}
TOKEN="verify-$RANDOM$RANDOM"
DATA="data/verify-$$"
AAWIZ_TOKENS="$TOKEN:user_verify" AAWIZ_DATA_DIR="$DATA" "$PY" -m server --port "$PORT" \
  >"$LOG.server" 2>&1 &
SERVER=$!
for _ in $(seq 1 40); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done

check "server answers /health" curl -fsS "http://127.0.0.1:$PORT/health"

if "$PY" scripts/mcp_smoke.py "http://127.0.0.1:$PORT/mcp" "$TOKEN" >"$LOG" 2>&1; then
  printf 'PASS  real MCP tools/call over Streamable HTTP\n'
else
  printf 'FAIL  real MCP tools/call over Streamable HTTP\n'
  FAILED=1
fi
sed 's/^/      /' "$LOG"

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{}')
if [ "$code" = "401" ]; then printf 'PASS  no token is refused (401)\n'
else printf 'FAIL  no token is refused (got %s)\n' "$code"; FAILED=1; fi

kill "$SERVER" 2>/dev/null
wait "$SERVER" 2>/dev/null
rm -rf "$DATA" "$LOG" "$LOG.server"

"$PY" -c "import importlib.metadata as m; print('      mcp SDK', m.version('mcp'))"
if [ "$FAILED" = 0 ]; then echo "RESULT: PASS"; else echo "RESULT: FAIL"; fi
exit "$FAILED"
