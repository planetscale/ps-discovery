"""CockroachDB feature analysis.

Filters crdb_internal / pg_extension out of composite-type and JSON
counts, finds array columns via format_type, and flags CockroachDB-only
features that force an application rewrite on PlanetScale Postgres.
"""

from typing import Any, Dict, List

from planetscale_discovery.database.analyzers.feature_analyzer import FeatureAnalyzer
from planetscale_discovery.database.cockroach import SYSTEM_SCHEMAS, user_database_sql
from planetscale_discovery.database.cockroach_compat import COPY_PATH, CompatDetector

_SYSTEM_SCHEMA_SQL = "(" + ", ".join(f"'{s}'" for s in SYSTEM_SCHEMAS) + ")"


class CockroachFeatureAnalyzer(FeatureAnalyzer):
    """Feature analyzer that ignores CRDB system catalogs and flags CRDB-only features."""

    def analyze(self) -> Dict[str, Any]:
        results = {
            "extensions_analysis": self._get_extensions_analysis(),
            "custom_data_types": self._get_custom_data_types(),
            "advanced_column_types": self._get_advanced_column_types(),
            "full_text_search": self._get_full_text_search(),
            "foreign_data_wrappers": {
                "foreign_data_wrappers": [],
                "foreign_servers": [],
            },
            "publication_subscription": {
                "publications": [],
                "subscriptions": [],
                "note": (
                    "CockroachDB has no PostgreSQL logical replication "
                    "(pg_publication / pg_subscription). The only path is a "
                    "downtime cutover with text or CSV COPY (SELECT ...). "
                    "Do not use pgcopydb or pg_dump. A custom changefeed "
                    "applier is not provided."
                ),
            },
            "partitioning_features": {"note": "See cockroach_features.regional_by_row"},
            "large_objects": {"large_objects": []},
            "background_workers": [],
            "event_triggers": [],
            "cockroach_features": self._get_cockroach_features(),
        }
        compat = results["cockroach_features"]
        results["compat_findings"] = compat.get("compat_findings", [])
        results["inferred_extensions"] = compat.get("inferred_extensions", [])
        results["cluster_context"] = compat.get("cluster_context") or {}
        results["copy_path"] = COPY_PATH
        return results

    def _get_custom_data_types(self) -> Dict[str, Any]:
        data = super()._get_custom_data_types()
        if "error" in data:
            return data
        for key in ("composite_types", "enum_types", "domain_types", "range_types"):
            data[key] = [
                row
                for row in data.get(key, [])
                if row.get("schema_name") not in SYSTEM_SCHEMAS
            ]
        all_custom = (
            data.get("composite_types", [])
            + data.get("enum_types", [])
            + data.get("domain_types", [])
            + data.get("range_types", [])
        )
        data["type_usage"] = {
            k: v
            for k, v in (data.get("type_usage") or {}).items()
            if not any(k.startswith(f"{schema}.") for schema in SYSTEM_SCHEMAS)
        }
        data["summary"] = {
            "composite_count": len(data.get("composite_types", [])),
            "enum_count": len(data.get("enum_types", [])),
            "domain_count": len(data.get("domain_types", [])),
            "range_count": len(data.get("range_types", [])),
            "total_custom_types": len(all_custom),
        }
        return data

    def _get_advanced_column_types(self) -> Dict[str, Any]:
        """attndims is 0 on CRDB arrays. Use information_schema types instead."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"""
                    SELECT table_schema AS schema_name, table_name, column_name,
                           data_type, crdb_sql_type, udt_name
                    FROM information_schema.columns
                    WHERE table_schema NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND {user_database_sql('table_catalog')}
                      AND table_catalog = current_database()
                      AND (
                          data_type = 'ARRAY'
                          OR crdb_sql_type LIKE '%[]'
                          OR udt_name LIKE '\\_%'
                      )
                    ORDER BY 1, 2, 3
                    """)
                array_columns = [dict(row) for row in cursor.fetchall()]

                cursor.execute(f"""
                    SELECT table_schema AS schema_name, table_name, column_name,
                           data_type, crdb_sql_type
                    FROM information_schema.columns
                    WHERE table_schema NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND {user_database_sql('table_catalog')}
                      AND table_catalog = current_database()
                      AND (crdb_sql_type IN ('JSON', 'JSONB') OR data_type IN ('json', 'jsonb'))
                    ORDER BY 1, 2, 3
                    """)
                json_columns = [dict(row) for row in cursor.fetchall()]

                cursor.execute(f"""
                    SELECT table_schema AS schema_name, table_name, column_name,
                           data_type, crdb_sql_type
                    FROM information_schema.columns
                    WHERE table_schema NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND {user_database_sql('table_catalog')}
                      AND table_catalog = current_database()
                      AND (data_type = 'vector' OR crdb_sql_type LIKE 'VECTOR%')
                    ORDER BY 1, 2, 3
                    """)
                vector_columns = [dict(row) for row in cursor.fetchall()]

                cursor.execute(f"""
                    SELECT table_schema AS schema_name, table_name, column_name,
                           data_type, crdb_sql_type
                    FROM information_schema.columns
                    WHERE table_schema NOT IN {_SYSTEM_SCHEMA_SQL}
                      AND {user_database_sql('table_catalog')}
                      AND table_catalog = current_database()
                      AND crdb_sql_type ~ '^(GEOMETRY|GEOGRAPHY)'
                    ORDER BY 1, 2, 3
                    """)
                spatial_columns = [dict(row) for row in cursor.fetchall()]

            return {
                "array_columns": array_columns,
                "json_columns": json_columns,
                "vector_columns": vector_columns,
                "spatial_columns": spatial_columns,
                "hstore_columns": [],
                "uuid_columns": [],
                "summary": {
                    "array_count": len(array_columns),
                    "json_count": len(json_columns),
                    "vector_count": len(vector_columns),
                    "spatial_count": len(spatial_columns),
                    "hstore_count": 0,
                },
            }
        except Exception as exc:
            self.add_error("Failed to analyze CockroachDB column types", exc)
            return {"error": str(exc)}

    def _get_cockroach_features(self) -> Dict[str, Any]:
        detector = CompatDetector(
            self.connection,
            add_error=self.add_error,
            add_warning=self.add_warning,
        )
        detected = detector.detect()
        findings = detected.get("compat_findings") or []
        by_key = {row["key"]: row for row in findings}

        def names(key: str) -> List[Any]:
            return list((by_key.get(key) or {}).get("objects") or [])

        flags = {
            "compat_findings": findings,
            "inferred_extensions": detected.get("inferred_extensions") or [],
            "cluster_context": detected.get("cluster_context") or {},
            "unique_rowid": names("serial_unique_rowid"),
            "hash_sharded_indexes": names("hash_sharded"),
            "regional_by_row": names("regional_by_row"),
            "column_families": names("column_families"),
            "spatial": names("spatial_types"),
            "full_text_search": names("tsvector"),
            "vector": names("vector_type"),
            "trigram": names("trigram_index"),
            "jsonb_inverted": names("jsonb_inverted"),
            "enums": names("enums"),
            "udfs": names("udfs"),
            "triggers": names("triggers"),
            "procedures": names("procedures"),
            "sequences": names("sequences"),
            "computed_columns": names("stored_computed") + names("virtual_computed"),
            "row_level_ttl": names("row_level_ttl"),
            "logical_replication": False,
            "migration_note": (
                "CockroachDB has no PostgreSQL logical replication. "
                "The default path is a downtime cutover using a bulk plain-text COPY. "
                "Schema comes from SHOW CREATE ALL TYPES, SHOW CREATE ALL TABLES, SHOW TRIGGERS, and "
                "information_schema.routines (pg_dump does not work). "
                "A changefeed WITH initial_scan='only' is a bulk-export alternative "
                "and works with kv.rangefeed.enabled off. Near-zero downtime would "
                "need a custom changefeed-to-Postgres applier with rangefeed on; "
                "that applier is not provided. Do not use pgcopydb --follow or pg_dump."
            ),
        }
        blockers = [f["key"] for f in findings if f.get("bucket") == "blocker"]
        if blockers:
            self.add_warning("CockroachDB blockers: " + ", ".join(blockers))
        return flags
