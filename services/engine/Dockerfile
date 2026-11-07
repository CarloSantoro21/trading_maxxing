# syntax=docker/dockerfile:1.7
# Imagen de engine. Contexto de build: raíz del repo.
#   docker build -f services/engine/Dockerfile -t trading-maxxing-engine .

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# --- builder: resuelve dependencias con uv a partir del lockfile ---
FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# 1) Solo manifiestos: esta capa se cachea mientras no cambien las dependencias
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY services/api/pyproject.toml services/api/
COPY services/agents/pyproject.toml services/agents/
COPY services/forecast/pyproject.toml services/forecast/
COPY services/engine/pyproject.toml services/engine/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package tm-engine

# 2) Código del paquete compartido y del servicio
COPY packages/shared/ packages/shared/
COPY services/engine/ services/engine/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package tm-engine

# --- runtime: solo el entorno virtual, usuario sin privilegios ---
FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
CMD ["python", "-m", "tm_engine"]
