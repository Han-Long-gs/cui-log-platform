# ---Stage 1: collect venv---
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

WORKDIR /app

# 3rd party dependencies only (cached until uv.lock changes)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-editable --no-install-workspace --package cui-db --package cui-config

COPY . /app

# workspace members
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package cui-db --package cui-config

# ---Stage 2: venv + migration files---
FROM python:3.14-slim

ENV PATH="/app/.venv/bin:$PATH"
COPY --from=builder /app/.venv /app/.venv

# alembic looks for alembic.ini in the cwd; alembic.ini finds migrations/ via %(here)s
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --uid=10001 app
USER app

CMD ["alembic", "upgrade", "head"]
