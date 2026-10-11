FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AAWIZ_HOST=0.0.0.0 \
    SIM_HOST=0.0.0.0 \
    AAWIZ_DATA_DIR=/app/data \
    MCP_URL=http://127.0.0.1:8000/mcp \
    BRAIN_MODE=scripted
# Demo placeholder token only. Pass your own with: docker run -e AAWIZ_TOKENS=... -e SIM_TOKEN=...
ENV AAWIZ_TOKENS=demo-token-change-me:user_demo \
    SIM_TOKEN=demo-token-change-me

COPY pyproject.toml README.md LICENSE ./
COPY server ./server
COPY simulator ./simulator
COPY scripts ./scripts
RUN pip install --no-cache-dir -e . \
 && useradd --create-home app \
 && mkdir -p /app/data && chown app /app/data
USER app

EXPOSE 8000 8001
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# Seed synthetic demo data, start the MCP server, then the simulator in the foreground.
CMD ["sh", "-c", "python scripts/seed_demo.py && (python -m server &) && sleep 2 && python -m simulator"]
