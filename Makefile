# All `catrix` commands run from the repository root: the registry (data_sources.yaml),
# the .env file and the data directory are resolved relative to it. Tests and linters run
# inside backend/, where their configuration lives.
.PHONY: up down api test test-integration test-e2e lint ingest assess ml demo

up:
	docker compose up --build

down:
	docker compose down

api:
	catrix api

test:
	cd backend && pytest tests/unit -q

test-integration:
	cd backend && pytest tests/integration -q -m integration

lint:
	cd backend && ruff check . && ruff format --check . && mypy catrix

ingest:
	catrix ingest all

assess:
	catrix assess && catrix score && catrix quality

ml:
	catrix ml build-dataset && catrix ml train && catrix ml predict

demo:
	catrix demo

test-e2e:
	cd frontend && npx playwright test
