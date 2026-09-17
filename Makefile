.PHONY: install test lint format typecheck up down update train backtest report dev-api dev-frontend

install:
	python3.10 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"
	cd frontend && npm install

test:
	. .venv/bin/activate && pytest backend/tests -v

lint:
	. .venv/bin/activate && ruff check backend scripts
	. .venv/bin/activate && black --check backend scripts

format:
	. .venv/bin/activate && ruff check --fix backend scripts
	. .venv/bin/activate && black backend scripts

typecheck:
	. .venv/bin/activate && mypy backend/app --ignore-missing-imports --explicit-package-bases

up:
	docker compose up --build

down:
	docker compose down

update:
	. .venv/bin/activate && python scripts/update_data.py

train:
	. .venv/bin/activate && python scripts/train_models.py

backtest:
	. .venv/bin/activate && python scripts/run_backtest.py laliga

report:
	. .venv/bin/activate && python scripts/generate_report.py

dev-api:
	. .venv/bin/activate && uvicorn backend.app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev
