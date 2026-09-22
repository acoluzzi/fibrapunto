.DEFAULT_GOAL := help
SHELL := /bin/bash
UV := $(HOME)/.local/bin/uv

# Everything goes through this file. Nobody should need to remember a compose
# invocation, least of all on stage.

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# --- environment -------------------------------------------------------------

.PHONY: install
install: ## Sync the uv workspace
	$(UV) sync --all-packages

.PHONY: db
db: ## Start Postgres alone (enough for the perimeter tests)
	docker compose up -d db
	@docker compose exec -T db sh -c 'until pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB} -q; do sleep 0.3; done'

.PHONY: up
up: ## Start every service
	docker compose up -d --build

.PHONY: down
down: ## Stop everything, keep the data
	docker compose down

.PHONY: clean
clean: ## Stop everything and destroy the data volume
	docker compose down -v

# --- the perimeter (§8) ------------------------------------------------------

.PHONY: grants
grants: ## Regenerate 004_grants.generated.sql from the perimeter policy
	$(UV) run fibrapunto-render-grants

.PHONY: grants-check
grants-check: ## Fail if the committed grants are stale
	$(UV) run fibrapunto-render-grants --check

.PHONY: migrate
migrate: db ## Apply every migration, including the generated grants
	$(UV) run python -m fibrapunto.scripts.apply_migrations

.PHONY: perimeter-proof
perimeter-proof: ## Run the same query as both identities, side by side (§8)
	$(UV) run python -m fibrapunto.scripts.perimeter_proof

# --- data (§17.12) -----------------------------------------------------------

.PHONY: reset
reset: migrate ## Reset to the known data state — idempotent
	$(UV) run fibrapunto-reset

# --- checks ------------------------------------------------------------------

.PHONY: test
test: ## Run the test suite
	$(UV) run pytest -q

.PHONY: lint
lint: ## Lint and format-check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

.PHONY: fmt
fmt: ## Format
	$(UV) run ruff format .

# --- models (§10) ------------------------------------------------------------

.PHONY: smoke
smoke: ## Verify OpenRouter: tool call, structured output, and the served model
	$(UV) run python -m fibrapunto.scripts.smoke_openrouter

# --- evaluation (§14) --------------------------------------------------------

.PHONY: eval
eval: ## Run the test set and write a versioned run directory
	$(UV) run python -m evals.run

.PHONY: eval-diff
eval-diff: ## Per-case regressions between two runs: make eval-diff A=... B=...
	$(UV) run python -m evals.run diff --a $(A) --b $(B)

.PHONY: eval-audit
eval-audit: ## Where the deterministic assertions and the judge disagreed (§14.2)
	$(UV) run python -m evals.run audit
