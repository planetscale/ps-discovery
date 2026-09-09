"""
Regression tests for raw query text leaking into analyzer output.

Before this was fixed, PerformanceAnalyzer emitted ``pg_stat_activity.query``
verbatim in three places: long-running transactions, and the blocked and
blocking statements in lock analysis. PostgreSQL never normalizes that column,
so the values were real customer data.

These tests fail if the raw field ever comes back.
"""

import re
from pathlib import Path

import pytest
from unittest.mock import MagicMock

from planetscale_discovery.database.analyzers.performance_analyzer import (
    PerformanceAnalyzer,
)

SECRET = "alice@example.com"


@pytest.fixture
def mock_connection():
    """Mock connection whose cursor works as a context manager."""
    connection = MagicMock()
    cursor = MagicMock()
    connection.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    connection.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return connection, cursor


@pytest.fixture
def analyzer(mock_connection):
    connection, _ = mock_connection
    return PerformanceAnalyzer(connection)


class TestLongRunningTransactionRedaction:
    def test_query_field_is_replaced(self, analyzer, mock_connection):
        _, cursor = mock_connection
        # The connection summary uses fetchone; the four list queries use
        # fetchall, in this order.
        cursor.fetchone.return_value = {"total_connections": 1}
        cursor.fetchall.side_effect = [
            [],  # connections by database
            [],  # connections by user
            [
                {
                    "pid": 42,
                    "usename": "app",
                    "query": f"SELECT * FROM users WHERE email = '{SECRET}'",
                }
            ],  # long-running transactions
            [],  # connection limits
        ]

        result = analyzer._get_connection_analysis()
        rows = result["long_running_transactions"]

        assert len(rows) == 1
        row = rows[0]
        assert "query" not in row, "raw query field must be dropped"
        assert SECRET not in row["query_redacted"]
        # The shape survives, which is what makes the row useful.
        assert "SELECT" in row["query_redacted"]
        # Non-sensitive context is still useful and must survive.
        assert row["pid"] == 42
        assert row["usename"] == "app"


class TestLockAnalysisRedaction:
    def test_blocked_and_blocking_statements_replaced(self, analyzer, mock_connection):
        _, cursor = mock_connection
        cursor.fetchall.side_effect = [
            [],  # lock summary
            [
                {
                    "blocked_pid": 1,
                    "blocked_user": "app",
                    "blocked_statement": f"UPDATE users SET x = 1 WHERE email = '{SECRET}'",
                    "blocking_pid": 2,
                    "blocking_user": "app",
                    "blocking_statement": f"DELETE FROM users WHERE email = '{SECRET}'",
                }
            ],
        ]

        result = analyzer._get_lock_analysis()
        rows = result["blocking_locks"]

        assert len(rows) == 1
        row = rows[0]
        assert "blocked_statement" not in row
        assert "blocking_statement" not in row
        assert SECRET not in row["blocked_statement_redacted"]
        assert SECRET not in row["blocking_statement_redacted"]
        assert "UPDATE" in row["blocked_statement_redacted"]
        assert "DELETE" in row["blocking_statement_redacted"]
        assert row["blocked_pid"] == 1
        assert row["blocking_pid"] == 2


class TestQueryPerformanceRedaction:
    """pg_stat_statements text is usually normalized, but not always."""

    def _run(self, analyzer, cursor, rows):
        cursor.fetchone.return_value = {"extversion": "1.11"}
        cursor.fetchall.side_effect = [rows, [], [], []]
        return analyzer._get_query_performance()

    def test_unnormalized_literal_is_redacted(self, analyzer, mock_connection):
        _, cursor = mock_connection
        result = self._run(
            analyzer,
            cursor,
            [
                {
                    "queryid": 1,
                    "query": f"SELECT 1 FROM t WHERE e = '{SECRET}'",
                    "calls": 5,
                }
            ],
        )

        row = result["top_queries_by_total_time"][0]
        assert "query" not in row
        assert SECRET not in row["query_redacted"]
        assert result["available"] is True
        assert result["extension_version"] == "1.11"

    def test_normalized_text_survives_intact(self, analyzer, mock_connection):
        """The common case must not be mangled."""
        _, cursor = mock_connection
        sql = "SELECT * FROM orders WHERE tenant_id = $1 AND status = $2"
        result = self._run(analyzer, cursor, [{"queryid": 1, "query": sql, "calls": 5}])

        row = result["top_queries_by_total_time"][0]
        assert row["query_redacted"] == sql

    def test_missing_extension_still_reports_an_error_key(
        self, analyzer, mock_connection
    ):
        """DatabaseDiscovery._categorize_error matches on this to file a gap.

        Changing the shape would silently drop the "missing_extension" analysis
        gap and its human explanation, so the key is load-bearing.
        """
        _, cursor = mock_connection
        cursor.fetchone.return_value = None

        result = analyzer._get_query_performance()

        assert result["available"] is False
        assert "pg_stat_statements" in result["error"]


class TestNoRawActivityQueryInSource:
    """Architectural guard: no SQL in the package may select a bare query column
    from pg_stat_activity without routing it through the sanitizer.

    Cheaper and more durable than catching this in review again.
    """

    def test_pg_stat_activity_query_is_always_redacted(self):
        package_root = Path(__file__).resolve().parents[2] / "planetscale_discovery"
        offenders = []

        for path in package_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "pg_stat_activity" not in source:
                continue
            # Any file selecting `query` from pg_stat_activity must also import
            # the sanitizer, so the value cannot reach output unredacted.
            selects_query = re.search(
                r"^\s*(?:\w+\.)?query\s*(?:AS\s+\w+)?\s*,?\s*$",
                source,
                re.IGNORECASE | re.MULTILINE,
            )
            if selects_query and "sanitize" not in source:
                offenders.append(str(path.relative_to(package_root)))

        assert not offenders, (
            "these files select query text from pg_stat_activity but do not "
            f"import the sanitizer: {offenders}"
        )
