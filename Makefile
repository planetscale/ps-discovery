# PlanetScale Discovery Tools - Development Makefile

.PHONY: help install install-dev test test-unit test-integration test-coverage clean lint format type-check docs

# Default target
help:
	@echo "PlanetScale Discovery Tools - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  install          Install package in development mode"
	@echo "  install-dev      Install package with development dependencies"
	@echo "  install-test     Install package with test dependencies only"
	@echo ""
	@echo "Testing:"
	@echo "  test             Run all tests"
	@echo "  test-unit        Run unit tests only"
	@echo "  test-integration Run integration tests only"
	@echo "  test-coverage    Run tests with coverage report"
	@echo "  test-fast        Run tests excluding slow tests"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint             Run code linting"
	@echo "  format           Format code with black"
	@echo "  type-check       Run type checking with mypy"
	@echo "  quality          Run all quality checks (lint + format + type-check)"
	@echo ""
	@echo "Utilities:"
	@echo "  clean            Clean build artifacts and cache"
	@echo "  docs             Generate documentation"

# Installation targets
install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

install-test:
	pip install -e ".[test]"

install-aws:
	pip install -e ".[aws]"

install-all:
	pip install -e ".[all]"

# Testing targets
test:
	pytest

test-unit:
	pytest -m "unit" --verbose

test-integration:
	pytest -m "integration" --verbose

test-coverage:
	pytest --cov=planetscale_discovery --cov-report=html --cov-report=term-missing --cov-report=xml

test-fast:
	pytest -m "not slow"

test-aws:
	pytest -m "aws" --verbose

test-db:
	pytest -m "db" --verbose

# Code quality targets
lint:
	flake8 planetscale_discovery tests

format:
	black planetscale_discovery tests

format-check:
	black --check planetscale_discovery tests

type-check:
	mypy planetscale_discovery

quality: lint format-check type-check

# Utility targets
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

docs:
	@echo "Documentation generation not yet implemented"

# Development workflow
dev-setup: install-dev
	@echo "Development environment setup complete"

dev-test: test-fast lint
	@echo "Quick development test complete"

ci-test: test-coverage quality
	@echo "Full CI test suite complete"

# Docker targets (if needed in future)
docker-build:
	@echo "Docker build not yet implemented"

docker-test:
	@echo "Docker test not yet implemented"

# Release targets (if needed in future)
build:
	python setup.py sdist bdist_wheel

upload-test:
	twine upload --repository testpypi dist/*

upload:
	twine upload dist/*