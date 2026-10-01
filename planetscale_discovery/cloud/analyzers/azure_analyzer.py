"""
Azure Cloud Database Environment Analyzer

Analyzes Azure Database for PostgreSQL / MySQL Flexible Servers and the
surrounding virtual network infrastructure.
"""

from typing import Any, Dict, List, Optional
import logging

try:
    from azure.identity import ClientSecretCredential, DefaultAzureCredential
    from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
    from azure.mgmt.postgresqlflexibleservers import PostgreSQLManagementClient
    from azure.mgmt.mysqlflexibleservers import MySQLManagementClient
    from azure.mgmt.network import NetworkManagementClient

    HAS_AZURE_LIBS = True
except ImportError:
    HAS_AZURE_LIBS = False

from ...common.base_analyzer import CloudAnalyzer
from ...common.utils import generate_timestamp
from .azure_sku_specs import is_arm_sku, sku_specs

DOCS_URL = (
    "https://github.com/planetscale/ps-discovery/blob/main/docs/providers/azure.md"
)

# Azure firewall-rule sentinels: 0.0.0.0 alone means "all Azure services",
# paired with 255.255.255.255 it means "the whole internet". Detected, not bound.
_ANY_ADDRESS = "0.0.0.0"  # nosec B104
_LAST_ADDRESS = "255.255.255.255"

# Resource keys written into resources[<region>]. A region holding none of a
# given type still reports an empty list.
RESOURCE_KEYS = (
    "postgresql_flexible_servers",
    "mysql_flexible_servers",
    "virtual_networks",
    "network_security_groups",
)

# Provider-neutral views of the Azure-native arrays above, for consumers that
# read one common vocabulary.
CANONICAL_KEYS = (
    "instances",
    "vpcs",
    "security_groups",
    "db_subnet_groups",
)

INSTANCES_KEY = "instances"

ENGINE_PRODUCT = {
    "postgres": "Azure Database for PostgreSQL",
    "mysql": "Azure Database for MySQL",
}


def _name_from_id(resource_id: Any) -> str:
    """The trailing resource name from an ARM id, or the value unchanged."""
    if not resource_id:
        return ""
    return str(resource_id).rstrip("/").split("/")[-1]


def _subnet_ref_from_id(subnet_id: Any) -> Any:
    """Split a subnet ARM id into (vnet_name, subnet_name).

    Returns (None, None) when the id is absent or not subnet-shaped.
    """
    if not subnet_id:
        return (None, None)
    parts = str(subnet_id).split("/")
    vnet = subnet = None
    for index, part in enumerate(parts):
        lowered = part.lower()
        if lowered == "virtualnetworks" and index + 1 < len(parts):
            vnet = parts[index + 1]
        elif lowered == "subnets" and index + 1 < len(parts):
            subnet = parts[index + 1]
    return (vnet, subnet)


def _normalize_location(value: Any) -> str:
    """Normalize an Azure location for comparison ("East US" -> "eastus")."""
    if not value:
        return ""
    return str(value).strip().lower().replace(" ", "")


def _enum_str(value: Any) -> str:
    """Render an Azure SDK enum (or plain value) as its wire string.

    Azure enums subclass ``str`` but remain ``Enum``, so ``str()`` yields
    ``"HighAvailabilityMode.ZONE_REDUNDANT"``; ``.value`` is the wire form.
    """
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _as_bool(value: Any) -> Any:
    """Coerce an Azure tri-state flag to a real bool, or None if absent.

    The MySQL SDK types ``is_read_only`` as the string ``"True"``/``"False"``,
    where a plain ``bool()`` would always be ``True``.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = _enum_str(value).strip().lower()
    if text in ("true", "1", "yes", "enabled"):
        return True
    if text in ("false", "0", "no", "disabled"):
        return False
    return None


def _resource_group_from_id(resource_id: Any) -> str:
    """Extract the resource group from an ARM resource ID.

    It is not a field on any server model, and the segment's case varies.
    """
    if not resource_id:
        return ""
    segments = str(resource_id).split("/")
    for index, segment in enumerate(segments):
        if segment.lower() == "resourcegroups" and index + 1 < len(segments):
            return segments[index + 1]
    return ""


def _server_version_full(server: Any) -> str:
    """The most complete engine version string the server model offers."""
    version = _enum_str(getattr(server, "version", "")).strip()
    minor = str(getattr(server, "minor_version", "") or "").strip()
    full = str(getattr(server, "full_version", "") or "").strip()

    if full:
        return full
    if version and minor and "." not in version:
        return f"{version}.{minor}"
    return version


def _props(obj: Any) -> Any:
    """Return the ``properties`` sub-object when a model nests its fields.

    azure-mgmt-network nests fields under ``properties``; Flexible Server
    models are flat, where this is a no-op.
    """
    return getattr(obj, "properties", None) or obj


class AzureAnalyzer(CloudAnalyzer):
    """Azure cloud database environment analyzer."""

    def __init__(self, config: Any, logger: logging.Logger = None):
        """Initialize Azure analyzer."""
        super().__init__(
            config.__dict__ if hasattr(config, "__dict__") else config, "azure", logger
        )
        self.credential = None
        self.subscription_id = self.config.get("subscription_id") or ""
        self.resource_groups = list(self.config.get("resource_groups") or [])
        # An empty filter means every region: a subscription-wide list costs
        # the same either way, and a default region would hide servers.
        self.regions = [
            _normalize_location(region)
            for region in (self.config.get("regions") or [])
            if _normalize_location(region)
        ]

        if not HAS_AZURE_LIBS:
            self.add_error(
                "Azure libraries not installed. Run: pip install azure-identity "
                "azure-mgmt-postgresqlflexibleservers "
                "azure-mgmt-mysqlflexibleservers azure-mgmt-network"
            )

        if not self.subscription_id:
            self.add_error(
                "Azure subscription ID is required. Azure has no ambient "
                "subscription default.\n"
                "  Set providers.azure.subscription_id, pass "
                "--azure-subscription,\n"
                "  or export AZURE_SUBSCRIPTION_ID.\n"
                f"  Setup guide: {DOCS_URL}"
            )

        self.postgresql_client = None
        self.mysql_client = None
        self.network_client = None
        self._resources_by_region: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        # Locations that held resources but fell outside the region filter.
        self._filtered_out_locations: Dict[str, int] = {}
        # Lowercased source server ARM id -> number of replicas pointing at it.
        self._replica_counts: Dict[str, int] = {}
        self._server_locations: Dict[str, str] = {}

    def _quiet_sdk_logging(self) -> None:
        """Stop the Azure SDK logging every HTTP request and response at INFO.

        Left alone under --log-level DEBUG, where that traffic is the point.
        """
        if self.logger.isEnabledFor(logging.DEBUG):
            return
        for name in ("azure", "msal", "msrest"):
            logging.getLogger(name).setLevel(logging.WARNING)

    def authenticate(self) -> bool:
        """Authenticate with Azure."""
        if not HAS_AZURE_LIBS or not self.subscription_id:
            return False

        self._quiet_sdk_logging()

        try:
            tenant_id = self.config.get("tenant_id")
            client_id = self.config.get("client_id")
            client_secret = self.config.get("client_secret")

            if tenant_id and client_id and client_secret:
                self.credential = ClientSecretCredential(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                self.logger.info("Authenticated with Azure using a service principal")
            else:
                # Chains the Azure CLI (az login), managed identity and the
                # AZURE_* environment variables.
                self.credential = DefaultAzureCredential()
                self.logger.info("Using Azure default credentials")

            self.postgresql_client = PostgreSQLManagementClient(
                self.credential, self.subscription_id
            )
            self.mysql_client = MySQLManagementClient(
                self.credential, self.subscription_id
            )
            self.network_client = NetworkManagementClient(
                self.credential, self.subscription_id
            )

            # Verify the credential with a real call. ItemPaged is lazy, so
            # the request fires only on iteration.
            try:
                for _ in self.postgresql_client.servers.list():
                    break
            except HttpResponseError as e:
                if not self._warn_if_tolerable(
                    e, "verifying Azure credentials against the PostgreSQL API"
                ):
                    raise

            self.logger.info(
                f"Successfully authenticated with Azure subscription "
                f"{self.subscription_id}"
            )
            return True

        except ClientAuthenticationError as e:
            self.add_error(
                "Azure authentication failed - no valid credentials found.\n"
                "  Quick start: az login\n"
                "  Or set: AZURE_TENANT_ID, AZURE_CLIENT_ID and "
                "AZURE_CLIENT_SECRET\n"
                f"  Setup guide: {DOCS_URL}",
                e,
            )
            return False
        except Exception as e:
            self.add_error(
                "Azure authentication failed.\n" f"  Setup guide: {DOCS_URL}",
                e,
            )
            return False

    def _warn_if_tolerable(self, error: Any, context: str) -> bool:
        """Record a permission or registration problem as a warning.

        Returns True when the caller should carry on: an unregistered resource
        provider or a missing privilege is a partial result, not a failed run.
        """
        code = _enum_str(getattr(getattr(error, "error", None), "code", "")) or ""
        status = getattr(error, "status_code", None)

        if code == "MissingSubscriptionRegistration":
            self.add_warning(
                f"Azure resource provider not registered while {context}. "
                "The subscription owner can enable it with: "
                "az provider register --namespace Microsoft.DBforPostgreSQL "
                "(and Microsoft.DBforMySQL, Microsoft.Network)"
            )
            return True

        if status in (401, 403) or code in ("AuthorizationFailed", "Forbidden"):
            self.add_warning(
                f"Insufficient permissions while {context}: {error}. "
                "The 'Reader' role on the subscription is enough for discovery."
            )
            return True

        return False

    def _list_servers(self, client: Any, label: str) -> List[Any]:
        """List Flexible Servers, honoring the resource_groups scope."""
        servers: List[Any] = []

        if self.resource_groups:
            for resource_group in self.resource_groups:
                try:
                    servers.extend(
                        client.servers.list_by_resource_group(resource_group)
                    )
                except HttpResponseError as e:
                    if not self._warn_if_tolerable(
                        e, f"listing {label} in resource group {resource_group}"
                    ):
                        self.add_warning(
                            f"Failed to list {label} in resource group "
                            f"{resource_group}: {e}"
                        )
                except Exception as e:
                    self.add_warning(
                        f"Failed to list {label} in resource group "
                        f"{resource_group}: {e}"
                    )
            return servers

        try:
            servers.extend(client.servers.list())
        except HttpResponseError as e:
            if not self._warn_if_tolerable(e, f"listing {label}"):
                self.add_warning(f"Failed to list {label}: {e}")
        except Exception as e:
            self.add_warning(f"Failed to list {label}: {e}")

        return servers

    def _fetch_servers_raw(
        self, client: Any, label: str, resource_key: str
    ) -> List[Any]:
        """Fetch server objects honoring discover_all and resources config.

        A populated resources.<resource_key> narrows to those names; otherwise
        discover_all decides whether to list at all.
        """
        discover_all = self.config.get("discover_all", True)
        resources = self.config.get("resources") or {}
        names = resources.get(resource_key) or []

        if names:
            wanted = {str(name).lower() for name in names}
            servers = [
                server
                for server in self._list_servers(client, label)
                if str(getattr(server, "name", "")).lower() in wanted
            ]
            found = {str(getattr(s, "name", "")).lower() for s in servers}
            for missing in sorted(wanted - found):
                self.logger.debug(
                    f"{label} '{missing}' not found in subscription "
                    f"{self.subscription_id}"
                )
            return servers

        if not discover_all:
            self.add_warning(
                f"discover_all=false and no resources.{resource_key} configured; "
                f"skipping {label} discovery"
            )
            return []

        return self._list_servers(client, label)

    def discover_resources(self) -> List[str]:
        """Discover Azure database resources."""
        resources: List[str] = []

        if not HAS_AZURE_LIBS:
            return resources

        for client, label, prefix, key in (
            (
                self.postgresql_client,
                "PostgreSQL Flexible Servers",
                "postgresql-flexible-server",
                "postgresql_flexible_servers",
            ),
            (
                self.mysql_client,
                "MySQL Flexible Servers",
                "mysql-flexible-server",
                "mysql_flexible_servers",
            ),
        ):
            if client is None:
                continue
            try:
                for server in self._fetch_servers_raw(client, label, key):
                    location = _normalize_location(getattr(server, "location", ""))
                    resources.append(
                        f"{prefix}:{location}:{getattr(server, 'name', '')}"
                    )
            except Exception as e:
                self.add_warning(f"Failed to discover {label}: {e}")

        return resources

    def analyze(self) -> Dict[str, Any]:
        """Analyze Azure cloud database environment."""
        analysis_results: Dict[str, Any] = {
            "provider": "azure",
            "timestamp": generate_timestamp(),
            "subscription_id": self.subscription_id,
            "regions_analyzed": [],
            "resources": {},
            "networking": {},
            "security": {},
            "operations": {},
            "costs": {},
            "summary": {},
            "complexity_factors": {},
            "metadata": self.get_analysis_metadata(),
        }

        if not HAS_AZURE_LIBS:
            return analysis_results

        # Azure lists per subscription, so fetch once and bucket by each
        # resource's own location.
        try:
            self._collect_subscription_resources()
        except Exception as e:
            self.add_error("Failed to collect Azure subscription resources", e)

        regions = self._regions_to_report()
        analysis_results["regions_analyzed"] = regions

        for region in regions:
            self.logger.info(f"Analyzing Azure resources in region {region}")

            try:
                analysis_results["resources"][region] = self._analyze_region(region)
            except Exception as e:
                self.add_error(f"Failed to analyze region {region}", e)

        analysis_results["summary"] = self._generate_azure_summary(
            analysis_results["resources"]
        )
        analysis_results["complexity_factors"] = self._assess_complexity(
            analysis_results["resources"]
        )
        analysis_results["metadata"] = self.get_analysis_metadata()

        return analysis_results

    def _collect_subscription_resources(self) -> None:
        """Fetch every resource type once and bucket it by its own location."""
        self._resources_by_region = {}

        for region in self.regions:
            self._seed_region(region)

        for key, items in (
            (
                "postgresql_flexible_servers",
                self._analyze_postgresql_flexible_servers(),
            ),
            ("mysql_flexible_servers", self._analyze_mysql_flexible_servers()),
            ("virtual_networks", self._analyze_virtual_networks()),
            ("network_security_groups", self._analyze_network_security_groups()),
        ):
            for item in items:
                self._bucket(item.get("location") or "unknown", key, item)

        self._count_replicas()
        self._warn_about_filtered_regions()

    def _count_replicas(self) -> None:
        """Index servers by id and count replicas per primary.

        A replica names its primary in source_server_resource_id, and may sit
        in a different region from it.
        """
        self._replica_counts = {}
        self._server_locations = {}

        for bucket in self._resources_by_region.values():
            for key in ("postgresql_flexible_servers", "mysql_flexible_servers"):
                for server in bucket.get(key) or []:
                    server_id = str(server.get("id") or "").lower()
                    if server_id:
                        self._server_locations[server_id] = server.get("location", "")

                    source = str(server.get("source_server_resource_id") or "").lower()
                    if source:
                        self._replica_counts[source] = (
                            self._replica_counts.get(source, 0) + 1
                        )

    def _warn_about_filtered_regions(self) -> None:
        """Say so when the region filter excluded resources that do exist.

        A filter that misses every server - `eastus` when the servers are in
        `eastus2` - would otherwise produce an empty report silently.
        """
        if not self.regions or not self._filtered_out_locations:
            return

        excluded = ", ".join(
            f"{location} ({count})"
            for location, count in sorted(self._filtered_out_locations.items())
        )
        found_nothing = not any(
            any(bucket.get(key) for key in RESOURCE_KEYS)
            for bucket in self._resources_by_region.values()
        )
        lead = (
            "The configured regions matched no resources at all"
            if found_nothing
            else "Some resources were outside the configured regions"
        )
        self.add_warning(
            f"{lead}. Configured: {', '.join(sorted(self.regions))}. "
            f"Skipped by region filter: {excluded}. "
            "Azure region names have no spaces and often end in a digit "
            "(eastus2, not eastus); remove the regions setting to scan every "
            "region."
        )

    def _seed_region(self, location: str) -> Dict[str, List[Dict[str, Any]]]:
        """Return the bucket for a location, creating it with every key."""
        if location not in self._resources_by_region:
            self._resources_by_region[location] = {key: [] for key in RESOURCE_KEYS}
        return self._resources_by_region[location]

    def _in_scope(self, location: str) -> bool:
        """Whether a location passes the configured region filter."""
        return not self.regions or location in self.regions

    def _skip_out_of_scope(self, location: str) -> bool:
        """True when the filter rejects this location, recording it first."""
        if self._in_scope(location):
            return False
        self._filtered_out_locations[location] = (
            self._filtered_out_locations.get(location, 0) + 1
        )
        return True

    def _bucket(self, location: str, key: str, item: Dict[str, Any]) -> None:
        """Place one resource in its location's bucket, honoring the filter."""
        if self._skip_out_of_scope(location):
            return
        self._seed_region(location)[key].append(item)

    def _regions_to_report(self) -> List[str]:
        """Decide which regions appear in the output.

        With a filter, report the filter itself, so a scanned-but-empty region
        stays visible; without one, only regions that hold resources.
        """
        if self.regions:
            return sorted(self.regions)
        return sorted(self._resources_by_region)

    def _analyze_region(self, region: str) -> Dict[str, Any]:
        """Assemble one region's results from the collected buckets."""
        bucket = self._resources_by_region.get(region) or {}

        region_analysis: Dict[str, Any] = {
            key: list(bucket.get(key) or []) for key in RESOURCE_KEYS
        }

        # Provider-neutral views, derived from the native arrays above.
        postgres_servers = region_analysis["postgresql_flexible_servers"]
        mysql_servers = region_analysis["mysql_flexible_servers"]

        region_analysis[INSTANCES_KEY] = [
            self._canonical_instance(server, "postgres") for server in postgres_servers
        ] + [self._canonical_instance(server, "mysql") for server in mysql_servers]

        region_analysis["vpcs"] = [
            self._canonical_vpc(vnet) for vnet in region_analysis["virtual_networks"]
        ]
        region_analysis["security_groups"] = [
            self._canonical_security_group(nsg)
            for nsg in region_analysis["network_security_groups"]
        ]
        region_analysis["db_subnet_groups"] = self._canonical_db_subnet_groups(
            postgres_servers + mysql_servers, region_analysis["vpcs"]
        )

        region_analysis["networking_summary"] = {}
        region_analysis["security_summary"] = {}

        return region_analysis

    def _analyze_postgresql_flexible_servers(self) -> List[Dict[str, Any]]:
        """Analyze Azure Database for PostgreSQL Flexible Servers."""
        servers = []

        for server in self._fetch_servers_raw(
            self.postgresql_client,
            "PostgreSQL Flexible Servers",
            "postgresql_flexible_servers",
        ):
            # Filter before mapping: mapping costs two API calls per server.
            if self._skip_out_of_scope(
                _normalize_location(getattr(server, "location", ""))
            ):
                continue

            try:
                servers.append(
                    self._analyze_server(server, self.postgresql_client, "postgres")
                )
            except Exception as e:
                self.add_warning(
                    f"Failed to analyze PostgreSQL Flexible Server "
                    f"'{getattr(server, 'name', 'unknown')}': {e}"
                )

        return servers

    def _analyze_mysql_flexible_servers(self) -> List[Dict[str, Any]]:
        """Analyze Azure Database for MySQL Flexible Servers."""
        servers = []

        for server in self._fetch_servers_raw(
            self.mysql_client, "MySQL Flexible Servers", "mysql_flexible_servers"
        ):
            # Filter before mapping: mapping costs two API calls per server.
            if self._skip_out_of_scope(
                _normalize_location(getattr(server, "location", ""))
            ):
                continue

            try:
                servers.append(self._analyze_server(server, self.mysql_client, "mysql"))
            except Exception as e:
                self.add_warning(
                    f"Failed to analyze MySQL Flexible Server "
                    f"'{getattr(server, 'name', 'unknown')}': {e}"
                )

        return servers

    def _compute_specs(
        self, sku_name: str, engine: str, storage_gb: Optional[int]
    ) -> Dict[str, Any]:
        """vCPU, RAM, storage and CPU architecture for a server's SKU.

        vcpu and ram_gb are omitted for an unrecognized SKU rather than
        guessed.
        """
        specs: Dict[str, Any] = {
            "storage_gb": storage_gb,
            "architecture": "aarch64" if is_arm_sku(sku_name) else "x86_64",
        }
        resolved = sku_specs(sku_name, engine)
        if resolved:
            specs["vcpu"], specs["ram_gb"] = resolved
        return specs

    def _analyze_server(self, server: Any, client: Any, engine: str) -> Dict[str, Any]:
        """Map one Flexible Server object to a result dict.

        Shared by both engines, with getattr fallbacks where they spell a
        field differently. No credential or identity fields are collected.
        """
        sku = getattr(server, "sku", None)
        storage = getattr(server, "storage", None)
        backup = getattr(server, "backup", None)
        high_availability = getattr(server, "high_availability", None)
        network = getattr(server, "network", None)
        data_encryption = getattr(server, "data_encryption", None)

        resource_id = getattr(server, "id", "")
        resource_group = _resource_group_from_id(resource_id)
        name = getattr(server, "name", "")

        server_data: Dict[str, Any] = {
            "id": resource_id,
            "name": name,
            "location": _normalize_location(getattr(server, "location", "")),
            "resource_group": resource_group,
            "version": _enum_str(getattr(server, "version", "")),
            # PostgreSQL reports minor_version, MySQL full_version.
            "minor_version": (
                getattr(server, "minor_version", None)
                or getattr(server, "full_version", None)
                or ""
            ),
            # One comparable version field: PostgreSQL splits it across
            # version and minor_version, MySQL reports it whole.
            "version_full": _server_version_full(server),
            "state": _enum_str(getattr(server, "state", "")),
            "fully_qualified_domain_name": getattr(
                server, "fully_qualified_domain_name", ""
            ),
            "availability_zone": getattr(server, "availability_zone", ""),
            "replication_role": _enum_str(getattr(server, "replication_role", "")),
            "replica_capacity": getattr(server, "replica_capacity", None),
            "source_server_resource_id": getattr(
                server, "source_server_resource_id", ""
            ),
            "sku": {
                "name": getattr(sku, "name", "") if sku else "",
                "tier": _enum_str(getattr(sku, "tier", "")) if sku else "",
            },
            # Azure states compute only as a SKU name, so vCPU and RAM come
            # from the generated SKU table.
            "compute_specs": self._compute_specs(
                getattr(sku, "name", "") if sku else "",
                engine,
                getattr(storage, "storage_size_gb", None) if storage else None,
            ),
            "storage": {
                "storage_size_gb": (
                    getattr(storage, "storage_size_gb", None) if storage else None
                ),
                "tier": _enum_str(getattr(storage, "tier", "")) if storage else "",
                "iops": getattr(storage, "iops", None) if storage else None,
                "auto_grow": (
                    _enum_str(getattr(storage, "auto_grow", "")) if storage else ""
                ),
                # MySQL only.
                "auto_io_scaling": (
                    _enum_str(getattr(storage, "auto_io_scaling", ""))
                    if storage
                    else ""
                ),
                "storage_redundancy": (
                    _enum_str(getattr(storage, "storage_redundancy", ""))
                    if storage
                    else ""
                ),
            },
            "backup": {
                "retention_days": (
                    getattr(backup, "backup_retention_days", None) if backup else None
                ),
                "geo_redundant_backup": (
                    _enum_str(getattr(backup, "geo_redundant_backup", ""))
                    if backup
                    else ""
                ),
            },
            "high_availability": {
                "mode": (
                    _enum_str(getattr(high_availability, "mode", ""))
                    if high_availability
                    else ""
                ),
                "state": (
                    _enum_str(getattr(high_availability, "state", ""))
                    if high_availability
                    else ""
                ),
                "standby_availability_zone": (
                    getattr(high_availability, "standby_availability_zone", "")
                    if high_availability
                    else ""
                ),
            },
            "network": {
                "public_network_access": (
                    _enum_str(getattr(network, "public_network_access", ""))
                    if network
                    else ""
                ),
                "delegated_subnet_resource_id": (
                    getattr(network, "delegated_subnet_resource_id", "")
                    if network
                    else ""
                ),
                "private_dns_zone_resource_id": (
                    (
                        getattr(network, "private_dns_zone_arm_resource_id", None)
                        or getattr(network, "private_dns_zone_resource_id", None)
                        or ""
                    )
                    if network
                    else ""
                ),
            },
            "data_encryption": {
                "type": (
                    _enum_str(getattr(data_encryption, "type", ""))
                    if data_encryption
                    else ""
                ),
            },
            "tags": dict(getattr(server, "tags", None) or {}),
        }

        if resource_group and name:
            server_data["server_parameters"] = self._get_server_configurations(
                client, resource_group, name
            )
            server_data["firewall_rules"] = self._get_server_firewall_rules(
                client, resource_group, name
            )
        else:
            server_data["server_parameters"] = []
            server_data["firewall_rules"] = []

        return server_data

    def _get_server_configurations(
        self, client: Any, resource_group: str, name: str
    ) -> List[Dict[str, Any]]:
        """Collect server parameters that differ from the service default."""
        parameters = []

        try:
            for configuration in client.configurations.list_by_server(
                resource_group, name
            ):
                source = _enum_str(getattr(configuration, "source", ""))
                if source in ("", "system-default"):
                    continue
                value = getattr(configuration, "value", None)
                if value is None:
                    value = getattr(configuration, "current_value", None)
                parameters.append(
                    {
                        "name": getattr(configuration, "name", ""),
                        "value": value,
                        "default_value": getattr(configuration, "default_value", None),
                        "source": source,
                        "is_read_only": _as_bool(
                            getattr(configuration, "is_read_only", None)
                        ),
                        "is_pending_restart": _as_bool(
                            getattr(configuration, "is_config_pending_restart", None)
                        ),
                    }
                )
        except HttpResponseError as e:
            if not self._warn_if_tolerable(
                e, f"reading server parameters for '{name}'"
            ):
                self.add_warning(f"Failed to read server parameters for '{name}': {e}")
        except Exception as e:
            self.add_warning(f"Failed to read server parameters for '{name}': {e}")

        return parameters

    def _get_server_firewall_rules(
        self, client: Any, resource_group: str, name: str
    ) -> List[Dict[str, Any]]:
        """Collect server-level firewall rules and flag the permissive ones."""
        rules = []

        try:
            for rule in client.firewall_rules.list_by_server(resource_group, name):
                start = getattr(rule, "start_ip_address", "") or ""
                end = getattr(rule, "end_ip_address", "") or ""
                rules.append(
                    {
                        "name": getattr(rule, "name", ""),
                        "start_ip_address": start,
                        "end_ip_address": end,
                        # Reaches beyond this subscription.
                        "allows_azure_services": start == _ANY_ADDRESS
                        and end == _ANY_ADDRESS,
                        "allows_all_addresses": start == _ANY_ADDRESS
                        and end == _LAST_ADDRESS,
                    }
                )
        except HttpResponseError as e:
            if not self._warn_if_tolerable(e, f"reading firewall rules for '{name}'"):
                self.add_warning(f"Failed to read firewall rules for '{name}': {e}")
        except Exception as e:
            self.add_warning(f"Failed to read firewall rules for '{name}': {e}")

        return rules

    def _list_network_resources(self, operations: Any, label: str) -> List[Any]:
        """List a network resource type, honoring the resource_groups scope.

        Uses the scoped ``list(rg)`` when resource_groups is set, so the
        setting narrows networking as well as servers.
        """
        items: List[Any] = []

        if self.resource_groups:
            for resource_group in self.resource_groups:
                try:
                    items.extend(operations.list(resource_group))
                except HttpResponseError as e:
                    if not self._warn_if_tolerable(
                        e, f"listing {label} in resource group {resource_group}"
                    ):
                        self.add_warning(
                            f"Failed to list {label} in resource group "
                            f"{resource_group}: {e}"
                        )
                except Exception as e:
                    self.add_warning(
                        f"Failed to list {label} in resource group "
                        f"{resource_group}: {e}"
                    )
            return items

        try:
            items.extend(operations.list_all())
        except HttpResponseError as e:
            if not self._warn_if_tolerable(e, f"listing {label}"):
                self.add_warning(f"Failed to list {label}: {e}")
        except Exception as e:
            self.add_warning(f"Failed to list {label}: {e}")

        return items

    def _analyze_virtual_networks(self) -> List[Dict[str, Any]]:
        """Analyze virtual networks across the subscription.

        One subscription-wide call, bucketed by each VNet's own location.
        """
        networks = []

        if self.network_client is None:
            return networks

        try:
            for vnet in self._list_network_resources(
                self.network_client.virtual_networks, "virtual networks"
            ):
                properties = _props(vnet)
                address_space = getattr(properties, "address_space", None)

                networks.append(
                    {
                        "name": getattr(vnet, "name", ""),
                        "location": _normalize_location(getattr(vnet, "location", "")),
                        "resource_group": _resource_group_from_id(
                            getattr(vnet, "id", "")
                        ),
                        "address_prefixes": (
                            list(getattr(address_space, "address_prefixes", None) or [])
                            if address_space
                            else []
                        ),
                        "subnets": self._map_subnets(
                            getattr(properties, "subnets", None) or []
                        ),
                        "peerings": self._map_peerings(
                            getattr(properties, "virtual_network_peerings", None) or []
                        ),
                        "tags": dict(getattr(vnet, "tags", None) or {}),
                    }
                )
        except HttpResponseError as e:
            if not self._warn_if_tolerable(e, "listing virtual networks"):
                self.add_warning(f"Failed to list virtual networks: {e}")
        except Exception as e:
            self.add_warning(f"Failed to list virtual networks: {e}")

        return networks

    def _map_subnets(self, subnets: Any) -> List[Dict[str, Any]]:
        """Map the subnets nested inside a virtual network."""
        mapped = []

        for subnet in subnets:
            properties = _props(subnet)
            nsg = getattr(properties, "network_security_group", None)
            delegations = getattr(properties, "delegations", None) or []
            service_endpoints = getattr(properties, "service_endpoints", None) or []

            mapped.append(
                {
                    "name": getattr(subnet, "name", ""),
                    "address_prefix": getattr(properties, "address_prefix", "") or "",
                    "address_prefixes": list(
                        getattr(properties, "address_prefixes", None) or []
                    ),
                    # A delegation to Microsoft.DBforPostgreSQL/flexibleServers
                    # is what makes a subnet VNet-injectable.
                    "delegations": [
                        _enum_str(getattr(_props(delegation), "service_name", ""))
                        for delegation in delegations
                    ],
                    "service_endpoints": [
                        _enum_str(getattr(endpoint, "service", ""))
                        for endpoint in service_endpoints
                    ],
                    "network_security_group": getattr(nsg, "id", "") if nsg else "",
                }
            )

        return mapped

    def _map_peerings(self, peerings: Any) -> List[Dict[str, Any]]:
        """Map virtual network peerings."""
        mapped = []

        for peering in peerings:
            properties = _props(peering)
            remote = getattr(properties, "remote_virtual_network", None)

            mapped.append(
                {
                    "name": getattr(peering, "name", ""),
                    "peering_state": _enum_str(
                        getattr(properties, "peering_state", "")
                    ),
                    "remote_virtual_network": (
                        getattr(remote, "id", "") if remote else ""
                    ),
                    "allow_virtual_network_access": getattr(
                        properties, "allow_virtual_network_access", None
                    ),
                    "allow_forwarded_traffic": getattr(
                        properties, "allow_forwarded_traffic", None
                    ),
                    "allow_gateway_transit": getattr(
                        properties, "allow_gateway_transit", None
                    ),
                }
            )

        return mapped

    def _analyze_network_security_groups(self) -> List[Dict[str, Any]]:
        """Analyze network security groups across the subscription.

        Only security_rules: Azure keeps built-in rules in a separate list.
        """
        groups = []

        if self.network_client is None:
            return groups

        try:
            for nsg in self._list_network_resources(
                self.network_client.network_security_groups,
                "network security groups",
            ):
                properties = _props(nsg)

                groups.append(
                    {
                        "name": getattr(nsg, "name", ""),
                        "location": _normalize_location(getattr(nsg, "location", "")),
                        "resource_group": _resource_group_from_id(
                            getattr(nsg, "id", "")
                        ),
                        "security_rules": [
                            self._parse_nsg_rule(rule)
                            for rule in (
                                getattr(properties, "security_rules", None) or []
                            )
                        ],
                        "tags": dict(getattr(nsg, "tags", None) or {}),
                    }
                )
        except HttpResponseError as e:
            if not self._warn_if_tolerable(e, "listing network security groups"):
                self.add_warning(f"Failed to list network security groups: {e}")
        except Exception as e:
            self.add_warning(f"Failed to list network security groups: {e}")

        return groups

    def _parse_nsg_rule(self, rule: Any) -> Dict[str, Any]:
        """Map one network security group rule."""
        properties = _props(rule)

        source_prefix = getattr(properties, "source_address_prefix", "") or ""
        source_prefixes = list(
            getattr(properties, "source_address_prefixes", None) or []
        )
        if source_prefix:
            source_prefixes = [source_prefix] + source_prefixes

        destination_port = getattr(properties, "destination_port_range", "") or ""
        destination_ports = list(
            getattr(properties, "destination_port_ranges", None) or []
        )
        if destination_port:
            destination_ports = [destination_port] + destination_ports

        return {
            "name": getattr(rule, "name", ""),
            "direction": _enum_str(getattr(properties, "direction", "")),
            "access": _enum_str(getattr(properties, "access", "")),
            "protocol": _enum_str(getattr(properties, "protocol", "")),
            "priority": getattr(properties, "priority", None),
            "source_address_prefixes": source_prefixes,
            "destination_port_ranges": destination_ports,
            # "Internet" and "*" both mean "reachable from anywhere".
            "open_to_internet": any(
                prefix in ("*", f"{_ANY_ADDRESS}/0", "Internet")
                for prefix in source_prefixes
            ),
        }

    def _canonical_instance(
        self, server: Dict[str, Any], engine: str
    ) -> Dict[str, Any]:
        """Render one Flexible Server in the provider-neutral instance shape.

        Azure-only detail is nested under a single `_azure` key.
        """
        sku = server.get("sku") or {}
        storage = server.get("storage") or {}
        backup = server.get("backup") or {}
        network = server.get("network") or {}
        high_availability = server.get("high_availability") or {}

        sku_name = sku.get("name") or ""
        specs = sku_specs(sku_name, engine)

        tier = sku.get("tier") or ""
        product = ENGINE_PRODUCT.get(engine, "Azure Database")
        detail = f"{product} - Flexible Server"
        if tier:
            detail = f"{detail} ({tier})"

        instance: Dict[str, Any] = {
            "db_instance_identifier": server.get("name", ""),
            "db_instance_class": sku_name,
            "provider": "azure",
            "region": server.get("location", ""),
            "engine": engine,
            "engine_version": server.get("version_full") or server.get("version", ""),
            "allocated_storage": storage.get("storage_size_gb"),
            # True for SameZone as well as ZoneRedundant: both provision a
            # standby. The exact mode stays in _azure.
            "multi_az": self._has_high_availability(server),
            "storage_type": storage.get("tier") or "",
            "iops": storage.get("iops"),
            "publicly_accessible": _enum_str(
                network.get("public_network_access")
            ).lower()
            == "enabled",
            # Azure always encrypts Flexible Server storage at rest; an empty
            # data_encryption means a service-managed key, not none.
            "storage_encrypted": True,
            "backup_retention_period": backup.get("retention_days"),
            "availability_zone": server.get("availability_zone") or "",
            "architecture": "aarch64" if is_arm_sku(sku_name) else "x86_64",
            "replica_count": self._replica_counts.get(
                str(server.get("id") or "").lower(), 0
            ),
            "provider_detail": detail,
            # Discovered by reading the Azure API, not inferred by a model.
            "_ai_extracted": False,
            "_azure": {
                "id": server.get("id", ""),
                "resource_group": server.get("resource_group", ""),
                "fully_qualified_domain_name": server.get(
                    "fully_qualified_domain_name", ""
                ),
                "sku": sku,
                "storage": storage,
                "backup": backup,
                "high_availability": high_availability,
                "network": network,
                "data_encryption": server.get("data_encryption") or {},
                "replication_role": server.get("replication_role", ""),
                "source_server_resource_id": server.get(
                    "source_server_resource_id", ""
                ),
                "tags": server.get("tags") or {},
                "server_parameters": server.get("server_parameters") or [],
                "firewall_rules": server.get("firewall_rules") or [],
            },
        }

        # Omit rather than guess: a wrong vCPU count misleads sizing.
        if specs:
            instance["cpu_cores"], instance["memory_gb"] = specs
        else:
            self.add_warning(
                f"Unknown Azure compute SKU '{sku_name}' on "
                f"'{server.get('name', '')}': vCPU and memory are omitted from "
                "the canonical instance. Add it to azure_sku_specs.py."
            )

        vnet_name, subnet_name = _subnet_ref_from_id(
            network.get("delegated_subnet_resource_id")
        )
        if vnet_name and subnet_name:
            instance["vpc_id"] = vnet_name
            instance["db_subnet_group"] = f"{vnet_name}-{subnet_name}"

        return instance

    @staticmethod
    def _canonical_vpc(vnet: Dict[str, Any]) -> Dict[str, Any]:
        """Render one virtual network in the provider-neutral network shape."""
        prefixes = vnet.get("address_prefixes") or []
        region = vnet.get("location", "")

        return {
            "vpc_id": vnet.get("name", ""),
            "name": vnet.get("name", ""),
            "cidr_block": prefixes[0] if prefixes else "",
            "cidr_blocks": list(prefixes),
            "state": "available",
            "is_default": False,
            "subnets": [
                {
                    "subnet_id": subnet.get("name", ""),
                    "name": subnet.get("name", ""),
                    "cidr_block": subnet.get("address_prefix", ""),
                    # Azure subnets span a region rather than a single zone.
                    "availability_zone": region,
                    "delegations": subnet.get("delegations") or [],
                    "service_endpoints": subnet.get("service_endpoints") or [],
                    # By name, to match security_groups[].group_id.
                    "network_security_group": _name_from_id(
                        subnet.get("network_security_group", "")
                    ),
                }
                for subnet in vnet.get("subnets") or []
            ],
            "peerings": vnet.get("peerings") or [],
            "resource_group": vnet.get("resource_group", ""),
            "tags": vnet.get("tags") or {},
        }

    @staticmethod
    def _canonical_security_group(nsg: Dict[str, Any]) -> Dict[str, Any]:
        """Render one network security group in the neutral shape."""
        return {
            "group_id": nsg.get("name", ""),
            "group_name": nsg.get("name", ""),
            "name": nsg.get("name", ""),
            "security_rules": nsg.get("security_rules") or [],
            "resource_group": nsg.get("resource_group", ""),
        }

    @staticmethod
    def _canonical_db_subnet_groups(
        servers: List[Dict[str, Any]], vpcs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build subnet groups for VNet-injected servers.

        A server with a public endpoint has no delegated subnet.
        """
        by_vnet = {vpc.get("name"): vpc for vpc in vpcs}
        groups: Dict[str, Dict[str, Any]] = {}

        for server in servers:
            network = server.get("network") or {}
            vnet_name, subnet_name = _subnet_ref_from_id(
                network.get("delegated_subnet_resource_id")
            )
            if not (vnet_name and subnet_name):
                continue

            name = f"{vnet_name}-{subnet_name}"
            if name in groups:
                continue

            vpc = by_vnet.get(vnet_name) or {}
            matching = [
                subnet
                for subnet in vpc.get("subnets") or []
                if subnet.get("name") == subnet_name
            ]
            groups[name] = {
                "name": name,
                "db_subnet_group_name": name,
                "vpc_id": vnet_name,
                "subnets": matching,
            }

        return list(groups.values())

    def _generate_azure_summary(self, resources: Dict[str, Any]) -> Dict[str, Any]:
        """Generate Azure analysis summary."""
        summary = {
            "regions_analyzed": len(resources),
            "postgresql_flexible_servers": 0,
            "mysql_flexible_servers": 0,
            "virtual_networks": 0,
            "network_security_groups": 0,
            "high_availability_servers": 0,
            "encrypted_servers": 0,
            "private_access_servers": 0,
            "geo_redundant_backup_servers": 0,
            "read_replicas": 0,
        }

        for region_data in resources.values():
            for key in RESOURCE_KEYS:
                summary[key] += len(region_data.get(key, []))

            for server in self._servers_in(region_data):
                if self._has_high_availability(server):
                    summary["high_availability_servers"] += 1

                # Azure always encrypts Flexible Server storage at rest.
                summary["encrypted_servers"] += 1

                if self._is_private_access(server):
                    summary["private_access_servers"] += 1

                if (
                    _enum_str(server.get("backup", {}).get("geo_redundant_backup"))
                    .lower()
                    .startswith("enabled")
                ):
                    summary["geo_redundant_backup_servers"] += 1

                if self._is_read_replica(server):
                    summary["read_replicas"] += 1

        return summary

    def _assess_complexity(self, resources: Dict[str, Any]) -> Dict[str, Any]:
        """Assess infrastructure complexity factors."""
        complexity = {
            "high_availability_deployments": 0,
            "read_replicas": 0,
            "cross_region_replicas": 0,
            "custom_vnet_configuration": False,
            "private_access_only_servers": 0,
            "custom_network_security_rules": 0,
            "custom_server_parameters": 0,
            "backup_enabled": False,
            "geo_redundant_backup_enabled": False,
        }

        for region, region_data in resources.items():
            for server in self._servers_in(region_data):
                if self._has_high_availability(server):
                    complexity["high_availability_deployments"] += 1

                if self._is_read_replica(server):
                    complexity["read_replicas"] += 1

                    source_id = str(
                        server.get("source_server_resource_id") or ""
                    ).lower()
                    source_location = self._server_locations.get(source_id)
                    if source_location is None:
                        self.add_warning(
                            f"Source server for replica "
                            f"'{server.get('name', '')}' was not scanned, so "
                            "it is not counted in cross_region_replicas. "
                            "Widen the region or resource group filter to "
                            "include it."
                        )
                    elif source_location != region:
                        complexity["cross_region_replicas"] += 1

                if self._is_private_access(server):
                    complexity["private_access_only_servers"] += 1

                if server.get("network", {}).get("delegated_subnet_resource_id"):
                    complexity["custom_vnet_configuration"] = True

                complexity["custom_server_parameters"] += sum(
                    1
                    for p in server.get("server_parameters", [])
                    if not p.get("is_read_only")
                )

                retention = server.get("backup", {}).get("retention_days")
                if retention and retention >= 1:
                    complexity["backup_enabled"] = True

                if (
                    _enum_str(server.get("backup", {}).get("geo_redundant_backup"))
                    .lower()
                    .startswith("enabled")
                ):
                    complexity["geo_redundant_backup_enabled"] = True

                # Permissive firewall rules are a migration concern.
                complexity["custom_network_security_rules"] += len(
                    server.get("firewall_rules", [])
                )

            if region_data.get("virtual_networks"):
                complexity["custom_vnet_configuration"] = True

            for nsg in region_data.get("network_security_groups", []):
                complexity["custom_network_security_rules"] += len(
                    nsg.get("security_rules", [])
                )

        return complexity

    @staticmethod
    def _servers_in(region_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Both engines' servers, for the counters that treat them alike."""
        return list(region_data.get("postgresql_flexible_servers", [])) + list(
            region_data.get("mysql_flexible_servers", [])
        )

    @staticmethod
    def _has_high_availability(server: Dict[str, Any]) -> bool:
        """True when HA is configured, in either zone-redundant or same-zone mode."""
        mode = _enum_str(server.get("high_availability", {}).get("mode")).lower()
        return mode not in ("", "disabled")

    @staticmethod
    def _is_private_access(server: Dict[str, Any]) -> bool:
        """True when the server is not reachable over a public endpoint."""
        network = server.get("network", {})
        access = _enum_str(network.get("public_network_access")).lower()
        return access == "disabled" or bool(network.get("delegated_subnet_resource_id"))

    @staticmethod
    def _is_read_replica(server: Dict[str, Any]) -> bool:
        """True when the server is a read replica rather than a primary.

        Keyed off source_server_resource_id, which every engine populates on a
        replica and leaves empty on a primary.
        """
        return bool(server.get("source_server_resource_id"))
