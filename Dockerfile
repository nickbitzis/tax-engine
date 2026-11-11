# One image. The command picks the job:
#   docker run tax-engine api | gate | nexus | returns | reconcile | export TX 2026-03
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
COPY --from=builder --chown=app /app/.venv /app/.venv
COPY --chown=app golden /app/golden
# The app writes the SQLite database (certificates, audit log) and the simulation cache under /app/data.
RUN mkdir -p /app/data/cache && chown -R app:app /app/data
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TAXENGINE_DB_PATH=/app/data/taxengine.db \
    TAXENGINE_CACHE_DIR=/app/data/cache
USER app
WORKDIR /app
# Run the year's simulation once at build time so the API starts in about a second.
RUN taxengine simulate
EXPOSE 8877
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD ["taxengine", "healthcheck", "--url", "http://127.0.0.1:8877/healthz"]
ENTRYPOINT ["taxengine"]
CMD ["api", "--host", "0.0.0.0", "--port", "8877"]
