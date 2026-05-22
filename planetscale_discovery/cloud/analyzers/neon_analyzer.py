"""
Neon Cloud Infrastructure Analyzer

Analyzes Neon serverless PostgreSQL projects including branches, compute endpoints,
connection pooling, autoscaling configuration, and database metadata.
"""

from typing import Dict, Any, List, Optional, Tuple
import logging

try:
    import requests

    HAS_NEON_LIBS = True
except ImportError:
    HAS_NEON_LIBS = False

from ...common.base_analyzer import CloudAnalyzer
from ...common.utils import generate_timestamp

# Neon Compute Unit (CU) specifications
# Maps CU size to vCPU, RAM, and estimated max_connections
# https://neon.tech/docs/manage/endpoints#compute-size-and-autoscaling-configuration
NEON_COMPUTE_SPECS = {
    0.25: {
        "vcpu": 0.25,
        "ram_gb": 1,
        "max_connections": 112,
    },
    0.5: {
        "vcpu": 0.5,
        "ram_gb": 2,
        "max_connections": 225,
    },
    1: {
        "vcpu": 1,
        "ram_gb": 4,
        "max_connections": 450,
    },
    2: {
        "vcpu": 2,
        "ram_gb": 8,
        "max_connections": 901,
    },
    3: {
        "vcpu": 3,
        "ram_gb": 12,
        "max_connections": 1351,
    },
    4: {
        "vcpu": 4,
        "ram_gb": 16,
        "max_connections": 1802,
    },
    5: {
        "vcpu": 5,
        "ram_gb": 20,
        "max_connections": 2252,
    },
    6: {
        "vcpu": 6,
        "ram_gb": 24,
        "max_connections": 2703,
    },
    7: {
        "vcpu": 7,
        "ram_gb": 28,
        "max_connections": 3153,
    },
    8: {
        "vcpu": 8,
        "ram_gb": 32,
        "max_connections": 3604,
    },
}


class NeonAnalyzer(CloudAnalyzer):
    """Analyzer for Neon serverless PostgreSQL infrastructure."""

    def __init__(self, config: Any, logger: Optional[logging.Logger] = None):
        """Initialize Neon analyzer."""
        super().__init__(config, "neon", logger)
        self.api_key = None
        self.api_base_url = "https://console.neon.tech/api/v2"
        self.session = None
        # owner_id (org_id) -> plan tier, populated during _discover_projects
        # so per-project results can carry the org's plan.
        self._org_plans: Dict[str, str] = {}

    def discover_resources(self) -> List[str]:
        """Discover available Neon projects."""
        try:
            if not self.authenticate():
                return []

            response = self.session.get(
                f"{self.api_base_url}/projects", params={"limit": 400}
            )

            if response.status_code != 200:
                return []

            data = response.json()
            projects = data.get("projects", [])
            return [p.get("id") for p in projects if p.get("id")]

        except Exception as e:
            self.add_error("Failed to discover resources", e)
            return []

    def authenticate(self) -> bool:
        """Authenticate with Neon API."""
        if not HAS_NEON_LIBS:
            self.add_error(
                "Neon libraries not installed. Install with: "
                "pip install 'planetscale-discovery-tools[neon]'"
            )
            return False

        try:
            # Get API key from config or environment
            self.api_key = getattr(self.config, "api_key", None)

            if not self.api_key:
                import os

                self.api_key = os.environ.get("NEON_API_KEY")

            if not self.api_key:
                self.add_error(
                    "No Neon API key provided. "
                    "Set it in your config file under providers.neon.api_key, "
                    "or set the NEON_API_KEY environment variable.\n"
                    "  Generate a key at: https://console.neon.tech/app/settings/api-keys\n"
                    "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/neon.md"
                )
                return False

            # Create session with auth header
            self.session = requests.Session()
            self.session.headers.update(
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            )

            # Probe with /users/me — works for both legacy accounts (projects under
            # the personal namespace) and modern accounts (projects under an org).
            # Listing /projects directly requires an org_id when the user's projects
            # are org-scoped, which causes auth probes without one to return HTTP 400.
            response = self.session.get(f"{self.api_base_url}/users/me")

            if response.status_code == 401:
                self.add_error(
                    "Invalid or expired Neon API key. "
                    "Verify your key is correct and hasn't been revoked.\n"
                    "  Generate a new key at: https://console.neon.tech/app/settings/api-keys\n"
                    "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/neon.md"
                )
                return False
            elif response.status_code != 200:
                self.add_error(f"Neon API error: HTTP {response.status_code}")
                return False

            self.logger.info("Successfully authenticated with Neon")
            return True

        except Exception as e:
            self.add_error("Neon authentication failed", e)
            return False

    def analyze(self) -> Dict[str, Any]:
        """
        Perform comprehensive Neon infrastructure analysis.

        Returns:
            Dictionary containing Neon infrastructure analysis
        """
        if not self.authenticate():
            return {
                "error": "Authentication failed",
                "timestamp": generate_timestamp(),
                "metadata": self.get_analysis_metadata(),
            }

        results = {
            "provider": "neon",
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
                f"Neon analysis completed. Found {len(projects)} project(s)"
            )

        except Exception as e:
            self.add_error("Neon analysis failed", e)

        results["metadata"] = self.get_analysis_metadata()
        return results

    def _discover_projects(self) -> List[Dict[str, Any]]:
        """
        Discover all accessible Neon projects.

        Resolution order:
          1. If --neon-target-project set: fetch that one project.
          2. If --neon-org-id set: list projects in that org only.
          3. Otherwise: try the legacy unscoped /projects listing first
             (still works for accounts with personal-namespace projects).
             If Neon returns the "org_id is required" error, fall back to
             listing the user's organizations and iterating over each.

        Returns:
            List of project details
        """
        projects = []

        try:
            target_project = getattr(self.config, "target_project", None)
            org_id = getattr(self.config, "org_id", None)

            # Pre-fetch orgs to build owner_id -> plan map. Modern Neon API
            # doesn't surface plan on the project response, so we need this
            # for the plan_tiers summary to be non-empty. One extra API call.
            orgs = self._list_user_organizations()
            self._org_plans = {
                org["id"]: org.get("plan")
                for org in orgs
                if org.get("id") and org.get("plan")
            }

            if target_project:
                project_data = self._analyze_project(target_project)
                if project_data:
                    projects.append(project_data)
                return projects

            if org_id:
                projects.extend(self._list_and_analyze_projects(org_id=org_id))
                return projects

            # No org filter — try unscoped listing, fall back to per-org iteration
            unscoped, needs_org = self._list_projects_unscoped()
            if needs_org:
                if not orgs:
                    self.add_warning(
                        "Neon API requires an org_id but no organizations were "
                        "discoverable for this key. Pass --neon-org-id explicitly."
                    )
                    return projects
                self.logger.info(
                    f"Iterating {len(orgs)} Neon organization(s) for project discovery"
                )
                for org in orgs:
                    oid = org.get("id")
                    if oid:
                        projects.extend(self._list_and_analyze_projects(org_id=oid))
            else:
                for project in unscoped:
                    project_id = project.get("id")
                    if project_id:
                        project_data = self._analyze_project(
                            project_id, project_info=project
                        )
                        if project_data:
                            projects.append(project_data)

        except Exception as e:
            self.add_error("Project discovery failed", e)

        return projects

    def _list_projects_unscoped(self) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Try listing projects without an org filter.

        Returns:
            (projects, needs_org) — `projects` is the list of project dicts
            (empty if needs_org is True); `needs_org` is True iff Neon
            responded that org_id is required.
        """
        try:
            response = self.session.get(
                f"{self.api_base_url}/projects", params={"limit": 400}
            )

            if response.status_code == 400:
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                msg = (body.get("message") or "").lower()
                if "org_id" in msg:
                    return [], True
                self.add_warning(
                    f"Neon API error listing projects: {body or 'HTTP 400'}"
                )
                return [], False

            if response.status_code != 200:
                self.add_warning(
                    f"Neon API error listing projects: HTTP {response.status_code}"
                )
                return [], False

            data = response.json()
            return list(data.get("projects", [])), False

        except Exception as e:
            self.add_warning(f"Failed to list projects: {e}")
            return [], False

    def _list_user_organizations(self) -> List[Dict[str, Any]]:
        """Return organizations the current API key has access to."""
        try:
            response = self.session.get(f"{self.api_base_url}/users/me/organizations")
            if response.status_code != 200:
                self.add_warning(
                    f"Failed to list Neon organizations: HTTP {response.status_code}"
                )
                return []
            return list(response.json().get("organizations", []))
        except Exception as e:
            self.add_warning(f"Failed to list Neon organizations: {e}")
            return []

    def _list_and_analyze_projects(
        self, org_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List projects (optionally scoped to an org) and run per-project analysis."""
        analyzed = []
        params = {"org_id": org_id} if org_id else {}
        for project in self._paginated_get(
            f"{self.api_base_url}/projects", params=params
        ):
            project_id = project.get("id")
            if not project_id:
                continue
            project_data = self._analyze_project(project_id, project_info=project)
            if project_data:
                analyzed.append(project_data)
        return analyzed

    def _analyze_project(
        self, project_id: str, project_info: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze a single Neon project.

        Args:
            project_id: Neon project ID
            project_info: Pre-fetched project data from list response (optional)

        Returns:
            Project analysis data or None if failed
        """
        try:
            # Get project details if not already provided
            if not project_info:
                response = self.session.get(
                    f"{self.api_base_url}/projects/{project_id}"
                )
                if response.status_code != 200:
                    self.add_warning(f"Failed to get project details for {project_id}")
                    return None
                data = response.json()
                project_info = data.get("project", data)

            # Plan tier lives on the organization, not the project. Modern
            # API: look up via owner_id in the org map we built during
            # discovery. Legacy API (pre-org-migration): some responses
            # included a nested owner object with subscription_type.
            owner_id = project_info.get("owner_id") or project_info.get("org_id")
            plan = self._org_plans.get(owner_id) if owner_id else None
            if not plan:
                legacy_owner = project_info.get("owner")
                if isinstance(legacy_owner, dict):
                    plan = legacy_owner.get("subscription_type")

            project_data = {
                "project_id": project_id,
                "timestamp": generate_timestamp(),
                "name": project_info.get("name", "Unknown"),
                "region_id": project_info.get("region_id", "Unknown"),
                "pg_version": project_info.get("pg_version"),
                "created_at": project_info.get("created_at"),
                "updated_at": project_info.get("updated_at"),
                "owner_id": owner_id,
                "plan": plan,
            }

            # Fetch branches
            branches = self._get_branches(project_id)
            project_data["branches"] = branches

            # Identify default branch
            default_branch_id = None
            for branch in branches:
                if branch.get("default"):
                    default_branch_id = branch.get("id")
                    break

            project_data["default_branch_id"] = default_branch_id

            # Fetch endpoints
            endpoints = self._get_endpoints(project_id)
            project_data["endpoints"] = endpoints

            # Fetch databases on default branch
            if default_branch_id:
                databases = self._get_databases(project_id, default_branch_id)
                project_data["databases"] = databases
            else:
                project_data["databases"] = []

            return project_data

        except Exception as e:
            self.add_warning(f"Failed to analyze project {project_id}: {e}")
            return None

    def _get_branches(self, project_id: str) -> List[Dict[str, Any]]:
        """Get branches for a project."""
        try:
            response = self.session.get(
                f"{self.api_base_url}/projects/{project_id}/branches"
            )

            if response.status_code != 200:
                self.add_warning(f"Failed to get branches for project {project_id}")
                return []

            data = response.json()
            branches = []

            for branch in data.get("branches", []):
                branches.append(
                    {
                        "id": branch.get("id"),
                        "name": branch.get("name"),
                        "default": branch.get("default", False),
                        "protected": branch.get("protected", False),
                        "current_state": branch.get("current_state"),
                        "parent_id": branch.get("parent_id"),
                        "logical_size": branch.get("logical_size"),
                        "physical_size": branch.get("physical_size"),
                        "created_at": branch.get("created_at"),
                        "updated_at": branch.get("updated_at"),
                    }
                )

            return branches

        except Exception as e:
            self.add_warning(f"Error getting branches: {e}")
            return []

    def _get_endpoints(self, project_id: str) -> List[Dict[str, Any]]:
        """Get compute endpoints for a project."""
        try:
            response = self.session.get(
                f"{self.api_base_url}/projects/{project_id}/endpoints"
            )

            if response.status_code != 200:
                self.add_warning(f"Failed to get endpoints for project {project_id}")
                return []

            data = response.json()
            endpoints = []

            for ep in data.get("endpoints", []):
                min_cu = ep.get("autoscaling_limit_min_cu", 0)
                max_cu = ep.get("autoscaling_limit_max_cu", 0)

                endpoint_data = {
                    "id": ep.get("id"),
                    "type": ep.get("type"),
                    "current_state": ep.get("current_state"),
                    "branch_id": ep.get("branch_id"),
                    "autoscaling_limit_min_cu": min_cu,
                    "autoscaling_limit_max_cu": max_cu,
                    "pooler_enabled": ep.get("pooler_enabled", False),
                    "pooler_mode": ep.get("pooler_mode"),
                    "suspend_timeout_seconds": ep.get("suspend_timeout_seconds"),
                    "created_at": ep.get("created_at"),
                }

                # Enrich with compute specs for the max CU size
                if max_cu and max_cu in NEON_COMPUTE_SPECS:
                    endpoint_data["compute_specs"] = NEON_COMPUTE_SPECS[max_cu]

                endpoints.append(endpoint_data)

            return endpoints

        except Exception as e:
            self.add_warning(f"Error getting endpoints: {e}")
            return []

    def _get_databases(self, project_id: str, branch_id: str) -> List[Dict[str, Any]]:
        """Get databases on a specific branch."""
        try:
            response = self.session.get(
                f"{self.api_base_url}/projects/{project_id}/branches/{branch_id}/databases"
            )

            if response.status_code != 200:
                self.add_warning(f"Failed to get databases for branch {branch_id}")
                return []

            data = response.json()
            databases = []

            for db in data.get("databases", []):
                databases.append(
                    {
                        "name": db.get("name"),
                        "owner_name": db.get("owner_name"),
                        "created_at": db.get("created_at"),
                    }
                )

            return databases

        except Exception as e:
            self.add_warning(f"Error getting databases: {e}")
            return []

    def _paginated_get(
        self, url: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch all items from a paginated Neon API endpoint.

        Neon uses cursor-based pagination with a 'cursor' parameter.

        Args:
            url: API endpoint URL
            params: Additional query parameters

        Returns:
            List of all items across pages
        """
        all_items = []
        request_params = dict(params) if params else {}
        request_params.setdefault("limit", 400)

        while True:
            try:
                response = self.session.get(url, params=request_params)

                if response.status_code == 429:
                    self.add_warning(
                        "Neon API rate limit reached. Results may be incomplete."
                    )
                    break

                if response.status_code != 200:
                    self.add_warning(
                        f"Neon API error during pagination: HTTP {response.status_code}"
                    )
                    break

                data = response.json()

                # Neon wraps list responses in a key matching the resource type
                # e.g., {"projects": [...], "pagination": {...}}
                # Find the list in the response
                items = []
                for key, value in data.items():
                    if isinstance(value, list):
                        items = value
                        break

                all_items.extend(items)

                # Check for next page cursor
                pagination = data.get("pagination", {})
                cursor = pagination.get("cursor")

                if not cursor or len(items) == 0:
                    break

                request_params["cursor"] = cursor

            except Exception as e:
                self.add_warning(f"Error during pagination: {e}")
                break

        return all_items

    def _generate_summary(self, projects: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate summary statistics for discovered projects."""
        regions = set()
        pg_versions = set()
        plan_tiers = {}
        total_branches = 0
        total_endpoints = 0
        endpoints_with_pooling = 0
        read_replicas = 0

        for project in projects:
            region = project.get("region_id")
            if region:
                regions.add(region)

            pg_version = project.get("pg_version")
            if pg_version:
                pg_versions.add(str(pg_version))

            plan = project.get("plan")
            if plan:
                plan_tiers[plan] = plan_tiers.get(plan, 0) + 1

            branches = project.get("branches", [])
            total_branches += len(branches)

            endpoints = project.get("endpoints", [])
            total_endpoints += len(endpoints)

            for ep in endpoints:
                if ep.get("pooler_enabled"):
                    endpoints_with_pooling += 1
                if ep.get("type") == "read_only":
                    read_replicas += 1

        return {
            "total_projects": len(projects),
            "regions": sorted(list(regions)),
            "pg_versions": sorted(list(pg_versions)),
            "total_branches": total_branches,
            "total_endpoints": total_endpoints,
            "endpoints_with_pooling": endpoints_with_pooling,
            "read_replicas": read_replicas,
            "plan_tiers": plan_tiers,
        }
