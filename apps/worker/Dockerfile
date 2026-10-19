# ref: https://docs.docker.com/get-started/docker-concepts/building-images/writing-a-dockerfile/
#       https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers-in-workspaces
#       https://docs.docker.com/get-started/docker-concepts/building-images/understanding-image-layers/
# ---Stage 1: collect venv---
FROM python:3.14-slim AS builder

# install uv
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

# change the working dir to /app
WORKDIR /app

# install dependencies (skip the workspace members, only the 3rd party pkg)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-editable --no-install-workspace --package worker

# copy the project into working dir
COPY . /app

# install dependencies (workspace members)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --package worker

# ---Stage 2: copy assembled venv into a new image---
FROM python:3.14-slim

# ensure the binary dir at the front of the PATH
ENV PATH="/app/.venv/bin:$PATH"
COPY --from=builder /app/.venv /app/.venv

# setup an app user so the container doesn't run as root
RUN useradd --uid=10001 app
USER app

# run the application
CMD ["celery", "-A", "worker.tasks:celery_app", "worker", "-l", "info", "-c", "2"]
