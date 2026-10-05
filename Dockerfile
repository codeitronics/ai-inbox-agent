FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY inbox_agent ./inbox_agent
RUN uv sync --frozen --no-dev

RUN useradd --create-home app && mkdir -p /app/data /app/logs && chown -R app /app/data /app/logs
USER app

ENV DEMO_MODE=true DATABASE_PATH=/app/data/inbox.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uv", "run", "--no-dev", "inbox-agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
