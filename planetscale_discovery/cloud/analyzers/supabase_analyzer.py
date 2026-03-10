"""
Supabase Cloud Infrastructure Analyzer

Analyzes Supabase-hosted PostgreSQL projects including database configuration,
connection pooling, networking, and operational details.
"""

from typing import Dict, Any, List, Optional
import logging

try:
    import requests

    HAS_SUPABASE_LIBS = True
except ImportError:
    HAS_SUPABASE_LIBS = False

from ...common.base_analyzer import CloudAnalyzer
from ...common.utils import generate_timestamp


class SupabaseAnalyzer(CloudAnalyzer):
    """Analyzer for Supabase managed PostgreSQL infrastructure."""

    def __init__(self, config: Any, logger: Optional[logging.Logger] = None):
        """Initialize Supabase analyzer."""
        super().__init__(config, "supabase", logger)
        self.access_token = None
        self.api_base_url = "https://api.supabase.com/v1"
        self.session = None

    def discover_resources(self) -> List[str]:
        """Discover available Supabase projects."""
        try:
            if not self.authenticate():
                return []

            response = self.session.get(f"{self.api_base_url}/projects")

            if response.status_code != 200:
                return []

            project_list = response.json()
            return [project.get("id") for project in project_list if project.get("id")]

        except Exception as e:
            self.add_error("Failed to discover resources", e)
            return []

    def authenticate(self) -> bool:
        """Authenticate with Supabase Management API."""
        if not HAS_SUPABASE_LIBS:
            self.add_error(
                "Supabase libraries not installed. Install with: pip install 'planetscale-discovery-tools[supabase]'"
            )
            return False

        try:
            # Get access token from config or environment
            self.access_token = getattr(self.config, "access_token", None)

            if not self.access_token:
                import os

                self.access_token = os.environ.get("SUPABASE_ACCESS_TOKEN")

            if not self.access_token:
                self.add_error(
                    "No Supabase access token provided. "
                    "Set it in your config file under providers.supabase.access_token, "
                    "or set the SUPABASE_ACCESS_TOKEN environment variable.\n"
                    "  Generate a token at: https://supabase.com/dashboard/account/tokens\n"
                    "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/supabase.md"
                )
                return False

            # Create session with auth header
            self.session = requests.Session()
            self.session.headers.update(
                {
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                }
            )

            # Test authentication by fetching projects
            response = self.session.get(f"{self.api_base_url}/projects")

            if response.status_code == 401:
                self.add_error(
                    "Invalid or expired Supabase access token. "
                    "Verify your token is correct and hasn't been revoked.\n"
                    "  Generate a new token at: https://supabase.com/dashboard/account/tokens\n"
                    "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/supabase.md"
                )
                return False
            elif response.status_code != 200:
                self.add_error(f"Supabase API error: HTTP {response.status_code}")
                return False

            self.logger.info("Successfully authenticated with Supabase")
            return True

        except Exception as e:
            self.add_error("Supabase authentication failed", e)
            return False

    def analyze(self) -> Dict[str, Any]:
        """
        Perform comprehensive Supabase infrastructure analysis.

        Returns:
            Dictionary containing Supabase infrastructure analysis
        """
        if not self.authenticate():
            return {
                "error": "Authentication failed",
                "timestamp": generate_timestamp(),
                "metadata": self.get_analysis_metadata(),
            }

        results = {
            "provider": "supabase",
            "timestamp": generate_timestamp(),
            "projects": [],
            "summary": {},
            "metadata": self.get_analysis_metadata(),
        }

        try:
            # Discover projects
            projects = self._discover_projects()
            results["projects"] = projects

            # Generate summary
            results["summary"] = self._generate_summary(projects)

            self.logger.info(
                f"Supabase analysis completed. Found {len(projects)} project(s)"
            )

        except Exception as e:
            self.add_error("Supabase analysis failed", e)

        results["metadata"] = self.get_analysis_metadata()
        return results

    def _discover_projects(self) -> List[Dict[str, Any]]:
        """
        Discover all accessible Supabase projects.

        Returns:
            List of project details
        """
        projects = []

        try:
            # Check if specific project is targeted
            target_ref = getattr(self.config, "project_ref", None)

            if target_ref:
                # Analyze specific project
                project_data = self._analyze_project(target_ref)
                if project_data:
                    projects.append(project_data)
            else:
                # Discover all projects
                response = self.session.get(f"{self.api_base_url}/projects")

                if response.status_code != 200:
                    self.add_warning(f"Failed to list projects: {response.status_code}")
                    return projects

                project_list = response.json()

                for project in project_list:
                    project_ref = project.get("id")
                    if project_ref:
                        project_data = self._analyze_project(project_ref)
                        if project_data:
                            projects.append(project_data)

        except Exception as e:
            self.add_error("Project discovery failed", e)

        return projects

    def _analyze_project(self, project_ref: str) -> Optional[Dict[str, Any]]:
        """
        Analyze a single Supabase project.

        Args:
            project_ref: Project reference ID

        Returns:
            Project analysis data or None if failed
        """
        try:
            project_data = {
                "project_ref": project_ref,
                "timestamp": generate_timestamp(),
            }

            # Get project details
            details = self._get_project_details(project_ref)
            if details:
                project_data.update(details)

            # Get database config
            db_config = self._get_database_config(project_ref)
            if db_config:
                project_data["database"] = db_config

            # Add plan-based compute specs if API doesn't provide them
            if db_config and not db_config.get("available"):
                plan = project_data.get("organization_plan", "free")
                project_data["compute_specs"] = self._get_plan_based_specs(plan)

            # Get pooling config
            pooling = self._get_pooling_config(project_ref)
            if pooling:
                project_data["connection_pooling"] = pooling

            # Get network config
            networking = self._get_network_config(project_ref)
            if networking:
                project_data["networking"] = networking

            return project_data

        except Exception as e:
            self.add_warning(f"Failed to analyze project {project_ref}: {e}")
            return None

    def _get_plan_based_specs(self, plan: str) -> Dict[str, Any]:
        """
        Get compute specifications based on Supabase plan tier.

        Args:
            plan: Supabase plan (free, pro, team, enterprise)

        Returns:
            Dictionary with compute and storage specs
        """
        # Supabase plan specifications as of 2024
        # https://supabase.com/pricing
        specs = {
            "free": {
                "compute": "Shared (2-core ARM)",
                "memory_gb": "Shared",
                "storage_gb": 500,  # 500 MB = 0.5 GB
                "connection_limit": 60,
                "note": "Free tier includes 500 MB database space and 2 GB file storage",
            },
            "pro": {
                "compute": "Dedicated 2-core ARM",
                "memory_gb": 1,
                "storage_gb": 8,
                "connection_limit": 200,
                "note": "Pro tier includes 8 GB database space, upgradable to 256 GB",
            },
            "team": {
                "compute": "Dedicated 2-core ARM (upgradable)",
                "memory_gb": 1,
                "storage_gb": 8,
                "connection_limit": 200,
                "note": "Team tier starts at 8 GB, upgradable compute and storage",
            },
            "enterprise": {
                "compute": "Custom",
                "memory_gb": "Custom",
                "storage_gb": "Custom",
                "connection_limit": "Custom",
                "note": "Enterprise tier with custom compute and storage",
            },
        }

        return specs.get(plan.lower(), specs["free"])

    def _get_project_details(self, project_ref: str) -> Optional[Dict[str, Any]]:
        """Get basic project information."""
        try:
            response = self.session.get(f"{self.api_base_url}/projects/{project_ref}")

            if response.status_code != 200:
                self.add_warning(f"Failed to get project details for {project_ref}")
                return None

            data = response.json()

            project_info = {
                "name": data.get("name", "Unknown"),
                "organization_id": data.get("organization_id"),
                "region": data.get("region", "Unknown"),
                "created_at": data.get("created_at"),
                "status": data.get("status", "unknown"),
            }

            # Get organization plan information
            org_id = data.get("organization_id")
            if org_id:
                org_data = self._get_organization_details(org_id)
                if org_data:
                    project_info["organization_plan"] = org_data.get("plan", "free")

            # Get database version if available
            database_info = data.get("database", {})
            if database_info:
                project_info["database_version"] = database_info.get("version")
                project_info["postgres_engine"] = database_info.get("postgres_engine")

            return project_info

        except Exception as e:
            self.add_warning(f"Error getting project details: {e}")
            return None

    def _get_organization_details(self, org_id: str) -> Optional[Dict[str, Any]]:
        """Get organization details including plan tier."""
        try:
            response = self.session.get(f"{self.api_base_url}/organizations/{org_id}")

            if response.status_code != 200:
                return None

            return response.json()

        except Exception as e:
            self.add_warning(f"Error getting organization details: {e}")
            return None

    def _get_database_config(self, project_ref: str) -> Optional[Dict[str, Any]]:
        """Get database configuration details."""
        try:
            # Supabase Management API endpoint for database settings
            response = self.session.get(
                f"{self.api_base_url}/projects/{project_ref}/config/database"
            )

            if response.status_code == 404:
                # Endpoint might not exist, return basic info
                self.add_warning(
                    "Database config endpoint not available, using plan-based defaults"
                )
                return {"available": False}

            if response.status_code != 200:
                return None

            data = response.json()

            return {
                "version": data.get("version", "Unknown"),
                "host": data.get("host"),
                "port": data.get("port", 5432),
                "instance_size": data.get("instance_size", "Unknown"),
                "connection_limit": data.get("db_max_connections"),
                "storage_gb": data.get("db_size_gb"),
            }

        except Exception as e:
            self.add_warning(f"Error getting database config: {e}")
            return {"available": False}

    def _get_pooling_config(self, project_ref: str) -> Optional[Dict[str, Any]]:
        """Get connection pooling (PgBouncer) configuration."""
        try:
            # Supabase uses PgBouncer for connection pooling
            response = self.session.get(
                f"{self.api_base_url}/projects/{project_ref}/config/pgbouncer"
            )

            if response.status_code == 404:
                # Return default info about Supabase pooling
                return {
                    "enabled": True,
                    "mode": "transaction",
                    "pool_size": 15,
                    "note": "Default Supabase PgBouncer configuration",
                }

            if response.status_code != 200:
                return None

            data = response.json()

            return {
                "enabled": data.get("enabled", True),
                "mode": data.get("pool_mode", "transaction"),
                "pool_size": data.get("default_pool_size", 15),
                "max_client_conn": data.get("max_client_conn"),
            }

        except Exception as e:
            self.add_warning(f"Error getting pooling config: {e}")
            return None

    def _get_network_config(self, project_ref: str) -> Optional[Dict[str, Any]]:
        """Get networking and connection information."""
        try:
            # Get project details which includes connection info
            response = self.session.get(f"{self.api_base_url}/projects/{project_ref}")

            if response.status_code != 200:
                return None

            data = response.json()

            # Extract database host from project data
            db_host = data.get("database", {}).get("host")
            if not db_host:
                # Construct default host
                db_host = f"db.{project_ref}.supabase.co"

            return {
                "host": db_host,
                "direct_connection": f"{db_host}:5432",
                "pooler_connection": f"{db_host}:6543",
                "ssl_enforced": True,
                "ipv6_enabled": data.get("ipv6_enabled", True),
                "connection_strings": {
                    "direct": f"postgresql://postgres:[YOUR-PASSWORD]@{db_host}:5432/postgres",
                    "pooler": f"postgresql://postgres:[YOUR-PASSWORD]@{db_host}:6543/postgres?pgbouncer=true",
                },
            }

        except Exception as e:
            self.add_warning(f"Error getting network config: {e}")
            return None

    def _generate_summary(self, projects: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate summary statistics for discovered projects."""
        regions = set()
        database_versions = set()

        for project in projects:
            if "region" in project:
                regions.add(project["region"])
            if "database" in project and "version" in project["database"]:
                database_versions.add(project["database"]["version"])

        # Convert sets to sorted lists
        summary = {
            "total_projects": len(projects),
            "regions": sorted(list(regions)),
            "database_versions": sorted(list(database_versions)),
        }

        return summary
