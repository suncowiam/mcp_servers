.PHONY: install test run smoke lint fmt

install:
	uv sync --group dev

test:
	uv run pytest -v

run:
	uv run python -m pm_manage_csvs

smoke:
	uv run python scripts/smoke_test.py

lint:
	uv run ruff check .
	uv run mypy src/

fmt:
	uv run ruff format .
	uv run ruff check --fix .
