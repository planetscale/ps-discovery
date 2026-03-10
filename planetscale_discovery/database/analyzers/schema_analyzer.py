"""
PostgreSQL Schema Analysis Module
Analyzes database structure, schemas, tables, and database objects.
"""

from typing import Dict, Any, List

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class SchemaAnalyzer(DatabaseAnalyzer):
    """Analyzes PostgreSQL database schema and structure."""

    def __init__(self, connection, config=None, logger=None):
        super().__init__(connection, config, logger)
        self._version_num = None

    def _get_server_version_num(self) -> int:
        """Get PostgreSQL version number (cached)."""
        if self._version_num is None:
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute("SHOW server_version_num")
                    self._version_num = int(cursor.fetchone()["server_version_num"])
            except Exception as e:
                self.logger.warning(f"Could not get server version: {e}")
                self._version_num = 0
        return self._version_num

    def analyze(self) -> Dict[str, Any]:
        """Run complete schema analysis."""
        results = {
            "database_catalog": self._get_database_catalog(),
            "schema_inventory": self._get_schema_inventory(),
            "table_analysis": self._get_table_analysis(),
            "index_analysis": self._get_index_analysis(),
            "constraint_analysis": self._get_constraint_analysis(),
            "function_analysis": self._get_function_analysis(),
            "trigger_analysis": self._get_trigger_analysis(),
            "view_analysis": self._get_view_analysis(),
            "sequence_analysis": self._get_sequence_analysis(),
            "partition_analysis": self._get_partition_analysis(),
            "inheritance_analysis": self._get_inheritance_analysis(),
        }

        return results

    def _get_database_catalog(self) -> List[Dict[str, Any]]:
        """Get information about all databases."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        d.datname as database_name,
                        pg_encoding_to_char(d.encoding) as encoding,
                        d.datcollate as collation,
                        d.datctype as ctype,
                        d.datistemplate as is_template,
                        d.datallowconn as allow_connections,
                        d.datconnlimit as connection_limit,
                        pg_database_size(d.datname) as size_bytes,
                        pg_size_pretty(pg_database_size(d.datname)) as size_human,
                        r.rolname as owner,
                        t.spcname as tablespace
                    FROM pg_database d
                    JOIN pg_roles r ON d.datdba = r.oid
                    LEFT JOIN pg_tablespace t ON d.dattablespace = t.oid
                    WHERE d.datname NOT IN ('template0', 'template1')
                    ORDER BY d.datname
                """)

                rows = list(cursor.fetchall())

            databases = []
            for row in rows:
                db_info = dict(row)

                # Get database-specific settings using a separate cursor
                try:
                    with self.connection.cursor() as detail_cursor:
                        detail_cursor.execute(
                            """
                            SELECT unnest(setconfig) as config_setting
                            FROM pg_db_role_setting
                            WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname = %s)
                            AND setrole = 0
                        """,
                            (db_info["database_name"],),
                        )

                        db_info["database_settings"] = [
                            r["config_setting"] for r in detail_cursor.fetchall()
                        ]
                except Exception as e:
                    db_info["database_settings"] = []
                    self.logger.warning(
                        f"Could not get settings for database {db_info['database_name']}: {e}"
                    )

                databases.append(db_info)

            return databases

        except Exception as e:
            self.logger.error(f"Failed to get database catalog: {e}")
            return []

    def _get_schema_inventory(self) -> List[Dict[str, Any]]:
        """Get information about all schemas."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        r.rolname as owner,
                        n.oid as schema_oid,
                        obj_description(n.oid, 'pg_namespace') as description,
                        CASE WHEN n.nspname LIKE 'pg_%' OR n.nspname = 'information_schema'
                             THEN true ELSE false END as is_system_schema
                    FROM pg_namespace n
                    JOIN pg_roles r ON n.nspowner = r.oid
                    ORDER BY is_system_schema, n.nspname
                """)

                rows = list(cursor.fetchall())

            schemas = []
            for row in rows:
                schema_info = dict(row)

                # Get schema privileges - use alternative method if information_schema.schema_privileges doesn't exist
                schema_info["privileges"] = []
                try:
                    with self.connection.cursor() as detail_cursor:
                        # First check if the view exists
                        detail_cursor.execute("""
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = 'information_schema'
                            AND table_name = 'schema_privileges'
                        """)

                        if detail_cursor.fetchone():
                            detail_cursor.execute(
                                """
                                SELECT
                                    grantee,
                                    privilege_type,
                                    is_grantable
                                FROM information_schema.schema_privileges
                                WHERE schema_name = %s
                                ORDER BY grantee, privilege_type
                            """,
                                (schema_info["schema_name"],),
                            )

                            schema_info["privileges"] = [
                                dict(r) for r in detail_cursor.fetchall()
                            ]
                        else:
                            # Alternative: get ACL from pg_namespace directly
                            detail_cursor.execute(
                                """
                                SELECT
                                    (aclexplode(nspacl)).grantee::regrole as grantee,
                                    (aclexplode(nspacl)).privilege_type as privilege_type,
                                    (aclexplode(nspacl)).is_grantable as is_grantable
                                FROM pg_namespace
                                WHERE nspname = %s AND nspacl IS NOT NULL
                            """,
                                (schema_info["schema_name"],),
                            )

                            schema_info["privileges"] = [
                                dict(r) for r in detail_cursor.fetchall()
                            ]

                except Exception as e:
                    self.logger.warning(
                        f"Could not get privileges for schema {schema_info['schema_name']}: {e}"
                    )

                # Get object counts in schema
                try:
                    with self.connection.cursor() as detail_cursor:
                        detail_cursor.execute(
                            """
                            SELECT
                                COUNT(*) FILTER (WHERE c.relkind = 'r') as table_count,
                                COUNT(*) FILTER (WHERE c.relkind = 'v') as view_count,
                                COUNT(*) FILTER (WHERE c.relkind = 'm') as materialized_view_count,
                                COUNT(*) FILTER (WHERE c.relkind = 'S') as sequence_count,
                                COUNT(*) FILTER (WHERE c.relkind = 'f') as foreign_table_count
                            FROM pg_class c
                            WHERE c.relnamespace = %s
                        """,
                            (schema_info["schema_oid"],),
                        )

                        counts = detail_cursor.fetchone()
                        schema_info["object_counts"] = dict(counts)
                except Exception as e:
                    schema_info["object_counts"] = {}
                    self.logger.warning(
                        f"Could not get object counts for schema {schema_info['schema_name']}: {e}"
                    )

                schemas.append(schema_info)

            return schemas

        except Exception as e:
            self.logger.error(f"Failed to get schema inventory: {e}")
            return []

    def _get_table_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about all tables."""
        try:
            # relispartition and partitioned tables added in PostgreSQL 10
            version_num = self._get_server_version_num()
            has_declarative_partitioning = version_num >= 100000

            with self.connection.cursor() as cursor:
                # Build query based on version
                if has_declarative_partitioning:
                    relkind_filter = "('r', 'p')"  # regular and partitioned tables
                    is_partition_col = "c.relispartition as is_partition"
                else:
                    relkind_filter = "('r')"  # only regular tables
                    is_partition_col = "false as is_partition"

                cursor.execute(f"""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        c.oid as table_oid,
                        r.rolname as owner,
                        c.relkind as table_type,
                        c.relpersistence as persistence,
                        {is_partition_col},
                        c.relhasindex as has_indexes,
                        c.relhasrules as has_rules,
                        c.relhastriggers as has_triggers,
                        c.relhassubclass as has_inheritance,
                        c.reltuples::bigint as estimated_rows,
                        pg_relation_size(c.oid) as table_size_bytes,
                        pg_size_pretty(pg_relation_size(c.oid)) as table_size_human,
                        pg_total_relation_size(c.oid) as total_size_bytes,
                        pg_size_pretty(pg_total_relation_size(c.oid)) as total_size_human,
                        obj_description(c.oid, 'pg_class') as description
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_roles r ON c.relowner = r.oid
                    WHERE c.relkind IN {relkind_filter}
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname
                """)

                rows = list(cursor.fetchall())

            tables = []
            for row in rows:
                table_info = dict(row)

                # Get column information
                try:
                    with self.connection.cursor() as detail_cursor:
                        detail_cursor.execute(
                            """
                            SELECT
                                a.attname as column_name,
                                a.attnum as column_number,
                                pg_catalog.format_type(a.atttypid, a.atttypmod) as data_type,
                                a.attnotnull as not_null,
                                a.atthasdef as has_default,
                                CASE WHEN d.adbin IS NOT NULL THEN true ELSE false END as has_default_value,
                                col_description(a.attrelid, a.attnum) as description,
                                a.attisdropped as is_dropped,
                                a.attstorage as storage_type,
                                a.attcollation as collation_oid
                            FROM pg_attribute a
                            LEFT JOIN pg_attrdef d ON (a.attrelid = d.adrelid AND a.attnum = d.adnum)
                            WHERE a.attrelid = %s
                            AND a.attnum > 0
                            AND NOT a.attisdropped
                            ORDER BY a.attnum
                        """,
                            (table_info["table_oid"],),
                        )

                        table_info["columns"] = [
                            dict(r) for r in detail_cursor.fetchall()
                        ]
                except Exception as e:
                    table_info["columns"] = []
                    self.logger.warning(
                        f"Could not get columns for table {table_info['table_name']}: {e}"
                    )

                # Get table statistics
                try:
                    with self.connection.cursor() as detail_cursor:
                        detail_cursor.execute(
                            """
                            SELECT
                                schemaname,
                                tablename,
                                attname,
                                n_distinct,
                                correlation
                            FROM pg_stats
                            WHERE schemaname = %s AND tablename = %s
                        """,
                            (table_info["schema_name"], table_info["table_name"]),
                        )

                        table_info["statistics"] = [
                            dict(r) for r in detail_cursor.fetchall()
                        ]
                except Exception as e:
                    table_info["statistics"] = []
                    self.logger.warning(
                        f"Could not get statistics for table {table_info['table_name']}: {e}"
                    )

                # Check if table is a partition (PostgreSQL 10+)
                if has_declarative_partitioning and table_info.get("is_partition"):
                    try:
                        with self.connection.cursor() as detail_cursor:
                            # Get parent table information
                            detail_cursor.execute(
                                """
                                SELECT
                                    parent_ns.nspname as parent_schema,
                                    parent_class.relname as parent_table,
                                    parent_class.oid as parent_oid
                                FROM pg_inherits i
                                JOIN pg_class parent_class ON i.inhparent = parent_class.oid
                                JOIN pg_namespace parent_ns ON parent_class.relnamespace = parent_ns.oid
                                WHERE i.inhrelid = %s::oid
                            """,
                                (table_info["table_oid"],),
                            )

                            parent_info = detail_cursor.fetchone()
                            if parent_info:
                                table_info["parent_table"] = dict(parent_info)
                    except Exception as e:
                        self.logger.warning(
                            f"Could not get parent table info for partition {table_info['table_name']}: {e}"
                        )

                # Check if table is partitioned (PostgreSQL 10+)
                if has_declarative_partitioning and table_info["table_type"] == "p":
                    try:
                        with self.connection.cursor() as detail_cursor:
                            detail_cursor.execute(
                                """
                                SELECT
                                    pg_get_partkeydef(%s::oid) as partition_key,
                                    partdefid
                                FROM pg_partitioned_table
                                WHERE partrelid = %s::oid
                            """,
                                (
                                    table_info["table_oid"],
                                    table_info["table_oid"],
                                ),
                            )

                            partition_info = detail_cursor.fetchone()
                            if partition_info:
                                table_info["partition_info"] = dict(partition_info)
                    except Exception as e:
                        self.logger.warning(
                            f"Could not get partition info for table {table_info['table_name']}: {e}"
                        )

                tables.append(table_info)

            return tables

        except Exception as e:
            self.logger.error(f"Failed to get table analysis: {e}")
            return []

    def _get_index_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about all indexes."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        i.relname as index_name,
                        t.relname as table_name,
                        am.amname as index_type,
                        i.reltuples::bigint as estimated_rows,
                        pg_relation_size(i.oid) as index_size_bytes,
                        pg_size_pretty(pg_relation_size(i.oid)) as index_size_human,
                        ix.indisunique as is_unique,
                        ix.indisprimary as is_primary,
                        ix.indisexclusion as is_exclusion,
                        ix.indimmediate as is_immediate,
                        ix.indisclustered as is_clustered,
                        ix.indisvalid as is_valid,
                        ix.indcheckxmin as check_xmin,
                        ix.indisready as is_ready,
                        ix.indislive as is_live,
                        pg_get_indexdef(i.oid) as index_definition,
                        pg_get_expr(ix.indexprs, ix.indrelid) as index_expressions,
                        pg_get_expr(ix.indpred, ix.indrelid) as index_predicate,
                        obj_description(i.oid, 'pg_class') as description
                    FROM pg_class i
                    JOIN pg_index ix ON i.oid = ix.indexrelid
                    JOIN pg_class t ON ix.indrelid = t.oid
                    JOIN pg_namespace n ON i.relnamespace = n.oid
                    JOIN pg_am am ON i.relam = am.oid
                    WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, t.relname, i.relname
                """)

                rows = list(cursor.fetchall())

            indexes = []
            for row in rows:
                index_info = dict(row)

                # Get index usage statistics
                try:
                    with self.connection.cursor() as detail_cursor:
                        detail_cursor.execute(
                            """
                            SELECT
                                idx_scan,
                                idx_tup_read,
                                idx_tup_fetch
                            FROM pg_stat_user_indexes
                            WHERE indexrelname = %s
                            AND schemaname = %s
                        """,
                            (index_info["index_name"], index_info["schema_name"]),
                        )

                        stats = detail_cursor.fetchone()
                        if stats:
                            index_info["usage_stats"] = dict(stats)
                except Exception as e:
                    self.logger.warning(
                        f"Could not get usage stats for index {index_info['index_name']}: {e}"
                    )

                indexes.append(index_info)

            return indexes

        except Exception as e:
            self.logger.error(f"Failed to get index analysis: {e}")
            return []

    def _get_constraint_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about all constraints."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        con.conname as constraint_name,
                        con.contype as constraint_type,
                        con.condeferrable as is_deferrable,
                        con.condeferred as is_deferred,
                        con.convalidated as is_validated,
                        pg_get_constraintdef(con.oid) as constraint_definition,
                        CASE WHEN con.contype = 'c' THEN pg_get_constraintdef(con.oid) ELSE NULL END as check_source,
                        array_to_string(con.conkey, ',') as constrained_columns,
                        f_n.nspname as foreign_schema,
                        f_c.relname as foreign_table,
                        array_to_string(con.confkey, ',') as foreign_columns,
                        con.confupdtype as foreign_update_action,
                        con.confdeltype as foreign_delete_action,
                        con.confmatchtype as foreign_match_type
                    FROM pg_constraint con
                    JOIN pg_class c ON con.conrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    LEFT JOIN pg_class f_c ON con.confrelid = f_c.oid
                    LEFT JOIN pg_namespace f_n ON f_c.relnamespace = f_n.oid
                    WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname, con.conname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get constraint analysis: {e}")
            return []

    def _get_function_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about all functions and procedures."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        p.proname as function_name,
                        l.lanname as language,
                        p.pronargs as num_args,
                        pg_get_function_arguments(p.oid) as arguments,
                        pg_get_function_result(p.oid) as return_type,
                        p.provolatile as volatility,
                        p.proisstrict as is_strict,
                        p.prosecdef as is_security_definer,
                        p.proleakproof as is_leakproof,
                        p.procost as estimated_cost,
                        p.prorows as estimated_rows,
                        p.prosrc as source_code,
                        obj_description(p.oid, 'pg_proc') as description,
                        r.rolname as owner
                    FROM pg_proc p
                    JOIN pg_namespace n ON p.pronamespace = n.oid
                    JOIN pg_language l ON p.prolang = l.oid
                    JOIN pg_roles r ON p.proowner = r.oid
                    WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, p.proname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get function analysis: {e}")
            return []

    def _get_trigger_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about all triggers."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        t.tgname as trigger_name,
                        p.proname as function_name,
                        t.tgtype as trigger_type,
                        t.tgenabled as is_enabled,
                        t.tgisinternal as is_internal,
                        pg_get_triggerdef(t.oid) as trigger_definition
                    FROM pg_trigger t
                    JOIN pg_class c ON t.tgrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_proc p ON t.tgfoid = p.oid
                    WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    AND NOT t.tgisinternal
                    ORDER BY n.nspname, c.relname, t.tgname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get trigger analysis: {e}")
            return []

    def _get_view_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about views and materialized views."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as view_name,
                        c.relkind as view_type,
                        r.rolname as owner,
                        pg_get_viewdef(c.oid) as view_definition,
                        obj_description(c.oid, 'pg_class') as description,
                        CASE
                            WHEN c.relkind = 'm' THEN pg_relation_size(c.oid)
                            ELSE NULL
                        END as materialized_size_bytes,
                        CASE
                            WHEN c.relkind = 'm' THEN pg_size_pretty(pg_relation_size(c.oid))
                            ELSE NULL
                        END as materialized_size_human
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_roles r ON c.relowner = r.oid
                    WHERE c.relkind IN ('v', 'm')  -- views and materialized views
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get view analysis: {e}")
            return []

    def _get_sequence_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about sequences."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        schemaname as schema_name,
                        sequencename as sequence_name,
                        sequenceowner as owner,
                        start_value,
                        min_value,
                        max_value,
                        increment_by as increment,
                        cycle as is_cycle,
                        cache_size,
                        last_value,
                        NULL::boolean as is_called
                    FROM pg_sequences
                    WHERE schemaname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY schemaname, sequencename
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get sequence analysis: {e}")
            return []

    def _get_partition_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about partitioned tables and partitions."""
        try:
            # Declarative partitioning added in PostgreSQL 10
            version_num = self._get_server_version_num()
            has_declarative_partitioning = version_num >= 100000

            if not has_declarative_partitioning:
                self.logger.debug(
                    "Skipping partition analysis (requires PostgreSQL 10+)"
                )
                return []

            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        parent_ns.nspname as parent_schema,
                        parent_class.relname as parent_table,
                        child_ns.nspname as partition_schema,
                        child_class.relname as partition_name,
                        pg_get_expr(child_class.relpartbound, child_class.oid) as partition_bound,
                        pg_relation_size(child_class.oid) as partition_size_bytes,
                        pg_size_pretty(pg_relation_size(child_class.oid)) as partition_size_human,
                        child_class.reltuples::bigint as estimated_rows
                    FROM pg_inherits i
                    JOIN pg_class parent_class ON i.inhparent = parent_class.oid
                    JOIN pg_namespace parent_ns ON parent_class.relnamespace = parent_ns.oid
                    JOIN pg_class child_class ON i.inhrelid = child_class.oid
                    JOIN pg_namespace child_ns ON child_class.relnamespace = child_ns.oid
                    WHERE child_class.relispartition = true
                    ORDER BY parent_ns.nspname, parent_class.relname, child_ns.nspname, child_class.relname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get partition analysis: {e}")
            return []

    def _get_inheritance_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about table inheritance."""
        try:
            # relispartition added in PostgreSQL 10
            version_num = self._get_server_version_num()
            has_declarative_partitioning = version_num >= 100000
            is_partition_col = (
                "child_class.relispartition as is_partition"
                if has_declarative_partitioning
                else "false as is_partition"
            )

            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT
                        parent_ns.nspname as parent_schema,
                        parent_class.relname as parent_table,
                        child_ns.nspname as child_schema,
                        child_class.relname as child_table,
                        i.inhseqno as inheritance_sequence,
                        {is_partition_col}
                    FROM pg_inherits i
                    JOIN pg_class parent_class ON i.inhparent = parent_class.oid
                    JOIN pg_namespace parent_ns ON parent_class.relnamespace = parent_ns.oid
                    JOIN pg_class child_class ON i.inhrelid = child_class.oid
                    JOIN pg_namespace child_ns ON child_class.relnamespace = child_ns.oid
                    ORDER BY parent_ns.nspname, parent_class.relname, i.inhseqno
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get inheritance analysis: {e}")
            return []
