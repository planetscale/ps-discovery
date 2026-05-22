"""
Unit tests for MySQL Replication Analyzer - binlog_retention section
"""

import pytest
from unittest.mock import MagicMock, patch

from planetscale_discovery.database.mysql_analyzers.replication_analyzer import (
    MySQLReplicationAnalyzer,
)


class TestMySQLReplicationAnalyzer:
    """Test cases for MySQLReplicationAnalyzer binlog retention."""

    @pytest.fixture
    def mock_connection(self):
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value = cursor
        return connection, cursor

    @pytest.fixture
    def analyzer(self, mock_connection):
        connection, _ = mock_connection
        return MySQLReplicationAnalyzer(connection)

    # ---------------------------------------------------------------
    # _get_binlog_retention
    # ---------------------------------------------------------------

    def test_binlog_retention_mysql8_seconds(self, mock_connection):
        """MySQL 8.0+ returns binlog_expire_logs_seconds (default 2592000 = 30 days)."""
        connection, cursor = mock_connection
        analyzer = MySQLReplicationAnalyzer(connection)

        cursor.fetchone.side_effect = [
            (2592000,),  # @@binlog_expire_logs_seconds
            (0,),  # @@expire_logs_days (deprecated, zero)
        ]

        result = analyzer._get_binlog_retention()

        assert result["expire_logs_seconds"] == 2592000
        assert result["retention_hours"] == 720.0  # 30 days
        assert result["expire_logs_days"] == 0

    def test_binlog_retention_legacy_days(self, mock_connection):
        """Older MySQL uses expire_logs_days only."""
        connection, cursor = mock_connection
        analyzer = MySQLReplicationAnalyzer(connection)

        cursor.fetchone.side_effect = [
            (0,),  # @@binlog_expire_logs_seconds = 0 (not set)
            (7,),  # @@expire_logs_days = 7
        ]

        result = analyzer._get_binlog_retention()

        assert result["expire_logs_seconds"] == 0
        assert result["expire_logs_days"] == 7
        assert result["retention_hours"] == 168.0  # 7 * 24

    def test_binlog_retention_no_retention_configured(self, mock_connection):
        """Should warn when no retention is configured (both zero)."""
        connection, cursor = mock_connection
        analyzer = MySQLReplicationAnalyzer(connection)

        cursor.fetchone.side_effect = [
            (0,),  # @@binlog_expire_logs_seconds
            (0,),  # @@expire_logs_days
        ]

        result = analyzer._get_binlog_retention()

        assert result["retention_hours"] == 0
        assert "warning" in result

    def test_binlog_retention_error_returns_empty(self, mock_connection):
        """On error, should return empty dict and record the error."""
        connection, cursor = mock_connection
        analyzer = MySQLReplicationAnalyzer(connection)

        cursor.execute.side_effect = Exception("access denied")

        result = analyzer._get_binlog_retention()

        assert result == {}
        assert len(analyzer.errors) == 1

    def test_binlog_retention_8_4_falls_back_when_expire_logs_days_missing(
        self, mock_connection
    ):
        """MySQL 8.4 removed @@expire_logs_days. The seconds-based retention
        should still come through, and the missing-variable error should be
        logged at debug — not surfaced as a warning or swallowed silently."""
        connection, cursor = mock_connection
        analyzer = MySQLReplicationAnalyzer(connection)

        # First SELECT (binlog_expire_logs_seconds) succeeds; second raises
        # because the variable was removed. fetchone is only called for the
        # successful first SELECT.
        cursor.fetchone.side_effect = [(3600,)]
        cursor.execute.side_effect = [
            None,  # SELECT @@binlog_expire_logs_seconds
            Exception("Unknown system variable 'expire_logs_days'"),
        ]

        with patch.object(analyzer.logger, "debug") as debug_mock:
            result = analyzer._get_binlog_retention()

        assert result["expire_logs_seconds"] == 3600
        assert result["retention_hours"] == 1.0
        assert "expire_logs_days" not in result
        # No warning surfaced — the retention number from seconds is authoritative.
        assert len(analyzer.warnings) == 0
        # But the underlying error was logged, not silently swallowed.
        debug_mock.assert_called_once()
        assert "expire_logs_days" in debug_mock.call_args[0][0]

    def test_analyze_includes_binlog_retention(self, mock_connection):
        """Full analyze() should include binlog_retention key."""
        connection, cursor = mock_connection
        analyzer = MySQLReplicationAnalyzer(connection)

        # Set up returns for all the queries in analyze()
        # _get_replica_status tries SHOW REPLICA STATUS
        cursor.description = None
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []

        result = analyzer.analyze()

        assert "binlog_retention" in result
