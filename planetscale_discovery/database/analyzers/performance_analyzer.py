"""
PostgreSQL Performance Analysis Module
Analyzes performance metrics, query statistics, and resource utilization.
"""

from typing import Dict, Any, List

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class PerformanceAnalyzer(DatabaseAnalyzer):
    """Analyzes PostgreSQL performance metrics and usage patterns."""

    def __init__(self, connection, config=None, logger=None):
        super().__init__(connection, config, logger)

    def analyze(self) -> Dict[str, Any]:
        """Run complete performance analysis."""
        results = {
            "connection_analysis": self._get_connection_analysis(),
            "query_performance": self._get_query_performance(),
            "table_statistics": self._get_table_statistics(),
            "index_usage": self._get_index_usage(),
            "database_activity": self._get_database_activity(),
            "resource_utilization": self._get_resource_utilization(),
            "cache_performance": self._get_cache_performance(),
            "lock_analysis": self._get_lock_analysis(),
            "wait_events": self._get_wait_events(),
            "replication_lag": self._get_replication_lag(),
        }

        return results

    def _get_connection_analysis(self) -> Dict[str, Any]:
        """Analyze current connections and connection patterns."""
        try:
            with self.connection.cursor() as cursor:
                # Current connections summary
                cursor.execute("""
                    SELECT
                        COUNT(*) as total_connections,
                        COUNT(*) FILTER (WHERE state = 'active') as active_connections,
                        COUNT(*) FILTER (WHERE state = 'idle') as idle_connections,
                        COUNT(*) FILTER (WHERE state = 'idle in transaction') as idle_in_transaction,
                        COUNT(*) FILTER (WHERE state = 'idle in transaction (aborted)') as idle_aborted,
                        COUNT(*) FILTER (WHERE backend_type = 'client backend') as client_backends,
                        COUNT(*) FILTER (WHERE backend_type != 'client backend') as system_backends
                    FROM pg_stat_activity
                """)
                connection_summary = dict(cursor.fetchone())

                # Connections by database
                cursor.execute("""
                    SELECT
                        datname,
                        COUNT(*) as connection_count,
                        COUNT(*) FILTER (WHERE state = 'active') as active_count
                    FROM pg_stat_activity
                    WHERE datname IS NOT NULL
                    GROUP BY datname
                    ORDER BY connection_count DESC
                """)
                connections_by_db = [dict(row) for row in cursor.fetchall()]

                # Connections by user
                cursor.execute("""
                    SELECT
                        usename,
                        COUNT(*) as connection_count,
                        COUNT(*) FILTER (WHERE state = 'active') as active_count
                    FROM pg_stat_activity
                    WHERE usename IS NOT NULL
                    GROUP BY usename
                    ORDER BY connection_count DESC
                """)
                connections_by_user = [dict(row) for row in cursor.fetchall()]

                # Long-running transactions
                cursor.execute("""
                    SELECT
                        pid,
                        usename,
                        datname,
                        application_name,
                        client_addr,
                        state,
                        query_start,
                        xact_start,
                        EXTRACT(EPOCH FROM (now() - xact_start))::int as transaction_duration_seconds,
                        query
                    FROM pg_stat_activity
                    WHERE state != 'idle'
                    AND xact_start IS NOT NULL
                    AND EXTRACT(EPOCH FROM (now() - xact_start)) > 300  -- 5 minutes
                    ORDER BY xact_start
                """)
                long_transactions = [dict(row) for row in cursor.fetchall()]

                # Connection limits
                cursor.execute(
                    "SELECT name, setting FROM pg_settings WHERE name IN ('max_connections', 'superuser_reserved_connections')"
                )
                connection_limits = {
                    row["name"]: row["setting"] for row in cursor.fetchall()
                }

                return {
                    "connection_summary": connection_summary,
                    "connections_by_database": connections_by_db,
                    "connections_by_user": connections_by_user,
                    "long_running_transactions": long_transactions,
                    "connection_limits": connection_limits,
                }

        except Exception as e:
            self.logger.error(f"Failed to get connection analysis: {e}")
            return {"error": str(e)}

    def _get_query_performance(self) -> Dict[str, Any]:
        """Analyze query performance using pg_stat_statements."""
        try:
            with self.connection.cursor() as cursor:
                # Check if pg_stat_statements is available
                cursor.execute(
                    "SELECT * FROM pg_extension WHERE extname = 'pg_stat_statements'"
                )
                if not cursor.fetchone():
                    return {"error": "pg_stat_statements extension not available"}

                # Top queries by total time
                cursor.execute("""
                    SELECT
                        queryid,
                        query,
                        calls,
                        total_exec_time,
                        mean_exec_time,
                        min_exec_time,
                        max_exec_time,
                        stddev_exec_time,
                        rows,
                        100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) AS hit_percent
                    FROM pg_stat_statements
                    ORDER BY total_exec_time DESC
                    LIMIT 20
                """)
                top_queries_by_time = [dict(row) for row in cursor.fetchall()]

                # Top queries by calls
                cursor.execute("""
                    SELECT
                        queryid,
                        query,
                        calls,
                        total_exec_time,
                        mean_exec_time,
                        rows,
                        100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) AS hit_percent
                    FROM pg_stat_statements
                    ORDER BY calls DESC
                    LIMIT 20
                """)
                top_queries_by_calls = [dict(row) for row in cursor.fetchall()]

                # Slowest queries by mean time
                cursor.execute("""
                    SELECT
                        queryid,
                        query,
                        calls,
                        total_exec_time,
                        mean_exec_time,
                        min_exec_time,
                        max_exec_time,
                        stddev_exec_time
                    FROM pg_stat_statements
                    WHERE calls > 5  -- Only consider queries called more than 5 times
                    ORDER BY mean_exec_time DESC
                    LIMIT 20
                """)
                slowest_queries = [dict(row) for row in cursor.fetchall()]

                # Queries with highest I/O
                cursor.execute("""
                    SELECT
                        queryid,
                        query,
                        calls,
                        total_exec_time,
                        shared_blks_read,
                        shared_blks_written,
                        shared_blks_dirtied,
                        temp_blks_read,
                        temp_blks_written,
                        100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) AS hit_percent
                    FROM pg_stat_statements
                    WHERE shared_blks_read + shared_blks_written > 0
                    ORDER BY (shared_blks_read + shared_blks_written) DESC
                    LIMIT 20
                """)
                high_io_queries = [dict(row) for row in cursor.fetchall()]

                return {
                    "top_queries_by_total_time": top_queries_by_time,
                    "top_queries_by_calls": top_queries_by_calls,
                    "slowest_queries_by_mean_time": slowest_queries,
                    "highest_io_queries": high_io_queries,
                }

        except Exception as e:
            self.logger.error(f"Failed to get query performance: {e}")
            return {"error": str(e)}

    def _get_table_statistics(self) -> List[Dict[str, Any]]:
        """Get table access and modification statistics."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        schemaname,
                        relname as tablename,
                        seq_scan,
                        seq_tup_read,
                        idx_scan,
                        idx_tup_fetch,
                        n_tup_ins,
                        n_tup_upd,
                        n_tup_del,
                        n_tup_hot_upd,
                        n_live_tup,
                        n_dead_tup,
                        n_mod_since_analyze,
                        last_vacuum,
                        last_autovacuum,
                        last_analyze,
                        last_autoanalyze,
                        vacuum_count,
                        autovacuum_count,
                        analyze_count,
                        autoanalyze_count
                    FROM pg_stat_user_tables
                    ORDER BY schemaname, relname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get table statistics: {e}")
            return []

    def _get_index_usage(self) -> List[Dict[str, Any]]:
        """Get index usage statistics."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        schemaname,
                        relname as tablename,
                        indexrelname,
                        idx_scan,
                        idx_tup_read,
                        idx_tup_fetch,
                        pg_relation_size(indexrelid) as index_size_bytes,
                        pg_size_pretty(pg_relation_size(indexrelid)) as index_size_human
                    FROM pg_stat_user_indexes
                    ORDER BY schemaname, relname, indexrelname
                """)

                index_stats = [dict(row) for row in cursor.fetchall()]

                # Find unused indexes
                unused_indexes = [idx for idx in index_stats if idx["idx_scan"] == 0]

                return {
                    "all_indexes": index_stats,
                    "unused_indexes": unused_indexes,
                    "unused_count": len(unused_indexes),
                    "total_unused_size_bytes": sum(
                        idx["index_size_bytes"] for idx in unused_indexes
                    ),
                }

        except Exception as e:
            self.logger.error(f"Failed to get index usage: {e}")
            return {"error": str(e)}

    def _get_database_activity(self) -> List[Dict[str, Any]]:
        """Get database activity statistics."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        datname,
                        numbackends,
                        xact_commit,
                        xact_rollback,
                        blks_read,
                        blks_hit,
                        tup_returned,
                        tup_fetched,
                        tup_inserted,
                        tup_updated,
                        tup_deleted,
                        conflicts,
                        temp_files,
                        temp_bytes,
                        deadlocks,
                        checksum_failures,
                        checksum_last_failure,
                        blk_read_time,
                        blk_write_time,
                        stats_reset,
                        100.0 * blks_hit / nullif(blks_hit + blks_read, 0) AS cache_hit_ratio
                    FROM pg_stat_database
                    WHERE datname IS NOT NULL
                    ORDER BY datname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get database activity: {e}")
            return []

    def _get_resource_utilization(self) -> Dict[str, Any]:
        """Get resource utilization metrics."""
        try:
            with self.connection.cursor() as cursor:
                # Database sizes
                cursor.execute("""
                    SELECT
                        datname,
                        pg_database_size(datname) as size_bytes,
                        pg_size_pretty(pg_database_size(datname)) as size_human
                    FROM pg_database
                    WHERE datname NOT IN ('template0', 'template1')
                    ORDER BY pg_database_size(datname) DESC
                """)
                database_sizes = [dict(row) for row in cursor.fetchall()]

                # Largest tables
                cursor.execute("""
                    SELECT
                        schemaname,
                        tablename,
                        pg_total_relation_size(schemaname||'.'||tablename) as total_size_bytes,
                        pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as total_size_human,
                        pg_relation_size(schemaname||'.'||tablename) as table_size_bytes,
                        pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) as table_size_human
                    FROM pg_tables
                    WHERE schemaname NOT IN ('information_schema', 'pg_catalog')
                    ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
                    LIMIT 20
                """)
                largest_tables = [dict(row) for row in cursor.fetchall()]

                # Temporary file usage
                cursor.execute("""
                    SELECT
                        datname,
                        temp_files,
                        temp_bytes,
                        pg_size_pretty(temp_bytes) as temp_size_human
                    FROM pg_stat_database
                    WHERE temp_files > 0
                    ORDER BY temp_bytes DESC
                """)
                temp_file_usage = [dict(row) for row in cursor.fetchall()]

                return {
                    "database_sizes": database_sizes,
                    "largest_tables": largest_tables,
                    "temp_file_usage": temp_file_usage,
                }

        except Exception as e:
            self.logger.error(f"Failed to get resource utilization: {e}")
            return {"error": str(e)}

    def _get_cache_performance(self) -> Dict[str, Any]:
        """Get cache hit ratios and buffer usage."""
        try:
            with self.connection.cursor() as cursor:
                # Overall cache hit ratio
                cursor.execute("""
                    SELECT
                        SUM(blks_hit) as total_blks_hit,
                        SUM(blks_read) as total_blks_read,
                        100.0 * SUM(blks_hit) / nullif(SUM(blks_hit) + SUM(blks_read), 0) AS overall_hit_ratio
                    FROM pg_stat_database
                """)
                overall_cache = dict(cursor.fetchone())

                # Cache hit ratio by database
                cursor.execute("""
                    SELECT
                        datname,
                        blks_hit,
                        blks_read,
                        100.0 * blks_hit / nullif(blks_hit + blks_read, 0) AS hit_ratio
                    FROM pg_stat_database
                    WHERE datname IS NOT NULL
                    ORDER BY hit_ratio DESC
                """)
                cache_by_database = [dict(row) for row in cursor.fetchall()]

                # Buffer usage (if pg_buffercache is available)
                buffer_usage = None
                try:
                    cursor.execute(
                        "SELECT COUNT(*) FROM pg_extension WHERE extname = 'pg_buffercache'"
                    )
                    if cursor.fetchone()["count"] > 0:
                        cursor.execute("""
                            SELECT
                                COUNT(*) as total_buffers,
                                COUNT(*) FILTER (WHERE isdirty = true) as dirty_buffers,
                                COUNT(DISTINCT reltablespace) as tablespaces_in_cache,
                                COUNT(DISTINCT reldatabase) as databases_in_cache
                            FROM pg_buffercache
                            WHERE reldatabase IS NOT NULL
                        """)
                        buffer_usage = dict(cursor.fetchone())
                except Exception as e:
                    self.logger.info(f"pg_buffercache not available: {e}")

                return {
                    "overall_cache_performance": overall_cache,
                    "cache_by_database": cache_by_database,
                    "buffer_usage": buffer_usage,
                }

        except Exception as e:
            self.logger.error(f"Failed to get cache performance: {e}")
            return {"error": str(e)}

    def _get_lock_analysis(self) -> Dict[str, Any]:
        """Analyze current locks and lock contention."""
        try:
            with self.connection.cursor() as cursor:
                # Current locks
                cursor.execute("""
                    SELECT
                        l.mode,
                        l.locktype,
                        l.granted,
                        l.fastpath,
                        COUNT(*) as lock_count
                    FROM pg_locks l
                    GROUP BY l.mode, l.locktype, l.granted, l.fastpath
                    ORDER BY lock_count DESC
                """)
                lock_summary = [dict(row) for row in cursor.fetchall()]

                # Blocking locks
                cursor.execute("""
                    SELECT
                        blocked_locks.pid AS blocked_pid,
                        blocked_activity.usename AS blocked_user,
                        blocked_activity.query AS blocked_statement,
                        blocking_locks.pid AS blocking_pid,
                        blocking_activity.usename AS blocking_user,
                        blocking_activity.query AS blocking_statement
                    FROM pg_catalog.pg_locks blocked_locks
                    JOIN pg_catalog.pg_stat_activity blocked_activity
                         ON blocked_activity.pid = blocked_locks.pid
                    JOIN pg_catalog.pg_locks blocking_locks
                         ON blocking_locks.locktype = blocked_locks.locktype
                         AND blocking_locks.DATABASE IS NOT DISTINCT FROM blocked_locks.DATABASE
                         AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
                         AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
                         AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
                         AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
                         AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
                         AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
                         AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
                         AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
                         AND blocking_locks.pid != blocked_locks.pid
                    JOIN pg_catalog.pg_stat_activity blocking_activity
                         ON blocking_activity.pid = blocking_locks.pid
                    WHERE NOT blocked_locks.GRANTED
                """)
                blocking_locks = [dict(row) for row in cursor.fetchall()]

                return {
                    "lock_summary": lock_summary,
                    "blocking_locks": blocking_locks,
                    "blocking_count": len(blocking_locks),
                }

        except Exception as e:
            self.logger.error(f"Failed to get lock analysis: {e}")
            return {"error": str(e)}

    def _get_wait_events(self) -> List[Dict[str, Any]]:
        """Get current wait events from active sessions."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        wait_event_type,
                        wait_event,
                        COUNT(*) as session_count,
                        array_agg(DISTINCT state) as states
                    FROM pg_stat_activity
                    WHERE wait_event IS NOT NULL
                    GROUP BY wait_event_type, wait_event
                    ORDER BY session_count DESC
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get wait events: {e}")
            return []

    def _get_replication_lag(self) -> Dict[str, Any]:
        """Get comprehensive replication and replica information."""
        try:
            with self.connection.cursor() as cursor:
                # Check if this is a primary or standby
                cursor.execute("SELECT pg_is_in_recovery() as is_in_recovery")
                result = cursor.fetchone()
                is_standby = (
                    result["is_in_recovery"] if isinstance(result, dict) else result[0]
                )

                replication_info = {
                    "is_standby": is_standby,
                    "server_role": "standby" if is_standby else "primary",
                }

                if not is_standby:
                    # Primary server - get replication slots and lag
                    cursor.execute("""
                        SELECT
                            slot_name,
                            plugin,
                            slot_type,
                            datoid,
                            database,
                            temporary,
                            active,
                            active_pid,
                            xmin,
                            catalog_xmin,
                            restart_lsn,
                            confirmed_flush_lsn,
                            wal_status,
                            safe_wal_size
                        FROM pg_replication_slots
                    """)
                    replication_slots = [dict(row) for row in cursor.fetchall()]
                    replication_info["replication_slots"] = replication_slots
                    replication_info["replication_slot_count"] = len(replication_slots)

                    # WAL sender processes (active replicas)
                    cursor.execute("""
                        SELECT
                            pid,
                            usename,
                            application_name,
                            client_addr,
                            client_hostname,
                            client_port,
                            backend_start,
                            backend_xmin,
                            state,
                            sent_lsn,
                            write_lsn,
                            flush_lsn,
                            replay_lsn,
                            COALESCE(EXTRACT(EPOCH FROM write_lag)::int, 0) as write_lag_seconds,
                            COALESCE(EXTRACT(EPOCH FROM flush_lag)::int, 0) as flush_lag_seconds,
                            COALESCE(EXTRACT(EPOCH FROM replay_lag)::int, 0) as replay_lag_seconds,
                            sync_priority,
                            sync_state,
                            reply_time
                        FROM pg_stat_replication
                    """)
                    wal_senders = [dict(row) for row in cursor.fetchall()]
                    replication_info["wal_senders"] = wal_senders
                    replication_info["active_replica_count"] = len(wal_senders)

                    # Get replication summary
                    streaming_replicas = [
                        w for w in wal_senders if w.get("state") == "streaming"
                    ]
                    sync_replicas = [
                        w for w in wal_senders if w.get("sync_state") == "sync"
                    ]
                    async_replicas = [
                        w for w in wal_senders if w.get("sync_state") == "async"
                    ]

                    # Calculate max lag, handling None values and empty lists
                    lag_values = [
                        w.get("replay_lag_seconds")
                        for w in wal_senders
                        if w.get("replay_lag_seconds") is not None
                    ]
                    max_lag = max(lag_values) if lag_values else 0

                    replication_info["replication_summary"] = {
                        "total_replicas": len(wal_senders),
                        "streaming_replicas": len(streaming_replicas),
                        "synchronous_replicas": len(sync_replicas),
                        "asynchronous_replicas": len(async_replicas),
                        "max_lag_seconds": max_lag,
                    }

                else:
                    # Standby server - get recovery info
                    cursor.execute("""
                        SELECT
                            pg_last_wal_receive_lsn() as last_wal_receive_lsn,
                            pg_last_wal_replay_lsn() as last_wal_replay_lsn,
                            pg_last_xact_replay_timestamp() as last_xact_replay_timestamp
                    """)
                    recovery_row = cursor.fetchone()
                    replication_info["recovery_info"] = {
                        "last_wal_receive_lsn": (
                            str(recovery_row["last_wal_receive_lsn"])
                            if recovery_row["last_wal_receive_lsn"]
                            else None
                        ),
                        "last_wal_replay_lsn": (
                            str(recovery_row["last_wal_replay_lsn"])
                            if recovery_row["last_wal_replay_lsn"]
                            else None
                        ),
                        "last_xact_replay_timestamp": (
                            str(recovery_row["last_xact_replay_timestamp"])
                            if recovery_row["last_xact_replay_timestamp"]
                            else None
                        ),
                    }

                    # Calculate lag if possible
                    if recovery_row["last_xact_replay_timestamp"]:
                        cursor.execute(
                            "SELECT COALESCE(EXTRACT(EPOCH FROM (now() - %s))::int, 0) as lag_seconds",
                            (recovery_row["last_xact_replay_timestamp"],),
                        )
                        lag_result = cursor.fetchone()
                        replication_info["recovery_info"]["lag_seconds"] = (
                            lag_result["lag_seconds"] or 0
                        )

                return replication_info

        except Exception as e:
            self.logger.error(f"Failed to get replication information: {e}")
            return {"error": str(e)}
