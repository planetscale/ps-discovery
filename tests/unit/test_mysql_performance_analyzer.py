"""
Unit tests for MySQL Performance Analyzer - lock_analysis section
"""

import pytest
from unittest.mock import MagicMock, patch

from planetscale_discovery.database.mysql_analyzers.performance_analyzer import (
    MySQLPerformanceAnalyzer,
)


class TestMySQLPerformanceAnalyzer:
    """Test cases for MySQLPerformanceAnalyzer lock analysis."""

    @pytest.fixture
    def mock_connection(self):
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value = cursor
        return connection, cursor

    @pytest.fixture
    def analyzer(self, mock_connection):
        connection, _ = mock_connection
        return MySQLPerformanceAnalyzer(connection, config={"status_sleep_interval": 0})

    # ---------------------------------------------------------------
    # analyze() - structure includes lock_analysis
    # ---------------------------------------------------------------

    def test_analyze_includes_lock_analysis(self, analyzer):
        """analyze() should include lock_analysis key."""
        with (
            patch.object(analyzer, "_get_status_counters", return_value={}),
            patch.object(analyzer, "_get_processlist_summary", return_value={}),
            patch.object(analyzer, "_get_lock_analysis", return_value={}),
        ):
            result = analyzer.analyze()

        assert "lock_analysis" in result

    # ---------------------------------------------------------------
    # _get_lock_analysis
    # ---------------------------------------------------------------

    def test_lock_analysis_extracts_counters(self, mock_connection):
        """lock_analysis should extract InnoDB lock counters from SHOW GLOBAL STATUS."""
        connection, cursor = mock_connection
        analyzer = MySQLPerformanceAnalyzer(connection)

        # SHOW GLOBAL STATUS returns tuples
        cursor.fetchall.return_value = [
            ("Innodb_row_lock_waits", "42"),
            ("Innodb_row_lock_time", "5000"),
            ("Innodb_row_lock_current_waits", "0"),
            ("Innodb_deadlocks", "3"),
            ("Table_locks_immediate", "1000"),
            ("Table_locks_waited", "5"),
        ]
        # performance_schema query
        cursor.fetchone.side_effect = [
            (2,),  # active_lock_waits count
            ("", "", "...some innodb status without deadlock..."),  # INNODB STATUS
        ]

        result = analyzer._get_lock_analysis()

        assert result["counters"]["Innodb_row_lock_waits"] == 42
        assert result["counters"]["Innodb_deadlocks"] == 3
        assert result["counters"]["Table_locks_waited"] == 5
        assert result["active_lock_waits"] == 2
        assert result["has_recent_deadlock"] is False

    def test_lock_analysis_detects_recent_deadlock(self, mock_connection):
        """Should detect LATEST DETECTED DEADLOCK in INNODB STATUS output."""
        connection, cursor = mock_connection
        analyzer = MySQLPerformanceAnalyzer(connection)

        cursor.fetchall.return_value = []
        cursor.fetchone.side_effect = [
            (0,),  # active_lock_waits
            ("", "", "...LATEST DETECTED DEADLOCK\n2026-04-09 10:00:00..."),
        ]

        result = analyzer._get_lock_analysis()
        assert result["has_recent_deadlock"] is True

    def test_lock_analysis_handles_no_performance_schema(self, mock_connection):
        """Should gracefully handle missing performance_schema."""
        connection, cursor = mock_connection
        analyzer = MySQLPerformanceAnalyzer(connection)

        cursor.fetchall.return_value = []

        # First fetchone for performance_schema raises, second for INNODB STATUS raises
        cursor.fetchone.side_effect = Exception("table doesn't exist")

        result = analyzer._get_lock_analysis()

        assert result["active_lock_waits"] is None
        assert result["has_recent_deadlock"] is None

    def test_lock_analysis_status_error_returns_empty_counters(self, mock_connection):
        """If SHOW GLOBAL STATUS fails, counters should be empty but no crash."""
        connection, cursor = mock_connection
        analyzer = MySQLPerformanceAnalyzer(connection)

        cursor.execute.side_effect = Exception("access denied")

        result = analyzer._get_lock_analysis()

        assert result["counters"] == {}
