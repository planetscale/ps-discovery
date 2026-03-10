"""
PostgreSQL Advanced Features Analysis Module
Analyzes advanced PostgreSQL features, extensions, and custom data types.
"""

from typing import Dict, Any, List

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class FeatureAnalyzer(DatabaseAnalyzer):
    """Analyzes advanced PostgreSQL features and extensions."""

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
        """Run complete advanced features analysis."""
        results = {
            "extensions_analysis": self._get_extensions_analysis(),
            "custom_data_types": self._get_custom_data_types(),
            "advanced_column_types": self._get_advanced_column_types(),
            "full_text_search": self._get_full_text_search(),
            "foreign_data_wrappers": self._get_foreign_data_wrappers(),
            "publication_subscription": self._get_publication_subscription(),
            "partitioning_features": self._get_partitioning_features(),
            "large_objects": self._get_large_objects(),
            "background_workers": self._get_background_workers(),
            "event_triggers": self._get_event_triggers(),
        }

        return results

    def _get_extensions_analysis(self) -> Dict[str, Any]:
        """Analyze installed extensions and their usage."""
        try:
            with self.connection.cursor() as cursor:
                # Get all installed extensions
                cursor.execute("""
                    SELECT
                        e.extname,
                        e.extversion,
                        n.nspname as schema_name,
                        e.extrelocatable,
                        e.extconfig as config_tables,
                        e.extcondition as config_conditions,
                        c.description
                    FROM pg_extension e
                    JOIN pg_namespace n ON e.extnamespace = n.oid
                    LEFT JOIN pg_description c ON c.objoid = e.oid
                        AND c.classoid = 'pg_extension'::regclass
                    ORDER BY e.extname
                """)
                extensions = [dict(row) for row in cursor.fetchall()]

                # Get extension dependencies
                cursor.execute("""
                    SELECT
                        e1.extname as extension,
                        e2.extname as depends_on
                    FROM pg_depend d
                    JOIN pg_extension e1 ON d.objid = e1.oid
                    JOIN pg_extension e2 ON d.refobjid = e2.oid
                    WHERE d.classid = 'pg_extension'::regclass
                    AND d.refclassid = 'pg_extension'::regclass
                """)
                extension_dependencies = [dict(row) for row in cursor.fetchall()]

                # Analyze PostGIS if available
                postgis_analysis = {}
                if any(ext["extname"] == "postgis" for ext in extensions):
                    postgis_analysis = self._analyze_postgis()

                # Count objects created by extensions
                extension_objects = {}
                for ext in extensions:
                    try:
                        cursor.execute(
                            """
                            SELECT
                                COUNT(*) FILTER (WHERE c.relkind = 'r') as tables,
                                COUNT(*) FILTER (WHERE c.relkind = 'v') as views,
                                COUNT(*) FILTER (WHERE c.relkind = 'S') as sequences,
                                COUNT(*) FILTER (WHERE c.relkind = 'f') as foreign_tables
                            FROM pg_depend d
                            JOIN pg_class c ON d.objid = c.oid
                            WHERE d.refclassid = 'pg_extension'::regclass
                            AND d.refobjid = (SELECT oid FROM pg_extension WHERE extname = %s)
                            AND d.classid = 'pg_class'::regclass
                        """,
                            (ext["extname"],),
                        )
                        extension_objects[ext["extname"]] = dict(cursor.fetchone())
                    except Exception as e:
                        self.logger.warning(
                            f"Could not get objects for extension {ext['extname']}: {e}"
                        )

                return {
                    "installed_extensions": extensions,
                    "extension_dependencies": extension_dependencies,
                    "extension_objects": extension_objects,
                    "postgis_analysis": postgis_analysis,
                    "extension_count": len(extensions),
                }

        except Exception as e:
            self.logger.error(f"Failed to get extensions analysis: {e}")
            return {"error": str(e)}

    def _analyze_postgis(self) -> Dict[str, Any]:
        """Analyze PostGIS usage if available."""
        try:
            with self.connection.cursor() as cursor:
                # PostGIS version and configuration
                cursor.execute("SELECT PostGIS_Version()")
                postgis_version = cursor.fetchone()[0]

                # Spatial reference systems
                cursor.execute("SELECT COUNT(*) as srid_count FROM spatial_ref_sys")
                srid_count = cursor.fetchone()["srid_count"]

                # Geometry columns
                cursor.execute("""
                    SELECT
                        f_table_schema,
                        f_table_name,
                        f_geometry_column,
                        coord_dimension,
                        srid,
                        type
                    FROM geometry_columns
                """)
                geometry_columns = [dict(row) for row in cursor.fetchall()]

                # Geography columns (if available)
                geography_columns = []
                try:
                    cursor.execute("""
                        SELECT
                            f_table_schema,
                            f_table_name,
                            f_geography_column,
                            coord_dimension,
                            srid,
                            type
                        FROM geography_columns
                    """)
                    geography_columns = [dict(row) for row in cursor.fetchall()]
                except Exception:
                    pass

                return {
                    "postgis_version": postgis_version,
                    "srid_count": srid_count,
                    "geometry_columns": geometry_columns,
                    "geography_columns": geography_columns,
                    "spatial_tables": len(geometry_columns) + len(geography_columns),
                }

        except Exception as e:
            self.logger.warning(f"Could not analyze PostGIS: {e}")
            return {"error": str(e)}

    def _get_custom_data_types(self) -> Dict[str, Any]:
        """Analyze custom data types."""
        try:
            with self.connection.cursor() as cursor:
                # Composite types
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        t.typname as type_name,
                        t.typtype as type_category,
                        obj_description(t.oid, 'pg_type') as description,
                        r.rolname as owner
                    FROM pg_type t
                    JOIN pg_namespace n ON t.typnamespace = n.oid
                    JOIN pg_roles r ON t.typowner = r.oid
                    WHERE t.typtype = 'c'  -- composite types
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, t.typname
                """)
                composite_types = [dict(row) for row in cursor.fetchall()]

                # Enum types
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        t.typname as type_name,
                        array_agg(e.enumlabel ORDER BY e.enumsortorder) as enum_values,
                        obj_description(t.oid, 'pg_type') as description,
                        r.rolname as owner
                    FROM pg_type t
                    JOIN pg_namespace n ON t.typnamespace = n.oid
                    JOIN pg_roles r ON t.typowner = r.oid
                    LEFT JOIN pg_enum e ON t.oid = e.enumtypid
                    WHERE t.typtype = 'e'  -- enum types
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    GROUP BY n.nspname, t.typname, t.oid, r.rolname
                    ORDER BY n.nspname, t.typname
                """)
                enum_types = [dict(row) for row in cursor.fetchall()]

                # Domain types
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        t.typname as type_name,
                        format_type(t.typbasetype, t.typtypmod) as base_type,
                        t.typnotnull as not_null,
                        CASE WHEN t.typdefault IS NOT NULL THEN true ELSE false END as has_default_value,
                        pg_get_constraintdef(c.oid) as check_constraint,
                        obj_description(t.oid, 'pg_type') as description,
                        r.rolname as owner
                    FROM pg_type t
                    JOIN pg_namespace n ON t.typnamespace = n.oid
                    JOIN pg_roles r ON t.typowner = r.oid
                    LEFT JOIN pg_constraint c ON c.contypid = t.oid
                    WHERE t.typtype = 'd'  -- domain types
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, t.typname
                """)
                domain_types = [dict(row) for row in cursor.fetchall()]

                # Range types
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        t.typname as type_name,
                        format_type(r.rngsubtype, 0) as subtype,
                        obj_description(t.oid, 'pg_type') as description,
                        ro.rolname as owner
                    FROM pg_type t
                    JOIN pg_namespace n ON t.typnamespace = n.oid
                    JOIN pg_roles ro ON t.typowner = ro.oid
                    JOIN pg_range r ON t.oid = r.rngtypid
                    WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, t.typname
                """)
                range_types = [dict(row) for row in cursor.fetchall()]

                # Get usage statistics for custom types
                type_usage = {}
                all_custom_types = (
                    composite_types + enum_types + domain_types + range_types
                )

                for custom_type in all_custom_types:
                    type_name = (
                        f"{custom_type['schema_name']}.{custom_type['type_name']}"
                    )
                    try:
                        cursor.execute(
                            """
                            SELECT COUNT(*) as usage_count
                            FROM pg_attribute a
                            JOIN pg_class c ON a.attrelid = c.oid
                            JOIN pg_namespace n ON c.relnamespace = n.oid
                            WHERE format_type(a.atttypid, a.atttypmod) = %s
                            AND NOT a.attisdropped
                        """,
                            (type_name,),
                        )
                        usage_count = cursor.fetchone()["usage_count"]
                        type_usage[type_name] = usage_count
                    except Exception as e:
                        self.logger.warning(
                            f"Could not get usage for type {type_name}: {e}"
                        )

                return {
                    "composite_types": composite_types,
                    "enum_types": enum_types,
                    "domain_types": domain_types,
                    "range_types": range_types,
                    "type_usage": type_usage,
                    "summary": {
                        "composite_count": len(composite_types),
                        "enum_count": len(enum_types),
                        "domain_count": len(domain_types),
                        "range_count": len(range_types),
                        "total_custom_types": len(all_custom_types),
                    },
                }

        except Exception as e:
            self.logger.error(f"Failed to get custom data types: {e}")
            return {"error": str(e)}

    def _get_advanced_column_types(self) -> Dict[str, Any]:
        """Analyze usage of advanced column types."""
        try:
            with self.connection.cursor() as cursor:
                # Array columns
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        a.attname as column_name,
                        format_type(a.atttypid, a.atttypmod) as data_type,
                        a.attndims as array_dimensions
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE a.attndims > 0  -- Array columns
                    AND NOT a.attisdropped
                    AND c.relkind = 'r'
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname, a.attname
                """)
                array_columns = [dict(row) for row in cursor.fetchall()]

                # JSON/JSONB columns
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        a.attname as column_name,
                        format_type(a.atttypid, a.atttypmod) as data_type
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_type t ON a.atttypid = t.oid
                    WHERE t.typname IN ('json', 'jsonb')
                    AND NOT a.attisdropped
                    AND c.relkind = 'r'
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname, a.attname
                """)
                json_columns = [dict(row) for row in cursor.fetchall()]

                # HSTORE columns (if extension is available)
                hstore_columns = []
                try:
                    cursor.execute("""
                        SELECT
                            n.nspname as schema_name,
                            c.relname as table_name,
                            a.attname as column_name,
                            format_type(a.atttypid, a.atttypmod) as data_type
                        FROM pg_attribute a
                        JOIN pg_class c ON a.attrelid = c.oid
                        JOIN pg_namespace n ON c.relnamespace = n.oid
                        JOIN pg_type t ON a.atttypid = t.oid
                        WHERE t.typname = 'hstore'
                        AND NOT a.attisdropped
                        AND c.relkind = 'r'
                        AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                        ORDER BY n.nspname, c.relname, a.attname
                    """)
                    hstore_columns = [dict(row) for row in cursor.fetchall()]
                except Exception:
                    pass

                # Geometry/Geography columns (PostGIS)
                spatial_columns = []
                try:
                    cursor.execute("""
                        SELECT
                            n.nspname as schema_name,
                            c.relname as table_name,
                            a.attname as column_name,
                            format_type(a.atttypid, a.atttypmod) as data_type
                        FROM pg_attribute a
                        JOIN pg_class c ON a.attrelid = c.oid
                        JOIN pg_namespace n ON c.relnamespace = n.oid
                        JOIN pg_type t ON a.atttypid = t.oid
                        WHERE t.typname IN ('geometry', 'geography')
                        AND NOT a.attisdropped
                        AND c.relkind = 'r'
                        AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                        ORDER BY n.nspname, c.relname, a.attname
                    """)
                    spatial_columns = [dict(row) for row in cursor.fetchall()]
                except Exception:
                    pass

                # Large object columns
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        a.attname as column_name,
                        format_type(a.atttypid, a.atttypmod) as data_type
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_type t ON a.atttypid = t.oid
                    WHERE t.typname = 'oid'
                    AND a.attname LIKE '%lob%' OR a.attname LIKE '%blob%' OR a.attname LIKE '%file%'
                    AND NOT a.attisdropped
                    AND c.relkind = 'r'
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname, a.attname
                """)
                lob_columns = [dict(row) for row in cursor.fetchall()]

                return {
                    "array_columns": array_columns,
                    "json_columns": json_columns,
                    "hstore_columns": hstore_columns,
                    "spatial_columns": spatial_columns,
                    "large_object_columns": lob_columns,
                    "summary": {
                        "array_count": len(array_columns),
                        "json_count": len(json_columns),
                        "hstore_count": len(hstore_columns),
                        "spatial_count": len(spatial_columns),
                        "lob_count": len(lob_columns),
                    },
                }

        except Exception as e:
            self.logger.error(f"Failed to get advanced column types: {e}")
            return {"error": str(e)}

    def _get_full_text_search(self) -> Dict[str, Any]:
        """Analyze full-text search usage."""
        try:
            with self.connection.cursor() as cursor:
                # Text search configurations
                cursor.execute("""
                    SELECT
                        cfgname as config_name,
                        cfgnamespace::regnamespace as schema_name,
                        cfgowner::regrole as owner
                    FROM pg_ts_config
                    WHERE cfgnamespace != (SELECT oid FROM pg_namespace WHERE nspname = 'pg_catalog')
                    ORDER BY cfgname
                """)
                ts_configs = [dict(row) for row in cursor.fetchall()]

                # Text search dictionaries
                cursor.execute("""
                    SELECT
                        dictname as dictionary_name,
                        dictnamespace::regnamespace as schema_name,
                        dictowner::regrole as owner,
                        tmplname as template_name
                    FROM pg_ts_dict d
                    JOIN pg_ts_template t ON d.dicttemplate = t.oid
                    WHERE d.dictnamespace != (SELECT oid FROM pg_namespace WHERE nspname = 'pg_catalog')
                    ORDER BY dictname
                """)
                ts_dictionaries = [dict(row) for row in cursor.fetchall()]

                # TSVECTOR columns
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        a.attname as column_name,
                        format_type(a.atttypid, a.atttypmod) as data_type
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_type t ON a.atttypid = t.oid
                    WHERE t.typname = 'tsvector'
                    AND NOT a.attisdropped
                    AND c.relkind = 'r'
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname, a.attname
                """)
                tsvector_columns = [dict(row) for row in cursor.fetchall()]

                # GIN indexes on tsvector columns
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        i.relname as index_name,
                        am.amname as index_type,
                        pg_get_indexdef(i.oid) as index_definition
                    FROM pg_index ix
                    JOIN pg_class i ON ix.indexrelid = i.oid
                    JOIN pg_class c ON ix.indrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_am am ON i.relam = am.oid
                    WHERE am.amname = 'gin'
                    AND pg_get_indexdef(i.oid) ~* 'tsvector|to_tsvector'
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname, i.relname
                """)
                fts_indexes = [dict(row) for row in cursor.fetchall()]

                return {
                    "ts_configurations": ts_configs,
                    "ts_dictionaries": ts_dictionaries,
                    "tsvector_columns": tsvector_columns,
                    "fts_indexes": fts_indexes,
                    "summary": {
                        "custom_ts_configs": len(ts_configs),
                        "custom_ts_dictionaries": len(ts_dictionaries),
                        "tsvector_columns": len(tsvector_columns),
                        "fts_indexes": len(fts_indexes),
                    },
                }

        except Exception as e:
            self.logger.error(f"Failed to get full-text search analysis: {e}")
            return {"error": str(e)}

    def _get_foreign_data_wrappers(self) -> Dict[str, Any]:
        """Analyze foreign data wrappers and foreign tables."""
        try:
            with self.connection.cursor() as cursor:
                # Foreign data wrappers
                cursor.execute("""
                    SELECT
                        fdwname as fdw_name,
                        fdwowner::regrole as owner,
                        fdwhandler::regproc as handler,
                        fdwvalidator::regproc as validator,
                        fdwoptions as options
                    FROM pg_foreign_data_wrapper
                    ORDER BY fdwname
                """)
                fdws = [dict(row) for row in cursor.fetchall()]

                # Foreign servers
                cursor.execute("""
                    SELECT
                        s.srvname as server_name,
                        s.srvowner::regrole as owner,
                        w.fdwname as fdw_name,
                        s.srvtype as server_type,
                        s.srvversion as server_version,
                        s.srvoptions as options
                    FROM pg_foreign_server s
                    JOIN pg_foreign_data_wrapper w ON s.srvfdw = w.oid
                    ORDER BY s.srvname
                """)
                foreign_servers = [dict(row) for row in cursor.fetchall()]

                # Foreign tables
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        s.srvname as server_name,
                        ft.ftoptions as table_options,
                        c.reltuples::bigint as estimated_rows
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_foreign_table ft ON c.oid = ft.ftrelid
                    JOIN pg_foreign_server s ON ft.ftserver = s.oid
                    WHERE c.relkind = 'f'
                    ORDER BY n.nspname, c.relname
                """)
                foreign_tables = [dict(row) for row in cursor.fetchall()]

                # User mappings (may require elevated permissions, especially on managed services like RDS)
                user_mappings = []
                # Only attempt if there are foreign servers to map to
                if foreign_servers:
                    try:
                        cursor.execute("""
                            SELECT
                                s.srvname as server_name,
                                CASE WHEN um.umuser = 0 THEN 'PUBLIC'
                                     ELSE um.umuser::regrole::text END as username,
                                um.umoptions as options
                            FROM pg_user_mapping um
                            JOIN pg_foreign_server s ON um.umserver = s.oid
                            ORDER BY s.srvname, username
                        """)
                        user_mappings = [dict(row) for row in cursor.fetchall()]
                    except Exception as e:
                        # This is expected on managed databases (RDS, Cloud SQL) where pg_user_mapping access is restricted
                        self.logger.debug(
                            f"Could not access pg_user_mapping (restricted on managed databases): {e}"
                        )

                return {
                    "foreign_data_wrappers": fdws,
                    "foreign_servers": foreign_servers,
                    "foreign_tables": foreign_tables,
                    "user_mappings": user_mappings,
                    "summary": {
                        "fdw_count": len(fdws),
                        "foreign_server_count": len(foreign_servers),
                        "foreign_table_count": len(foreign_tables),
                        "user_mapping_count": len(user_mappings),
                    },
                }

        except Exception as e:
            self.logger.error(f"Failed to get foreign data wrappers: {e}")
            return {"error": str(e)}

    def _get_publication_subscription(self) -> Dict[str, Any]:
        """Analyze logical replication publications and subscriptions."""
        try:
            with self.connection.cursor() as cursor:
                # Publications
                cursor.execute("""
                    SELECT
                        pubname as publication_name,
                        pubowner::regrole as owner,
                        puballtables as all_tables,
                        pubinsert,
                        pubupdate,
                        pubdelete,
                        pubtruncate
                    FROM pg_publication
                    ORDER BY pubname
                """)
                publications = [dict(row) for row in cursor.fetchall()]

                # Publication tables
                cursor.execute("""
                    SELECT
                        pub.pubname as publication_name,
                        n.nspname as schema_name,
                        c.relname as table_name
                    FROM pg_publication_rel pr
                    JOIN pg_publication pub ON pr.prpubid = pub.oid
                    JOIN pg_class c ON pr.prrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    ORDER BY pub.pubname, n.nspname, c.relname
                """)
                publication_tables = [dict(row) for row in cursor.fetchall()]

                # Subscriptions (if any)
                subscriptions = []
                try:
                    cursor.execute("""
                        SELECT
                            subname as subscription_name,
                            subowner::regrole as owner,
                            subenabled as enabled,
                            subconninfo as connection_info,
                            subslotname as slot_name,
                            subsynccommit as sync_commit,
                            subpublications as publications
                        FROM pg_subscription
                        ORDER BY subname
                    """)
                    subscriptions = [dict(row) for row in cursor.fetchall()]
                except Exception:
                    # Subscriptions might not be available in all versions
                    pass

                return {
                    "publications": publications,
                    "publication_tables": publication_tables,
                    "subscriptions": subscriptions,
                    "summary": {
                        "publication_count": len(publications),
                        "subscription_count": len(subscriptions),
                        "published_table_count": len(publication_tables),
                    },
                }

        except Exception as e:
            self.logger.error(f"Failed to get publication/subscription analysis: {e}")
            return {"error": str(e)}

    def _get_partitioning_features(self) -> Dict[str, Any]:
        """Analyze partitioning usage and strategies."""
        try:
            # Declarative partitioning added in PostgreSQL 10
            version_num = self._get_server_version_num()
            has_declarative_partitioning = version_num >= 100000

            if not has_declarative_partitioning:
                self.logger.debug(
                    "Skipping partitioning features (requires PostgreSQL 10+)"
                )
                return {
                    "partitioned_tables": [],
                    "partition_counts": {},
                    "summary": {
                        "partitioned_table_count": 0,
                        "total_partitions": 0,
                        "note": "Declarative partitioning requires PostgreSQL 10+",
                    },
                }

            with self.connection.cursor() as cursor:
                # Partitioned tables
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        pg_get_partkeydef(c.oid) as partition_key,
                        c.reltuples::bigint as estimated_rows,
                        pg_relation_size(c.oid) as table_size_bytes
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE c.relkind = 'p'  -- partitioned table
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname
                """)
                partitioned_tables = [dict(row) for row in cursor.fetchall()]

                # Partition count per partitioned table
                partition_counts = {}
                for table in partitioned_tables:
                    try:
                        cursor.execute(
                            """
                            SELECT COUNT(*) as partition_count
                            FROM pg_inherits i
                            JOIN pg_class parent ON i.inhparent = parent.oid
                            JOIN pg_namespace n ON parent.relnamespace = n.oid
                            WHERE n.nspname = %s AND parent.relname = %s
                        """,
                            (table["schema_name"], table["table_name"]),
                        )
                        count = cursor.fetchone()["partition_count"]
                        partition_counts[
                            f"{table['schema_name']}.{table['table_name']}"
                        ] = count
                    except Exception as e:
                        self.logger.warning(
                            f"Could not get partition count for {table['table_name']}: {e}"
                        )

                return {
                    "partitioned_tables": partitioned_tables,
                    "partition_counts": partition_counts,
                    "summary": {
                        "partitioned_table_count": len(partitioned_tables),
                        "total_partitions": sum(partition_counts.values()),
                    },
                }

        except Exception as e:
            self.logger.error(f"Failed to get partitioning features: {e}")
            return {"error": str(e)}

    def _get_large_objects(self) -> Dict[str, Any]:
        """Analyze large object usage."""
        try:
            with self.connection.cursor() as cursor:
                # Large objects count and total size
                cursor.execute("""
                    SELECT
                        COUNT(*) as lob_count,
                        SUM(CASE WHEN lomowner IS NOT NULL THEN 1 ELSE 0 END) as owned_lobs
                    FROM pg_largeobject_metadata
                """)
                lob_summary = dict(cursor.fetchone())

                # Large objects by owner
                cursor.execute("""
                    SELECT
                        lomowner::regrole as owner,
                        COUNT(*) as lob_count
                    FROM pg_largeobject_metadata
                    GROUP BY lomowner::regrole
                    ORDER BY lob_count DESC
                """)
                lobs_by_owner = [dict(row) for row in cursor.fetchall()]

                return {"summary": lob_summary, "lobs_by_owner": lobs_by_owner}

        except Exception as e:
            self.logger.error(f"Failed to get large objects analysis: {e}")
            return {"error": str(e)}

    def _get_background_workers(self) -> List[Dict[str, Any]]:
        """Get information about background worker processes."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        pid,
                        backend_type,
                        backend_start
                    FROM pg_stat_activity
                    WHERE backend_type != 'client backend'
                    ORDER BY backend_type, backend_start
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get background workers: {e}")
            return []

    def _get_event_triggers(self) -> List[Dict[str, Any]]:
        """Analyze event triggers."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        evtname as trigger_name,
                        evtevent as event,
                        evtowner::regrole as owner,
                        evtfoid::regproc as function_name,
                        evtenabled as enabled,
                        evttags as tags
                    FROM pg_event_trigger
                    ORDER BY evtname
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get event triggers: {e}")
            return []
