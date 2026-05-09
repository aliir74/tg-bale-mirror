.PHONY: install dev test lint format typecheck check run clean

install:
	uv sync

dev:
	uv sync --extra dev

test:
	uv run pytest -v

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests

typecheck:
	uv run pyright

check: lint typecheck test

run:
	uv run python -m src.main

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
