.PHONY: dev down logs test lint bench clean

dev:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f api worker

test:
	cd backend && pytest -v

lint:
	cd backend && ruff check app tests
	cd frontend && npx tsc --noEmit

bench:
	python3 benchmarks/load_test.py --url http://localhost:8000 --requests 100 --concurrency 10

clean:
	rm -rf backend/.pytest_cache backend/.ruff_cache frontend/dist
