.DEFAULT_GOAL := help
SHELL := /bin/bash
# Whichever uv is on PATH (Homebrew, pipx, the installer script), falling back
# to the installer's default location. A hard-coded path fails quietly on any
# machine that installed it differently: `make demo` starts the containers,
# skips the migrations, and the backend dies on its first connection.
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)

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

.PHONY: demo
demo: up reset ## Start everything and print where to look
	@echo
	@echo "  chat + trace + metrics   http://localhost:5173"
	@echo "  agent API                http://localhost:8000/docs"
	@echo "  billing system (legacy)  http://localhost:8081/docs"
	@echo "  recommendation system    http://localhost:8082/docs"
	@echo
	@echo "  provoke a failure mid-conversation:  make billing-mode M=timeout"
	@echo "  prove the perimeter:                 make perimeter-proof"
	@echo

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

# --- the billing contract (§7) -----------------------------------------------

.PHONY: openapi
openapi: ## Regenerate contracts/billing-openapi.v1.yaml from the stub
	$(UV) run python -m mock_billing.export_openapi

.PHONY: openapi-check
openapi-check: ## Fail if the committed contract is stale
	$(UV) run python -m mock_billing.export_openapi --check

.PHONY: billing-mode
billing-mode: ## Force a failure mode: make billing-mode M=timeout (§7)
	@curl -sS -X POST localhost:8081/_control/mode \
		-H 'content-type: application/json' \
		-d '{"mode":"$(M)","ttl_seconds":120}' | python3 -m json.tool

# --- data (§17.12) -----------------------------------------------------------

.PHONY: reset
reset: migrate ## Reset to the known data state — idempotent
	$(UV) run fibrapunto-reset
	@# The offers budget back to full, if the recommendation system is up.
	-@curl -sS -X POST localhost:8082/_control/reset >/dev/null 2>&1 || true

# --- credentials (§17.7) -----------------------------------------------------

.PHONY: seed-credential
seed-credential: ## Seed the credential store on a fresh clone
	$(UV) run python -m fibrapunto.scripts.rotate_credential --seed

.PHONY: rotate-credential
rotate-credential: ## Rotate the billing credential — no code, no prompt, no restart
	$(UV) run python -m fibrapunto.scripts.rotate_credential

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

# Both start from the known data state (§17.12). A run that inherits the
# previous run's records finds "already registered" cases on the contracts it
# shares with it, and the metrics measure the leak rather than the agent.
.PHONY: eval
eval: reset ## Reset, then run the test set and write a versioned run directory
	$(UV) run python -m evals.run

.PHONY: eval-quick
eval-quick: reset ## Reset, then deterministic assertions only — no judge
	$(UV) run python -m evals.run --no-judge

.PHONY: eval-diff
eval-diff: ## Per-case regressions; defaults to previous→latest, or A=<id> B=<id>
	$(UV) run python -m evals.run diff --a $(or $(A),previous) --b $(or $(B),latest)

.PHONY: eval-audit
eval-audit: ## Where the deterministic assertions and the judge disagreed (§14.2)
	$(UV) run python -m evals.run audit

.PHONY: eval-judge
eval-judge: ## Grade a run's saved transcripts again, no agent calls: RUN=<id|latest>
	$(UV) run python -m evals.run judge --run $(or $(RUN),latest)
