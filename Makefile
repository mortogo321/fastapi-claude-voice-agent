.PHONY: env install fmt lint typecheck test test-cov run up down migrate clean

PY := .venv/bin/python
PIP := .venv/bin/pip
ENV ?= development

# Copy the per-env template to `.env` (what config.py actually reads).
# Usage: `make env` (dev) or `make env ENV=staging|production`.
env:
	@test -f .env.$(ENV) || { echo "missing .env.$(ENV)"; exit 1; }
	cp .env.$(ENV) .env
	@echo "copied .env.$(ENV) -> .env"

install:
	python3 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

fmt:
	$(PY) -m ruff format app tests
	$(PY) -m ruff check --fix app tests

lint:
	$(PY) -m ruff format --check app tests
	$(PY) -m ruff check app tests

typecheck:
	$(PY) -m mypy app

test:
	$(PY) -m pytest

test-cov:
	$(PY) -m pytest --cov=app --cov-report=term-missing --cov-report=xml

run:
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

up:
	docker compose up --build

down:
	docker compose down -v

migrate:
	$(PY) -m alembic upgrade head

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info coverage.xml .coverage
