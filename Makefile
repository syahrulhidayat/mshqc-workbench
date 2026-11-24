COMPOSE := $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v podman-compose >/dev/null 2>&1; then echo "podman-compose"; elif command -v podman >/dev/null 2>&1; then echo "podman compose"; else echo "docker compose"; fi)

.PHONY: dev dev-infra down test lint verify bench bench-update

dev:
	$(COMPOSE) up --build

dev-infra:
	$(COMPOSE) up -d postgres redis

down:
	$(COMPOSE) down -v

test:
	cd backend && pytest -v

lint:
	cd backend && ruff check app tests
	cd frontend && npx tsc --noEmit

verify: lint test
	cd frontend && npm run build

bench:
	python3 benchmarks/load_test.py --url http://localhost:8000 --requests 100 --concurrency 10

bench-update:
	python3 benchmarks/load_test.py --url http://localhost:8000 --requests 100 --concurrency 10 --update-readme README.md
