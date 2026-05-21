# PlanetScale Discovery Tools - Development Makefile

.PHONY: help install install-dev test test-unit test-integration test-coverage clean lint format format-check \
        type-check quality docs build-test-image test-local test-unit-local test-coverage-local

# Auto-detect container runtime (podman or docker)
CONTAINER_RT := $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)
CONTAINER_IMAGE := ps-discovery-test

# Python version for the test container (override with: make test PYTHON_VERSION=3.9)
PYTHON_VERSION ?= 3.13

# Default target
help:
	@echo "PlanetScale Discovery Tools - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  install          Install package in development mode"
	@echo "  install-dev      Install package with development dependencies"
	@echo "  install-test     Install package with test dependencies only"
	@echo "  install-all      Install package with all provider dependencies"
	@echo ""
	@echo "Testing (runs in container — all deps included):"
	@echo "  test             Run all tests"
	@echo "  test-unit        Run unit tests only"
	@echo "  test-coverage    Run tests with coverage report"
	@echo "  test-fast        Run tests excluding slow tests"
	@echo "  build-test-image Build the test container image only"
	@echo ""
	@echo "  Override Python version: make test PYTHON_VERSION=3.9"
	@echo "  Default Python version:  $(PYTHON_VERSION)"
	@echo ""
	@echo "Testing (local — may miss optional deps):"
	@echo "  test-local          Run all tests locally"
	@echo "  test-unit-local     Run unit tests locally"
	@echo "  test-coverage-local Run tests with coverage locally"
	@echo ""
	@echo "Code Quality (runs in container):"
	@echo "  lint             Run code linting"
	@echo "  format-check     Check code formatting"
	@echo "  type-check       Run type checking with mypy"
	@echo "  quality          Run all quality checks (lint + format-check + type-check)"
	@echo ""
	@echo "Code Quality (local):"
	@echo "  format           Format code with black (modifies files — runs locally)"
	@echo ""
	@echo "Utilities:"
	@echo "  clean            Clean build artifacts and cache"
	@echo ""
	@echo "Container runtime: $(or $(CONTAINER_RT),not found)"

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

# ---------------------------------------------------------------------------
# Container test image
# ---------------------------------------------------------------------------
build-test-image:
ifndef CONTAINER_RT
	$(error No container runtime found. Install podman or docker.)
endif
	$(CONTAINER_RT) build -f Dockerfile.test --build-arg PYTHON_VERSION=$(PYTHON_VERSION) -t $(CONTAINER_IMAGE) .

# ---------------------------------------------------------------------------
# Testing — container (default)
# ---------------------------------------------------------------------------
test: build-test-image
	$(CONTAINER_RT) run --rm $(CONTAINER_IMAGE) python -m pytest tests/ -v --tb=short

test-unit: build-test-image
	$(CONTAINER_RT) run --rm $(CONTAINER_IMAGE) python -m pytest tests/unit/ -v --tb=short

test-coverage: build-test-image
	$(CONTAINER_RT) run --rm $(CONTAINER_IMAGE) python -m pytest tests/ -v --tb=short \
		--cov=planetscale_discovery --cov-report=term-missing

test-fast: build-test-image
	$(CONTAINER_RT) run --rm $(CONTAINER_IMAGE) python -m pytest tests/ -v --tb=short -m "not slow"

test-integration: build-test-image
	$(CONTAINER_RT) run --rm $(CONTAINER_IMAGE) python -m pytest tests/ -v --tb=short -m "integration"

# ---------------------------------------------------------------------------
# Testing — local (escape hatch when you don't need the container)
# ---------------------------------------------------------------------------
test-local:
	pytest

test-unit-local:
	pytest tests/unit/ --verbose

test-coverage-local:
	pytest --cov=planetscale_discovery --cov-report=html --cov-report=term-missing --cov-report=xml

# ---------------------------------------------------------------------------
# Code quality — container (default for checks)
# ---------------------------------------------------------------------------
lint: build-test-image
	$(CONTAINER_RT) run --rm $(CONTAINER_IMAGE) python -m flake8 planetscale_discovery tests

format-check: build-test-image
	$(CONTAINER_RT) run --rm $(CONTAINER_IMAGE) python -m black --check planetscale_discovery tests

type-check: build-test-image
	$(CONTAINER_RT) run --rm $(CONTAINER_IMAGE) python -m mypy planetscale_discovery

quality: build-test-image
	$(CONTAINER_RT) run --rm $(CONTAINER_IMAGE) sh -c \
		"python -m flake8 planetscale_discovery tests && \
		 python -m black --check planetscale_discovery tests && \
		 python -m mypy planetscale_discovery"

# ---------------------------------------------------------------------------
# Code quality — local (format modifies files, so it must run locally)
# ---------------------------------------------------------------------------
format:
	black planetscale_discovery tests

# ---------------------------------------------------------------------------
# Utility targets
# ---------------------------------------------------------------------------
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

# Release targets
build:
	python setup.py sdist bdist_wheel

upload-test:
	twine upload --repository testpypi dist/*

upload:
	twine upload dist/*
