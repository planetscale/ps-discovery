"""
PostgreSQL Data Size Analysis Module

Analyzes actual data sizes within columns to identify LOBs and large values.
This analyzer is OPT-IN only due to performance implications (requires table scans).

IMPORTANT: This analyzer can be expensive on large databases. Use sampling
or target specific tables to avoid performance issues.
"""

from typing import Dict, Any, List, Optional

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class DataSizeAnalyzer(DatabaseAnalyzer):
    """
    Analyzes column data sizes to identify large text/bytea/json fields.

    This analyzer helps identify:
    - Tables with LOB (Large Object) data
    - Columns exceeding size thresholds (1KB, 64KB)
    - Maximum and average column sizes
    - PostgreSQL TOAST storage strategies
    - Row size distribution

    PostgreSQL TOAST Storage Strategies:
    - PLAIN: No compression or out-of-line storage (inline only)
    - EXTENDED: Compression first, then out-of-line storage (default for most types)
    - EXTERNAL: Out-of-line storage without compression (for already compressed data)
    - MAIN: Compression first, avoid out-of-line storage if possible

    Performance: Uses TABLESAMPLE for efficient sampling on large tables.
    """

    def __init__(
        self, connection, config: Optional[Dict[str, Any]] = None, logger=None
    ):
        """
        Initialize data size analyzer.

        Args:
            connection: PostgreSQL database connection
            config: Configuration dictionary with options:
                - enabled: Whether to run this analyzer (default: False)
                - sample_percent: Percentage of rows to sample (default: 10)
                - max_table_size_gb: Skip tables larger than this (default: 10)
                - target_tables: List of specific tables to analyze (optional)
                - target_schemas: List of schemas to analyze (default: ['public'])
                - check_column_types: Column types to check (default: text, bytea, json, jsonb)
                - size_thresholds: Dict of size thresholds in bytes (default: 1KB, 64KB)
        """
        super().__init__(connection, config, logger)

        # Default configuration
        self.enabled = self.config.get("enabled", False)
        self.sample_percent = self.config.get("sample_percent", 10)
        self.max_table_size_gb = self.config.get("max_table_size_gb", 10)
        self.target_tables = self.config.get("target_tables", [])
        self.target_schemas = self.config.get("target_schemas", ["public"])
        self.check_column_types = self.config.get(
            "check_column_types",
            ["text", "bytea", "json", "jsonb", "character varying", "varchar"],
        )
        self.size_thresholds = self.config.get(
            "size_thresholds",
            {
                "1kb": 1024,
                "64kb": 65536,
            },
        )

    def analyze(self) -> Dict[str, Any]:
        """
        Run data size analysis.

        Returns:
            Dictionary containing data size analysis results
        """
        if not self.enabled:
            return {
                "enabled": False,
                "message": "Data size analysis is disabled. Set data_size.enabled=true to enable.",
                "note": "This analyzer requires table scans and can be expensive on large databases.",
            }

        self.logger.info(
            "Starting data size analysis (this may take time on large databases)"
        )

        results = {
            "enabled": True,
            "configuration": {
                "sample_percent": self.sample_percent,
                "max_table_size_gb": self.max_table_size_gb,
                "target_schemas": self.target_schemas,
                "target_tables": self.target_tables if self.target_tables else "all",
                "size_thresholds": self.size_thresholds,
            },
            "tables_analyzed": [],
            "tables_skipped": [],
            "summary": {
                "total_tables_analyzed": 0,
                "total_tables_skipped": 0,
                "tables_with_large_columns": 0,
                "columns_exceeding_1kb": 0,
                "columns_exceeding_64kb": 0,
            },
        }

        # Get list of tables to analyze
        tables = self._get_tables_to_analyze()

        for table in tables:
            schema_name = table["schema_name"]
            table_name = table["table_name"]
            table_size_gb = table["size_gb"]

            # Skip large tables if configured
            if table_size_gb > self.max_table_size_gb:
                self.logger.info(
                    f"Skipping {schema_name}.{table_name} ({table_size_gb:.2f}GB) - "
                    f"exceeds max_table_size_gb={self.max_table_size_gb}"
                )
                results["tables_skipped"].append(
                    {
                        "schema": schema_name,
                        "table": table_name,
                        "size_gb": table_size_gb,
                        "reason": "exceeds_size_limit",
                    }
                )
                results["summary"]["total_tables_skipped"] += 1
                continue

            # Analyze table
            try:
                table_analysis = self._analyze_table(schema_name, table_name, table)
                if table_analysis:
                    results["tables_analyzed"].append(table_analysis)
                    results["summary"]["total_tables_analyzed"] += 1

                    # Update summary statistics
                    if table_analysis.get("has_large_columns"):
                        results["summary"]["tables_with_large_columns"] += 1

                    for col in table_analysis.get("columns", []):
                        if col.get("count_gt_1kb", 0) > 0:
                            results["summary"]["columns_exceeding_1kb"] += 1
                        if col.get("count_gt_64kb", 0) > 0:
                            results["summary"]["columns_exceeding_64kb"] += 1

            except Exception as e:
                self.logger.error(f"Failed to analyze {schema_name}.{table_name}: {e}")
                results["tables_skipped"].append(
                    {
                        "schema": schema_name,
                        "table": table_name,
                        "reason": f"error: {str(e)}",
                    }
                )
                results["summary"]["total_tables_skipped"] += 1

        self.logger.info(
            f"Data size analysis complete: {results['summary']['total_tables_analyzed']} tables analyzed, "
            f"{results['summary']['total_tables_skipped']} skipped"
        )

        return results

    def _get_tables_to_analyze(self) -> List[Dict[str, Any]]:
        """Get list of tables to analyze based on configuration."""
        try:
            with self.connection.cursor() as cursor:
                # Build WHERE clause based on configuration
                where_clauses = []
                params = []

                # Filter by schemas
                if self.target_schemas:
                    placeholders = ",".join(["%s"] * len(self.target_schemas))
                    where_clauses.append(f"n.nspname IN ({placeholders})")
                    params.extend(self.target_schemas)

                # Filter by specific tables if configured
                if self.target_tables:
                    table_conditions = []
                    for table_spec in self.target_tables:
                        if "." in table_spec:
                            schema, table = table_spec.split(".", 1)
                            table_conditions.append(
                                "(n.nspname = %s AND c.relname = %s)"
                            )
                            params.extend([schema, table])
                        else:
                            table_conditions.append("c.relname = %s")
                            params.append(table_spec)
                    where_clauses.append(f"({' OR '.join(table_conditions)})")

                where_clause = " AND ".join(where_clauses) if where_clauses else "TRUE"

                query = f"""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        c.oid as table_oid,
                        pg_total_relation_size(c.oid) as size_bytes,
                        pg_total_relation_size(c.oid)::numeric / (1024^3) as size_gb,
                        c.reltuples::bigint as estimated_rows
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE c.relkind = 'r'  -- regular tables only
                    AND {where_clause}
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY size_bytes DESC
                """

                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get tables to analyze: {e}")
            return []

    def _analyze_table(
        self, schema_name: str, table_name: str, table_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze data sizes for a specific table.

        Uses TABLESAMPLE for efficient sampling on large tables.
        """
        try:
            # Get columns to analyze
            columns_to_check = self._get_columns_to_check(schema_name, table_name)

            if not columns_to_check:
                self.logger.debug(
                    f"No checkable columns found in {schema_name}.{table_name}"
                )
                return None

            self.logger.info(
                f"Analyzing {schema_name}.{table_name} "
                f"({table_info['estimated_rows']:,} rows, {len(columns_to_check)} columns)"
            )

            # Build and execute analysis query
            column_analyses = []

            for col_name, col_type, storage_strategy in columns_to_check:
                col_analysis = self._analyze_column(
                    schema_name, table_name, col_name, table_info
                )
                if col_analysis:
                    # Add PostgreSQL-specific TOAST storage information
                    col_analysis["data_type"] = col_type
                    col_analysis["toast_storage_strategy"] = storage_strategy
                    column_analyses.append(col_analysis)

            # Determine if table has large columns
            has_large_columns = any(
                col.get("count_gt_1kb", 0) > 0 or col.get("count_gt_64kb", 0) > 0
                for col in column_analyses
            )

            return {
                "schema": schema_name,
                "table": table_name,
                "estimated_rows": table_info["estimated_rows"],
                "size_gb": float(table_info["size_gb"]),
                "sampled": self.sample_percent < 100,
                "sample_percent": self.sample_percent,
                "columns": column_analyses,
                "has_large_columns": has_large_columns,
            }

        except Exception as e:
            self.logger.error(
                f"Failed to analyze table {schema_name}.{table_name}: {e}"
            )
            return None

    def _get_columns_to_check(self, schema_name: str, table_name: str) -> List[tuple]:
        """Get list of columns to check based on data types."""
        try:
            with self.connection.cursor() as cursor:
                # Build type filter
                type_patterns = []
                for col_type in self.check_column_types:
                    type_patterns.append(
                        f"pg_catalog.format_type(a.atttypid, a.atttypmod) ILIKE '%{col_type}%'"
                    )

                type_filter = " OR ".join(type_patterns)

                cursor.execute(
                    f"""
                    SELECT
                        a.attname as column_name,
                        pg_catalog.format_type(a.atttypid, a.atttypmod) as data_type,
                        CASE a.attstorage
                            WHEN 'p' THEN 'PLAIN'
                            WHEN 'e' THEN 'EXTERNAL'
                            WHEN 'm' THEN 'MAIN'
                            WHEN 'x' THEN 'EXTENDED'
                        END as storage_strategy
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE n.nspname = %s
                    AND c.relname = %s
                    AND a.attnum > 0
                    AND NOT a.attisdropped
                    AND ({type_filter})
                    ORDER BY a.attnum
                    """,
                    (schema_name, table_name),
                )

                return [
                    (row["column_name"], row["data_type"], row["storage_strategy"])
                    for row in cursor.fetchall()
                ]

        except Exception as e:
            self.logger.error(
                f"Failed to get columns for {schema_name}.{table_name}: {e}"
            )
            return []

    def _analyze_column(
        self,
        schema_name: str,
        table_name: str,
        column_name: str,
        table_info: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Analyze size statistics for a specific column using sampling."""
        try:
            with self.connection.cursor() as cursor:
                # Build TABLESAMPLE clause for large tables
                sample_clause = ""
                if self.sample_percent < 100 and table_info["estimated_rows"] > 1000:
                    sample_clause = f"TABLESAMPLE BERNOULLI ({self.sample_percent})"

                # Build size check query
                query = f"""
                    SELECT
                        COUNT(*) as total_rows,
                        COUNT(*) FILTER (WHERE pg_column_size({column_name}) > %s) as count_gt_1kb,
                        COUNT(*) FILTER (WHERE pg_column_size({column_name}) > %s) as count_gt_64kb,
                        MAX(pg_column_size({column_name})) as max_size_bytes,
                        AVG(pg_column_size({column_name}))::bigint as avg_size_bytes,
                        pg_size_pretty(MAX(pg_column_size({column_name}))::bigint) as max_size_human,
                        pg_size_pretty(AVG(pg_column_size({column_name}))::bigint) as avg_size_human
                    FROM "{schema_name}"."{table_name}" {sample_clause}
                """

                cursor.execute(
                    query, (self.size_thresholds["1kb"], self.size_thresholds["64kb"])
                )

                result = dict(cursor.fetchone())

                # Only return if we have data and some values exceed thresholds
                if result["total_rows"] > 0:
                    return {
                        "column_name": column_name,
                        "total_rows_checked": result["total_rows"],
                        "count_gt_1kb": result["count_gt_1kb"],
                        "count_gt_64kb": result["count_gt_64kb"],
                        "max_size_bytes": result["max_size_bytes"],
                        "avg_size_bytes": result["avg_size_bytes"],
                        "max_size_human": result["max_size_human"],
                        "avg_size_human": result["avg_size_human"],
                        "percent_gt_1kb": (
                            round(
                                (result["count_gt_1kb"] / result["total_rows"]) * 100, 2
                            )
                            if result["total_rows"] > 0
                            else 0
                        ),
                        "percent_gt_64kb": (
                            round(
                                (result["count_gt_64kb"] / result["total_rows"]) * 100,
                                2,
                            )
                            if result["total_rows"] > 0
                            else 0
                        ),
                    }

                return None

        except Exception as e:
            self.logger.warning(
                f"Failed to analyze column {schema_name}.{table_name}.{column_name}: {e}"
            )
            return None
