.PHONY: runners up down lint test test-sandbox

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
