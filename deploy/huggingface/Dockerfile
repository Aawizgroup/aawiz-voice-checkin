# Dockerfile for a Hugging Face Docker Space. Note: on 13 October 2026 Hugging Face required a
# paid plan for Docker Spaces, so the public demo is hosted elsewhere with deploy/Dockerfile.demo.
# This file is kept for anyone who has such a plan.
# Copy this file and README.md from this folder into the Space. Nothing else is needed there:
# the app is installed from the public GitHub branch below.
#
# Space settings needed: secret NEBIUS_API_KEY, variable NEBIUS_MODEL.
FROM python:3.12-slim

# Hugging Face runs the container as user id 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

# Change REF (or use "Factory rebuild" in the Space settings) to pick up new commits.
ARG REF=feature/nebius-personal-ai
RUN pip install --no-cache-dir --user \
    "aawiz-voice-checkin[nebius] @ https://github.com/Aawizgroup/aawiz-voice-checkin/archive/refs/heads/${REF}.zip"

# The public demo: local backend only, per-visitor synthetic data, browser speech, Nebius brain.
ENV DEMO_PUBLIC=true \
    BRAIN_MODE=nebius \
    BACKEND_MODE=local \
    AAWIZ_HOST=127.0.0.1 \
    AAWIZ_PORT=8000 \
    MCP_URL=http://127.0.0.1:8000/mcp \
    AAWIZ_DATA_DIR=/tmp/aawiz-data \
    SIM_HOST=0.0.0.0 \
    SIM_PORT=7860

EXPOSE 7860

# A random internal token per start; the MCP server is reachable only inside the container.
CMD ["sh", "-c", "T=$(python -c 'import secrets; print(secrets.token_hex(24))'); export AAWIZ_TOKENS=\"$T:demo\" SIM_TOKEN=\"$T\"; python -m server & sleep 2; exec python -m simulator"]
