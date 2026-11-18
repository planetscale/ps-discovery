"""
PostgreSQL Schema Analysis Module
Analyzes database structure, schemas, tables, and database objects.
"""

from typing import Dict, Any, List, Optional

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer
from planetscale_discovery.database.citus import (
    CitusContext,
    apply_logical_database_size,
    citus_errors,
    function_filter,
    invalid_index_flag,
    namespace_filter,
    namespace_name_filter,
    relation_filter,
    size_pretty,
    where,
)

# Fields of a table entry that hold logical values for a Citus table. The
# coordinator's own values are kept under citus.coordinator.
CITUS_TABLE_FIELDS = (
    "estimated_rows",
    "table_size_bytes",
    "table_size_human",
    "total_size_bytes",
    "total_size_human",
    "statistics",
)


def not_extension_member(catalog: str, oid_expr: str) -> str:
    """SQL condition that the object is not owned by an extension."""
    return (
        "NOT EXISTS ( SELECT 1 FROM pg_depend d"
        f" WHERE d.classid = '{catalog}'::regclass"
        f" AND d.objid = {oid_expr} AND d.deptype = 'e' )"
    )


def qualified_name(name_expr: str, schema_expr: str) -> str:
    """SQL for an object's name, schema-qualified outside pg_catalog.

    Matches the output of format_type and regprocedure under the pinned
    search_path, for objects that have no reg* type on every version.
    """
    return (
        f"CASE WHEN {schema_expr} = 'pg_catalog' THEN quote_ident({name_expr})"
        f" ELSE quote_ident({schema_expr}) || '.' || quote_ident({name_expr}) END"
    )


class SchemaAnalyzer(DatabaseAnalyzer):
    """Analyzes PostgreSQL database schema and structure."""

    def __init__(self, connection, config=None, logger=None):
        super().__init__(connection, config, logger)
        self._version_num = None
        self.citus: Optional[CitusContext] = self.config.get("citus")

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
            "temp_schema_counts": self._get_temp_schema_counts(),
            "table_analysis": self._get_table_analysis(),
            "index_analysis": self._get_index_analysis(),
            "constraint_analysis": self._get_constraint_analysis(),
            "function_analysis": self._get_function_analysis(),
            "operator_analysis": self._get_operator_analysis(),
            "aggregate_analysis": self._get_aggregate_analysis(),
            "cast_analysis": self._get_cast_analysis(),
            "collation_analysis": self._get_collation_analysis(),
            "operator_class_analysis": self._get_operator_class_analysis(),
            "operator_family_analysis": self._get_operator_family_analysis(),
            "role_search_path_analysis": self._get_role_search_path_analysis(),
            "rule_analysis": self._get_rule_analysis(),
            "trigger_analysis": self._get_trigger_analysis(),
            "view_analysis": self._get_view_analysis(),
            "sequence_analysis": self._get_sequence_analysis(),
            "partition_analysis": self._get_partition_analysis(),
            "inheritance_analysis": self._get_inheritance_analysis(),
        }

        if self.citus is not None:
            self._apply_citus(results, self.citus)

        return results

    def _apply_citus(self, results: Dict[str, Any], ctx: CitusContext) -> None:
        """Fill the existing fields of Citus tables with logical values.

        Regular tables keep their values and receive only a table type.
        """
        with citus_errors(
            "Citus table values could not be combined.", "schema analysis"
        ):
            self._apply_citus_values(results, ctx)

    def _apply_citus_values(self, results: Dict[str, Any], ctx: CitusContext) -> None:
        stats = ctx.shard_statistics(self.connection)
        indexes = {
            (info["schema_name"], info["index_name"]): info
            for info in stats.indexes.values()
        }
        sizes = [
            value
            for combined in stats.tables.values()
            for value in (combined["table_size_bytes"], combined["total_size_bytes"])
        ] + [info["index_size_bytes"] for info in stats.indexes.values()]

        for table_info in results["table_analysis"]:
            # By oid: a table renamed between the classification and this
            # listing would otherwise be labeled regular, with its shell's values.
            table = ctx.tables.get(table_info.get("table_oid"))
            if table is None:
                table_info["citus"] = {"table_type": "regular"}
                continue
            combined = stats.tables[table.oid]
            coordinator = {key: table_info.get(key) for key in CITUS_TABLE_FIELDS}
            table_info["estimated_rows"] = combined["estimated_rows"]
            table_info["table_size_bytes"] = combined["table_size_bytes"]
            table_info["total_size_bytes"] = combined["total_size_bytes"]
            table_info["statistics"] = combined["statistics"]
            table_info["citus"] = {
                **table.describe(),
                "placements_per_shard": combined["placements_per_shard"],
                "source": "shards",
                "coordinator": coordinator,
                "shard_statistics": {
                    "shards_read": combined["shards_read"],
                    "shards_never_analyzed": combined["shards_never_analyzed"],
                    "inactive_placements": combined["inactive_placements"],
                    "placement_size_bytes": combined["placement_size_bytes"],
                    "columns": combined["columns"],
                },
            }

        pretty = size_pretty(self.connection, sizes)

        for table_info in results["table_analysis"]:
            if table_info["citus"].get("source") == "shards":
                table_info["table_size_human"] = pretty.get(
                    table_info["table_size_bytes"]
                )
                table_info["total_size_human"] = pretty.get(
                    table_info["total_size_bytes"]
                )

        apply_logical_database_size(
            self.connection, ctx, results["database_catalog"], "database_name"
        )

        for index_info in results["index_analysis"]:
            info = indexes.get((index_info["schema_name"], index_info["index_name"]))
            if info is None:
                continue
            index_info["citus"] = {
                "source": "shards",
                "shards_read": info["shards_read"],
                **invalid_index_flag(info),
                "coordinator": {
                    key: index_info.get(key)
                    for key in (
                        "estimated_rows",
                        "index_size_bytes",
                        "index_size_human",
                        "usage_stats",
                    )
                },
            }
            index_info["estimated_rows"] = info["estimated_rows"]
            index_info["index_size_bytes"] = info["index_size_bytes"]
            index_info["index_size_human"] = pretty.get(info["index_size_bytes"])
            # PostgreSQL keeps no usage counters for a partitioned index, so a
            # Citus parent's index has no usage_stats, as a regular one has none.
            if info["idx_scan"] is not None:
                index_info["usage_stats"] = {
                    "idx_scan": info["idx_scan"],
                    "idx_tup_read": info["idx_tup_read"],
                    "idx_tup_fetch": info["idx_tup_fetch"],
                }

        for partition in results["partition_analysis"]:
            table = ctx.table_by_name(
                partition["partition_schema"], partition["partition_name"]
            )
            if table is None:
                continue
            combined = stats.tables[table.oid]
            partition["citus"] = {
                "source": "shards",
                "coordinator": {
                    key: partition.get(key)
                    for key in (
                        "partition_size_bytes",
                        "partition_size_human",
                        "estimated_rows",
                    )
                },
            }
            partition["partition_size_bytes"] = combined["table_size_bytes"]
            partition["partition_size_human"] = pretty.get(combined["table_size_bytes"])
            partition["estimated_rows"] = combined["estimated_rows"]

    def _get_temp_schema_counts(self) -> Dict[str, int]:
        """Count per-backend temp namespaces. Not migration-relevant; surfaced
        only as a workload signal (high counts indicate unpooled connection
        churn with temp-table usage)."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE nspname LIKE 'pg_temp_%') AS pg_temp_count,
                        COUNT(*) FILTER (WHERE nspname LIKE 'pg_toast_temp_%') AS pg_toast_temp_count
                    FROM pg_namespace
                """)
                row = cursor.fetchone()
            return {
                "pg_temp_count": int(row["pg_temp_count"]),
                "pg_toast_temp_count": int(row["pg_toast_temp_count"]),
            }
        except Exception as e:
            self.logger.warning(f"Could not count temp schemas: {e}")
            return {"pg_temp_count": 0, "pg_toast_temp_count": 0}

    def _get_database_catalog(self) -> List[Dict[str, Any]]:
        """Get information about all databases."""
        try:
            # Always exclude internal system databases; users can add more via config.
            excluded = ["template0", "template1", "rdsadmin"] + list(
                self.config.get("excluded_databases", [])
            )
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
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
                    WHERE NOT (d.datname = ANY(%s))
                    ORDER BY d.datname
                    """,
                    (excluded,),
                )

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
                # Exclude per-backend temp namespaces (pg_temp_N / pg_toast_temp_N).
                # They have no migration value and can number in the tens of
                # thousands on databases with high max_connections + unpooled
                # workloads — see _get_temp_schema_counts for a count signal.
                cursor.execute(f"""
                    SELECT
                        n.nspname as schema_name,
                        r.rolname as owner,
                        n.oid as schema_oid,
                        obj_description(n.oid, 'pg_namespace') as description,
                        CASE WHEN n.nspname LIKE 'pg_%' OR n.nspname = 'information_schema'
                             THEN true ELSE false END as is_system_schema
                    FROM pg_namespace n
                    JOIN pg_roles r ON n.nspowner = r.oid
                    WHERE n.nspname NOT LIKE 'pg_temp_%'
                      AND n.nspname NOT LIKE 'pg_toast_temp_%'
                      {namespace_filter(self.citus, "n.oid")}
                    ORDER BY is_system_schema, n.nspname
                """)

                rows = list(cursor.fetchall())

            # Pre-flight: the per-schema loop below does 2 sub-queries per row.
            # Bail out instead of running for hours on a pathologically large
            # catalog.
            schema_count = len(rows)
            if schema_count > 10000:
                self.logger.error(
                    f"Aborting schema inventory: {schema_count} non-temp schemas "
                    "exceeds safe limit (10000). Investigate catalog bloat."
                )
                return []
            if schema_count > 1000:
                self.logger.warning(
                    f"Schema inventory will iterate {schema_count} schemas — "
                    "this may take several minutes."
                )

            schemas = []
            for idx, row in enumerate(rows):
                if schema_count > 100 and idx > 0 and idx % 100 == 0:
                    self.logger.info(f"Schema inventory progress: {idx}/{schema_count}")
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
            identity_col = (
                "a.attidentity" if has_declarative_partitioning else "''"
            ) + " as identity_kind"
            generated_attr = "a.attgenerated" if version_num >= 120000 else "''"
            generated_col = generated_attr + " as generated_kind"

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
                    {relation_filter(self.citus, "c.oid", "n.oid")}
                    ORDER BY n.nspname, c.relname
                """)

                rows = list(cursor.fetchall())

            tables = []
            for row in rows:
                table_info = dict(row)

                # Get column information.
                # default_sequence is the sequence that the column default
                # calls when the default is one nextval() call and nothing
                # else. A generated column keeps its expression in the
                # default slot, so the expression goes to generated_expression
                # and never to default_expression.
                try:
                    with self.connection.cursor() as detail_cursor:
                        detail_cursor.execute(
                            f"""
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
                                a.attcollation as collation_oid,
                                {identity_col},
                                {generated_col},
                                (
                                    SELECT dep.objid::regclass::text
                                    FROM pg_depend dep
                                    JOIN pg_class seq ON seq.oid = dep.objid AND seq.relkind = 'S'
                                    WHERE dep.classid = 'pg_class'::regclass
                                    AND dep.refclassid = 'pg_class'::regclass
                                    AND dep.refobjid = a.attrelid
                                    AND dep.refobjsubid = a.attnum
                                    AND dep.deptype IN ('a', 'i')
                                    LIMIT 1
                                ) as owned_sequence,
                                (
                                    SELECT dep.refobjid::regclass::text
                                    FROM pg_depend dep
                                    JOIN pg_class seq ON seq.oid = dep.refobjid AND seq.relkind = 'S'
                                    WHERE dep.classid = 'pg_attrdef'::regclass
                                    AND dep.objid = d.oid
                                    AND dep.refclassid = 'pg_class'::regclass
                                    AND pg_get_expr(d.adbin, d.adrelid)
                                        ~ '^nextval\\(''(?:[^'']|'''')+''::regclass\\)$'
                                    LIMIT 1
                                ) as default_sequence,
                                CASE WHEN {generated_attr} = ''
                                    THEN pg_get_expr(d.adbin, d.adrelid)
                                END as default_expression,
                                CASE WHEN {generated_attr} <> ''
                                    THEN pg_get_expr(d.adbin, d.adrelid)
                                END as generated_expression
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
                                null_frac,
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
                cursor.execute(f"""
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
                    {relation_filter(self.citus, "t.oid", "n.oid")}
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
                cursor.execute(f"""
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
                    {relation_filter(self.citus, "con.conrelid", "n.oid")}
                    ORDER BY n.nspname, c.relname, con.conname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get constraint analysis: {e}")
            return []

    def _get_function_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about all functions and procedures."""
        try:
            # prokind replaced proisagg and proiswindow in PostgreSQL 11
            kind = (
                "p.prokind"
                if self._get_server_version_num() >= 110000
                else "CASE WHEN p.proisagg THEN 'a'"
                " WHEN p.proiswindow THEN 'w' ELSE 'f' END"
            )
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT
                        n.nspname as schema_name,
                        p.proname as function_name,
                        {kind} as kind,
                        l.lanname as language,
                        p.pronargs as num_args,
                        pg_get_function_arguments(p.oid) as arguments,
                        pg_get_function_result(p.oid) as return_type,
                        p.provolatile as volatility,
                        p.proisstrict as is_strict,
                        p.prosecdef as is_security_definer,
                        p.proleakproof as is_leakproof,
                        p.proparallel as parallel,
                        (
                            SELECT substr(setting, 13)
                            FROM unnest(p.proconfig) as setting
                            WHERE setting LIKE 'search_path=%'
                        ) as search_path,
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
                    {function_filter(self.citus, "p.oid", "n.oid")}
                    ORDER BY n.nspname, p.proname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get function analysis: {e}")
            return []

    def _get_operator_analysis(self) -> List[Dict[str, Any]]:
        """Get the user-defined operators, leaving out those of extensions."""
        try:
            with self.connection.cursor() as cursor:
                # oprleft is 0 for a prefix operator and oprright for a postfix
                # one, where format_type would return '-'.
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        o.oprname as operator_name,
                        CASE WHEN o.oprleft <> 0
                            THEN format_type(o.oprleft, NULL) END as left_type,
                        CASE WHEN o.oprright <> 0
                            THEN format_type(o.oprright, NULL) END as right_type,
                        format_type(o.oprresult, NULL) as result_type,
                        o.oprcode::oid::regprocedure::text as "function",
                        NULLIF(o.oprcom, 0)::regoperator::text as commutator,
                        NULLIF(o.oprnegate, 0)::regoperator::text as negator,
                        NULLIF(o.oprrest::oid, 0)::regproc::text as "restrict",
                        NULLIF(o.oprjoin::oid, 0)::regproc::text as "join",
                        o.oprcanhash as hashes,
                        o.oprcanmerge as merges
                    FROM pg_operator o
                    JOIN pg_namespace n ON o.oprnamespace = n.oid
                    WHERE n.nspname NOT IN ('information_schema', 'pg_catalog')
                    AND NOT EXISTS (
                        SELECT 1 FROM pg_depend d
                        WHERE d.classid = 'pg_operator'::regclass
                        AND d.objid = o.oid
                        AND d.deptype = 'e'
                    )
                    ORDER BY n.nspname, o.oprname, left_type, right_type
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.add_error("Failed to get operator analysis", e)
            return []

    def _rows(self, section: str, sql: str) -> List[Dict[str, Any]]:
        """Run one section's query, recording a failure as an error."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.add_error(f"Failed to get {section}", e)
            return []

    def _get_aggregate_analysis(self) -> List[Dict[str, Any]]:
        """Get the user-defined aggregates, which pg_proc alone cannot recreate."""
        modify = self._get_server_version_num() >= 110000
        final_modify = "a.aggfinalmodify" if modify else "NULL"
        moving_final_modify = "a.aggmfinalmodify" if modify else "NULL"
        return self._rows(
            "aggregate analysis",
            f"""
            SELECT
                n.nspname as schema_name,
                p.proname as aggregate_name,
                pg_get_function_arguments(p.oid) as arguments,
                pg_get_function_result(p.oid) as result_type,
                a.aggkind as kind,
                a.aggnumdirectargs as num_direct_args,
                a.aggtransfn::oid::regprocedure::text as transition_function,
                format_type(a.aggtranstype, NULL) as state_type,
                a.agginitval as initial_value,
                NULLIF(a.aggfinalfn::oid, 0)::regprocedure::text as final_function,
                a.aggfinalextra as final_extra,
                {final_modify}::text as final_modify,
                NULLIF(a.aggcombinefn::oid, 0)::regprocedure::text as combine_function,
                NULLIF(a.aggserialfn::oid, 0)::regprocedure::text as serial_function,
                NULLIF(a.aggdeserialfn::oid, 0)::regprocedure::text
                    as deserial_function,
                NULLIF(a.aggmtransfn::oid, 0)::regprocedure::text
                    as moving_transition_function,
                NULLIF(a.aggminvtransfn::oid, 0)::regprocedure::text
                    as moving_inverse_function,
                CASE WHEN a.aggmtranstype <> 0
                    THEN format_type(a.aggmtranstype, NULL) END as moving_state_type,
                a.aggminitval as moving_initial_value,
                NULLIF(a.aggmfinalfn::oid, 0)::regprocedure::text
                    as moving_final_function,
                a.aggmfinalextra as moving_final_extra,
                {moving_final_modify}::text as moving_final_modify,
                NULLIF(a.aggsortop, 0)::regoperator::text as sort_operator,
                p.proparallel as parallel
            FROM pg_aggregate a
            JOIN pg_proc p ON a.aggfnoid = p.oid
            JOIN pg_namespace n ON p.pronamespace = n.oid
            WHERE n.nspname NOT IN ('information_schema', 'pg_catalog')
            AND {not_extension_member("pg_proc", "p.oid")}
            ORDER BY n.nspname, p.proname, arguments
        """,
        )

    def _get_cast_analysis(self) -> List[Dict[str, Any]]:
        """Get the user-defined casts, which change how operators resolve."""
        # 16384 is FirstNormalObjectId: every cast below it is built in. An
        # internal dependency marks a cast CREATE TYPE made, such as a range
        # type's cast to its multirange, which recreating the type recreates.
        return self._rows(
            "cast analysis",
            """
            SELECT
                format_type(c.castsource, NULL) as source_type,
                format_type(c.casttarget, NULL) as target_type,
                NULLIF(c.castfunc, 0)::regprocedure::text as "function",
                c.castcontext as context,
                c.castmethod as method
            FROM pg_cast c
            WHERE c.oid >= 16384
            AND NOT EXISTS ( SELECT 1 FROM pg_depend d
                WHERE d.classid = 'pg_cast'::regclass
                AND d.objid = c.oid AND d.deptype IN ('e', 'i') )
            ORDER BY source_type, target_type
        """,
        )

    def _get_collation_analysis(self) -> List[Dict[str, Any]]:
        """Get the user-defined collations."""
        version = self._get_server_version_num()
        provider = "c.collprovider" if version >= 100000 else "'c'"
        deterministic = "c.collisdeterministic" if version >= 120000 else "true"
        # The ICU locale moved out of collcollate in PostgreSQL 15.
        if version >= 170000:
            locale = "c.colllocale"
        elif version >= 150000:
            locale = "c.colliculocale"
        else:
            locale = "NULL"
        icu_rules = "c.collicurules" if version >= 160000 else "NULL"
        return self._rows(
            "collation analysis",
            f"""
            SELECT
                n.nspname as schema_name,
                c.collname as collation_name,
                {provider} as provider,
                {deterministic} as deterministic,
                c.collcollate as lc_collate,
                c.collctype as lc_ctype,
                {locale}::text as locale,
                {icu_rules}::text as icu_rules
            FROM pg_collation c
            JOIN pg_namespace n ON c.collnamespace = n.oid
            WHERE n.nspname NOT IN ('information_schema', 'pg_catalog')
            AND {not_extension_member("pg_collation", "c.oid")}
            ORDER BY n.nspname, c.collname
        """,
        )

    @staticmethod
    def _opclass_members(owner_catalog: str, owner_oid: str, family_oid: str) -> str:
        """SQL for the operators and support functions bound to a class or family.

        A class's own members depend on the class; members added to a family
        with ALTER OPERATOR FAMILY depend on the family. A FOR ORDER BY member
        also depends on its sort family, so its own family must match too.
        """
        sort_family = qualified_name("sf.opfname", "sfn.nspname")
        return f"""
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'strategy', ao.amopstrategy,
                    'operator', ao.amopopr::regoperator::text,
                    'purpose', ao.amoppurpose,
                    'sort_family', (
                        SELECT {sort_family}
                        FROM pg_opfamily sf
                        JOIN pg_namespace sfn ON sf.opfnamespace = sfn.oid
                        WHERE sf.oid = ao.amopsortfamily
                    )
                ) ORDER BY ao.amopstrategy)
                FROM pg_amop ao
                JOIN pg_depend dep ON dep.classid = 'pg_amop'::regclass
                    AND dep.objid = ao.oid
                WHERE dep.refclassid = '{owner_catalog}'::regclass
                AND dep.refobjid = {owner_oid}
                AND ao.amopfamily = {family_oid}
            ), '[]') as operators,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'number', ap.amprocnum,
                    'function', ap.amproc::oid::regprocedure::text,
                    'left_type', format_type(ap.amproclefttype, NULL),
                    'right_type', format_type(ap.amprocrighttype, NULL)
                ) ORDER BY ap.amprocnum)
                FROM pg_amproc ap
                JOIN pg_depend dep ON dep.classid = 'pg_amproc'::regclass
                    AND dep.objid = ap.oid
                WHERE dep.refclassid = '{owner_catalog}'::regclass
                AND dep.refobjid = {owner_oid}
                AND ap.amprocfamily = {family_oid}
            ), '[]') as functions"""

    def _get_operator_class_analysis(self) -> List[Dict[str, Any]]:
        """Get the user-defined operator classes, which an index can name."""
        return self._rows(
            "operator class analysis",
            f"""
            SELECT
                n.nspname as schema_name,
                oc.opcname as class_name,
                am.amname as access_method,
                format_type(oc.opcintype, NULL) as input_type,
                oc.opcdefault as is_default,
                {qualified_name("f.opfname", "fn.nspname")} as family,
                CASE WHEN oc.opckeytype <> 0
                    THEN format_type(oc.opckeytype, NULL) END as storage_type,
                {self._opclass_members("pg_opclass", "oc.oid", "oc.opcfamily")}
            FROM pg_opclass oc
            JOIN pg_namespace n ON oc.opcnamespace = n.oid
            JOIN pg_am am ON oc.opcmethod = am.oid
            JOIN pg_opfamily f ON oc.opcfamily = f.oid
            JOIN pg_namespace fn ON f.opfnamespace = fn.oid
            WHERE n.nspname NOT IN ('information_schema', 'pg_catalog')
            AND {not_extension_member("pg_opclass", "oc.oid")}
            ORDER BY n.nspname, oc.opcname, am.amname
        """,
        )

    def _get_operator_family_analysis(self) -> List[Dict[str, Any]]:
        """Get the user-defined operator families and their loose members."""
        return self._rows(
            "operator family analysis",
            f"""
            SELECT
                n.nspname as schema_name,
                f.opfname as family_name,
                am.amname as access_method,
                {self._opclass_members("pg_opfamily", "f.oid", "f.oid")}
            FROM pg_opfamily f
            JOIN pg_namespace n ON f.opfnamespace = n.oid
            JOIN pg_am am ON f.opfmethod = am.oid
            WHERE n.nspname NOT IN ('information_schema', 'pg_catalog')
            AND {not_extension_member("pg_opfamily", "f.oid")}
            ORDER BY n.nspname, f.opfname, am.amname
        """,
        )

    def _get_role_search_path_analysis(self) -> List[Dict[str, Any]]:
        """Get each role's search_path, which resolves the names it queries.

        Only search_path is read: a role setting can hold any value, a secret
        included. database_name is null for a setting that applies everywhere.
        """
        return self._rows(
            "role search_path analysis",
            """
            SELECT
                r.rolname as role_name,
                db.datname as database_name,
                substr(s.setting, 13) as search_path
            FROM pg_db_role_setting rs
            JOIN pg_roles r ON rs.setrole = r.oid
            LEFT JOIN pg_database db ON rs.setdatabase = db.oid
            CROSS JOIN LATERAL unnest(rs.setconfig) as s(setting)
            WHERE s.setting LIKE 'search_path=%'
            AND (rs.setdatabase = 0 OR db.datname = current_database())
            ORDER BY r.rolname, db.datname NULLS FIRST
        """,
        )

    def _get_rule_analysis(self) -> List[Dict[str, Any]]:
        """Get the rules on tables, which can send a statement elsewhere.

        A view's own _RETURN rule is its definition, already in view_analysis.
        """
        return self._rows(
            "rule analysis",
            f"""
            SELECT
                n.nspname as schema_name,
                c.relname as table_name,
                r.rulename as rule_name,
                r.ev_type as event,
                r.is_instead,
                r.ev_enabled as enabled,
                pg_get_ruledef(r.oid) as definition
            FROM pg_rewrite r
            JOIN pg_class c ON r.ev_class = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE r.rulename <> '_RETURN'
            AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
            {relation_filter(self.citus, "c.oid", "n.oid")}
            ORDER BY n.nspname, c.relname, r.rulename
        """,
        )

    def _get_trigger_analysis(self) -> List[Dict[str, Any]]:
        """Get detailed information about all triggers."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
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
                    {relation_filter(self.citus, "c.oid", "n.oid")}
                    {function_filter(self.citus, "p.oid", "p.pronamespace")}
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
                cursor.execute(f"""
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
                    {relation_filter(self.citus, "c.oid", "n.oid")}
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
                cursor.execute(f"""
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
                    {namespace_name_filter(self.citus, "schemaname")}
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
                cursor.execute(f"""
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
                    JOIN pg_class parent_class ON i.inhparent = parent_class.oid AND parent_class.relkind = 'p'
                    JOIN pg_namespace parent_ns ON parent_class.relnamespace = parent_ns.oid
                    JOIN pg_class child_class ON i.inhrelid = child_class.oid
                    JOIN pg_namespace child_ns ON child_class.relnamespace = child_ns.oid
                    WHERE child_class.relispartition = true
                    {relation_filter(self.citus, "child_class.oid", "child_ns.oid")}
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
            inheritance_filter = where(
                relation_filter(self.citus, "child_class.oid", "child_ns.oid")
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
                    {inheritance_filter}
                    ORDER BY parent_ns.nspname, parent_class.relname, i.inhseqno
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get inheritance analysis: {e}")
            return []
