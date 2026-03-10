"""
Heroku Cloud Infrastructure Analyzer

Analyzes Heroku-hosted PostgreSQL databases including add-on configuration,
connection pooling (PgBouncer), follower databases, and plan-based specifications.
"""

from typing import Dict, Any, List, Optional
import logging

try:
    import requests

    HAS_HEROKU_LIBS = True
except ImportError:
    HAS_HEROKU_LIBS = False

from ...common.base_analyzer import CloudAnalyzer
from ...common.utils import generate_timestamp

# Static mapping of Heroku Postgres plan suffixes to specifications.
# Plan names come from the addon plan.name field, e.g. "heroku-postgresql:standard-0".
# Static mapping of Heroku Postgres plan suffixes to specifications.
# Sources:
#   - https://devcenter.heroku.com/articles/heroku-postgres-plans
#   - https://devcenter.heroku.com/articles/heroku-postgres-production-tier-technical-characterization
# Essential plans are multi-tenant (shared CPU), so vcpu is None.
# Production plans (Standard+) are single-tenant with dedicated vCPUs and provisioned IOPS.

_PRODUCTION_PLAN_HARDWARE = {
    # suffix -> (vcpu, ram_gb, storage_gb, connection_limit, piops)
    "0": (2, 4, 64, 200, 3000),
    "2": (2, 8, 256, 500, 3000),
    "3": (2, 15, 512, 500, 3000),
    "4": (4, 30, 768, 500, 3000),
    "5": (8, 61, 1024, 500, 4000),
    "6": (16, 122, 1536, 500, 6000),
    "7": (32, 244, 2048, 500, 9000),
    "8": (64, 488, 3072, 500, 12000),
    "9": (96, 768, 4096, 500, 16000),
    "10": (128, 1024, 8192, 500, 32000),
}


def _build_plan_specs():
    specs = {}
    # Essential plans (multi-tenant, shared CPU)
    for suffix, storage_gb, conn_limit in [("0", 1, 20), ("1", 10, 20), ("2", 32, 40)]:
        specs[f"essential-{suffix}"] = {
            "tier": "essential",
            "vcpu": None,
            "ram_gb": 0,
            "storage_gb": storage_gb,
            "connection_limit": conn_limit,
            "piops": None,
            "ha": False,
            "fork": False,
            "follow": False,
        }
    # Production tiers: standard, premium, private, shield
    # HA is only available on premium, private, and shield — NOT standard.
    # https://devcenter.heroku.com/articles/heroku-postgres-plans
    _HA_TIERS = {"premium", "private", "shield"}
    for tier in ("standard", "premium", "private", "shield"):
        for suffix, (
            vcpu,
            ram,
            storage,
            conns,
            piops,
        ) in _PRODUCTION_PLAN_HARDWARE.items():
            specs[f"{tier}-{suffix}"] = {
                "tier": tier,
                "vcpu": vcpu,
                "ram_gb": ram,
                "storage_gb": storage,
                "connection_limit": conns,
                "piops": piops,
                "ha": tier in _HA_TIERS,
                "fork": True,
                "follow": True,
            }
    return specs


HEROKU_PLAN_SPECS = _build_plan_specs()


class HerokuAnalyzer(CloudAnalyzer):
    """Analyzer for Heroku Postgres managed database infrastructure."""

    def __init__(self, config: Any, logger: Optional[logging.Logger] = None):
        """Initialize Heroku analyzer."""
        super().__init__(config, "heroku", logger)
        self.api_key = None
        self.api_base_url = "https://api.heroku.com"
        self.data_api_base_url = "https://api.data.heroku.com"
        self.session = None

    def discover_resources(self) -> List[str]:
        """Discover available Heroku apps with Postgres add-ons."""
        try:
            if not self.authenticate():
                return []

            apps = self._get_all_apps()
            return [str(app.get("name")) for app in apps if app.get("name")]

        except Exception as e:
            self.add_error("Failed to discover resources", e)
            return []

    def authenticate(self) -> bool:
        """Authenticate with Heroku Platform API."""
        if not HAS_HEROKU_LIBS:
            self.add_error(
                "Heroku libraries not installed. Install with: pip install 'planetscale-discovery-tools[heroku]'"
            )
            return False

        try:
            self.api_key = getattr(self.config, "api_key", None)

            if not self.api_key:
                import os

                self.api_key = os.environ.get("HEROKU_API_KEY")

            if not self.api_key:
                self.add_error(
                    "No Heroku API key provided. "
                    "Set it in your config file under providers.heroku.api_key, "
                    "pass --heroku-api-key on the command line, "
                    "or set the HEROKU_API_KEY environment variable.\n"
                    "  To create an API key, run: heroku authorizations:create --short\n"
                    "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/heroku.md"
                )
                return False

            self.session = requests.Session()
            self.session.headers.update(
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/vnd.heroku+json; version=3",
                    "Content-Type": "application/json",
                }
            )

            # Test authentication by fetching account info
            response = self.session.get(f"{self.api_base_url}/account")

            if response.status_code == 401:
                self.add_error(
                    "Invalid or expired Heroku API key. "
                    "Verify your key is correct and hasn't been revoked.\n"
                    "  To create a new key, run: heroku authorizations:create --short\n"
                    "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/heroku.md"
                )
                return False
            elif response.status_code != 200:
                self.add_error(f"Heroku API error: HTTP {response.status_code}")
                return False

            self.logger.info("Successfully authenticated with Heroku")
            return True

        except Exception as e:
            self.add_error("Heroku authentication failed", e)
            return False

    def analyze(self) -> Dict[str, Any]:
        """Perform comprehensive Heroku Postgres infrastructure analysis."""
        if not self.authenticate():
            return {
                "error": "Authentication failed",
                "timestamp": generate_timestamp(),
                "metadata": self.get_analysis_metadata(),
            }

        results = {
            "provider": "heroku",
            "timestamp": generate_timestamp(),
            "apps": [],
            "summary": {},
            "metadata": self.get_analysis_metadata(),
        }

        try:
            apps = self._discover_apps()
            results["apps"] = apps
            results["summary"] = self._generate_summary(apps)

            self.logger.info(
                f"Heroku analysis completed. Found {len(apps)} app(s) with Postgres"
            )

        except Exception as e:
            self.add_error("Heroku analysis failed", e)

        results["metadata"] = self.get_analysis_metadata()
        return results

    def _paginated_get(self, url: str) -> List[Dict[str, Any]]:
        """Handle Heroku's Range/Next-Range header pagination.

        Heroku uses the Range header for pagination. Responses include a
        Next-Range header when more results are available.
        """
        all_items = []

        headers = {"Range": "id ..; max=200"}

        while True:
            response = self.session.get(url, headers=headers)

            if response.status_code == 429:
                self.add_warning("Heroku API rate limit reached")
                break

            if response.status_code not in (200, 206):
                break

            items = response.json()
            if not items:
                break

            all_items.extend(items)

            next_range = response.headers.get("Next-Range")
            if not next_range or response.status_code == 200:
                break

            headers["Range"] = next_range

        return all_items

    def _get_all_apps(self) -> List[Dict[str, Any]]:
        """Fetch all Heroku apps using pagination."""
        return self._paginated_get(f"{self.api_base_url}/apps")

    def _discover_apps(self) -> List[Dict[str, Any]]:
        """Discover Heroku apps with Postgres add-ons and analyze them."""
        analyzed_apps = []

        try:
            target_app = getattr(self.config, "target_app", None)

            if target_app:
                app_data = self._analyze_app(target_app)
                if app_data:
                    analyzed_apps.append(app_data)
            else:
                apps = self._get_all_apps()

                for app in apps:
                    app_name = app.get("name")
                    if not app_name:
                        continue

                    app_data = self._analyze_app(app_name, app_info=app)
                    if app_data and app_data.get("databases"):
                        analyzed_apps.append(app_data)

        except Exception as e:
            self.add_error("App discovery failed", e)

        return analyzed_apps

    def _analyze_app(
        self, app_name: str, app_info: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Analyze a single Heroku app for Postgres add-ons."""
        try:
            app_data = {
                "name": app_name,
                "region": (app_info or {}).get("region", {}).get("name", "unknown"),
                "databases": [],
                "connection_pooling": {},
                "followers": [],
                "attachments": [],
            }

            # Get Postgres add-ons for this app
            addons = self._get_postgres_addons(app_name)
            for addon in addons:
                db_info = self._build_database_info(addon)
                app_data["databases"].append(db_info)

            if not app_data["databases"]:
                return None

            # Get config vars to detect pooling and followers
            config_var_names = self._get_config_var_names(app_name)
            app_data["connection_pooling"] = self._detect_connection_pooling(
                config_var_names
            )
            app_data["followers"] = self._detect_followers(config_var_names)

            # Get addon attachments for cross-app detection
            attachments = self._get_addon_attachments(app_name)
            app_data["attachments"] = attachments

            return app_data

        except Exception as e:
            self.add_warning(f"Failed to analyze app {app_name}: {e}")
            return None

    def _get_postgres_addons(self, app_name: str) -> List[Dict[str, Any]]:
        """Get Heroku Postgres add-ons for an app."""
        try:
            response = self.session.get(f"{self.api_base_url}/apps/{app_name}/addons")

            if response.status_code != 200:
                return []

            addons = response.json()
            return [
                a
                for a in addons
                if a.get("addon_service", {}).get("name") == "heroku-postgresql"
            ]

        except Exception as e:
            self.add_warning(f"Failed to get add-ons for {app_name}: {e}")
            return []

    def _build_database_info(self, addon: Dict[str, Any]) -> Dict[str, Any]:
        """Build database info dict from an addon response."""
        plan_name_full = addon.get("plan", {}).get("name", "")
        # Extract plan suffix: "heroku-postgresql:standard-0" -> "standard-0"
        plan_suffix = plan_name_full.split(":")[-1] if ":" in plan_name_full else ""

        db_info = {
            "addon_id": addon.get("id", ""),
            "addon_name": addon.get("name", ""),
            "plan_name": plan_suffix,
            "plan_specs": self._enrich_with_plan_specs(plan_suffix),
            "state": addon.get("state", "unknown"),
            "created_at": addon.get("created_at", ""),
            "config_var_names": addon.get("config_vars", []),
        }

        # Enrich with live database details from the Heroku Data API
        addon_id = addon.get("id", "")
        if addon_id:
            details = self._get_database_details(addon_id)
            if details:
                db_info["database_details"] = details

        return db_info

    def _get_database_details(self, addon_id: str) -> Optional[Dict[str, Any]]:
        """Fetch live database details from the Heroku Data API.

        Calls https://api.data.heroku.com/client/v11/databases/{addon_id}
        which is the same endpoint backing `heroku pg:info`. Returns storage
        usage, connection counts, PG version, and operational metadata.

        Credential values (resource_url, database_password) are never stored.
        """
        try:
            response = self.session.get(
                f"{self.data_api_base_url}/client/v11/databases/{addon_id}"
            )

            if response.status_code != 200:
                self.add_warning(
                    f"Data API returned {response.status_code} for addon {addon_id}"
                )
                return None

            data = response.json()

            # Parse the info array into a lookup dict for easier access
            info_lookup = {}
            for item in data.get("info", []):
                name = item.get("name", "")
                values = item.get("values", [])
                info_lookup[name] = values[0] if len(values) == 1 else values

            # Build a safe subset — never store credentials or connection URLs
            details = {
                "num_bytes": data.get("num_bytes"),
                "num_tables": data.get("num_tables"),
                "num_connections": data.get("num_connections"),
                "num_connections_waiting": data.get("num_connections_waiting"),
                "postgres_version": data.get("postgres_version"),
                "data_size": info_lookup.get("Data Size"),
                "status": info_lookup.get("Status"),
                "fork_follow": info_lookup.get("Fork/Follow"),
                "rollback": info_lookup.get("Rollback"),
                "continuous_protection": info_lookup.get("Continuous Protection"),
                "data_encryption": info_lookup.get("Data Encryption"),
                "maintenance": info_lookup.get("Maintenance"),
                "maintenance_window": info_lookup.get("Maintenance window"),
            }

            return details

        except Exception as e:
            self.add_warning(f"Failed to get database details for {addon_id}: {e}")
            return None

    def _get_config_var_names(self, app_name: str) -> List[str]:
        """Get config var key names for an app (values are never stored)."""
        try:
            response = self.session.get(
                f"{self.api_base_url}/apps/{app_name}/config-vars"
            )

            if response.status_code != 200:
                return []

            config_vars = response.json()
            return list(config_vars.keys())

        except Exception as e:
            self.add_warning(f"Failed to get config vars for {app_name}: {e}")
            return []

    def _detect_connection_pooling(self, config_var_names: List[str]) -> Dict[str, Any]:
        """Detect PgBouncer connection pooling from config var names.

        Heroku Postgres connection pooling is indicated by config vars
        ending in _CONNECTION_POOL_URL or DATABASE_CONNECTION_POOL_URL.
        """
        pool_vars = [name for name in config_var_names if "CONNECTION_POOL" in name]

        if pool_vars:
            return {
                "detected": True,
                "mode": "transaction",
                "pool_config_vars": pool_vars,
            }

        return {"detected": False}

    def _detect_followers(self, config_var_names: List[str]) -> List[Dict[str, Any]]:
        """Detect follower (replica) databases from config var names.

        Multiple HEROKU_POSTGRESQL_*_URL vars (beyond DATABASE_URL) suggest
        followers or additional databases attached to the app.
        """
        pg_url_vars = [
            name
            for name in config_var_names
            if name.startswith("HEROKU_POSTGRESQL_") and name.endswith("_URL")
        ]

        followers = []
        for var_name in pg_url_vars:
            # Extract color name: HEROKU_POSTGRESQL_AMBER_URL -> AMBER
            parts = var_name.replace("HEROKU_POSTGRESQL_", "").replace("_URL", "")
            followers.append(
                {
                    "config_var": var_name,
                    "color": parts,
                }
            )

        return followers

    def _get_addon_attachments(self, app_name: str) -> List[Dict[str, Any]]:
        """Get addon attachments for cross-app database sharing detection."""
        try:
            response = self.session.get(
                f"{self.api_base_url}/apps/{app_name}/addon-attachments"
            )

            if response.status_code != 200:
                return []

            attachments = response.json()
            return [
                {
                    "name": a.get("name", ""),
                    "addon_name": a.get("addon", {}).get("name", ""),
                    "app_name": a.get("app", {}).get("name", ""),
                    "is_cross_app": a.get("app", {}).get("name") != app_name,
                }
                for a in attachments
            ]

        except Exception as e:
            self.add_warning(f"Failed to get attachments for {app_name}: {e}")
            return []

    def _enrich_with_plan_specs(self, plan_suffix: str) -> Dict[str, Any]:
        """Look up plan specifications from the static plan mapping."""
        specs = HEROKU_PLAN_SPECS.get(plan_suffix)
        if specs:
            return dict(specs)

        # Unknown plan - return what we can infer from the name
        tier = plan_suffix.split("-")[0] if "-" in plan_suffix else plan_suffix
        return {
            "tier": tier or "unknown",
            "vcpu": "unknown",
            "ram_gb": "unknown",
            "storage_gb": "unknown",
            "connection_limit": "unknown",
            "piops": "unknown",
            "ha": "unknown",
            "fork": "unknown",
            "follow": "unknown",
        }

    def _generate_summary(self, apps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate summary statistics for discovered apps."""
        total_databases = 0
        plans = {}
        regions = set()
        pooling_count = 0
        follower_count = 0

        for app in apps:
            regions.add(app.get("region", "unknown"))

            dbs = app.get("databases", [])
            total_databases += len(dbs)

            for db in dbs:
                plan = db.get("plan_name", "unknown")
                plans[plan] = plans.get(plan, 0) + 1

            if app.get("connection_pooling", {}).get("detected"):
                pooling_count += 1

            follower_count += len(app.get("followers", []))

        return {
            "total_apps": len(apps),
            "total_databases": total_databases,
            "plans": plans,
            "regions": sorted(list(regions)),
            "apps_with_pooling": pooling_count,
            "total_followers": follower_count,
        }
