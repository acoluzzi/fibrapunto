# The legacy billing system (§7).
# Built from the workspace root so uv can resolve the shared lockfile.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

WORKDIR /app

# Manifests first: the layer cache then survives source edits, which matters
# because this image is rebuilt on every `make up` during a demo.
COPY pyproject.toml uv.lock ./
COPY mock-billing/pyproject.toml mock-billing/
COPY recommendation-system/pyproject.toml recommendation-system/
COPY backend/pyproject.toml backend/
COPY evals/pyproject.toml evals/

RUN uv sync --frozen --package mock-billing --no-install-project

COPY mock-billing/ mock-billing/

RUN uv sync --frozen --package mock-billing

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8081

CMD ["uvicorn", "mock_billing.main:app", "--host", "0.0.0.0", "--port", "8081"]
