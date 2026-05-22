"""
MySQL Feature/Technology Detection Module
Detects MySQL features relevant to PlanetScale migration assessment.
"""

from typing import Dict, Any

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer

SYSTEM_DATABASES = frozenset(
    [
        "information_schema",
        "mysql",
        "performance_schema",
        "sys",
        "_vt",
        "test",
    ]
)


class MySQLFeatureAnalyzer(DatabaseAnalyzer):
    """Detects MySQL technologies and features for migration assessment."""

    def analyze(self) -> Dict[str, Any]:
        variables = self._get_variables()
        results = {
            "technologies_detected": self._detect_technologies(variables),
        }
        return results

    def _get_variables(self) -> Dict[str, str]:
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW GLOBAL VARIABLES")
            rows = cursor.fetchall()
            cursor.close()
            return {row[0]: row[1] for row in rows}
        except Exception as e:
            self.add_error(f"Failed to get variables for feature detection: {e}", e)
            return {}

    def _detect_technologies(self, variables: Dict[str, str]) -> Dict[str, Any]:
        techs = {
            "full_text_indexing": self._has_fulltext_indexes(),
            "geospatial_types": self._has_spatial_columns(),
            "foreign_keys": self._has_foreign_keys(),
            "partitioning": self._has_partitions(),
            "innodb_compression": self._has_innodb_compression(),
            "ssl": self._detect_ssl(variables),
            "explicit_lock_tables": self._detect_lock_tables(),
            "xa_transactions": self._detect_xa(variables),
            "prepared_statements": self._detect_prepared_stmts(variables),
            "galera_cluster": self._detect_galera(variables),
        }
        return techs

    def _has_fulltext_indexes(self) -> bool:
        try:
            filter_clause = self._user_schema_filter("s.table_schema")
            query = f"""
                SELECT COUNT(*) AS cnt
                FROM information_schema.statistics s
                WHERE s.index_type = 'FULLTEXT'
                AND {filter_clause}
            """
            result = self.execute_query_single(query)
            return int(result.get("cnt", 0)) > 0
        except Exception:
            return False

    def _has_spatial_columns(self) -> bool:
        try:
            filter_clause = self._user_schema_filter("table_schema")
            query = f"""
                SELECT COUNT(*) AS cnt
                FROM information_schema.columns
                WHERE data_type IN (
                    'geometry', 'point', 'linestring', 'polygon',
                    'multipoint', 'multilinestring', 'multipolygon', 'geometrycollection'
                )
                AND {filter_clause}
            """
            result = self.execute_query_single(query)
            return int(result.get("cnt", 0)) > 0
        except Exception:
            return False

    def _has_foreign_keys(self) -> bool:
        try:
            filter_clause = self._user_schema_filter("constraint_schema")
            query = f"""
                SELECT COUNT(*) AS cnt
                FROM information_schema.referential_constraints
                WHERE {filter_clause}
            """
            result = self.execute_query_single(query)
            return int(result.get("cnt", 0)) > 0
        except Exception:
            return False

    def _has_partitions(self) -> bool:
        try:
            filter_clause = self._user_schema_filter("table_schema")
            query = f"""
                SELECT COUNT(*) AS cnt
                FROM information_schema.partitions
                WHERE partition_name IS NOT NULL
                AND {filter_clause}
            """
            result = self.execute_query_single(query)
            return int(result.get("cnt", 0)) > 0
        except Exception:
            return False

    def _has_innodb_compression(self) -> bool:
        try:
            filter_clause = self._user_schema_filter("table_schema")
            query = f"""
                SELECT COUNT(*) AS cnt
                FROM information_schema.tables
                WHERE row_format = 'Compressed'
                AND engine = 'InnoDB'
                AND {filter_clause}
            """
            result = self.execute_query_single(query)
            return int(result.get("cnt", 0)) > 0
        except Exception:
            return False

    def _detect_ssl(self, variables: Dict[str, str]) -> bool:
        have_ssl = variables.get("have_ssl", "").upper()
        return have_ssl == "YES"

    def _detect_lock_tables(self) -> bool:
        """Detect LOCK TABLES usage from status counters."""
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Table_locks_immediate'")
            row = cursor.fetchone()
            cursor.close()
            if row:
                return int(row[1]) > 0
        except Exception:
            pass
        return False

    def _detect_xa(self, variables: Dict[str, str]) -> bool:
        """Detect XA transactions support (innodb_support_xa was removed in 8.0, XA is always on)."""
        xa_var = variables.get("innodb_support_xa", "")
        if xa_var:
            return xa_var.upper() == "ON"
        # In MySQL 8.0+, XA is always supported. Check status for actual usage.
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Com_xa_%'")
            rows = cursor.fetchall()
            cursor.close()
            for row in rows:
                if int(row[1]) > 0:
                    return True
        except Exception:
            pass
        return False

    def _detect_prepared_stmts(self, variables: Dict[str, str]) -> bool:
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Prepared_stmt_count'")
            row = cursor.fetchone()
            cursor.close()
            if row:
                return int(row[1]) > 0
        except Exception:
            pass
        return False

    def _detect_galera(self, variables: Dict[str, str]) -> bool:
        return any(k.startswith("wsrep_") for k in variables)

    def _user_schema_filter(self, col: str) -> str:
        quoted = ", ".join(f"'{db}'" for db in SYSTEM_DATABASES)
        return f"{col} NOT IN ({quoted})"
