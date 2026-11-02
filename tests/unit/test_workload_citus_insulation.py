"""Workload capture runs no Citus code, even on a server with the extension.

Its schema read runs every query in one transaction, so a failed Citus query
would abort every schema query after it.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from planetscale_discovery.workload.cli_workload import _collect_schema
from tests.fixtures.database_responses import FakeConnection, citus_handlers

CITUS_MARKERS = ("citus", "pg_dist_", "run_command_on", "relation_is_a_known_shard")


def test_the_schema_read_issues_no_citus_query():
    """Catches Citus detection reaching workload capture's transaction."""
    connection = FakeConnection(citus_handlers())
    config = SimpleNamespace(
        database=SimpleNamespace(
            host="h",
            port=5432,
            database="postgres",
            username="u",
            password="p",
            ssl_mode="prefer",
            statement_timeout="300s",
        )
    )
    with patch("planetscale_discovery.database.discovery.psycopg2.connect") as connect:
        schema = _collect_schema(connection, config, logger=Mock())
    connect.assert_not_called()

    assert connection.statements(), "the schema read ran no query"
    for sql in connection.statements():
        assert not any(marker in sql for marker in CITUS_MARKERS), sql
    assert all("citus" not in table for table in schema.get("table_analysis", []))
