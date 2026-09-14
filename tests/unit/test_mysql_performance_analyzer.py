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


class TestProcesslistTruncationDetection:
    """Without PROCESS, the processlist silently covers one account only."""

    @pytest.fixture
    def mock_connection(self):
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value = cursor
        return connection, cursor

    @pytest.fixture
    def analyzer(self, mock_connection):
        connection, _ = mock_connection
        return MySQLPerformanceAnalyzer(connection)

    @staticmethod
    def _status(threads_connected):
        return {"counters": {"Threads_connected": {"current": threads_connected}}}

    def _processlist(self, cursor, rows):
        cursor.description = [
            ("Id",),
            ("User",),
            ("Host",),
            ("db",),
            ("Command",),
            ("State",),
        ]
        cursor.fetchall.return_value = rows

    def test_truncated_processlist_warns(self, analyzer, mock_connection):
        """The real prod case: 2 visible rows against 253 connected threads."""
        _, cursor = mock_connection
        self._processlist(
            cursor,
            [
                (1, "app", "10.0.0.1", "prod", "Query", "init"),
                (2, "app", "10.0.0.2", "prod", "Sleep", ""),
            ],
        )

        summary = analyzer._get_processlist_summary(self._status(253))

        assert summary["total_processes"] == 2
        assert summary["threads_connected"] == 253
        assert summary["processlist_truncated"] is True
        assert len(analyzer.warnings) == 1
        assert "PROCESS" in analyzer.warnings[0]["message"]

    def test_full_processlist_does_not_warn(self, analyzer, mock_connection):
        """With PROCESS the visible rows match (or exceed) Threads_connected."""
        _, cursor = mock_connection
        rows = [(i, "app", "10.0.0.1", "prod", "Sleep", "") for i in range(12)]
        self._processlist(cursor, rows)

        summary = analyzer._get_processlist_summary(self._status(10))

        assert summary["processlist_truncated"] is False
        assert len(analyzer.warnings) == 0

    def test_small_churn_does_not_warn(self, analyzer, mock_connection):
        """Connections opening between the two reads must not trip the check."""
        _, cursor = mock_connection
        rows = [(i, "app", "10.0.0.1", "prod", "Sleep", "") for i in range(20)]
        self._processlist(cursor, rows)

        summary = analyzer._get_processlist_summary(self._status(23))

        assert summary["processlist_truncated"] is False
        assert len(analyzer.warnings) == 0

    def test_missing_status_skips_check(self, analyzer, mock_connection):
        """No status snapshot means no claim either way."""
        _, cursor = mock_connection
        self._processlist(cursor, [(1, "app", "10.0.0.1", "prod", "Sleep", "")])

        summary = analyzer._get_processlist_summary({"error": "failed"})

        assert "processlist_truncated" not in summary
        assert "threads_connected" not in summary
        assert len(analyzer.warnings) == 0

    def test_called_without_status_still_works(self, analyzer, mock_connection):
        """The parameter is optional; existing callers must keep working."""
        _, cursor = mock_connection
        self._processlist(cursor, [(1, "app", "10.0.0.1", "prod", "Sleep", "")])

        summary = analyzer._get_processlist_summary()

        assert summary["total_processes"] == 1
        assert "processlist_truncated" not in summary
