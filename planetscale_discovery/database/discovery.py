#!/usr/bin/env python3
"""
PostgreSQL Environment Discovery Tool
Main entry point for comprehensive PostgreSQL environment analysis.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any

from planetscale_discovery import __version__
import psycopg2
import psycopg2.extensions
from psycopg2.extras import RealDictCursor

from .analyzers.config_analyzer import ConfigAnalyzer
from .analyzers.schema_analyzer import SchemaAnalyzer
from .analyzers.performance_analyzer import PerformanceAnalyzer
from .analyzers.security_analyzer import SecurityAnalyzer
from .analyzers.feature_analyzer import FeatureAnalyzer
from .analyzers.data_size_analyzer import DataSizeAnalyzer
from .analyzers.citus_analyzer import CitusAnalyzer
from ..config.config_manager import effective_ssl_mode
from .citus import CitusClusterError, detect_citus, preflight
from .cockroach import (
    allow_unsafe_internals,
    detect_cockroach_flavor,
    restricts_internals_by_default,
)


class DatabaseDiscoveryTool:
    """Database discovery tool wrapper for unified CLI."""

    def __init__(self, config):
        """Initialize with configuration."""
        self.config = config

    def discover(self):
        """Run database discovery."""
        # Convert config to dict format expected by PostgreSQLDiscovery
        config_dict = {
            "host": self.config.database.host,
            "port": self.config.database.port,
            "database": self.config.database.database,
            "user": self.config.database.username,
            "password": self.config.database.password,
            "sslmode": effective_ssl_mode(self.config.database),
            "connect_timeout": self.config.database.connection_timeout,
        }
        if getattr(self.config.database, "ssl_root_cert", None):
            config_dict["sslrootcert"] = self.config.database.ssl_root_cert
        if getattr(self.config.database, "ssl_cert", None):
            config_dict["sslcert"] = self.config.database.ssl_cert
        if getattr(self.config.database, "ssl_key", None):
            config_dict["sslkey"] = self.config.database.ssl_key

        discovery = PostgreSQLDiscovery(
            config_dict,
            data_size_config=self.config.database.data_size,
            excluded_databases=self.config.database.excluded_databases,
            statement_timeout=self.config.database.statement_timeout,
            citus_detection=True,
        )
        if not discovery.connect():
            raise ConnectionError("Failed to connect to PostgreSQL database")

        # Use database_analyzers if set by CLI --analyzers flag, otherwise use default
        analysis_modules = getattr(
            self.config,
            "database_analyzers",
            ["config", "schema", "performance", "security", "features"],
        )

        # Add data_size module if enabled in config
        if (
            self.config.database.data_size.enabled
            and "data_size" not in analysis_modules
        ):
            analysis_modules.append("data_size")

        return discovery.run_analysis(analysis_modules)


class PostgreSQLDiscovery:
    """Main PostgreSQL environment discovery coordinator."""

    def __init__(
        self,
        connection_params: Dict[str, Any],
        data_size_config=None,
        excluded_databases=None,
        statement_timeout: str = "300s",
        citus_detection: bool = False,
    ):
        # statement_timeout is a server GUC, not a libpq connection parameter, so
        # it is held separately. Leaving it inside connection_params would make
        # psycopg2.connect() reject it as an unknown keyword.
        self.connection_params = {
            k: v for k, v in connection_params.items() if k != "statement_timeout"
        }
        self.statement_timeout = (
            connection_params.get("statement_timeout") or statement_timeout
        )
        self.data_size_config = data_size_config
        self.excluded_databases = excluded_databases or []
        # Only callers that can stop on CitusClusterError opt in. Workload
        # capture does not: its connection runs every query in one transaction.
        self.citus_detection = citus_detection
        self.citus = None
        self.connection = None
        self.results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "discovery_version": __version__,
            "engine": "postgres",
            "connection_info": {},
            "analysis_results": {},
            "analysis_gaps": [],
        }

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        self.logger = logging.getLogger(__name__)

    def connect(self) -> bool:
        """Establish connection to PostgreSQL database."""
        try:
            self.connection = psycopg2.connect(
                cursor_factory=RealDictCursor, **self.connection_params
            )

            # Set connection to use UTF-8 with error handling
            # This tells psycopg2 to replace invalid UTF-8 sequences
            self.connection.set_client_encoding("UTF8")

            # Test connection and get basic info
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT version(), current_database(), current_user")
                result = cursor.fetchone()
                self.results["connection_info"] = {
                    "version_string": result["version"],
                    "current_database": result["current_database"],
                    "current_user": result["current_user"],
                }

            flavor = detect_cockroach_flavor(result["version"])
            if flavor:
                self.results["engine"] = "cockroachdb"
                self.results["flavor"] = flavor
                self.logger.info(
                    f"Detected CockroachDB {flavor['version']} from version(); "
                    "using CockroachDB-native analyzers"
                )
                self.logger.info(f"Connected to CockroachDB: {result['version']}")
            else:
                self.logger.info(f"Connected to PostgreSQL: {result['version']}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to connect to PostgreSQL: {e}")
            return False

    def run_analysis(
        self, modules: list = None, reuse_connection: bool = False
    ) -> Dict[str, Any]:
        """Run comprehensive PostgreSQL environment analysis."""
        if not self.connection:
            raise RuntimeError("No database connection established")

        available_modules = self._analyzer_classes()

        # Run all modules if none specified. The citus module is added only
        # when the extension is detected.
        if modules is None:
            modules = [name for name in available_modules if name != "citus"]

        self.logger.info(f"Running analysis modules: {modules}")

        analyzer_connection = None
        try:
            if reuse_connection:
                analyzer_connection = self.connection
            else:
                analyzer_connection = psycopg2.connect(
                    cursor_factory=RealDictCursor, **self.connection_params
                )
                analyzer_connection.autocommit = True
                with analyzer_connection.cursor() as cursor:
                    cursor.execute(
                        "SET statement_timeout = %s", (self.statement_timeout,)
                    )
                    cursor.execute("SET search_path = pg_catalog")
            if self.results.get("engine") == "cockroachdb":
                self._allow_cockroach_internals(analyzer_connection)

        except Exception as e:
            self.logger.error(f"Failed to create analyzer connection: {e}")
            for module_name in modules:
                self.results["analysis_results"][module_name] = {
                    "error": str(e),
                    "status": "failed",
                }
                self._add_analysis_gap(
                    module_name,
                    "connection_issue",
                    "Could not create analyzer connection",
                    str(e),
                    "high",
                )
            return self.results

        try:
            # Raises CitusClusterError before any analyzer runs, so a cluster
            # that cannot be read fails in seconds, not after a full run.
            citus_ctx = self._prepare_citus(analyzer_connection)
            if citus_ctx is not None and "citus" not in modules:
                modules = list(modules) + ["citus"]
            citus_config = {"citus": citus_ctx} if citus_ctx is not None else {}

            for module_name in modules:
                if module_name not in available_modules:
                    self.logger.warning(f"Unknown module: {module_name}")
                    continue
                if module_name == "citus" and citus_ctx is None:
                    self.logger.warning(
                        "Skipping citus analysis: the citus extension was not detected"
                    )
                    continue

                try:
                    self.logger.info(f"Running {module_name} analysis...")
                    module_start = time.monotonic()

                    # Pass configuration to analyzers that need it
                    if module_name == "schema":
                        analyzer = available_modules[module_name](
                            analyzer_connection,
                            config={
                                "excluded_databases": self.excluded_databases,
                                **citus_config,
                            },
                        )
                    elif module_name == "data_size" and self.data_size_config:
                        # Pass configuration to data_size analyzer
                        analyzer = available_modules[module_name](
                            analyzer_connection,
                            config={
                                "enabled": self.data_size_config.enabled,
                                "sample_percent": self.data_size_config.sample_percent,
                                "max_table_size_gb": self.data_size_config.max_table_size_gb,
                                "target_tables": self.data_size_config.target_tables,
                                "target_schemas": self.data_size_config.target_schemas,
                                "check_column_types": self.data_size_config.check_column_types,
                                "size_thresholds": self.data_size_config.size_thresholds,
                                **citus_config,
                            },
                        )
                    elif citus_config:
                        analyzer = available_modules[module_name](
                            analyzer_connection, config=citus_config
                        )
                    else:
                        analyzer = available_modules[module_name](analyzer_connection)

                    module_results = analyzer.analyze()
                    self.results["analysis_results"][module_name] = module_results

                    # Extract gaps from module results
                    self._extract_analysis_gaps(module_name, module_results)
                    self._collect_analyzer_issues(module_name, analyzer)

                    elapsed = time.monotonic() - module_start
                    self.logger.info(
                        f"Completed {module_name} analysis in {elapsed:.1f}s"
                    )

                except CitusClusterError:
                    # Recorded as a module failure, it would let a report with
                    # shell values for every Citus table be written.
                    raise
                except Exception as e:
                    self.logger.error(f"Failed to run {module_name} analysis: {e}")
                    self.results["analysis_results"][module_name] = {
                        "error": str(e),
                        "status": "failed",
                    }
                    # Track module-level failure as a gap
                    self._add_analysis_gap(
                        module_name,
                        "module_failure",
                        f"Entire {module_name} analysis failed",
                        str(e),
                        "high",
                    )
        finally:
            if analyzer_connection and not reuse_connection:
                analyzer_connection.close()

        if self.results.get("engine") == "cockroachdb":
            self._add_cockroach_gaps()

        return self.results

    def _prepare_citus(self, connection):
        """Detect the citus extension and confirm the cluster can be read.

        Returns the context when the extension is installed, else None. Raises
        CitusClusterError when a node, shard or privilege is unavailable.
        """
        if not self.citus_detection:
            return None
        ctx = detect_citus(connection)
        if ctx is None:
            return None
        self.logger.info(
            f"Detected the citus extension {ctx.extension_version}; "
            "checking every node and shard"
        )
        preflight(connection, ctx)
        self.citus = ctx
        self.results["connection_info"]["citus"] = ctx.connection_info()
        return ctx

    def _allow_cockroach_internals(self, connection):
        """Let the analyzer session read crdb_internal on CockroachDB 26.1+."""
        error = allow_unsafe_internals(connection)
        if error is None:
            self.results["connection_info"]["allow_unsafe_internals"] = "on"
            self.logger.info(
                "Set allow_unsafe_internals = on for this session so crdb_internal "
                "can be read. CockroachDB logs each read as unsafe_internals_accessed."
            )
            return
        flavor = self.results.get("flavor") or {}
        if not restricts_internals_by_default(flavor):
            self.logger.debug(f"allow_unsafe_internals not set: {error}")
            return
        self.logger.warning(f"Could not set allow_unsafe_internals = on: {error}")
        self._add_analysis_gap(
            "config",
            "cockroach_internals_blocked",
            (
                f"CockroachDB {flavor.get('version')} blocks crdb_internal by default "
                "and this session could not set allow_unsafe_internals = on"
            ),
            error,
            "high",
        )

    def _analyzer_classes(self):
        """PostgreSQL analyzers stay on the Postgres path.

        CockroachDB is detected from version() at connect time and swapped
        in here so the Postgres analyzer modules are not modified.
        """
        if self.results.get("engine") == "cockroachdb":
            from .cockroach_analyzers import (
                CockroachConfigAnalyzer,
                CockroachFeatureAnalyzer,
                CockroachPerformanceAnalyzer,
                CockroachSchemaAnalyzer,
            )

            return {
                "config": CockroachConfigAnalyzer,
                "schema": CockroachSchemaAnalyzer,
                "performance": CockroachPerformanceAnalyzer,
                "security": SecurityAnalyzer,
                "features": CockroachFeatureAnalyzer,
                # pg_relation_size / pg_database_size do not exist. Sizing is
                # in CockroachSchemaAnalyzer via tenant_span_stats.live_bytes.
            }
        return {
            "config": ConfigAnalyzer,
            "schema": SchemaAnalyzer,
            "performance": PerformanceAnalyzer,
            "security": SecurityAnalyzer,
            "features": FeatureAnalyzer,
            "data_size": DataSizeAnalyzer,
            "citus": CitusAnalyzer,
        }

    def _add_cockroach_gaps(self):
        """Record honest CockroachDB migration limits, not a silent success.

        A default full run (no ``--analyzers`` filter; ``run_analysis()``
        with modules unset) always requests schema and features, so both
        ``cockroach_unassessed`` and ``no_logical_replication`` are recorded
        when those catalogs come back empty or unread. Gaps are omitted only
        when a module was deliberately excluded (``--analyzers config``).
        Omit the gap rather than reword it as "not assessed".
        """
        analysis = self.results.get("analysis_results") or {}
        if "schema" in analysis:
            tables = (analysis.get("schema") or {}).get("table_analysis") or []
            if not tables:
                self._add_analysis_gap(
                    "schema",
                    "cockroach_unassessed",
                    "Not assessed: this looks like CockroachDB, sizes and tables could not be read",
                    "Table inventory is empty after CockroachDB-native sizing",
                    "high",
                )
        if "features" in analysis:
            self._add_analysis_gap(
                "features",
                "no_logical_replication",
                (
                    "CockroachDB has no PostgreSQL logical replication. "
                    "The only path is a downtime cutover with text or CSV COPY "
                    "(SELECT ...). Do not use pgcopydb or pg_dump for schema or data. "
                    "A custom changefeed-to-Postgres applier is not provided. "
                    "Schema comes from SHOW CREATE ALL TYPES, SHOW CREATE ALL TABLES, "
                    "SHOW TRIGGERS, and information_schema.routines."
                ),
                "pgcopydb 0.18 and pg_dump are not working; custom changefeed applier is not provided",
                "high",
            )
        config = self.results.get("analysis_results", {}).get("config") or {}
        for gap in (config.get("discovery_privileges") or {}).get("gaps") or []:
            self._add_analysis_gap(
                "config",
                "missing_privilege",
                gap.get("description") or f"Missing privilege {gap.get('privilege')}",
                gap.get("privilege") or "privilege",
                gap.get("severity") or "high",
            )
        for zone in (config.get("cockroach_cluster") or {}).get(
            "over_replicated_zones"
        ) or []:
            self._add_analysis_gap(
                "config",
                "over_replicated_zone",
                (
                    f"Zone {zone.get('target')} asks for {zone.get('num_replicas')} "
                    f"replicas on {zone.get('node_count')} nodes"
                ),
                "num_replicas exceeds node count",
                "medium",
            )

    def _add_analysis_gap(
        self,
        module: str,
        gap_type: str,
        description: str,
        error_msg: str,
        severity: str,
    ):
        """Add an analysis gap to the results."""
        gap = {
            "module": module,
            "type": gap_type,
            "description": description,
            "error_message": error_msg,
            "severity": severity,  # low, medium, high
            "impact": self._determine_impact(module, gap_type, error_msg),
        }
        self.results["analysis_gaps"].append(gap)

    def _collect_analyzer_issues(self, module_name: str, analyzer: Any):
        """Record analyzer errors and warnings as analysis gaps.

        Analyzers degrade gracefully: a rejected sub-query returns an empty list
        and records the reason on the analyzer. Without this, an empty section in
        the report is indistinguishable from a section that genuinely found
        nothing -- which is how a failed query can quietly become "no columns".
        """
        for error in getattr(analyzer, "errors", []):
            message = error.get("message", "Analysis error")
            gap_type, severity = self._categorize_error(message)
            self._add_analysis_gap(
                module_name,
                gap_type,
                message,
                error.get("exception", message),
                severity,
            )

        for warning in getattr(analyzer, "warnings", []):
            message = warning.get("message", "Analysis warning")
            self._add_analysis_gap(
                module_name, "analysis_warning", message, message, "low"
            )

    def _extract_analysis_gaps(self, module_name: str, module_results: dict):
        """Extract analysis gaps from module results."""
        if not isinstance(module_results, dict):
            return

        # Recursively look for 'error' keys in the results
        self._scan_for_errors(module_name, module_results, "")

    def _scan_for_errors(self, module_name: str, data: dict, path: str):
        """Recursively scan for error entries in analysis results."""
        if isinstance(data, dict):
            if "error" in data:
                # Found an error - categorize it
                error_msg = data["error"]
                feature_name = path or "general"
                gap_type, severity = self._categorize_error(error_msg)

                # Generate human-readable explanation
                human_explanation = self._generate_human_explanation(
                    module_name, feature_name, gap_type, error_msg
                )

                self._add_analysis_gap(
                    module_name,
                    gap_type,
                    f"Failed to analyze {feature_name} in {module_name}",
                    human_explanation,
                    severity,
                )
            else:
                # Keep scanning deeper
                for key, value in data.items():
                    if isinstance(value, dict):
                        new_path = f"{path}.{key}" if path else key
                        self._scan_for_errors(module_name, value, new_path)
                    elif isinstance(value, list):
                        for i, item in enumerate(value):
                            if isinstance(item, dict):
                                new_path = (
                                    f"{path}.{key}[{i}]" if path else f"{key}[{i}]"
                                )
                                self._scan_for_errors(module_name, item, new_path)

    def _categorize_error(self, error_msg: str):
        """Categorize error message to determine gap type and severity."""
        error_msg_lower = error_msg.lower()

        if "access to crdb_internal and system is restricted" in error_msg_lower:
            return "cockroach_internals_blocked", "high"

        # Permission-related errors
        if "permission denied" in error_msg_lower:
            return "permission_denied", "medium"

        # Extension-related errors
        if (
            "extension not available" in error_msg_lower
            or "pg_stat_statements" in error_msg_lower
        ):
            return "missing_extension", "low"

        # PostgreSQL version compatibility issues
        if "does not exist" in error_msg_lower or "unknown function" in error_msg_lower:
            if any(
                name in error_msg_lower
                for name in (
                    "pg_database_size",
                    "pg_relation_size",
                    "pg_total_relation_size",
                    "table inventory",
                    "tenant_span_stats",
                )
            ):
                return "sizing_unavailable", "high"
            if "relation" in error_msg_lower or "column" in error_msg_lower:
                return "version_compatibility", "medium"
            elif "function" in error_msg_lower:
                return "version_compatibility", "low"

        # Connection or query issues
        if "connection" in error_msg_lower or "timeout" in error_msg_lower:
            return "connection_issue", "high"

        # Default categorization
        return "analysis_error", "low"

    def _generate_human_explanation(
        self, module: str, feature: str, gap_type: str, error_msg: str
    ) -> str:
        """Generate a human-readable explanation for the analysis gap."""
        error_msg_lower = error_msg.lower()

        if gap_type == "permission_denied":
            if "pg_user_mapping" in error_msg_lower:
                return "Database user lacks permissions to view foreign server user mappings"
            elif "replication" in error_msg_lower:
                return "Database user lacks permissions to view replication status"
            else:
                return "Database user lacks sufficient permissions for this analysis"

        elif gap_type == "missing_extension":
            if "pg_stat_statements" in error_msg_lower:
                return "pg_stat_statements extension is not installed or enabled"
            else:
                return "Required PostgreSQL extension is not available"

        elif gap_type == "version_compatibility":
            if "pg_sequence_data" in error_msg_lower:
                return "Analysis uses features not available in PostgreSQL 17 (sequence data structure changed)"
            elif "tablename" in error_msg_lower:
                return (
                    "Analysis uses system catalog columns that changed in PostgreSQL 17"
                )
            elif "unnest" in error_msg_lower and "aclexplode" in error_msg_lower:
                return "Analysis uses PostgreSQL functions with different syntax in version 17"
            else:
                return "Analysis feature not compatible with this PostgreSQL version"

        elif gap_type == "connection_issue":
            return "Network or connection problems prevented this analysis"

        else:
            # Default explanations based on feature
            if "replication" in feature.lower():
                return "No replication configuration detected or accessible"
            elif "foreign_data" in feature.lower():
                return "Unable to access foreign data wrapper configuration"
            elif "performance" in module.lower():
                return "Performance monitoring data not accessible"
            elif "security" in module.lower():
                return "Security configuration details not accessible"
            else:
                return "Analysis could not be completed due to system limitations"

    def _determine_impact(self, module: str, gap_type: str, error_msg: str):
        """Determine the impact of an analysis gap."""
        error_msg_lower = error_msg.lower()

        # High impact gaps
        if gap_type == "permission_denied":
            if "foreign data wrapper" in error_msg_lower or "fdw" in error_msg_lower:
                return "External database connections may not be detected - verify manually"
            elif "replication" in error_msg_lower:
                return "Replication setup may not be detected - verify if database is part of a primary/replica configuration"
            elif "user_mapping" in error_msg_lower:
                return "User mappings for foreign servers may not be detected"
            else:
                return "Some advanced security features may not be fully analyzed"

        if gap_type == "version_compatibility":
            if "pg_sequence_data" in error_msg_lower:
                return "Sequence analysis may be incomplete - manually verify sequence usage in application"
            elif "tablename" in error_msg_lower:
                return "Some performance statistics may be unavailable - consider running analysis on older PostgreSQL version"
            else:
                return "Some features may not be available in this PostgreSQL version"

        # Medium/Low impact
        if gap_type == "connection_issue":
            return "Analysis was interrupted - consider re-running with better network connectivity"

        if gap_type in ("sizing_unavailable", "cockroach_unassessed"):
            return (
                "Database size and table inventory are missing. "
                "Do not quote 0 bytes or 0 tables."
            )
        if gap_type == "cockroach_internals_blocked":
            return (
                "Sizes, tables and compatibility checks read crdb_internal and are missing. "
                "Check that the session can run SET allow_unsafe_internals = on, then re-run."
            )
        if gap_type == "over_replicated_zone":
            return (
                "The zone asks for more replicas than the cluster has nodes, so its "
                "ranges stay under-replicated. Replica counts and replicated sizes "
                "will not match the zone config. Logical sizes are not affected."
            )
        if gap_type == "no_logical_replication":
            return (
                "Cannot use pgcopydb or pg_dump. "
                "Plan a downtime COPY (SELECT ...) cutover. "
                "A custom changefeed applier is not provided."
            )

        return "Minor analysis limitation - should not significantly impact results"

    def close(self):
        """Close database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
