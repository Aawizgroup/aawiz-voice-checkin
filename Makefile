# Uses .venv when it exists (after `make install`), otherwise the active Python.
VENV_BIN := $(if $(wildcard .venv/bin/python),.venv/bin/,)
PY := $(VENV_BIN)python

.PHONY: install run-server run-sim seed test lint verify demo

.env:
	cp .env.example .env

install: .env
	python3 -m venv .venv
	.venv/bin/python -m pip install -q --upgrade pip
	.venv/bin/python -m pip install -q -e ".[dev]"
	.venv/bin/python -m playwright install chromium

run-server: .env
	$(PY) -m server

run-sim: .env
	$(PY) -m simulator

seed: .env
	$(PY) scripts/seed_demo.py

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

verify:
	bash scripts/verify.sh

# Seeds synthetic data, then runs the MCP server (background) and the simulator.
demo: seed
	@echo "Open http://127.0.0.1:8001 in Chrome. Ctrl+C stops both."
	@trap 'kill 0' INT TERM EXIT; $(PY) -m server & sleep 2; $(PY) -m simulator
