"""
Cloud Database Environment Discovery Tool

Multi-cloud database infrastructure discovery and analysis.
"""

import logging
from typing import Dict, Any

from planetscale_discovery import __version__
from ..config.config_manager import DiscoveryConfig
from ..common.utils import (
    setup_logging,
    generate_timestamp,
)

# Lazy imports for optional cloud provider dependencies
# These will be imported only when the respective provider is enabled


class CloudDiscoveryTool:
    """Main cloud discovery tool orchestrator."""

    def __init__(self, config: DiscoveryConfig):
        """Initialize the discovery tool."""
        self.config = config
        # Use existing logger if available, otherwise set up new one
        self.logger = logging.getLogger("planetscale_discovery")
        if not self.logger.handlers:
            self.logger = setup_logging(config.log_level, config.log_file)
        self.results = {
            "discovery_version": __version__,
            "timestamp": generate_timestamp(),
            "providers": {},
            "summary": {},
            "errors": [],
            "warnings": [],
        }

    def discover(self) -> Dict[str, Any]:
        """Run discovery across all enabled providers."""
        self.logger.info("Starting cloud database environment discovery")

        try:
            # Discover AWS resources
            if self.config.aws.enabled:
                self._discover_aws()

            # Discover GCP resources
            if self.config.gcp.enabled:
                self._discover_gcp()

            # Discover Supabase resources
            if self.config.supabase.enabled:
                self._discover_supabase()

            # Discover Heroku resources
            if self.config.heroku.enabled:
                self._discover_heroku()

            # Generate summary
            self._generate_summary()

            self.logger.info("Cloud discovery completed successfully")

        except Exception as e:
            self.logger.error(f"Discovery failed: {e}")
            self.results["errors"].append(
                {
                    "timestamp": generate_timestamp(),
                    "message": f"Discovery failed: {e}",
                    "type": "FATAL",
                }
            )

        return self.results

    def run(self) -> Dict[str, Any]:
        """Alias for discover() method for compatibility."""
        return self.discover()

    def _discover_aws(self) -> None:
        """Discover AWS resources."""
        self.logger.info("Starting AWS discovery")

        try:
            # Lazy import AWS analyzer only when needed
            try:
                from .analyzers.aws_analyzer import AWSAnalyzer
            except ImportError:
                self.logger.error(
                    'AWS dependencies not installed. Install with: pip install "ps-discovery[aws]"'
                )
                self.results["errors"].append(
                    {
                        "timestamp": generate_timestamp(),
                        "message": "AWS dependencies not installed",
                        "type": "DEPENDENCY_ERROR",
                    }
                )
                return

            # Pass the target_database from main config to AWS config
            aws_config = self.config.aws
            target_db = getattr(self.config, "target_database", None)
            if target_db:
                aws_config.target_database = target_db

            aws_analyzer = AWSAnalyzer(aws_config, self.logger)

            if not aws_analyzer.authenticate():
                for error in aws_analyzer.errors:
                    self.results["errors"].append(error)
                return

            aws_results = aws_analyzer.analyze()
            self.results["providers"]["aws"] = aws_results

            self.logger.info(
                f"AWS discovery completed. Found {len(aws_results.get('resources', {}))} resource types"
            )

        except Exception as e:
            self.logger.error(f"AWS discovery failed: {e}")
            self.results["errors"].append(
                {
                    "timestamp": generate_timestamp(),
                    "message": f"AWS discovery failed: {e}",
                    "type": "PROVIDER_ERROR",
                }
            )

    def _discover_gcp(self) -> None:
        """Discover GCP resources."""
        self.logger.info("Starting GCP discovery")

        try:
            # Lazy import GCP analyzer only when needed
            try:
                from .analyzers.gcp_analyzer import GCPAnalyzer
            except ImportError:
                self.logger.error(
                    'GCP dependencies not installed. Install with: pip install "ps-discovery[gcp]"'
                )
                self.results["errors"].append(
                    {
                        "timestamp": generate_timestamp(),
                        "message": "GCP dependencies not installed",
                        "type": "DEPENDENCY_ERROR",
                    }
                )
                return

            gcp_analyzer = GCPAnalyzer(self.config.gcp, self.logger)

            if not gcp_analyzer.authenticate():
                for error in gcp_analyzer.errors:
                    self.results["errors"].append(error)
                return

            gcp_results = gcp_analyzer.analyze()
            self.results["providers"]["gcp"] = gcp_results

            self.logger.info(
                f"GCP discovery completed. Found {len(gcp_results.get('resources', {}))} resource types"
            )

        except Exception as e:
            self.logger.error(f"GCP discovery failed: {e}")
            self.results["errors"].append(
                {
                    "timestamp": generate_timestamp(),
                    "message": f"GCP discovery failed: {e}",
                    "type": "PROVIDER_ERROR",
                }
            )

    def _discover_supabase(self) -> None:
        """Discover Supabase resources."""
        self.logger.info("Starting Supabase discovery")

        try:
            # Lazy import Supabase analyzer only when needed
            try:
                from .analyzers.supabase_analyzer import SupabaseAnalyzer
            except ImportError:
                self.logger.error(
                    'Supabase dependencies not installed. Install with: pip install "ps-discovery[supabase]"'
                )
                self.results["errors"].append(
                    {
                        "timestamp": generate_timestamp(),
                        "message": "Supabase dependencies not installed",
                        "type": "DEPENDENCY_ERROR",
                    }
                )
                return

            supabase_analyzer = SupabaseAnalyzer(self.config.supabase, self.logger)

            if not supabase_analyzer.authenticate():
                for error in supabase_analyzer.errors:
                    self.results["errors"].append(error)
                return

            supabase_results = supabase_analyzer.analyze()
            self.results["providers"]["supabase"] = supabase_results

            self.logger.info(
                f"Supabase discovery completed. Found {len(supabase_results.get('projects', []))} project(s)"
            )

        except Exception as e:
            self.logger.error(f"Supabase discovery failed: {e}")
            self.results["errors"].append(
                {
                    "timestamp": generate_timestamp(),
                    "message": f"Supabase discovery failed: {e}",
                    "type": "PROVIDER_ERROR",
                }
            )

    def _discover_heroku(self) -> None:
        """Discover Heroku resources."""
        self.logger.info("Starting Heroku discovery")

        try:
            # Lazy import Heroku analyzer only when needed
            try:
                from .analyzers.heroku_analyzer import HerokuAnalyzer
            except ImportError:
                self.logger.error(
                    'Heroku dependencies not installed. Install with: pip install "ps-discovery[heroku]"'
                )
                self.results["errors"].append(
                    {
                        "timestamp": generate_timestamp(),
                        "message": "Heroku dependencies not installed",
                        "type": "DEPENDENCY_ERROR",
                    }
                )
                return

            heroku_analyzer = HerokuAnalyzer(self.config.heroku, self.logger)

            if not heroku_analyzer.authenticate():
                for error in heroku_analyzer.errors:
                    self.results["errors"].append(error)
                return

            heroku_results = heroku_analyzer.analyze()
            self.results["providers"]["heroku"] = heroku_results

            self.logger.info(
                f"Heroku discovery completed. Found {len(heroku_results.get('apps', []))} app(s)"
            )

        except Exception as e:
            self.logger.error(f"Heroku discovery failed: {e}")
            self.results["errors"].append(
                {
                    "timestamp": generate_timestamp(),
                    "message": f"Heroku discovery failed: {e}",
                    "type": "PROVIDER_ERROR",
                }
            )

    def _generate_summary(self) -> None:
        """Generate discovery summary."""
        summary = {
            "providers_discovered": list(self.results["providers"].keys()),
            "total_databases": 0,
            "total_clusters": 0,
            "total_regions": 0,
        }

        # Aggregate statistics from all providers
        regions_set = set()

        for provider, data in self.results["providers"].items():
            provider_summary = data.get("summary", {})

            summary["total_databases"] += provider_summary.get("database_instances", 0)
            summary["total_databases"] += provider_summary.get("cloud_sql_instances", 0)
            summary["total_databases"] += provider_summary.get("total_projects", 0)
            summary["total_databases"] += provider_summary.get("total_databases", 0)
            summary["total_clusters"] += provider_summary.get("database_clusters", 0)
            summary["total_clusters"] += provider_summary.get("alloydb_clusters", 0)

            # Collect regions
            regions_set.update(data.get("regions_analyzed", []))
            regions_set.update(provider_summary.get("regions", []))

        summary["total_regions"] = len(regions_set)
        summary["regions"] = sorted(list(regions_set))

        self.results["summary"] = summary
