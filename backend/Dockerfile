# The API, the jobs worker and the `zama` CLI: one image, the command picks the role.
# Built from the project root (see docker-compose.yml): next to backend/ the app needs
# scenarios/ (the ready scenarios of the admin) and docs/legacy-prompts/ (seed-prompts) —
# app/settings.py finds them from PROJECT_ROOT.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv

# the packages live outside the code folder: in dev the code is mounted over /app/backend
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
RUN uv sync --frozen --no-dev
COPY scenarios/ /app/scenarios/
COPY docs/legacy-prompts/ /app/docs/legacy-prompts/

RUN useradd --system --uid 10001 --no-create-home zama
USER zama

EXPOSE 8090
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
