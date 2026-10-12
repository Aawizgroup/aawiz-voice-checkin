FROM python:3.12-slim

# User id 1000 is what Hugging Face Docker Spaces run as; it is fine everywhere else too.
RUN useradd --create-home --uid 1000 app
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AAWIZ_HOST=0.0.0.0 \
    SIM_HOST=0.0.0.0 \
    AAWIZ_DATA_DIR=/app/data \
    MCP_URL=http://127.0.0.1:8000/mcp \
    BRAIN_MODE=scripted
# Demo placeholder token only. Pass your own with: docker run -e AAWIZ_TOKENS=... -e SIM_TOKEN=...
# With DEMO_PUBLIC=true a random token is generated at start instead.
ENV AAWIZ_TOKENS=demo-token-change-me:user_demo \
    SIM_TOKEN=demo-token-change-me

COPY pyproject.toml README.md LICENSE ./
COPY server ./server
COPY simulator ./simulator
COPY scripts ./scripts
RUN pip install --no-cache-dir -e ".[nebius]" \
 && mkdir -p /app/data && chown app /app/data
USER app

# 8000: MCP server, 8001: simulator. For a hosted demo on one port (Hugging Face Spaces expects
# 7860): docker run -e DEMO_PUBLIC=true -e BRAIN_MODE=nebius -e SIM_PORT=7860 -p 7860:7860 ...
EXPOSE 8000 8001 7860
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["sh", "scripts/start.sh"]
