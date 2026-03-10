#!/usr/bin/env python3
"""
PostgreSQL Environment Discovery Tool
Main entry point for comprehensive PostgreSQL environment analysis.
"""

import logging
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
            "sslmode": self.config.database.ssl_mode,
            "connect_timeout": self.config.database.connection_timeout,
        }

        discovery = PostgreSQLDiscovery(
            config_dict, data_size_config=self.config.database.data_size
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

    def __init__(self, connection_params: Dict[str, Any], data_size_config=None):
        self.connection_params = connection_params
        self.data_size_config = data_size_config
        self.connection = None
        self.results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "discovery_version": __version__,
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

            self.logger.info(f"Connected to PostgreSQL: {result['version']}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to connect to PostgreSQL: {e}")
            return False

    def run_analysis(self, modules: list = None) -> Dict[str, Any]:
        """Run comprehensive PostgreSQL environment analysis."""
        if not self.connection:
            raise RuntimeError("No database connection established")

        available_modules = {
            "config": ConfigAnalyzer,
            "schema": SchemaAnalyzer,
            "performance": PerformanceAnalyzer,
            "security": SecurityAnalyzer,
            "features": FeatureAnalyzer,
            "data_size": DataSizeAnalyzer,
        }

        # Run all modules if none specified
        if modules is None:
            modules = list(available_modules.keys())

        self.logger.info(f"Running analysis modules: {modules}")

        # Create a single shared connection for all analyzers
        analyzer_connection = None
        try:
            analyzer_connection = psycopg2.connect(
                cursor_factory=RealDictCursor, **self.connection_params
            )
            analyzer_connection.autocommit = True

            # Set statement timeout to protect against runaway queries
            statement_timeout = self.connection_params.get("statement_timeout", "300s")
            with analyzer_connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = %s", (statement_timeout,))

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
            for module_name in modules:
                if module_name not in available_modules:
                    self.logger.warning(f"Unknown module: {module_name}")
                    continue

                try:
                    self.logger.info(f"Running {module_name} analysis...")

                    # Pass configuration to data_size analyzer
                    if module_name == "data_size" and self.data_size_config:
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
                            },
                        )
                    else:
                        analyzer = available_modules[module_name](analyzer_connection)

                    module_results = analyzer.analyze()
                    self.results["analysis_results"][module_name] = module_results

                    # Extract gaps from module results
                    self._extract_analysis_gaps(module_name, module_results)

                    self.logger.info(f"Completed {module_name} analysis")

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
            if analyzer_connection:
                analyzer_connection.close()

        return self.results

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
        if "does not exist" in error_msg_lower:
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

        return "Minor analysis limitation - should not significantly impact results"

    def close(self):
        """Close database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
