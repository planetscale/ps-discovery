# Contributing to PlanetScale Discovery Tools

Thank you for your interest in contributing to the PlanetScale Discovery Tools. This guide covers the development setup, testing practices, and workflow for contributing changes.

## Development Setup

### Prerequisites

- Python 3.9 or higher
- Git
- pipx (recommended) or pip

### Getting Started

```bash
# Clone repository and install dependencies
git clone <repository-url>
cd ps-discovery-tools

# Install with development dependencies
pip install -e ".[dev]"

# Or use pipx for isolated environment
pipx install -e ".[dev]"

# Install pre-commit hook (recommended)
cp .githooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# Run tests to verify setup
make dev-test
```

### Pre-commit Hook

A pre-commit hook is available to run tests and linting before each commit, preventing CI/CD failures:

```bash
# Install the hook
cp .githooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

The hook runs:
- Black code formatting check
- Flake8 linting
- Mypy type checking
- Unit tests

To bypass the hook temporarily (not recommended):
```bash
git commit --no-verify -m "message"
```

## Testing

### Test Suite Overview

The test suite includes:

- **Unit Tests**: Test individual components and analyzers
- **Integration Tests**: Test complete workflows and CLI interface
- **Mock Testing**: AWS and database interaction testing using mocks
- **Coverage Reporting**: Code coverage analysis and reporting

### Running Tests

#### Using the Test Runner Script

```bash
# Run all tests
python run_tests.py --all

# Run only unit tests
python run_tests.py --unit

# Run only integration tests
python run_tests.py --integration

# Run tests with coverage
python run_tests.py --coverage

# Run fast tests (exclude slow tests)
python run_tests.py --fast

# Run code quality checks
python run_tests.py --lint --format --type-check
```

#### Using Makefile

```bash
# Quick development test
make dev-test

# Run all tests with coverage
make test-coverage

# Run specific test types
make test-unit
make test-integration

# Code quality checks
make quality
```

#### Using pytest Directly

```bash
# Run all tests
pytest

# Run specific test categories
pytest -m "unit"
pytest -m "integration"
pytest -m "not slow"

# Run with coverage
pytest --cov=planetscale_discovery --cov-report=html

# Run specific test files
pytest tests/unit/test_config_analyzer.py -v
```

### Test Categories

Tests are organized with pytest markers:

- `unit`: Unit tests for individual components
- `integration`: End-to-end workflow tests
- `aws`: Tests requiring AWS services (uses mocks)
- `db`: Tests requiring database connections (uses mocks)
- `slow`: Long-running tests (can be excluded)

### Test Structure

```text
tests/
├── conftest.py              # Shared test configuration
├── fixtures/
│   ├── aws_responses.py     # Mock AWS API responses
│   └── database_responses.py # Mock database query responses
├── unit/
│   ├── test_config_analyzer.py
│   ├── test_aws_analyzer.py
│   └── test_report_generators.py
└── integration/
    ├── test_discovery_workflow.py
    └── test_cli.py
```

### Writing New Tests

#### Unit Test Example

```python
import pytest
from planetscale_discovery.database.analyzers.config_analyzer import ConfigAnalyzer

class TestConfigAnalyzer:
    def test_version_parsing(self, mock_connection):
        analyzer = ConfigAnalyzer(mock_connection)
        result = analyzer._get_version_info()
        assert result['major_version'] == 17
```

#### Integration Test Example

```python
@pytest.mark.integration
def test_complete_discovery_workflow(self, mock_database_config):
    with patch('psycopg2.connect') as mock_connect:
        # Setup mocks
        discovery = DatabaseDiscovery(mock_database_config)
        result = discovery.run()
        assert 'analysis_results' in result
```

### Test Data and Mocks

The test suite uses comprehensive mock data:

- **AWS API Responses**: Realistic RDS, Aurora, VPC, and security group data
- **Database Responses**: PostgreSQL system catalog and statistics responses
- **Configuration Files**: Sample YAML configurations for testing

### Code Coverage

Coverage reports are generated in multiple formats:

- **Terminal**: Summary displayed after test runs
- **HTML**: Detailed coverage report in `htmlcov/index.html`
- **XML**: Machine-readable coverage data for CI systems

Target coverage: **80%** minimum across the codebase.

## Code Quality

### Formatting and Linting

This project uses:

- **black**: Code formatting (required before committing)
- **flake8**: Style guide enforcement
- **mypy**: Static type checking

```bash
# Run all quality checks
make quality

# Or run individually
black .
flake8 planetscale_discovery
mypy planetscale_discovery
```

## Making Changes

### Development Workflow

1. Create a feature branch from `main`
2. Make your changes
3. Write tests for your changes
4. Run the test suite
5. Run code quality checks (`black .`, `flake8`, `pytest`)
6. Commit your changes
7. Push and create a pull request

### Commit Messages

Follow conventional commit format:

```text
type(scope): brief description

Detailed description if needed

Fixes #123
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`

## Project Structure

```text
ps-discovery-tools/
├── planetscale_discovery/
│   ├── __init__.py
│   ├── cli.py                    # CLI interface
│   ├── database/                 # Database discovery
│   │   ├── discovery.py
│   │   ├── analyzers/
│   │   └── report_generator.py
│   ├── cloud/                    # Cloud discovery
│   │   ├── discovery.py
│   │   ├── analyzers/
│   │   └── report_generator.py
│   ├── config/                   # Configuration management
│   └── common/                   # Shared utilities
├── tests/                        # Test suite
├── docs/                         # Documentation
├── Makefile                      # Development tasks
├── setup.py                      # Package configuration
└── README.md                     # User documentation
```

## Getting Help

If you encounter issues during development:

1. Check the test output for detailed error messages
2. Review the testing documentation above
3. Ensure all dependencies are installed correctly
4. Verify Python version compatibility (3.9+)

## License

This project is licensed under the Apache 2.0 License. See [LICENSE](LICENSE) for details.
