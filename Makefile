COMPOSE := $(shell if command -v docker >/dev/null 2>&1; then echo 'docker compose'; elif command -v podman >/dev/null 2>&1; then echo 'podman compose'; else echo 'docker compose'; fi)

.PHONY: dev down logs test lint verify bench bench-update clean

dev:
	$(COMPOSE) up --build

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f api worker

test:
	cd backend && pytest -v

lint:
	cd backend && ruff check app tests ../benchmarks/load_test.py
	cd frontend && npx tsc --noEmit

verify: lint test
	cd frontend && npm run build

bench:
	python3 benchmarks/load_test.py --url http://localhost:8000 --requests 100 --concurrency 10

bench-update:
	python3 benchmarks/load_test.py --url http://localhost:8000 --requests 100 --concurrency 10 --update-readme README.md

clean:
	rm -rf backend/.pytest_cache backend/.ruff_cache frontend/dist
