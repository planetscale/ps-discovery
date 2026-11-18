"""CockroachDB performance analysis.

pg_stat_activity, pg_stat_user_tables and pg_stat_statements are empty
or missing. Use crdb_internal.statement_statistics, index usage, and
SHOW CLUSTER SESSIONS instead.
"""

from typing import Any, Dict, List

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer
from planetscale_discovery.common.sanitize import redact_sql
from planetscale_discovery.database.cockroach import SYSTEM_SCHEMAS

_SYSTEM_SCHEMA_SQL = "(" + ", ".join(f"'{s}'" for s in SYSTEM_SCHEMAS) + ")"


class CockroachPerformanceAnalyzer(DatabaseAnalyzer):
    """Performance analyzer backed by crdb_internal catalogs."""

    def analyze(self) -> Dict[str, Any]:
        return {
            "connection_analysis": self._get_connection_analysis(),
            "query_performance": self._get_query_performance(),
            "table_statistics": self._get_table_statistics(),
            "index_usage": self._get_index_usage(),
            "database_activity": {"note": "pg_stat_database is empty on CockroachDB"},
            "resource_utilization": self._get_resource_utilization(),
            "cache_performance": {},
            "lock_analysis": {},
            "wait_events": {},
            "replication_lag": {
                "note": (
                    "CockroachDB has no PostgreSQL WAL replica lag. "
                    "See config.cockroach_cluster for replica counts."
                )
            },
        }

    def _get_connection_analysis(self) -> Dict[str, Any]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        application_name,
                        count(*) AS connection_count
                    FROM [SHOW CLUSTER SESSIONS]
                    GROUP BY 1
                    ORDER BY 2 DESC
                    """)
                by_app = [dict(row) for row in cursor.fetchall()]
                total = sum(int(row.get("connection_count") or 0) for row in by_app)
            return {
                "total_connections": total,
                "active_connections": total,
                "idle_connections": 0,
                "by_application": by_app,
                "source": "SHOW CLUSTER SESSIONS",
                "max_connections": None,
            }
        except Exception as exc:
            self.add_error(
                "Connection analysis used SHOW CLUSTER SESSIONS and failed "
                "(pg_stat_activity is empty on CockroachDB)",
                exc,
            )
            return {
                "total_connections": 0,
                "active_connections": 0,
                "source": "SHOW CLUSTER SESSIONS",
            }

    def _get_query_performance(self) -> Dict[str, Any]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        metadata->>'db' AS db,
                        app_name,
                        sum((statistics->'statistics'->>'cnt')::INT8) AS exec_count,
                        round(
                            sum(
                                (statistics->'statistics'->>'cnt')::FLOAT8
                                * (statistics->'statistics'->'svcLat'->>'mean')::FLOAT8
                            )::NUMERIC,
                            2
                        ) AS total_svc_sec,
                        round(
                            (
                                sum(
                                    (statistics->'statistics'->>'cnt')::FLOAT8
                                    * (statistics->'statistics'->'svcLat'->>'mean')::FLOAT8
                                )
                                / NULLIF(
                                    sum((statistics->'statistics'->>'cnt')::FLOAT8),
                                    0
                                )
                                * 1000
                            )::NUMERIC,
                            2
                        ) AS mean_ms,
                        round(
                            sum(
                                (statistics->'statistics'->>'cnt')::FLOAT8
                                * (statistics->'statistics'->'rowsRead'->>'mean')::FLOAT8
                            )::NUMERIC
                        ) AS rows_read,
                        left(metadata->>'query', 200) AS query
                    FROM crdb_internal.statement_statistics
                    WHERE aggregated_ts > now() - INTERVAL '1 day'
                      AND app_name NOT LIKE '$ internal%'
                      AND metadata->>'db' = current_database()
                    GROUP BY 1, 2, fingerprint_id, metadata->>'query'
                    ORDER BY total_svc_sec DESC
                    LIMIT 25
                    """)
                rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                raw = row.pop("query", None)
                row["query_redacted"] = redact_sql(raw)
            return {
                "source": "crdb_internal.statement_statistics",
                "window": "1 day",
                "top_statements": rows,
            }
        except Exception as exc:
            self.add_error(
                "Top statements from crdb_internal.statement_statistics failed",
                exc,
            )
            return {"error": str(exc), "source": "crdb_internal.statement_statistics"}

    def _get_table_statistics(self) -> List[Dict[str, Any]]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT
                        t.schema_name,
                        t.name AS table_name,
                        rs.estimated_row_count
                    FROM crdb_internal.tables AS t
                    LEFT JOIN crdb_internal.table_row_statistics AS rs
                      ON rs.table_id = t.table_id
                    WHERE t.database_name = current_database()
                      AND t.schema_name NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND t.drop_time IS NULL
                    ORDER BY t.schema_name, t.name
                    """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            self.add_warning(f"table_row_statistics unavailable: {exc}")
            return []

    def _get_index_usage(self) -> List[Dict[str, Any]]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT
                        t.schema_name,
                        ti.descriptor_name AS table_name,
                        ti.index_name,
                        ti.index_type,
                        ti.is_sharded,
                        COALESCE(us.total_reads, 0) AS total_reads,
                        us.last_read
                    FROM crdb_internal.table_indexes AS ti
                    JOIN crdb_internal.tables AS t
                      ON t.table_id = ti.descriptor_id
                    LEFT JOIN crdb_internal.index_usage_statistics AS us
                      ON us.table_id = ti.descriptor_id
                     AND us.index_id = ti.index_id
                    WHERE t.database_name = current_database()
                      AND t.schema_name NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND t.drop_time IS NULL
                    ORDER BY total_reads DESC, table_name, index_name
                    """)
                rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                row["idx_scan"] = int(row.get("total_reads") or 0)
            return rows
        except Exception as exc:
            self.add_error("CockroachDB index usage statistics failed", exc)
            return []

    def _get_resource_utilization(self) -> Dict[str, Any]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        node_id,
                        store_id,
                        used,
                        available,
                        logical_bytes
                    FROM crdb_internal.kv_store_status
                    """)
                stores = [dict(row) for row in cursor.fetchall()]
            return {
                "stores": stores,
                "note": (
                    "Store used/logical_bytes are per-store physical figures "
                    "(includes MVCC garbage and system data). Do not quote "
                    "them as database size. Use schema.database_catalog.size_bytes."
                ),
            }
        except Exception as exc:
            self.add_warning(f"kv_store_status unavailable: {exc}")
            return {}
