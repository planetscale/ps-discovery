"""The CLI on a MySQL-only install, where setup.sh installs no psycopg2.

setup.sh checks an install with ``./ps-discovery --help``. The parser registers
the PostgreSQL-only workload subcommand on every run, so a module-level psycopg2
import anywhere on that path fails a MySQL install before it can run.
"""

import importlib
import json
import logging
import sys

import pytest

PACKAGE = "planetscale_discovery"
PSYCOPG2_MODULES = (
    "psycopg2",
    "psycopg2.errorcodes",
    "psycopg2.errors",
    "psycopg2.extensions",
    "psycopg2.extras",
    "psycopg2.pool",
    "psycopg2.sql",
)


@pytest.fixture
def without_psycopg2(monkeypatch):
    """Make psycopg2 unimportable and the package import afresh.

    A None entry in sys.modules makes an import of that name fail. Cached
    submodules are blocked too, because an import of ``psycopg2.extras`` that
    finds it in sys.modules never looks at its parent. The package is imported
    afresh so a module-level import runs again; monkeypatch restores both.
    """
    for name in list(sys.modules):
        if name == PACKAGE or name.startswith(PACKAGE + "."):
            monkeypatch.delitem(sys.modules, name)
    cached = [n for n in sys.modules if n.startswith("psycopg2.")]
    for name in (*PSYCOPG2_MODULES, *cached):
        monkeypatch.setitem(sys.modules, name, None)


def _run_cli(monkeypatch, argv, prepare=None):
    """Run the CLI as the wrapper does. Returns its exit code, or the module it
    could not import, so a broken import fails the caller's assertion."""
    monkeypatch.setattr(sys, "argv", ["ps-discovery", *argv])
    try:
        cli = importlib.import_module(f"{PACKAGE}.cli")
        if prepare is not None:
            prepare(cli)
        cli.main()
    except SystemExit as e:
        return e.code
    except ModuleNotFoundError as e:
        return f"No module named {e.name!r}"
    return 0


def test_help_exits_zero_without_psycopg2(without_psycopg2, monkeypatch, capsys):
    """Catches a module-level psycopg2 import on the parser's import path, which
    fails setup.sh's ``./ps-discovery --help`` check on a MySQL-only install."""
    assert _run_cli(monkeypatch, ["--help"]) == 0
    # The workload subcommand is still offered, not dropped to make --help pass.
    assert "workload" in capsys.readouterr().out


def test_mysql_discovery_runs_without_psycopg2(without_psycopg2, monkeypatch, tmp_path):
    """Catches a psycopg2 import anywhere on a MySQL discovery run's path:
    the parser, the MySQL discovery tool, the summary and the report writer."""
    results = {
        "engine": "mysql",
        "connection_info": {"host": "db.internal", "version": "8.0.36"},
        "analysis_results": {"schema": {"table_analysis": [{"name": "orders"}]}},
        "analysis_gaps": [],
    }

    def prepare(cli):
        mysql_discovery = importlib.import_module(f"{PACKAGE}.database.mysql_discovery")
        monkeypatch.setattr(
            mysql_discovery.MySQLDiscoveryTool, "discover", lambda self: results
        )
        monkeypatch.setattr(
            cli, "setup_logging", lambda *a, **k: logging.getLogger(__name__)
        )

    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    argv = [
        "database",
        "--engine",
        "mysql",
        "--host",
        "db.internal",
        "--username",
        "discovery",
        "--output-dir",
        str(out),
    ]

    assert _run_cli(monkeypatch, argv, prepare) == 0
    reports = list(out.glob("planetscale_discovery_results_*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["database_results"] == results
