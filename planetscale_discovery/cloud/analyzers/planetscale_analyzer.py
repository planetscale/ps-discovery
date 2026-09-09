"""
PlanetScale Postgres Cloud Infrastructure Analyzer

Records the current PlanetScale Postgres estate: organizations, databases,
branches and the cluster size each branch runs. Covers Postgres only.
Collects metadata only and draws no conclusion from it.
"""

from typing import Dict, Any, List, Optional
import logging

try:
    import requests

    HAS_PLANETSCALE_LIBS = True
except ImportError:
    HAS_PLANETSCALE_LIBS = False

from ...common.base_analyzer import CloudAnalyzer
from ...common.utils import generate_timestamp

POSTGRES_KIND = "postgresql"

# Stops an unbounded loop if the API keeps returning a next_page value.
MAX_PAGES = 200

# The branch record carries only the autoscaling envelope, and a non-Metal SKU
# reports no storage at all, so read consumption from the instant metrics API.
# Metric name -> field name in the recorded storage block.
STORAGE_METRICS = {
    "planetscale_volume_disk_usage_bytes": "bytes_used",
    "planetscale_volume_capacity_bytes": "bytes_capacity",
    "planetscale_volume_usage_percentage": "usage_percentage",
}

_DOCS_URL = "https://github.com/planetscale/ps-discovery/blob/main/docs/providers/planetscale.md"
_TOKEN_URL = "https://app.planetscale.com/settings/service-tokens"


class PlanetScaleAnalyzer(CloudAnalyzer):
    """Analyzer for PlanetScale Postgres infrastructure."""

    def __init__(self, config: Any, logger: Optional[logging.Logger] = None):
        """Initialize PlanetScale analyzer."""
        super().__init__(config, "planetscale", logger)
        self.service_token_id = None
        self.service_token = None
        self.api_base_url = "https://api.planetscale.com/v1"
        self.session = None
        # Cluster size SKU name -> spec, refreshed per organization.
        self._cluster_skus: Dict[str, Dict[str, Any]] = {}

    def discover_resources(self) -> List[str]:
        """Discover accessible PlanetScale Postgres database names."""
        try:
            if not self.authenticate():
                return []

            names = []
            for org in self._resolve_organizations():
                for database in self._list_postgres_databases(org):
                    name = database.get("name")
                    if name:
                        names.append(name)
            return names

        except Exception as e:
            self.add_error("Failed to discover resources", e)
            return []

    def authenticate(self) -> bool:
        """Authenticate with the PlanetScale API."""
        if not HAS_PLANETSCALE_LIBS:
            self.add_error(
                "PlanetScale libraries not installed. Install with: "
                "pip install 'planetscale-discovery-tools[planetscale]'"
            )
            return False

        try:
            self.service_token_id = getattr(self.config, "service_token_id", None)
            self.service_token = getattr(self.config, "service_token", None)

            if not self.service_token_id or not self.service_token:
                import os

                self.service_token_id = self.service_token_id or os.environ.get(
                    "PLANETSCALE_SERVICE_TOKEN_ID"
                )
                self.service_token = self.service_token or os.environ.get(
                    "PLANETSCALE_SERVICE_TOKEN"
                )

            if not self.service_token_id or not self.service_token:
                self.add_error(
                    "No PlanetScale service token provided. "
                    "Set both values in your config file under "
                    "providers.planetscale.service_token_id and "
                    "providers.planetscale.service_token, or set the "
                    "PLANETSCALE_SERVICE_TOKEN_ID and PLANETSCALE_SERVICE_TOKEN "
                    "environment variables.\n"
                    f"  Create a service token at: {_TOKEN_URL}\n"
                    f"  Setup guide: {_DOCS_URL}"
                )
                return False

            self.session = requests.Session()
            # PlanetScale service tokens send "<id>:<token>" with no scheme.
            self.session.headers.update(
                {
                    "Authorization": f"{self.service_token_id}:{self.service_token}",
                    "Accept": "application/json",
                }
            )

            # A token scoped to one organization cannot list organizations, so
            # probe that organization instead of the list it cannot read.
            configured_org = getattr(self.config, "organization", None)
            if configured_org:
                probe_url = f"{self.api_base_url}/organizations/{configured_org}"
            else:
                probe_url = f"{self.api_base_url}/organizations"
            response = self.session.get(probe_url)

            if response.status_code == 401:
                self.add_error(
                    "Invalid or expired PlanetScale service token. "
                    "Verify the token ID and token are correct and the token "
                    "has not been deleted.\n"
                    f"  Manage service tokens at: {_TOKEN_URL}\n"
                    f"  Setup guide: {_DOCS_URL}"
                )
                return False
            elif response.status_code == 403:
                if configured_org:
                    self.add_error(
                        "PlanetScale service token lacks the read_organization "
                        f"access on organization {configured_org}.\n"
                        f"  Setup guide: {_DOCS_URL}"
                    )
                else:
                    self.add_error(
                        "PlanetScale service token lacks the read_organization "
                        "access needed to list organizations.\n"
                        f"  Setup guide: {_DOCS_URL}"
                    )
                return False
            elif response.status_code != 200:
                self.add_error(f"PlanetScale API error: HTTP {response.status_code}")
                return False

            self.logger.info("Successfully authenticated with PlanetScale")
            return True

        except Exception as e:
            self.add_error("PlanetScale authentication failed", e)
            return False

    def analyze(self) -> Dict[str, Any]:
        """Analyze the PlanetScale Postgres estate."""
        if not self.authenticate():
            return {
                "error": "Authentication failed",
                "timestamp": generate_timestamp(),
                "metadata": self.get_analysis_metadata(),
            }

        results: Dict[str, Any] = {
            "provider": "planetscale",
            "timestamp": generate_timestamp(),
            "organizations": [],
            "summary": {},
            "metadata": self.get_analysis_metadata(),
        }

        try:
            organizations = self._discover_organizations()
            results["organizations"] = organizations
            results["summary"] = self._generate_summary(organizations)

            database_count = sum(len(org.get("databases", [])) for org in organizations)
            self.logger.info(
                f"PlanetScale analysis completed. Found {database_count} "
                f"Postgres database(s) across {len(organizations)} organization(s)"
            )

        except Exception as e:
            self.add_error("PlanetScale analysis failed", e)

        results["metadata"] = self.get_analysis_metadata()
        return results

    def _paginated_get(
        self, url: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Fetch every page of a paginated list endpoint."""
        all_items: List[Dict[str, Any]] = []
        request_params = dict(params) if params else {}
        request_params.setdefault("per_page", 100)
        request_params["page"] = 1

        for _ in range(MAX_PAGES):
            try:
                response = self.session.get(url, params=request_params)

                if response.status_code == 429:
                    self.add_warning(
                        "PlanetScale API rate limit reached. "
                        "Results may be incomplete."
                    )
                    break
                if response.status_code != 200:
                    self.add_warning(
                        f"PlanetScale API error during pagination: "
                        f"HTTP {response.status_code} for {url}"
                    )
                    break

                data = response.json()
                # cluster-size-skus returns a bare array, not a paged object.
                if isinstance(data, list):
                    all_items.extend(data)
                    break
                items = data.get("data") or []
                all_items.extend(items)

                next_page = data.get("next_page")
                if not next_page or not items:
                    break
                request_params["page"] = next_page

            except Exception as e:
                self.add_warning(f"Error during pagination: {e}")
                break

        return all_items

    def _resolve_organizations(self) -> List[str]:
        """Return the organization names to analyze."""
        configured = getattr(self.config, "organization", None)
        if configured:
            return [configured]

        names = []
        for org in self._paginated_get(f"{self.api_base_url}/organizations"):
            name = org.get("name")
            if name:
                names.append(name)
        return names

    def _discover_organizations(self) -> List[Dict[str, Any]]:
        """Analyze every organization in scope."""
        organizations = []

        for org_name in self._resolve_organizations():
            try:
                organizations.append(self._analyze_organization(org_name))
            except Exception as e:
                self.add_warning(f"Failed to analyze organization {org_name}: {e}")

        return organizations

    def _analyze_organization(self, org_name: str) -> Dict[str, Any]:
        """Analyze a single organization."""
        details = self._get_organization_details(org_name)
        self._cluster_skus = self._get_cluster_skus(org_name)
        databases = self._list_postgres_databases(org_name)

        org_data = {
            "name": org_name,
            "timestamp": generate_timestamp(),
            "plan": details.get("plan"),
            "database_count": details.get("database_count"),
            "single_tenancy": details.get("single_tenancy"),
            "created_at": details.get("created_at"),
            "cluster_size_skus": self._cluster_skus,
            "databases": [
                self._analyze_database(org_name, database) for database in databases
            ],
        }
        return org_data

    def _get_organization_details(self, org_name: str) -> Dict[str, Any]:
        """Fetch plan and tenancy details for an organization."""
        try:
            response = self.session.get(f"{self.api_base_url}/organizations/{org_name}")
            if response.status_code != 200:
                self.add_warning(
                    f"Failed to get organization details for {org_name}: "
                    f"HTTP {response.status_code}"
                )
                return {}
            return response.json()
        except Exception as e:
            self.add_warning(f"Failed to get organization details for {org_name}: {e}")
            return {}

    def _get_cluster_skus(self, org_name: str) -> Dict[str, Dict[str, Any]]:
        """Fetch the Postgres cluster size SKUs available to an organization."""
        skus: Dict[str, Dict[str, Any]] = {}
        try:
            # Without the engine filter the endpoint returns the MySQL catalog,
            # whose names never match a Postgres branch cluster_name.
            items = self._paginated_get(
                f"{self.api_base_url}/organizations/{org_name}/cluster-size-skus",
                params={"engine": POSTGRES_KIND},
            )
            for sku in items:
                name = sku.get("name")
                if not name:
                    continue
                skus[name] = {
                    "display_name": sku.get("display_name"),
                    "cpu": sku.get("cpu"),
                    "ram": sku.get("ram"),
                    "storage": sku.get("storage"),
                    "provider": sku.get("provider"),
                    "metal": sku.get("metal"),
                }
        except Exception as e:
            self.add_warning(f"Failed to get cluster size SKUs for {org_name}: {e}")
        return skus

    def _list_postgres_databases(self, org_name: str) -> List[Dict[str, Any]]:
        """Return the Postgres databases of an organization."""
        postgres = []
        target = getattr(self.config, "target_database", None)
        try:
            # The list endpoint has no engine filter, so filter client-side.
            for database in self._paginated_get(
                f"{self.api_base_url}/organizations/{org_name}/databases"
            ):
                if database.get("kind") != POSTGRES_KIND:
                    continue
                if target and database.get("name") != target:
                    continue
                postgres.append(database)
        except Exception as e:
            self.add_warning(f"Failed to list databases for {org_name}: {e}")

        if target and not postgres:
            self.add_warning(
                f"Target database {target} is not a Postgres database in "
                f"organization {org_name}."
            )
        return postgres

    def _analyze_database(
        self, org_name: str, database: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Record one Postgres database and its branches."""
        db_name = database.get("name")
        region = database.get("region") or {}

        return {
            "id": database.get("id"),
            "name": db_name,
            "kind": database.get("kind"),
            "state": database.get("state"),
            "ready": database.get("ready"),
            "region": region.get("slug") or region.get("id"),
            "region_provider": region.get("provider"),
            "branches_count": database.get("branches_count"),
            "production_branches_count": database.get("production_branches_count"),
            "development_branches_count": database.get("development_branches_count"),
            "created_at": database.get("created_at"),
            "branches": self._get_branches(org_name, db_name) if db_name else [],
        }

    def _get_branches(self, org_name: str, db_name: str) -> List[Dict[str, Any]]:
        """Record the branches of one database."""
        branches = []
        try:
            for branch in self._paginated_get(
                f"{self.api_base_url}/organizations/{org_name}"
                f"/databases/{db_name}/branches"
            ):
                region = branch.get("region") or {}
                cluster_name = branch.get("cluster_name")
                branch_name = branch.get("name")
                branches.append(
                    {
                        "id": branch.get("id"),
                        "name": branch_name,
                        "kind": branch.get("kind"),
                        "production": branch.get("production"),
                        "state": branch.get("state"),
                        "ready": branch.get("ready"),
                        "metal": branch.get("metal"),
                        "cluster_name": cluster_name,
                        "cluster_iops": branch.get("cluster_iops"),
                        "cluster_size": self._enrich_cluster_size(cluster_name),
                        "storage": self._get_branch_storage(org_name, db_name, branch),
                        "storage_config": self._storage_config(branch),
                        "replicas": branch.get("replicas"),
                        "has_replicas": branch.get("has_replicas"),
                        "has_read_only_replicas": branch.get("has_read_only_replicas"),
                        "region": region.get("slug") or region.get("id"),
                        "parent_branch": branch.get("parent_branch"),
                        "created_at": branch.get("created_at"),
                    }
                )
        except Exception as e:
            self.add_warning(f"Failed to list branches for {db_name}: {e}")
        return branches

    def _enrich_cluster_size(self, cluster_name: Optional[str]) -> Dict[str, Any]:
        """Return the SKU spec for a cluster size name."""
        if not cluster_name:
            return {}
        return self._cluster_skus.get(cluster_name, {})

    @staticmethod
    def _storage_config(branch: Dict[str, Any]) -> Dict[str, Any]:
        """Return the storage settings of a branch, which are not consumption."""
        return {
            "autoscaling": branch.get("storage_autoscaling"),
            "shrinking": branch.get("storage_shrinking"),
            "minimum_bytes": branch.get("minimum_storage_bytes"),
            "maximum_bytes": branch.get("maximum_storage_bytes"),
            "type": branch.get("storage_type"),
            "iops": branch.get("storage_iops"),
            "throughput_mibs": branch.get("storage_throughput_mibs"),
        }

    def _get_branch_storage(
        self, org_name: str, db_name: str, branch: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Read the current volume usage of one branch."""
        storage: Dict[str, Any] = {
            "bytes_used": None,
            "bytes_capacity": None,
            "usage_percentage": None,
            "pods": [],
        }

        branch_name = branch.get("name")
        if not branch_name or not branch.get("ready"):
            return storage

        try:
            response = self.session.get(
                f"{self.api_base_url}/organizations/{org_name}"
                f"/databases/{db_name}/branches/{branch_name}/metrics/instant",
                params={"metrics": ",".join(STORAGE_METRICS)},
            )
            if response.status_code != 200:
                self.add_warning(
                    f"Failed to get storage metrics for {db_name}/{branch_name}: "
                    f"HTTP {response.status_code}"
                )
                return storage
            payload = response.json()
        except Exception as e:
            self.add_warning(
                f"Failed to get storage metrics for {db_name}/{branch_name}: {e}"
            )
            return storage

        # The API reports one value per pod, so group by pod before it is read.
        pods: Dict[str, Dict[str, Any]] = {}
        for metric in payload.get("metrics") or []:
            field = STORAGE_METRICS.get(metric.get("metric"))
            if not field:
                continue
            for value in metric.get("values") or []:
                pod = value.get("pod") or ""
                entry = pods.setdefault(pod, {"pod": pod, "role": value.get("role")})
                entry[field] = value.get("value")

        storage["pods"] = list(pods.values())
        primary = self._select_primary_pod(storage["pods"])
        if primary:
            for field in STORAGE_METRICS.values():
                storage[field] = primary.get(field)
        return storage

    @staticmethod
    def _select_primary_pod(
        pods: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Return the pod whose volume holds the whole branch."""
        if not pods:
            return None
        for pod in pods:
            if str(pod.get("role") or "").lower() == "primary":
                return pod
        # A replica volume matches the primary, so the largest one is safe.
        return max(pods, key=lambda pod: pod.get("bytes_used") or 0)

    def _generate_summary(self, organizations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarize the Postgres estate."""
        regions = set()
        cluster_sizes: Dict[str, int] = {}
        plans: Dict[str, int] = {}
        total_databases = 0
        total_branches = 0
        production_branches = 0
        total_storage_bytes_used = 0

        for org in organizations:
            plan = org.get("plan")
            if plan:
                plans[plan] = plans.get(plan, 0) + 1

            for database in org.get("databases", []):
                total_databases += 1
                if database.get("region"):
                    regions.add(database["region"])

                for branch in database.get("branches", []):
                    total_branches += 1
                    if branch.get("production"):
                        production_branches += 1
                    if branch.get("region"):
                        regions.add(branch["region"])
                    bytes_used = (branch.get("storage") or {}).get("bytes_used")
                    if bytes_used:
                        total_storage_bytes_used += int(bytes_used)
                    cluster_name = branch.get("cluster_name")
                    if cluster_name:
                        cluster_sizes[cluster_name] = (
                            cluster_sizes.get(cluster_name, 0) + 1
                        )

        return {
            "total_organizations": len(organizations),
            "total_databases": total_databases,
            "total_branches": total_branches,
            "production_branches": production_branches,
            "total_storage_bytes_used": total_storage_bytes_used,
            "regions": sorted(regions),
            "cluster_sizes": cluster_sizes,
            "plans": plans,
        }
