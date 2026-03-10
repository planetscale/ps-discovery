"""
Unit tests for PostgreSQL Performance Analyzer
"""

import pytest
from unittest.mock import MagicMock, patch
from psycopg2 import OperationalError

from planetscale_discovery.database.analyzers.performance_analyzer import (
    PerformanceAnalyzer,
)


class TestPerformanceAnalyzer:
    """Test cases for PerformanceAnalyzer"""

    @pytest.fixture
    def mock_connection(self):
        """Create a mock database connection with context-manager cursor."""
        connection = MagicMock()
        cursor_mock = MagicMock()
        connection.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
        connection.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return connection, cursor_mock

    @pytest.fixture
    def analyzer(self, mock_connection):
        """Create PerformanceAnalyzer instance with mock connection."""
        connection, _ = mock_connection
        return PerformanceAnalyzer(connection)

    # -------------------------------------------------------------------------
    # analyze() top-level structure
    # -------------------------------------------------------------------------

    def test_analyze_returns_expected_keys(self, analyzer, mock_connection):
        """Test that analyze() returns all expected top-level keys."""
        with (
            patch.object(analyzer, "_get_connection_analysis", return_value={}),
            patch.object(analyzer, "_get_query_performance", return_value={}),
            patch.object(analyzer, "_get_table_statistics", return_value=[]),
            patch.object(analyzer, "_get_index_usage", return_value={}),
            patch.object(analyzer, "_get_database_activity", return_value=[]),
            patch.object(analyzer, "_get_resource_utilization", return_value={}),
            patch.object(analyzer, "_get_cache_performance", return_value={}),
            patch.object(analyzer, "_get_lock_analysis", return_value={}),
            patch.object(analyzer, "_get_wait_events", return_value=[]),
            patch.object(analyzer, "_get_replication_lag", return_value={}),
        ):
            result = analyzer.analyze()

        expected_keys = {
            "connection_analysis",
            "query_performance",
            "table_statistics",
            "index_usage",
            "database_activity",
            "resource_utilization",
            "cache_performance",
            "lock_analysis",
            "wait_events",
            "replication_lag",
        }
        assert set(result.keys()) == expected_keys

    # -------------------------------------------------------------------------
    # _get_connection_analysis
    # -------------------------------------------------------------------------

    def test_connection_analysis_success(self, analyzer, mock_connection):
        """Test connection analysis with realistic data."""
        _, cursor = mock_connection

        connection_summary = {
            "total_connections": 25,
            "active_connections": 3,
            "idle_connections": 18,
            "idle_in_transaction": 2,
            "idle_aborted": 0,
            "client_backends": 20,
            "system_backends": 5,
        }
        connections_by_db = [
            {"datname": "mydb", "connection_count": 15, "active_count": 2},
            {"datname": "postgres", "connection_count": 10, "active_count": 1},
        ]
        connections_by_user = [
            {"usename": "app_user", "connection_count": 20, "active_count": 3},
        ]
        long_transactions = []
        connection_limits_rows = [
            {"name": "max_connections", "setting": "100"},
            {"name": "superuser_reserved_connections", "setting": "3"},
        ]

        cursor.fetchone.return_value = connection_summary
        cursor.fetchall.side_effect = [
            connections_by_db,
            connections_by_user,
            long_transactions,
            connection_limits_rows,
        ]

        result = analyzer._get_connection_analysis()

        assert result["connection_summary"]["total_connections"] == 25
        assert result["connection_summary"]["active_connections"] == 3
        assert len(result["connections_by_database"]) == 2
        assert len(result["connections_by_user"]) == 1
        assert result["long_running_transactions"] == []
        assert result["connection_limits"]["max_connections"] == "100"

    def test_connection_analysis_error(self, analyzer, mock_connection):
        """Test connection analysis graceful degradation on error."""
        _, cursor = mock_connection
        cursor.execute.side_effect = OperationalError("permission denied")

        result = analyzer._get_connection_analysis()

        assert "error" in result

    def test_connection_analysis_with_long_transactions(
        self, analyzer, mock_connection
    ):
        """Test connection analysis with long-running transactions present."""
        _, cursor = mock_connection

        cursor.fetchone.return_value = {
            "total_connections": 5,
            "active_connections": 2,
            "idle_connections": 3,
            "idle_in_transaction": 0,
            "idle_aborted": 0,
            "client_backends": 5,
            "system_backends": 0,
        }
        long_tx = [
            {
                "pid": 1234,
                "usename": "admin",
                "datname": "mydb",
                "application_name": "",
                "client_addr": "10.0.0.1",
                "state": "active",
                "query_start": "2024-01-01T00:00:00",
                "xact_start": "2024-01-01T00:00:00",
                "transaction_duration_seconds": 600,
                "query": "SELECT 1",
            }
        ]
        cursor.fetchall.side_effect = [
            [{"datname": "mydb", "connection_count": 5, "active_count": 2}],
            [{"usename": "admin", "connection_count": 5, "active_count": 2}],
            long_tx,
            [{"name": "max_connections", "setting": "100"}],
        ]

        result = analyzer._get_connection_analysis()

        assert len(result["long_running_transactions"]) == 1
        assert result["long_running_transactions"][0]["pid"] == 1234
        assert (
            result["long_running_transactions"][0]["transaction_duration_seconds"]
            == 600
        )

    # -------------------------------------------------------------------------
    # _get_query_performance
    # -------------------------------------------------------------------------

    def test_query_performance_with_pg_stat_statements(self, analyzer, mock_connection):
        """Test query performance when pg_stat_statements is available."""
        _, cursor = mock_connection

        # First call: check extension exists
        cursor.fetchone.return_value = {"extname": "pg_stat_statements"}

        top_by_time = [
            {
                "queryid": 123,
                "query": "SELECT * FROM users",
                "calls": 1000,
                "total_exec_time": 5000.0,
                "mean_exec_time": 5.0,
                "min_exec_time": 0.1,
                "max_exec_time": 50.0,
                "stddev_exec_time": 3.2,
                "rows": 50000,
                "hit_percent": 99.5,
            }
        ]
        top_by_calls = [
            {
                "queryid": 456,
                "query": "SELECT 1",
                "calls": 100000,
                "total_exec_time": 100.0,
                "mean_exec_time": 0.001,
                "rows": 100000,
                "hit_percent": 100.0,
            }
        ]
        slowest = []
        high_io = []

        cursor.fetchall.side_effect = [top_by_time, top_by_calls, slowest, high_io]

        result = analyzer._get_query_performance()

        assert "top_queries_by_total_time" in result
        assert len(result["top_queries_by_total_time"]) == 1
        assert result["top_queries_by_total_time"][0]["calls"] == 1000
        assert len(result["top_queries_by_calls"]) == 1
        assert result["slowest_queries_by_mean_time"] == []
        assert result["highest_io_queries"] == []

    def test_query_performance_no_extension(self, analyzer, mock_connection):
        """Test query performance when pg_stat_statements is not installed."""
        _, cursor = mock_connection

        # Extension check returns None (not found)
        cursor.fetchone.return_value = None

        result = analyzer._get_query_performance()

        assert "error" in result
        assert "pg_stat_statements" in result["error"]

    def test_query_performance_error(self, analyzer, mock_connection):
        """Test query performance graceful degradation on error."""
        _, cursor = mock_connection
        cursor.execute.side_effect = Exception(
            "permission denied for relation pg_stat_statements"
        )

        result = analyzer._get_query_performance()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_table_statistics
    # -------------------------------------------------------------------------

    def test_table_statistics_success(self, analyzer, mock_connection):
        """Test table statistics returns expected data."""
        _, cursor = mock_connection

        cursor.fetchall.return_value = [
            {
                "schemaname": "public",
                "tablename": "users",
                "seq_scan": 100,
                "seq_tup_read": 50000,
                "idx_scan": 5000,
                "idx_tup_fetch": 5000,
                "n_tup_ins": 1000,
                "n_tup_upd": 500,
                "n_tup_del": 50,
                "n_tup_hot_upd": 300,
                "n_live_tup": 10000,
                "n_dead_tup": 200,
                "n_mod_since_analyze": 100,
                "last_vacuum": None,
                "last_autovacuum": "2024-01-01T00:00:00",
                "last_analyze": None,
                "last_autoanalyze": "2024-01-01T00:00:00",
                "vacuum_count": 0,
                "autovacuum_count": 5,
                "analyze_count": 0,
                "autoanalyze_count": 5,
            }
        ]

        result = analyzer._get_table_statistics()

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["tablename"] == "users"
        assert result[0]["n_live_tup"] == 10000

    def test_table_statistics_empty(self, analyzer, mock_connection):
        """Test table statistics with no tables."""
        _, cursor = mock_connection
        cursor.fetchall.return_value = []

        result = analyzer._get_table_statistics()

        assert result == []

    def test_table_statistics_error(self, analyzer, mock_connection):
        """Test table statistics graceful degradation on error."""
        _, cursor = mock_connection
        cursor.execute.side_effect = OperationalError("permission denied")

        result = analyzer._get_table_statistics()

        assert result == []

    # -------------------------------------------------------------------------
    # _get_index_usage
    # -------------------------------------------------------------------------

    def test_index_usage_success(self, analyzer, mock_connection):
        """Test index usage with used and unused indexes."""
        _, cursor = mock_connection

        cursor.fetchall.return_value = [
            {
                "schemaname": "public",
                "tablename": "users",
                "indexrelname": "users_pkey",
                "idx_scan": 5000,
                "idx_tup_read": 5000,
                "idx_tup_fetch": 5000,
                "index_size_bytes": 8192,
                "index_size_human": "8192 bytes",
            },
            {
                "schemaname": "public",
                "tablename": "users",
                "indexrelname": "users_unused_idx",
                "idx_scan": 0,
                "idx_tup_read": 0,
                "idx_tup_fetch": 0,
                "index_size_bytes": 16384,
                "index_size_human": "16 kB",
            },
        ]

        result = analyzer._get_index_usage()

        assert len(result["all_indexes"]) == 2
        assert result["unused_count"] == 1
        assert result["unused_indexes"][0]["indexrelname"] == "users_unused_idx"
        assert result["total_unused_size_bytes"] == 16384

    def test_index_usage_no_unused(self, analyzer, mock_connection):
        """Test index usage when all indexes are used."""
        _, cursor = mock_connection

        cursor.fetchall.return_value = [
            {
                "schemaname": "public",
                "tablename": "users",
                "indexrelname": "users_pkey",
                "idx_scan": 1000,
                "idx_tup_read": 1000,
                "idx_tup_fetch": 1000,
                "index_size_bytes": 8192,
                "index_size_human": "8192 bytes",
            },
        ]

        result = analyzer._get_index_usage()

        assert result["unused_count"] == 0
        assert result["total_unused_size_bytes"] == 0

    def test_index_usage_empty(self, analyzer, mock_connection):
        """Test index usage with no indexes."""
        _, cursor = mock_connection
        cursor.fetchall.return_value = []

        result = analyzer._get_index_usage()

        assert result["all_indexes"] == []
        assert result["unused_count"] == 0

    def test_index_usage_error(self, analyzer, mock_connection):
        """Test index usage graceful degradation on error."""
        _, cursor = mock_connection
        cursor.execute.side_effect = Exception("relation does not exist")

        result = analyzer._get_index_usage()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_database_activity
    # -------------------------------------------------------------------------

    def test_database_activity_success(self, analyzer, mock_connection):
        """Test database activity returns expected data."""
        _, cursor = mock_connection

        cursor.fetchall.return_value = [
            {
                "datname": "mydb",
                "numbackends": 10,
                "xact_commit": 100000,
                "xact_rollback": 50,
                "blks_read": 1000,
                "blks_hit": 99000,
                "tup_returned": 500000,
                "tup_fetched": 200000,
                "tup_inserted": 10000,
                "tup_updated": 5000,
                "tup_deleted": 500,
                "conflicts": 0,
                "temp_files": 10,
                "temp_bytes": 1048576,
                "deadlocks": 2,
                "checksum_failures": 0,
                "checksum_last_failure": None,
                "blk_read_time": 100.5,
                "blk_write_time": 50.2,
                "stats_reset": "2024-01-01T00:00:00",
                "cache_hit_ratio": 99.0,
            }
        ]

        result = analyzer._get_database_activity()

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["datname"] == "mydb"
        assert result[0]["deadlocks"] == 2

    def test_database_activity_empty(self, analyzer, mock_connection):
        """Test database activity with empty results."""
        _, cursor = mock_connection
        cursor.fetchall.return_value = []

        result = analyzer._get_database_activity()

        assert result == []

    def test_database_activity_error(self, analyzer, mock_connection):
        """Test database activity graceful degradation on error."""
        _, cursor = mock_connection
        cursor.execute.side_effect = OperationalError("connection lost")

        result = analyzer._get_database_activity()

        assert result == []

    # -------------------------------------------------------------------------
    # _get_resource_utilization
    # -------------------------------------------------------------------------

    def test_resource_utilization_success(self, analyzer, mock_connection):
        """Test resource utilization returns database sizes and largest tables."""
        _, cursor = mock_connection

        db_sizes = [
            {"datname": "mydb", "size_bytes": 1073741824, "size_human": "1 GB"},
        ]
        largest_tables = [
            {
                "schemaname": "public",
                "tablename": "events",
                "total_size_bytes": 536870912,
                "total_size_human": "512 MB",
                "table_size_bytes": 402653184,
                "table_size_human": "384 MB",
            }
        ]
        temp_usage = []

        cursor.fetchall.side_effect = [db_sizes, largest_tables, temp_usage]

        result = analyzer._get_resource_utilization()

        assert len(result["database_sizes"]) == 1
        assert result["database_sizes"][0]["size_bytes"] == 1073741824
        assert len(result["largest_tables"]) == 1
        assert result["temp_file_usage"] == []

    def test_resource_utilization_error(self, analyzer, mock_connection):
        """Test resource utilization graceful degradation on error."""
        _, cursor = mock_connection
        cursor.execute.side_effect = OperationalError("permission denied")

        result = analyzer._get_resource_utilization()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_cache_performance
    # -------------------------------------------------------------------------

    def test_cache_performance_success(self, analyzer, mock_connection):
        """Test cache performance with buffer cache data."""
        _, cursor = mock_connection

        overall_cache = {
            "total_blks_hit": 990000,
            "total_blks_read": 10000,
            "overall_hit_ratio": 99.0,
        }
        cache_by_db = [
            {
                "datname": "mydb",
                "blks_hit": 990000,
                "blks_read": 10000,
                "hit_ratio": 99.0,
            }
        ]

        cursor.fetchone.side_effect = [
            overall_cache,  # overall cache query
            {"count": 1},  # pg_buffercache extension check
            {  # buffer usage query
                "total_buffers": 16384,
                "dirty_buffers": 100,
                "tablespaces_in_cache": 1,
                "databases_in_cache": 2,
            },
        ]
        cursor.fetchall.return_value = cache_by_db

        result = analyzer._get_cache_performance()

        assert result["overall_cache_performance"]["overall_hit_ratio"] == 99.0
        assert len(result["cache_by_database"]) == 1
        assert result["buffer_usage"]["total_buffers"] == 16384

    def test_cache_performance_no_buffercache(self, analyzer, mock_connection):
        """Test cache performance when pg_buffercache is not available."""
        _, cursor = mock_connection

        overall_cache = {
            "total_blks_hit": 990000,
            "total_blks_read": 10000,
            "overall_hit_ratio": 99.0,
        }
        cache_by_db = [
            {
                "datname": "mydb",
                "blks_hit": 990000,
                "blks_read": 10000,
                "hit_ratio": 99.0,
            }
        ]

        cursor.fetchone.side_effect = [
            overall_cache,
            {"count": 0},  # pg_buffercache NOT installed
        ]
        cursor.fetchall.return_value = cache_by_db

        result = analyzer._get_cache_performance()

        assert result["overall_cache_performance"]["overall_hit_ratio"] == 99.0
        assert result["buffer_usage"] is None

    def test_cache_performance_buffercache_error(self, analyzer, mock_connection):
        """Test cache performance when pg_buffercache query fails (graceful)."""
        _, cursor = mock_connection

        overall_cache = {
            "total_blks_hit": 500,
            "total_blks_read": 500,
            "overall_hit_ratio": 50.0,
        }
        cache_by_db = []

        # fetchone calls: overall_cache, then buffercache check raises
        call_count = [0]

        def fetchone_side_effect():
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                return overall_cache
            # The second fetchone is inside the inner try/except for buffercache
            raise OperationalError("permission denied for pg_buffercache")

        cursor.fetchone.side_effect = fetchone_side_effect
        cursor.fetchall.return_value = cache_by_db

        result = analyzer._get_cache_performance()

        assert result["overall_cache_performance"]["overall_hit_ratio"] == 50.0
        assert result["buffer_usage"] is None

    def test_cache_performance_total_error(self, analyzer, mock_connection):
        """Test cache performance when the main query fails."""
        _, cursor = mock_connection
        cursor.execute.side_effect = OperationalError("connection reset")

        result = analyzer._get_cache_performance()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_lock_analysis
    # -------------------------------------------------------------------------

    def test_lock_analysis_success(self, analyzer, mock_connection):
        """Test lock analysis with locks present."""
        _, cursor = mock_connection

        lock_summary = [
            {
                "mode": "AccessShareLock",
                "locktype": "relation",
                "granted": True,
                "fastpath": True,
                "lock_count": 10,
            },
            {
                "mode": "RowExclusiveLock",
                "locktype": "relation",
                "granted": True,
                "fastpath": False,
                "lock_count": 3,
            },
        ]
        blocking_locks = []

        cursor.fetchall.side_effect = [lock_summary, blocking_locks]

        result = analyzer._get_lock_analysis()

        assert len(result["lock_summary"]) == 2
        assert result["blocking_count"] == 0
        assert result["blocking_locks"] == []

    def test_lock_analysis_with_blocking(self, analyzer, mock_connection):
        """Test lock analysis with blocking locks present."""
        _, cursor = mock_connection

        lock_summary = [
            {
                "mode": "ExclusiveLock",
                "locktype": "relation",
                "granted": True,
                "fastpath": False,
                "lock_count": 1,
            },
        ]
        blocking_locks = [
            {
                "blocked_pid": 100,
                "blocked_user": "app_user",
                "blocked_statement": "UPDATE users SET name='x'",
                "blocking_pid": 200,
                "blocking_user": "admin",
                "blocking_statement": "ALTER TABLE users ADD COLUMN foo int",
            }
        ]

        cursor.fetchall.side_effect = [lock_summary, blocking_locks]

        result = analyzer._get_lock_analysis()

        assert result["blocking_count"] == 1
        assert result["blocking_locks"][0]["blocked_pid"] == 100
        assert result["blocking_locks"][0]["blocking_pid"] == 200

    def test_lock_analysis_error(self, analyzer, mock_connection):
        """Test lock analysis graceful degradation on error."""
        _, cursor = mock_connection
        cursor.execute.side_effect = Exception("permission denied")

        result = analyzer._get_lock_analysis()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_wait_events
    # -------------------------------------------------------------------------

    def test_wait_events_success(self, analyzer, mock_connection):
        """Test wait events returns expected data."""
        _, cursor = mock_connection

        cursor.fetchall.return_value = [
            {
                "wait_event_type": "Client",
                "wait_event": "ClientRead",
                "session_count": 5,
                "states": ["idle"],
            },
            {
                "wait_event_type": "IO",
                "wait_event": "DataFileRead",
                "session_count": 2,
                "states": ["active"],
            },
        ]

        result = analyzer._get_wait_events()

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["wait_event_type"] == "Client"
        assert result[1]["session_count"] == 2

    def test_wait_events_empty(self, analyzer, mock_connection):
        """Test wait events with no waits."""
        _, cursor = mock_connection
        cursor.fetchall.return_value = []

        result = analyzer._get_wait_events()

        assert result == []

    def test_wait_events_error(self, analyzer, mock_connection):
        """Test wait events graceful degradation on error."""
        _, cursor = mock_connection
        cursor.execute.side_effect = OperationalError("permission denied")

        result = analyzer._get_wait_events()

        assert result == []

    # -------------------------------------------------------------------------
    # _get_replication_lag - primary server
    # -------------------------------------------------------------------------

    def test_replication_lag_primary_with_replicas(self, analyzer, mock_connection):
        """Test replication info on a primary server with active replicas."""
        _, cursor = mock_connection

        # First fetchone: is_in_recovery check
        replication_slots = [
            {
                "slot_name": "replica_1",
                "plugin": None,
                "slot_type": "physical",
                "datoid": None,
                "database": None,
                "temporary": False,
                "active": True,
                "active_pid": 1234,
                "xmin": None,
                "catalog_xmin": None,
                "restart_lsn": "0/5000000",
                "confirmed_flush_lsn": None,
                "wal_status": "reserved",
                "safe_wal_size": None,
            }
        ]
        wal_senders = [
            {
                "pid": 1234,
                "usename": "replication_user",
                "application_name": "replica_1",
                "client_addr": "10.0.0.2",
                "client_hostname": None,
                "client_port": 5432,
                "backend_start": "2024-01-01T00:00:00",
                "backend_xmin": None,
                "state": "streaming",
                "sent_lsn": "0/6000000",
                "write_lsn": "0/6000000",
                "flush_lsn": "0/6000000",
                "replay_lsn": "0/6000000",
                "write_lag_seconds": 0,
                "flush_lag_seconds": 0,
                "replay_lag_seconds": 1,
                "sync_priority": 0,
                "sync_state": "async",
                "reply_time": "2024-01-01T01:00:00",
            }
        ]

        cursor.fetchone.return_value = {"is_in_recovery": False}
        cursor.fetchall.side_effect = [replication_slots, wal_senders]

        result = analyzer._get_replication_lag()

        assert result["is_standby"] is False
        assert result["server_role"] == "primary"
        assert result["replication_slot_count"] == 1
        assert result["active_replica_count"] == 1
        assert result["replication_summary"]["streaming_replicas"] == 1
        assert result["replication_summary"]["asynchronous_replicas"] == 1
        assert result["replication_summary"]["synchronous_replicas"] == 0
        assert result["replication_summary"]["max_lag_seconds"] == 1

    def test_replication_lag_primary_no_replicas(self, analyzer, mock_connection):
        """Test replication info on a primary with no replicas."""
        _, cursor = mock_connection

        cursor.fetchone.return_value = {"is_in_recovery": False}
        cursor.fetchall.side_effect = [[], []]  # no slots, no wal_senders

        result = analyzer._get_replication_lag()

        assert result["is_standby"] is False
        assert result["server_role"] == "primary"
        assert result["replication_slot_count"] == 0
        assert result["active_replica_count"] == 0
        assert result["replication_summary"]["max_lag_seconds"] == 0

    # -------------------------------------------------------------------------
    # _get_replication_lag - standby server
    # -------------------------------------------------------------------------

    def test_replication_lag_standby(self, analyzer, mock_connection):
        """Test replication info on a standby server."""
        _, cursor = mock_connection

        cursor.fetchone.side_effect = [
            {"is_in_recovery": True},  # is_in_recovery check
            {  # recovery info
                "last_wal_receive_lsn": "0/6000000",
                "last_wal_replay_lsn": "0/5F00000",
                "last_xact_replay_timestamp": "2024-01-01T01:00:00",
            },
            {"lag_seconds": 5},  # lag calculation
        ]

        result = analyzer._get_replication_lag()

        assert result["is_standby"] is True
        assert result["server_role"] == "standby"
        assert result["recovery_info"]["last_wal_receive_lsn"] == "0/6000000"
        assert result["recovery_info"]["lag_seconds"] == 5

    def test_replication_lag_standby_no_replay_timestamp(
        self, analyzer, mock_connection
    ):
        """Test standby when last_xact_replay_timestamp is None."""
        _, cursor = mock_connection

        cursor.fetchone.side_effect = [
            {"is_in_recovery": True},
            {
                "last_wal_receive_lsn": "0/6000000",
                "last_wal_replay_lsn": "0/5F00000",
                "last_xact_replay_timestamp": None,
            },
        ]

        result = analyzer._get_replication_lag()

        assert result["is_standby"] is True
        assert result["recovery_info"]["last_xact_replay_timestamp"] is None
        # lag_seconds should not be present since timestamp was None
        assert "lag_seconds" not in result["recovery_info"]

    def test_replication_lag_error(self, analyzer, mock_connection):
        """Test replication info graceful degradation on error."""
        _, cursor = mock_connection
        cursor.execute.side_effect = OperationalError(
            "permission denied for function pg_is_in_recovery"
        )

        result = analyzer._get_replication_lag()

        assert "error" in result

    # -------------------------------------------------------------------------
    # Permission denied scenarios (managed databases)
    # -------------------------------------------------------------------------

    def test_permission_denied_all_modules(self, analyzer, mock_connection):
        """Test that analyze() completes even when all sub-queries fail with permission errors."""
        _, cursor = mock_connection
        cursor.execute.side_effect = OperationalError(
            "permission denied for relation pg_stat_activity"
        )

        result = analyzer.analyze()

        # All keys should be present
        assert "connection_analysis" in result
        assert "query_performance" in result
        assert "table_statistics" in result
        assert "index_usage" in result
        assert "database_activity" in result
        assert "resource_utilization" in result
        assert "cache_performance" in result
        assert "lock_analysis" in result
        assert "wait_events" in result
        assert "replication_lag" in result

        # Methods that return dicts should have error keys
        assert "error" in result["connection_analysis"]
        assert "error" in result["query_performance"]
        assert "error" in result["resource_utilization"]
        assert "error" in result["cache_performance"]
        assert "error" in result["lock_analysis"]
        assert "error" in result["replication_lag"]

        # Methods that return lists should return empty lists
        assert result["table_statistics"] == []
        assert result["database_activity"] == []
        assert result["wait_events"] == []

    def test_partial_permission_denied(self, analyzer, mock_connection):
        """Test that some modules succeed while others fail due to permissions."""
        # Use patch to simulate mixed results
        with (
            patch.object(
                analyzer,
                "_get_connection_analysis",
                return_value={"error": "permission denied"},
            ),
            patch.object(
                analyzer,
                "_get_query_performance",
                return_value={"error": "pg_stat_statements extension not available"},
            ),
            patch.object(
                analyzer,
                "_get_table_statistics",
                return_value=[
                    {"schemaname": "public", "tablename": "users", "seq_scan": 100}
                ],
            ),
            patch.object(
                analyzer,
                "_get_index_usage",
                return_value={
                    "all_indexes": [],
                    "unused_count": 0,
                    "unused_indexes": [],
                    "total_unused_size_bytes": 0,
                },
            ),
            patch.object(analyzer, "_get_database_activity", return_value=[]),
            patch.object(
                analyzer,
                "_get_resource_utilization",
                return_value={"error": "permission denied"},
            ),
            patch.object(
                analyzer,
                "_get_cache_performance",
                return_value={
                    "overall_cache_performance": {"overall_hit_ratio": 99.0},
                    "cache_by_database": [],
                    "buffer_usage": None,
                },
            ),
            patch.object(
                analyzer,
                "_get_lock_analysis",
                return_value={
                    "lock_summary": [],
                    "blocking_locks": [],
                    "blocking_count": 0,
                },
            ),
            patch.object(analyzer, "_get_wait_events", return_value=[]),
            patch.object(
                analyzer,
                "_get_replication_lag",
                return_value={"is_standby": False, "server_role": "primary"},
            ),
        ):
            result = analyzer.analyze()

        # Failed modules
        assert "error" in result["connection_analysis"]
        assert "error" in result["query_performance"]
        assert "error" in result["resource_utilization"]

        # Succeeded modules
        assert len(result["table_statistics"]) == 1
        assert (
            result["cache_performance"]["overall_cache_performance"][
                "overall_hit_ratio"
            ]
            == 99.0
        )
        assert result["lock_analysis"]["blocking_count"] == 0
        assert result["replication_lag"]["server_role"] == "primary"
