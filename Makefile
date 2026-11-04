.PHONY: runners up down lint test test-sandbox seed

runners:
	docker build -t verdict-runner-cpp:1 runners/cpp
	docker build -t verdict-runner-python:1 runners/python

up: runners
	docker compose up --build -d --wait

down:
	docker compose down

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest

test-sandbox:
	uv run pytest tests/sandbox

# The web container can write the problems volume; the worker only reads it.
seed:
	docker compose exec web python -m verdict.cli seed
