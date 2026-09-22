# The agent and the platform.
# Built from the workspace root so uv can resolve the shared lockfile.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/
COPY mock-billing/pyproject.toml mock-billing/
COPY evals/pyproject.toml evals/

RUN uv sync --frozen --package fibrapunto --no-install-project

COPY backend/ backend/
# Read at runtime, not vendored into the package: the perimeter policy, the
# billing contract and the fixtures are reviewable artefacts, and the container
# should be running the same ones that are committed.
COPY contracts/ contracts/
COPY db/ db/

RUN uv sync --frozen --package fibrapunto

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "fibrapunto.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
