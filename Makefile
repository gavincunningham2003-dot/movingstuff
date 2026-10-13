.PHONY: up down api test lint ingest assess ml

up:
	docker compose up --build

down:
	docker compose down

api:
	cd backend && catrix api --reload

test:
	cd backend && pytest tests/unit -q

test-integration:
	cd backend && pytest tests/integration -q -m integration

lint:
	cd backend && ruff check . && ruff format --check . && mypy catrix

ingest:
	cd backend && catrix ingest all

assess:
	cd backend && catrix assess && catrix score && catrix quality

ml:
	cd backend && catrix ml build-dataset && catrix ml train && catrix ml predict
