FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies before copying the source so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev

# A named volume mounted here inherits this ownership on first use, so the app user can write uploads.
RUN useradd --uid 10001 --no-create-home verdict \
    && mkdir -p /data/problems \
    && chown verdict:verdict /data/problems
USER verdict

EXPOSE 8000
CMD ["python", "-m", "verdict.web", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
