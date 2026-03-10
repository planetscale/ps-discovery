# AGENTS.md - PlanetScale Discovery Tools

## Project Overview

PlanetScale Discovery Tools analyze PostgreSQL databases and cloud infrastructure (AWS, GCP, Supabase, Heroku) to assess migration complexity to PlanetScale. The tool collects metadata only -- never actual customer data. It runs locally in customer environments with minimal permissions.

## Project Structure

```
planetscale_discovery/
  cli.py                          # Entry point (ps-discovery command)
  __main__.py                     # Python -m execution
  __init__.py                     # Package version (__version__)
  common/
    base_analyzer.py              # Abstract base classes (BaseAnalyzer, DatabaseAnalyzer, CloudAnalyzer)
    utils.py                      # Shared helpers
  config/
    config_manager.py             # YAML config loading and validation
  database/
    discovery.py                  # Orchestrates database analyzers
    report_generator.py           # JSON and Markdown report generation
    analyzers/
      config_analyzer.py          # PostgreSQL version and server settings
      schema_analyzer.py          # Tables, indexes, constraints, views, functions
      performance_analyzer.py     # pg_stat_* views, cache ratios, slow queries
      security_analyzer.py        # Users, roles, grants, RLS policies
      feature_analyzer.py         # Extensions, custom types, advanced features
      data_size_analyzer.py       # Large column and LOB analysis
  cloud/
    discovery.py                  # Orchestrates cloud analyzers
    report_generator.py           # Cloud infrastructure reports
    analyzers/
      aws_analyzer.py             # RDS, Aurora, VPC, security groups
      gcp_analyzer.py             # Cloud SQL, AlloyDB, VPC networks
      supabase_analyzer.py        # Supabase managed PostgreSQL
      heroku_analyzer.py          # Heroku Postgres add-ons
tests/
  conftest.py                     # Shared pytest fixtures
  fixtures/                       # Mock data (database_responses.py, aws_responses.py)
  unit/                           # Unit tests (no external deps required)
  integration/                    # Integration tests (require real credentials)
```

Key top-level files: `VERSION`, `setup.py`, `pyproject.toml`, `requirements.txt`, `setup.sh`, `build_release.sh`, `bump_version.sh`, `run_tests.py`, `Makefile`.

## Development Setup

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[all]"          # Editable install with all cloud providers
pip install pytest pytest-cov pytest-mock moto responses  # Test deps
pip install black flake8 mypy    # Dev tools
```

Or use `./setup.sh` which handles venv creation and dependency prompts interactively.

## Testing

CI runs tests across Python 3.9-3.13 via `.github/workflows/tests.yml`.

```bash
# Run all tests
python -m pytest tests/ -v --tb=short

# Unit tests only (fast, no external deps)
python -m pytest tests/unit/

# With coverage
python -m pytest tests/ --cov=planetscale_discovery --cov-report=term-missing

# Integration tests (require cloud credentials)
python -m pytest tests/integration/ -m integration

# Or use the helper script
python run_tests.py
```

Test fixtures live in `tests/fixtures/`. Unit tests mock all external connections using pytest-mock and moto (for AWS). Integration tests are marked with `@pytest.mark.integration` and skip gracefully when credentials are unavailable.

## Code Style

- **Formatter**: `black` -- run `python -m black planetscale_discovery tests` before committing
- **Linter**: `flake8` -- run `python -m flake8 planetscale_discovery tests`
- **Type checker**: `mypy` -- run `python -m mypy planetscale_discovery` (config in `mypy.ini`)
- **Security**: `bandit` -- run `python -m bandit -r planetscale_discovery/ -ll -c .bandit`

CI enforces all of the above on every push and PR to main.

## Architecture

### Analyzer Pattern

All analyzers inherit from `BaseAnalyzer` (in `common/base_analyzer.py`), which provides:
- `analyze()` -- abstract method each analyzer implements
- `execute_query()` -- runs SQL and returns list of dicts
- `add_error()` / `add_warning()` -- structured error collection
- `get_analysis_metadata()` -- timestamps, error counts

Database analyzers extend `DatabaseAnalyzer` (adds a psycopg2 connection). Cloud analyzers extend `CloudAnalyzer` (adds `authenticate()` and `discover_resources()` methods).

### Discovery Orchestration

`DatabaseDiscovery.run_discovery()` and `CloudDiscovery.run_discovery()` iterate over their respective analyzers. Each analyzer runs independently -- if one fails, the others continue. Errors are captured in metadata, not raised.

### CLI Layer

`cli.py` provides subcommands: `database`, `cloud`, `both`, `config-template`. The `ps-discovery` wrapper script (shell script at project root) activates the venv automatically.

### Graceful Degradation

This is a core design principle. Permission errors produce warnings, not failures. Missing extensions (e.g., pg_stat_statements) are noted and skipped. Managed database environments (RDS, Cloud SQL) have limited privileges by design, and the tool adapts. Every sub-analysis returns an empty result on error rather than raising an exception.

## Adding New Analyzers

### Database Analyzer

Create a file in `planetscale_discovery/database/analyzers/`, inherit from `DatabaseAnalyzer`:

```python
from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer

class NewAnalyzer(DatabaseAnalyzer):
    def analyze(self):
        results = {}
        results['feature'] = self._analyze_feature()
        results['metadata'] = self.get_analysis_metadata()
        return results

    def _analyze_feature(self):
        try:
            return self.execute_query("SELECT ... FROM ...")
        except Exception as e:
            self.add_error("Feature analysis failed", e)
            return []
```

Then register it in `database/discovery.py` and add tests in `tests/unit/`.

### Cloud Analyzer

Create a file in `planetscale_discovery/cloud/analyzers/`, inherit from `CloudAnalyzer`:

```python
from planetscale_discovery.common.base_analyzer import CloudAnalyzer

class NewProviderAnalyzer(CloudAnalyzer):
    def __init__(self, config, logger=None):
        super().__init__(config, "new_provider", logger)

    def authenticate(self):
        # Return True on success, False on failure
        ...

    def analyze(self):
        if not self.authenticate():
            return {"error": "Authentication failed"}
        return self._discover_resources()
```

Register in `cloud/discovery.py` and add tests.

## Releasing

See [RELEASING.md](RELEASING.md) for the full process. Summary:

1. Ensure tests, black, flake8, and mypy all pass
2. Update `CHANGELOG.md`
3. Bump version with `./bump_version.sh patch|minor|major` -- this updates `VERSION` and `setup.py`
4. Manually verify `pyproject.toml` and `planetscale_discovery/__init__.py` also match the new version
5. Commit, tag (`git tag -a vX.Y.Z`), and push with `--tags`
6. GitHub Actions builds the release tarball and creates the GitHub Release automatically

Version is tracked in four places that must stay in sync:
- `VERSION`
- `setup.py` (version field)
- `pyproject.toml` (project.version)
- `planetscale_discovery/__init__.py` (`__version__`)
