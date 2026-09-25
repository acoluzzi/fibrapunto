# The recommendation system: offers and their budget.
# Built from the workspace root so uv can resolve the shared lockfile.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY mock-billing/pyproject.toml mock-billing/
COPY recommendation-system/pyproject.toml recommendation-system/
COPY backend/pyproject.toml backend/
COPY evals/pyproject.toml evals/

RUN uv sync --frozen --package recommendation-system --no-install-project

COPY recommendation-system/ recommendation-system/

RUN uv sync --frozen --package recommendation-system

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8082

CMD ["uvicorn", "recommendation_system.main:app", "--host", "0.0.0.0", "--port", "8082"]
