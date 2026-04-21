"""
MySQL Schema Analysis Module
Analyzes tables, indexes, views, routines, triggers, constraints, and partitions
via information_schema queries (no mysqldump dependency).

Supports per-database iteration for environments (like PlanetScale/Vitess)
where information_schema is scoped to the current database context.
"""

from typing import Dict, Any, List

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


def _quote_identifier(ident: str) -> str:
    """Quote a MySQL identifier, doubling any embedded backticks per MySQL rules."""
    return "`" + ident.replace("`", "``") + "`"


class MySQLSchemaAnalyzer(DatabaseAnalyzer):
    """Analyzes MySQL schema via information_schema."""

    def analyze(self) -> Dict[str, Any]:
        database_list = self._get_database_list()

        # Try a single cross-database query first
        tables = self._get_table_analysis()
        dbs_in_tables = set(t.get("schema_name") for t in tables)

        # If we found fewer databases in results than in database_list,
        # information_schema is likely scoped (PlanetScale/Vitess).
        # Fall back to per-database iteration.
        needs_iteration = len(database_list) > 1 and len(dbs_in_tables) < len(
            database_list
        )

        if needs_iteration:
            self.logger.info(
                f"Detected scoped information_schema ({len(dbs_in_tables)} of "
                f"{len(database_list)} databases visible). Iterating per-database."
            )
            return self._analyze_per_database(database_list)

        # Normal path: single-query results cover all databases
        routines = self._get_routine_analysis()
        results = {
            "database_catalog": database_list,
            "table_analysis": tables,
            "column_analysis": self._get_column_analysis(),
            "index_analysis": self._get_index_analysis(),
            "view_analysis": self._get_view_analysis(),
            "routine_analysis": routines,
            "function_analysis": routines,
            "trigger_analysis": self._get_trigger_analysis(),
            "constraint_analysis": self._get_constraint_analysis(),
            "check_constraint_analysis": self._get_check_constraints(),
            "partition_analysis": self._get_partition_analysis(),
            "db_object_counts": self._get_db_object_counts(),
            "db_storage_engines": self._get_db_storage_engines(),
            "db_index_types": self._get_db_index_types(),
            "db_column_types": self._get_db_column_types(),
        }
        return results

    def _analyze_per_database(self, database_list: List[str]) -> Dict[str, Any]:
        """Iterate USE <db> for each database and merge results."""
        merged = {
            "database_catalog": database_list,
            "table_analysis": [],
            "column_analysis": [],
            "index_analysis": [],
            "view_analysis": [],
            "routine_analysis": [],
            "function_analysis": [],
            "trigger_analysis": [],
            "constraint_analysis": [],
            "check_constraint_analysis": [],
            "partition_analysis": [],
            "db_object_counts": [],
            "db_storage_engines": [],
            "db_index_types": [],
            "db_column_types": [],
        }

        for db in database_list:
            try:
                cursor = self.connection.cursor()
                cursor.execute("USE " + _quote_identifier(db))
                cursor.close()
            except Exception as e:
                self.logger.warning(f"Cannot USE `{db}`, skipping: {e}")
                continue

            self.logger.info(f"Analyzing database: {db}")

            merged["table_analysis"].extend(self._get_table_analysis())
            merged["column_analysis"].extend(self._get_column_analysis())
            merged["index_analysis"].extend(self._get_index_analysis())
            merged["view_analysis"].extend(self._get_view_analysis())
            routines = self._get_routine_analysis()
            merged["routine_analysis"].extend(routines)
            merged["function_analysis"].extend(routines)
            merged["trigger_analysis"].extend(self._get_trigger_analysis())
            merged["constraint_analysis"].extend(self._get_constraint_analysis())
            merged["check_constraint_analysis"].extend(self._get_check_constraints())
            merged["partition_analysis"].extend(self._get_partition_analysis())
            merged["db_object_counts"].extend(self._get_db_object_counts())
            merged["db_storage_engines"].extend(self._get_db_storage_engines())
            merged["db_index_types"].extend(self._get_db_index_types())
            merged["db_column_types"].extend(self._get_db_column_types())

        # Sort tables by size descending (they come pre-sorted per-db but not globally)
        merged["table_analysis"].sort(
            key=lambda t: t.get("total_size_bytes", 0), reverse=True
        )

        return merged

    def _user_databases_filter(self, col: str = "table_schema") -> str:
        """SQL fragment to exclude system databases."""
        quoted = ", ".join(f"'{db}'" for db in SYSTEM_DATABASES)
        return f"{col} NOT IN ({quoted})"

    def _get_database_list(self) -> List[str]:
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW DATABASES")
            rows = cursor.fetchall()
            cursor.close()
            all_dbs = [row[0] for row in rows]
            return [db for db in all_dbs if db.lower() not in SYSTEM_DATABASES]
        except Exception as e:
            self.add_error(f"Failed to get database list: {e}", e)
            return []

    def _get_table_analysis(self) -> List[Dict[str, Any]]:
        try:
            query = f"""
                SELECT
                    t.table_schema AS schema_name,
                    t.table_name AS table_name,
                    t.engine AS engine,
                    CAST(COALESCE(t.table_rows, 0) AS SIGNED) AS estimated_rows,
                    CAST(COALESCE(t.data_length, 0) AS SIGNED) AS data_length_bytes,
                    CAST(COALESCE(t.index_length, 0) AS SIGNED) AS index_length_bytes,
                    CAST(COALESCE(t.data_length, 0) + COALESCE(t.index_length, 0) AS SIGNED) AS total_size_bytes,
                    t.row_format AS row_format,
                    t.table_collation AS table_collation,
                    t.create_time AS create_time,
                    t.update_time AS update_time,
                    t.table_comment AS table_comment,
                    CASE WHEN pk.table_name IS NOT NULL THEN 1 ELSE 0 END AS has_primary_key,
                    CASE WHEN uq.table_name IS NOT NULL THEN 1 ELSE 0 END AS has_unique_key
                FROM information_schema.tables t
                LEFT JOIN (
                    SELECT DISTINCT table_schema, table_name
                    FROM information_schema.statistics
                    WHERE index_name = 'PRIMARY'
                ) pk ON t.table_schema = pk.table_schema AND t.table_name = pk.table_name
                LEFT JOIN (
                    SELECT DISTINCT table_schema, table_name
                    FROM information_schema.statistics
                    WHERE non_unique = 0
                ) uq ON t.table_schema = uq.table_schema AND t.table_name = uq.table_name
                WHERE t.table_type = 'BASE TABLE'
                AND {self._user_databases_filter('t.table_schema')}
                ORDER BY total_size_bytes DESC, t.table_name ASC
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get table analysis: {e}", e)
            return []

    def _get_index_analysis(self) -> List[Dict[str, Any]]:
        try:
            query = f"""
                SELECT
                    s.table_schema AS schema_name,
                    s.table_name AS table_name,
                    s.index_name AS index_name,
                    s.index_type AS index_type,
                    NOT s.non_unique AS is_unique,
                    s.index_name = 'PRIMARY' AS is_primary,
                    GROUP_CONCAT(s.column_name ORDER BY s.seq_in_index) AS columns,
                    COUNT(*) AS column_count,
                    s.nullable AS nullable,
                    MAX(s.cardinality) AS cardinality,
                    GROUP_CONCAT(s.sub_part ORDER BY s.seq_in_index) AS prefix_lengths
                FROM information_schema.statistics s
                WHERE {self._user_databases_filter('s.table_schema')}
                GROUP BY s.table_schema, s.table_name, s.index_name, s.index_type, s.non_unique, s.nullable
                ORDER BY s.table_schema, s.table_name, s.index_name
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get index analysis: {e}", e)
            return []

    def _get_view_analysis(self) -> List[Dict[str, Any]]:
        try:
            query = f"""
                SELECT
                    table_schema AS schema_name,
                    table_name AS view_name,
                    view_definition AS view_definition,
                    check_option AS check_option,
                    is_updatable AS is_updatable,
                    definer AS definer,
                    security_type AS security_type
                FROM information_schema.views
                WHERE {self._user_databases_filter()}
                ORDER BY table_schema, table_name
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get view analysis: {e}", e)
            return []

    def _get_routine_analysis(self) -> List[Dict[str, Any]]:
        try:
            query = f"""
                SELECT
                    routine_schema AS schema_name,
                    routine_name AS routine_name,
                    routine_type AS type,
                    data_type AS return_type,
                    definer AS definer,
                    security_type AS security_type,
                    is_deterministic AS is_deterministic,
                    sql_data_access AS sql_data_access,
                    created AS created,
                    last_altered AS last_altered
                FROM information_schema.routines
                WHERE {self._user_databases_filter('routine_schema')}
                ORDER BY routine_schema, routine_type, routine_name
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get routine analysis: {e}", e)
            return []

    def _get_trigger_analysis(self) -> List[Dict[str, Any]]:
        try:
            query = f"""
                SELECT
                    trigger_schema AS schema_name,
                    trigger_name AS trigger_name,
                    event_manipulation AS event,
                    event_object_table AS table_name,
                    action_timing AS timing,
                    action_statement AS statement,
                    definer AS definer,
                    created AS created
                FROM information_schema.triggers
                WHERE {self._user_databases_filter('trigger_schema')}
                ORDER BY trigger_schema, event_object_table, action_timing, event_manipulation
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get trigger analysis: {e}", e)
            return []

    def _get_constraint_analysis(self) -> List[Dict[str, Any]]:
        try:
            query = f"""
                SELECT
                    tc.constraint_schema AS schema_name,
                    tc.table_name AS table_name,
                    tc.constraint_name AS constraint_name,
                    tc.constraint_type AS constraint_type,
                    kcu.column_name AS column_name,
                    kcu.referenced_table_schema AS referenced_database,
                    kcu.referenced_table_name AS referenced_table_name,
                    kcu.referenced_column_name AS referenced_column_name,
                    rc.update_rule AS on_update,
                    rc.delete_rule AS on_delete
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_schema = kcu.constraint_schema
                    AND tc.constraint_name = kcu.constraint_name
                    AND tc.table_name = kcu.table_name
                LEFT JOIN information_schema.referential_constraints rc
                    ON tc.constraint_schema = rc.constraint_schema
                    AND tc.constraint_name = rc.constraint_name
                WHERE {self._user_databases_filter('tc.constraint_schema')}
                ORDER BY tc.constraint_schema, tc.table_name, tc.constraint_name, kcu.ordinal_position
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get constraint analysis: {e}", e)
            return []

    def _get_partition_analysis(self) -> List[Dict[str, Any]]:
        try:
            query = f"""
                SELECT
                    table_schema AS schema_name,
                    table_name AS table_name,
                    partition_name AS partition_name,
                    subpartition_name AS subpartition_name,
                    partition_method AS partition_method,
                    subpartition_method AS subpartition_method,
                    partition_expression AS partition_expression,
                    subpartition_expression AS subpartition_expression,
                    partition_description AS partition_value,
                    table_rows AS estimated_rows,
                    COALESCE(data_length, 0) + COALESCE(index_length, 0) AS partition_size_bytes
                FROM information_schema.partitions
                WHERE partition_name IS NOT NULL
                AND {self._user_databases_filter()}
                ORDER BY table_schema, table_name, partition_ordinal_position
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get partition analysis: {e}", e)
            return []

    def _get_column_analysis(self) -> List[Dict[str, Any]]:
        """Per-column detail: data types, AUTO_INCREMENT, generated columns, charset."""
        try:
            query = f"""
                SELECT
                    table_schema AS schema_name,
                    table_name AS table_name,
                    column_name AS column_name,
                    ordinal_position AS ordinal_position,
                    data_type AS data_type,
                    column_type AS column_type,
                    is_nullable AS is_nullable,
                    column_default AS column_default,
                    extra AS extra,
                    character_set_name AS character_set_name,
                    collation_name AS collation_name,
                    column_comment AS column_comment,
                    generation_expression AS generation_expression
                FROM information_schema.columns
                WHERE {self._user_databases_filter()}
                ORDER BY table_schema, table_name, ordinal_position
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get column analysis: {e}", e)
            return []

    def _get_check_constraints(self) -> List[Dict[str, Any]]:
        """CHECK constraints (MySQL 8.0.16+). Gracefully returns empty on older versions."""
        try:
            query = f"""
                SELECT
                    cc.constraint_schema AS schema_name,
                    tc.table_name AS table_name,
                    cc.constraint_name AS constraint_name,
                    cc.check_clause AS check_clause
                FROM information_schema.check_constraints cc
                JOIN information_schema.table_constraints tc
                    ON cc.constraint_schema = tc.constraint_schema
                    AND cc.constraint_name = tc.constraint_name
                    AND tc.constraint_type = 'CHECK'
                WHERE {self._user_databases_filter('cc.constraint_schema')}
                ORDER BY cc.constraint_schema, tc.table_name, cc.constraint_name
            """
            return self.execute_query(query)
        except Exception as e:
            # information_schema.check_constraints doesn't exist before 8.0.16
            if (
                "check_constraints" in str(e).lower()
                or "doesn't exist" in str(e).lower()
            ):
                self.logger.info(
                    "CHECK constraints not available (requires MySQL 8.0.16+)"
                )
                return []
            self.add_error(f"Failed to get check constraints: {e}", e)
            return []

    def _get_db_object_counts(self) -> List[Dict[str, Any]]:
        """Per-database object counts (tables, views, routines, triggers)."""
        try:
            query = f"""
                SELECT
                    t.table_schema AS schema_name,
                    SUM(CASE WHEN t.table_type = 'BASE TABLE' THEN 1 ELSE 0 END) AS table_count,
                    SUM(CASE WHEN t.table_type = 'VIEW' THEN 1 ELSE 0 END) AS view_count,
                    COALESCE(r.routine_count, 0) AS routine_count,
                    COALESCE(tr.trigger_count, 0) AS trigger_count
                FROM information_schema.tables t
                LEFT JOIN (
                    SELECT routine_schema, COUNT(*) AS routine_count
                    FROM information_schema.routines
                    GROUP BY routine_schema
                ) r ON t.table_schema = r.routine_schema
                LEFT JOIN (
                    SELECT trigger_schema, COUNT(*) AS trigger_count
                    FROM information_schema.triggers
                    GROUP BY trigger_schema
                ) tr ON t.table_schema = tr.trigger_schema
                WHERE {self._user_databases_filter('t.table_schema')}
                GROUP BY t.table_schema
                ORDER BY t.table_schema
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get db object counts: {e}", e)
            return []

    def _get_db_storage_engines(self) -> List[Dict[str, Any]]:
        """Per-database storage engine distribution."""
        try:
            query = f"""
                SELECT
                    table_schema AS schema_name,
                    engine AS engine,
                    COUNT(*) AS table_count
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                AND {self._user_databases_filter()}
                GROUP BY table_schema, engine
                ORDER BY table_schema, table_count DESC
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get db storage engines: {e}", e)
            return []

    def _get_db_index_types(self) -> List[Dict[str, Any]]:
        """Per-database index type distribution."""
        try:
            query = f"""
                SELECT
                    s.table_schema AS schema_name,
                    s.index_type AS index_type,
                    COUNT(DISTINCT CONCAT(s.table_name, '.', s.index_name)) AS index_count
                FROM information_schema.statistics s
                WHERE {self._user_databases_filter('s.table_schema')}
                GROUP BY s.table_schema, s.index_type
                ORDER BY s.table_schema, index_count DESC
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get db index types: {e}", e)
            return []

    def _get_db_column_types(self) -> List[Dict[str, Any]]:
        """Per-database column type distribution."""
        try:
            query = f"""
                SELECT
                    table_schema AS schema_name,
                    data_type AS data_type,
                    COUNT(*) AS column_count
                FROM information_schema.columns
                WHERE {self._user_databases_filter()}
                GROUP BY table_schema, data_type
                ORDER BY table_schema, column_count DESC
            """
            return self.execute_query(query)
        except Exception as e:
            self.add_error(f"Failed to get db column types: {e}", e)
            return []
