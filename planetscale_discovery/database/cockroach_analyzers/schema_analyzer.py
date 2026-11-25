"""CockroachDB schema analysis.

Overrides the PostgreSQL size and inventory queries that fail or lie on
CockroachDB (pg_database_size, pg_relation_size, pg_get_triggerdef,
crdb_internal counted as user objects). Constraints, functions and
sequences stay on the PostgreSQL path because those catalogs work.
"""

from typing import Any, Dict, List, Optional

from planetscale_discovery.database.analyzers.schema_analyzer import SchemaAnalyzer
from planetscale_discovery.database.cockroach import (
    SYSTEM_DATABASES,
    SYSTEM_SCHEMAS,
    is_system_database,
    quote_ident,
    user_database_sql,
)
from planetscale_discovery.database.cockroach_compat import (
    COPY_PATH,
    classify_indexdef,
)

_SYSTEM_SCHEMA_SQL = "(" + ", ".join(f"'{s}'" for s in SYSTEM_SCHEMAS) + ")"


class CockroachSchemaAnalyzer(SchemaAnalyzer):
    """Schema analyzer that sizes from tenant_span_stats.live_bytes."""

    _user_db_cache: Optional[List[str]] = None
    _cluster_creates_cache: Optional[List[Dict[str, Any]]] = None
    _sequence_cache: Optional[List[Dict[str, Any]]] = None

    def analyze(self) -> Dict[str, Any]:
        results = {
            "database_catalog": self._get_database_catalog(),
            "schema_inventory": self._get_schema_inventory(),
            "temp_schema_counts": self._get_temp_schema_counts(),
            "table_analysis": self._get_table_analysis(),
            "index_analysis": self._get_index_analysis(),
            "constraint_analysis": self._get_constraint_analysis(),
            "function_analysis": self._get_function_analysis(),
            "procedure_analysis": self._get_procedure_analysis(),
            "trigger_analysis": self._get_trigger_analysis(),
            "view_analysis": self._get_view_analysis(),
            "sequence_analysis": self._get_sequence_analysis(),
            "partition_analysis": self._get_partition_analysis(),
            "inheritance_analysis": self._get_inheritance_analysis(),
            "size_semantics": {
                "logical_bytes_field": "total_size_bytes",
                "meaning": (
                    "crdb_internal.tenant_span_stats total_stats.live_bytes: "
                    "one replica, uncompressed, includes indexes"
                ),
            },
        }
        results["schema_source"] = {
            "pg_dump": "not working",
            "pgcopydb": "not working",
            "method": (
                "SHOW CREATE ALL TYPES + SHOW CREATE ALL TABLES + "
                "SHOW TRIGGERS / SHOW CREATE TRIGGER "
                "+ information_schema.routines"
            ),
            "note": COPY_PATH["schema_source"]["note"],
            "copy": COPY_PATH["migration_path"]["note"],
            "show_create_all_types": self._show_create_all_types(),
            "show_create_all_tables": self._show_create_all_tables(),
            "routines": self._get_routines(),
        }
        if not results["table_analysis"]:
            self.add_error(
                "Table inventory is empty after CockroachDB-native sizing. "
                "Sizes and tables could not be read."
            )
        return results

    def _pretty_bytes(self, value) -> str:
        try:
            n = int(value or 0)
        except (TypeError, ValueError):
            n = 0
        if n < 1024:
            return f"{n} B"
        if n < 1024**2:
            return f"{n / 1024:.1f} KiB"
        if n < 1024**3:
            return f"{n / 1024**2:.1f} MiB"
        return f"{n / 1024**3:.2f} GiB"

    def _user_databases(self) -> List[str]:
        if getattr(self, "_user_db_cache", None) is not None:
            return self._user_db_cache
        extra = set(self.config.get("excluded_databases") or [])
        current = self._current_database()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SHOW DATABASES")
                names = []
                for row in cursor.fetchall():
                    name = row.get("database_name")
                    if not isinstance(name, str):
                        continue
                    if (
                        name
                        and not is_system_database(name, current)
                        and name not in extra
                    ):
                        names.append(name)
                self._user_db_cache = names
                return names
        except Exception as exc:
            self.add_warning(f"SHOW DATABASES failed: {exc}")
            self._user_db_cache = []
            return []

    def _cluster_table_creates(self) -> List[Dict[str, Any]]:
        """create_statements is per-database. Read the connected database only."""
        if getattr(self, "_cluster_creates_cache", None) is not None:
            return self._cluster_creates_cache
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT database_name, schema_name, descriptor_name,
                           descriptor_type, create_statement
                    FROM crdb_internal.create_statements
                    WHERE descriptor_type = 'table'
                      AND schema_name NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND {user_database_sql('database_name')}
                      AND database_name = current_database()
                    """)
                out = [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            self.add_warning(f"Couldn't read create_statements: {exc}")
            out = []
        self._cluster_creates_cache = out
        return out

    def _base_table_keys(self) -> set:
        return {
            (c.get("database_name"), c.get("schema_name"), c.get("descriptor_name"))
            for c in self._cluster_table_creates()
        }

    def _current_database(self) -> Optional[str]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT current_database() AS current_database")
                row = cursor.fetchone() or {}
            if not isinstance(row, dict):
                return None
            value = row.get("current_database")
            return value if isinstance(value, str) and value else None
        except Exception as exc:
            self.add_warning(
                f"current_database() failed ({exc}); "
                "defaultdb and postgres will be skipped even if this session "
                "is connected to one of them."
            )
            return None

    def _span_stats_by_table(
        self, current_db_only: bool = False
    ) -> List[Dict[str, Any]]:
        current_filter = (
            "AND t.database_name = current_database()" if current_db_only else ""
        )
        sql = f"""
            SELECT
                t.database_name,
                t.schema_name,
                t.name AS table_name,
                t.table_id,
                t.locality,
                rs.estimated_row_count,
                (s.stats->'total_stats'->>'live_bytes')::INT8 AS logical_live_bytes,
                (s.stats->'approximate_total_stats'->>'live_bytes')::INT8
                    AS live_bytes_all_replicas,
                (s.stats->>'approximate_disk_bytes')::INT8 AS disk_bytes_all_replicas,
                (s.stats->>'range_count')::INT AS range_count,
                (s.stats->>'replica_count')::INT AS replica_count
            FROM crdb_internal.tables AS t
            JOIN crdb_internal.table_spans AS ts
              ON ts.descriptor_id = t.table_id AND NOT ts.dropped
            LEFT JOIN crdb_internal.table_row_statistics AS rs
              ON rs.table_id = t.table_id
            , LATERAL crdb_internal.tenant_span_stats(
                ARRAY[(ts.start_key, ts.end_key)]
            ) AS s
            WHERE {user_database_sql('t.database_name')}
              AND t.schema_name NOT IN {_SYSTEM_SCHEMA_SQL}
              AND t.drop_time IS NULL
              {current_filter}
            ORDER BY (s.stats->'total_stats'->>'live_bytes')::INT8 DESC
        """
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            rows = [dict(row) for row in cursor.fetchall()]
        allowed = self._base_table_keys()
        if not allowed:
            return rows
        return [
            row
            for row in rows
            if (row.get("database_name"), row.get("schema_name"), row.get("table_name"))
            in allowed
        ]

    def _get_database_catalog(self) -> List[Dict[str, Any]]:
        """Catalog the connected database with logical live_bytes."""
        current = self._current_database()
        excluded = [
            db for db in SYSTEM_DATABASES if is_system_database(db, current)
        ] + list(self.config.get("excluded_databases", []))
        try:
            sizes: Dict[str, int] = {}
            replicas: Dict[str, Any] = {}
            size_read_status = "ok"
            size_read_error = None
            try:
                for row in self._span_stats_by_table(current_db_only=True):
                    db = row["database_name"]
                    sizes[db] = sizes.get(db, 0) + int(
                        row.get("logical_live_bytes") or 0
                    )
                    replicas[db] = row.get("replica_count")
            except Exception as exc:
                size_read_status = "couldn't read"
                size_read_error = str(exc)
                self.add_error(
                    "CockroachDB tenant_span_stats catalog sizing failed", exc
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
                        r.rolname as owner,
                        t.spcname as tablespace
                    FROM pg_database d
                    JOIN pg_roles r ON d.datdba = r.oid
                    LEFT JOIN pg_tablespace t ON d.dattablespace = t.oid
                    WHERE NOT (d.datname = ANY(%s))
                      AND d.datname = current_database()
                    ORDER BY d.datname
                    """,
                    (excluded,),
                )
                rows = [dict(row) for row in cursor.fetchall()]

            catalogs = []
            for row in rows:
                name = row["database_name"]
                logical = sizes.get(name, 0)
                row["size_bytes"] = logical
                row["size_human"] = self._pretty_bytes(logical)
                row["size_kind"] = "logical_live_bytes_1copy"
                row["replica_count"] = replicas.get(name)
                row["read_status"] = size_read_status
                if size_read_error:
                    row["read_error"] = size_read_error
                row["database_settings"] = []
                catalogs.append(row)
            return catalogs
        except Exception as exc:
            self.add_error("Failed to get CockroachDB database catalog", exc)
            return []

    def _get_schema_inventory(self) -> List[Dict[str, Any]]:
        """User schemas only. Skip information_schema.schema_privileges
        (CockroachDB has no schema_name column there)."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT
                        n.nspname as schema_name,
                        r.rolname as owner,
                        n.oid as schema_oid,
                        obj_description(n.oid, 'pg_namespace') as description,
                        false as is_system_schema
                    FROM pg_namespace n
                    JOIN pg_roles r ON n.nspowner = r.oid
                    WHERE n.nspname NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND n.nspname NOT LIKE 'pg_temp_%'
                      AND n.nspname NOT LIKE 'pg_toast_temp_%'
                      AND n.nspname NOT LIKE 'pg_%'
                    ORDER BY n.nspname
                    """)
                rows = [dict(row) for row in cursor.fetchall()]

            schemas = []
            for row in rows:
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
                            (row["schema_oid"],),
                        )
                        row["object_counts"] = dict(detail_cursor.fetchone() or {})
                except Exception as exc:
                    row["object_counts"] = {}
                    self.add_warning(
                        f"Could not count objects in schema {row['schema_name']}: {exc}"
                    )
                row["privileges"] = []
                schemas.append(row)
            return schemas
        except Exception as exc:
            self.add_error("Failed to get CockroachDB schema inventory", exc)
            return []

    def _table_columns(self, schema_name: str, table_name: str) -> List[Dict[str, Any]]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        a.attname as column_name,
                        a.attnum as column_number,
                        pg_catalog.format_type(a.atttypid, a.atttypmod) as data_type,
                        a.attnotnull as not_null,
                        a.atthasdef as has_default,
                        CASE WHEN d.adbin IS NOT NULL THEN true ELSE false END
                            as has_default_value,
                        pg_get_expr(d.adbin, d.adrelid) as column_default,
                        col_description(a.attrelid, a.attnum) as description,
                        a.attisdropped as is_dropped
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    LEFT JOIN pg_attrdef d
                      ON a.attrelid = d.adrelid AND a.attnum = d.adnum
                    WHERE n.nspname = %s
                      AND c.relname = %s
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    ORDER BY a.attnum
                    """,
                    (schema_name, table_name),
                )
                return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            self.add_warning(
                f"Could not read columns for {schema_name}.{table_name}: {exc}"
            )
            return []

    def _create_statement(
        self, schema_name: str, table_name: str, database_name: str = None
    ) -> str:
        for row in self._cluster_table_creates():
            if row.get("schema_name") != schema_name:
                continue
            if row.get("descriptor_name") != table_name:
                continue
            if database_name and row.get("database_name") != database_name:
                continue
            return row.get("create_statement") or ""
        return ""

    def _get_table_analysis(self) -> List[Dict[str, Any]]:
        try:
            # A report is for the connected database. Catalog and
            # table_analysis both stay on current_database().
            stats = self._span_stats_by_table(current_db_only=True)
        except Exception as exc:
            self.add_error("CockroachDB table sizing failed (tenant_span_stats)", exc)
            return []

        current = self._current_database()
        if current:
            stats = [
                row for row in stats if row.get("database_name") in (None, current)
            ]

        tables = []
        for row in stats:
            schema_name = row["schema_name"]
            table_name = row["table_name"]
            logical = int(row.get("logical_live_bytes") or 0)
            rows_est = row.get("estimated_row_count")
            try:
                estimated_rows = int(rows_est) if rows_est is not None else -1
            except (TypeError, ValueError):
                estimated_rows = -1

            create_sql = self._create_statement(
                schema_name, table_name, row.get("database_name")
            )
            columns = self._table_columns(schema_name, table_name)
            uses_unique_rowid = (
                any(
                    "unique_rowid()" in str(col.get("column_default") or "").lower()
                    for col in columns
                )
                or "unique_rowid()" in create_sql.lower()
            )

            tables.append(
                {
                    "database_name": row.get("database_name"),
                    "schema_name": schema_name,
                    "table_name": table_name,
                    "table_oid": row.get("table_id"),
                    "owner": None,
                    "table_type": "r",
                    "persistence": "p",
                    "is_partition": False,
                    "has_indexes": True,
                    "has_rules": False,
                    "has_triggers": False,
                    "has_inheritance": False,
                    "estimated_rows": estimated_rows,
                    "table_size_bytes": logical,
                    "table_size_human": self._pretty_bytes(logical),
                    "total_size_bytes": logical,
                    "total_size_human": self._pretty_bytes(logical),
                    "size_kind": "logical_live_bytes_1copy",
                    "replicated_live_bytes": int(
                        row.get("live_bytes_all_replicas") or 0
                    ),
                    "disk_bytes_all_replicas": int(
                        row.get("disk_bytes_all_replicas") or 0
                    ),
                    "range_count": row.get("range_count"),
                    "replica_count": row.get("replica_count"),
                    "locality": row.get("locality"),
                    "uses_unique_rowid": uses_unique_rowid,
                    "regional_by_row": bool(
                        row.get("locality")
                        and "regional by row" in str(row.get("locality")).lower()
                    ),
                    "has_column_families": "FAMILY" in create_sql.upper(),
                    "create_statement": create_sql or None,
                    "columns": columns,
                    "statistics": [],
                    "description": None,
                }
            )
        return tables

    def _index_definition(self, schema_name, table_name, index_name) -> str:
        cache = getattr(self, "_pg_indexdef_cache", None)
        if cache is None:
            cache = {}
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(f"""
                        SELECT schemaname, tablename, indexname, indexdef
                        FROM pg_indexes
                        WHERE schemaname NOT IN {_SYSTEM_SCHEMA_SQL}
                        """)
                    for row in cursor.fetchall():
                        cache[
                            (row["schemaname"], row["tablename"], row["indexname"])
                        ] = (row["indexdef"] or "")
            except Exception as exc:
                self.add_warning(f"pg_indexes.indexdef unavailable: {exc}")
            self._pg_indexdef_cache = cache
        return cache.get((schema_name, table_name, index_name), "")

    def _index_span_bytes(self) -> Dict[tuple, int]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        isp.descriptor_id,
                        isp.index_id,
                        (s.stats->'total_stats'->>'live_bytes')::INT8 AS logical_live_bytes
                    FROM crdb_internal.index_spans AS isp
                    , LATERAL crdb_internal.tenant_span_stats(
                        ARRAY[(isp.start_key, isp.end_key)]
                    ) AS s
                    """)
                out = {}
                for row in cursor.fetchall():
                    out[(row["descriptor_id"], row["index_id"])] = int(
                        row.get("logical_live_bytes") or 0
                    )
                return out
        except Exception as exc:
            self.add_warning(
                f"Per-index tenant_span_stats unavailable; index sizes will be 0: {exc}"
            )
            return {}

    def _get_index_analysis(self) -> List[Dict[str, Any]]:
        try:
            sizes = self._index_span_bytes()
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT
                        ti.descriptor_id,
                        ti.descriptor_name AS table_name,
                        ti.index_id,
                        ti.index_name,
                        ti.index_type,
                        ti.is_unique,
                        ti.is_inverted,
                        ti.is_sharded,
                        COALESCE(ti.shard_bucket_count, 0) AS shard_bucket_count,
                        t.database_name,
                        t.schema_name,
                        us.total_reads,
                        us.last_read
                    FROM crdb_internal.table_indexes AS ti
                    JOIN crdb_internal.tables AS t
                      ON t.table_id = ti.descriptor_id
                    LEFT JOIN crdb_internal.index_usage_statistics AS us
                      ON us.table_id = ti.descriptor_id
                     AND us.index_id = ti.index_id
                    WHERE {user_database_sql('t.database_name')}
                      AND t.schema_name NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND t.drop_time IS NULL
                      AND t.database_name = current_database()
                    ORDER BY t.schema_name, ti.descriptor_name, ti.index_name
                    """)
                rows = [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            self.add_error("CockroachDB index inventory failed", exc)
            return []

        seq_names = {
            (s.get("schema_name"), s.get("sequence_name"))
            for s in self._get_sequence_analysis()
        }
        allowed = self._base_table_keys()
        indexes = []
        for row in rows:
            if (row.get("schema_name"), row.get("table_name")) in seq_names:
                continue
            if (
                allowed
                and (
                    row.get("database_name"),
                    row.get("schema_name"),
                    row.get("table_name"),
                )
                not in allowed
            ):
                continue
            size = sizes.get((row["descriptor_id"], row["index_id"]), 0)
            index_type = (row.get("index_type") or "forward").lower()
            if row.get("is_inverted"):
                index_type = "inverted"
            if row.get("is_sharded"):
                index_type = f"hash-sharded/{index_type}"
            definition = self._index_definition(
                row.get("schema_name"), row.get("table_name"), row.get("index_name")
            )
            classified = classify_indexdef(definition or "")
            if classified:
                index_type = classified["kind"]
            is_primary = (row.get("index_name") or "").lower() in (
                "primary",
                "primary_key",
            ) or (row.get("index_type") or "").lower() == "primary"
            indexes.append(
                {
                    "database_name": row.get("database_name"),
                    "schema_name": row["schema_name"],
                    "table_name": row["table_name"],
                    "index_name": row["index_name"],
                    "index_type": index_type,
                    "estimated_rows": None,
                    "index_size_bytes": size,
                    "index_size_human": self._pretty_bytes(size),
                    "is_unique": bool(row.get("is_unique")),
                    "is_primary": is_primary,
                    "is_exclusion": False,
                    "is_immediate": True,
                    "is_clustered": False,
                    "is_valid": True,
                    "is_ready": True,
                    "is_live": True,
                    "is_sharded": bool(row.get("is_sharded")),
                    "shard_bucket_count": int(row.get("shard_bucket_count") or 0),
                    "is_inverted": bool(row.get("is_inverted")),
                    "index_definition": definition,
                    "index_kind": classified.get("kind") if classified else index_type,
                    "index_rewrite": classified.get("rewrite") if classified else None,
                    "index_expressions": None,
                    "index_predicate": None,
                    "description": None,
                    "usage_stats": {
                        "idx_scan": int(row.get("total_reads") or 0),
                        "last_read": (
                            str(row["last_read"]) if row.get("last_read") else None
                        ),
                    },
                }
            )
        return indexes

    def _get_view_analysis(self) -> List[Dict[str, Any]]:
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
                        NULL::bigint as materialized_size_bytes,
                        NULL::text as materialized_size_human,
                        NULL::text as extension
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_roles r ON c.relowner = r.oid
                    WHERE c.relkind IN ('v', 'm')
                      AND n.nspname NOT IN {_SYSTEM_SCHEMA_SQL}
                    ORDER BY n.nspname, c.relname
                    """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            self.add_error("Failed to get CockroachDB view analysis", exc)
            return []

    def _get_trigger_analysis(self) -> List[Dict[str, Any]]:
        """pg_trigger and information_schema.triggers are empty. Use SHOW TRIGGERS."""
        try:
            tables = []
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT table_catalog AS database_name, table_schema AS schema_name,
                           table_name
                    FROM information_schema.tables
                    WHERE table_type = 'BASE TABLE'
                      AND table_schema NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND {user_database_sql('table_catalog')}
                      AND table_catalog = current_database()
                    """)
                tables = [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            self.add_error(
                "Couldn't read table list for SHOW TRIGGERS",
                exc,
            )
            return []

        rows: List[Dict[str, Any]] = []
        failures: List[str] = []
        for table in tables:
            schema = table["schema_name"]
            name = table["table_name"]
            # Analyzer connections set search_path=pg_catalog. Always schema-qualify.
            qualified = f"{quote_ident(schema)}.{quote_ident(name)}"
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(f"SHOW TRIGGERS FROM {qualified}")
                    for row in cursor.fetchall():
                        rec = dict(row)
                        rec["schema_name"] = schema
                        rec["table_name"] = name
                        rec["database_name"] = table.get("database_name")
                        rec.setdefault("trigger_name", rec.get("name"))
                        rec.setdefault("trigger_definition", None)
                        trigger_name = rec.get("trigger_name")
                        if trigger_name:
                            try:
                                cursor.execute(
                                    f"SHOW CREATE TRIGGER {quote_ident(trigger_name)} ON {qualified}"
                                )
                                created = cursor.fetchone()
                                if created:
                                    rec["trigger_definition"] = (
                                        created.get("create_statement")
                                        or created.get("create_trigger")
                                        or next(iter(created.values()), None)
                                    )
                            except Exception:
                                pass
                        rows.append(rec)
            except Exception as exc:
                failures.append(f"{schema}.{name}: {exc}")
        if failures and not rows:
            self.add_error(
                "Couldn't read triggers via SHOW TRIGGERS "
                "(pg_trigger and information_schema.triggers are empty on CockroachDB): "
                + "; ".join(failures[:5]),
                failures[0],
            )
        elif failures:
            self.add_warning("SHOW TRIGGERS incomplete: " + "; ".join(failures[:5]))
        return rows

    def _get_partition_analysis(self) -> List[Dict[str, Any]]:
        """CockroachDB has no PG declarative partitions. REGIONAL BY ROW
        is recorded on tables and in the features analyzer."""
        return []

    def _get_constraint_analysis(self) -> List[Dict[str, Any]]:
        rows = super()._get_constraint_analysis()
        filtered = []
        for row in rows:
            schema = row.get("schema_name")
            table = row.get("table_name") or ""
            if schema in SYSTEM_SCHEMAS:
                continue
            # Sequences show up as tables with a primary PK. Drop them.
            if table.endswith("_seq") or table.endswith("_id_seq"):
                continue
            name = str(row.get("constraint_name") or "")
            row["is_hash_shard_check"] = (
                name.startswith("check_crdb_internal_") and "_shard_" in name
            )
            filtered.append(row)
        return filtered

    def _get_function_analysis(self) -> List[Dict[str, Any]]:
        """Procedures appear as void functions in pg_proc. Use routines."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT
                        routine_schema AS schema_name,
                        routine_name AS function_name,
                        routine_type,
                        external_language AS language,
                        routine_definition AS source_code,
                        data_type AS return_type
                    FROM information_schema.routines
                    WHERE routine_schema NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND routine_type = 'FUNCTION'
                    ORDER BY 1, 2
                    """)
                rows = [dict(row) for row in cursor.fetchall()]
                try:
                    cursor.execute(f"""
                        SELECT schema_name, function_name, create_statement
                        FROM crdb_internal.create_function_statements
                        WHERE schema_name NOT IN {_SYSTEM_SCHEMA_SQL}
                        """)
                    by_name = {
                        (r["schema_name"], r["function_name"]): r["create_statement"]
                        for r in (dict(x) for x in cursor.fetchall())
                    }
                    for row in rows:
                        ddl = by_name.get((row["schema_name"], row["function_name"]))
                        if ddl:
                            row["create_statement"] = ddl
                except Exception:
                    pass
                return rows
        except Exception as exc:
            self.add_error(
                "Couldn't read functions from information_schema.routines", exc
            )
            return []

    def _get_procedure_analysis(self) -> List[Dict[str, Any]]:
        """pg_proc reports procedures as functions returning void.

        Prefer create_procedure_statements, then routines.routine_type.
        """
        try:
            with self.connection.cursor() as cursor:
                try:
                    cursor.execute(f"""
                        SELECT database_name, schema_name,
                               function_name AS procedure_name,
                               create_statement AS source_code
                        FROM crdb_internal.create_procedure_statements
                        WHERE schema_name NOT IN {_SYSTEM_SCHEMA_SQL}
                        """)
                    rows = [dict(row) for row in cursor.fetchall()]
                    if rows:
                        return rows
                except Exception:
                    try:
                        self.connection.rollback()
                    except Exception:
                        pass
                cursor.execute(f"""
                    SELECT
                        routine_schema AS schema_name,
                        routine_name AS procedure_name,
                        external_language AS language,
                        routine_definition AS source_code
                    FROM information_schema.routines
                    WHERE routine_schema NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND routine_type = 'PROCEDURE'
                    ORDER BY 1, 2
                    """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            self.add_error(
                "Couldn't read procedures (create_procedure_statements / routines)",
                exc,
            )
            return []

    def _get_sequence_analysis(self) -> List[Dict[str, Any]]:
        """Sequences appear as tables in pg_class. Use information_schema."""
        if self._sequence_cache is not None:
            return self._sequence_cache
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT
                        sequence_schema AS schema_name,
                        sequence_name,
                        data_type,
                        numeric_precision,
                        start_value,
                        minimum_value,
                        maximum_value,
                        increment
                    FROM information_schema.sequences
                    WHERE sequence_schema NOT IN {_SYSTEM_SCHEMA_SQL}
                    ORDER BY 1, 2
                    """)
                self._sequence_cache = [dict(row) for row in cursor.fetchall()]
                return self._sequence_cache
        except Exception as exc:
            self.add_error(
                "Couldn't read sequences from information_schema.sequences", exc
            )
            self._sequence_cache = []
            return []

    def _show_create_all_types(self) -> List[Dict[str, Any]]:
        """Enums and other user-defined types. Run before SHOW CREATE ALL TABLES,
        because tables that use a type need the type to exist first."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SHOW CREATE ALL TYPES")
                rows = cursor.fetchall() or []
                out = []
                for row in rows:
                    rec = dict(row) if not isinstance(row, dict) else row
                    statement = (
                        rec.get("create_statement")
                        or rec.get("statement")
                        or next(iter(rec.values()), None)
                    )
                    out.append({"create_statement": statement})
                return out
        except Exception as exc:
            self.add_error("Couldn't read SHOW CREATE ALL TYPES", exc)
            return []

    def _show_create_all_tables(self) -> List[Dict[str, Any]]:
        """pg_dump does not work. Schema comes from SHOW CREATE ALL TABLES."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SHOW CREATE ALL TABLES")
                rows = cursor.fetchall() or []
                out = []
                for row in rows:
                    rec = dict(row) if not isinstance(row, dict) else row
                    statement = (
                        rec.get("create_statement")
                        or rec.get("statement")
                        or next(iter(rec.values()), None)
                    )
                    out.append({"create_statement": statement})
                return out
        except Exception as exc:
            self.add_error(
                "Couldn't read SHOW CREATE ALL TABLES "
                "(pg_dump does not work against CockroachDB)",
                exc,
            )
            return []

    def _get_routines(self) -> List[Dict[str, Any]]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT routine_schema, routine_name, routine_type,
                           external_language, routine_definition
                    FROM information_schema.routines
                    WHERE routine_schema NOT IN {_SYSTEM_SCHEMA_SQL}
                    ORDER BY 1, 2, 3
                    """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            self.add_error("Couldn't read information_schema.routines", exc)
            return []
