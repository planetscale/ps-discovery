"""
GCP Cloud Database Environment Analyzer

Analyzes GCP Cloud SQL, AlloyDB, and related infrastructure.
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import logging

try:
    from google.cloud import compute_v1
    from google.cloud import monitoring_v3
    from google.cloud import alloydb_v1
    from google.oauth2 import service_account
    from google.auth import default
    from google.auth.exceptions import DefaultCredentialsError
    from googleapiclient import discovery
    from googleapiclient.errors import HttpError

    HAS_GCP_LIBS = True
except ImportError:
    HAS_GCP_LIBS = False

from ...common.base_analyzer import CloudAnalyzer
from ...common.utils import generate_timestamp


class GCPAnalyzer(CloudAnalyzer):
    """GCP cloud database environment analyzer."""

    def __init__(self, config: Any, logger: logging.Logger = None):
        """Initialize GCP analyzer."""
        super().__init__(
            config.__dict__ if hasattr(config, "__dict__") else config, "gcp", logger
        )
        self.credentials = None
        self.project_id = config.project_id
        self.regions = config.regions if config.regions else ["us-central1"]

        if not HAS_GCP_LIBS:
            self.add_error(
                "GCP libraries not installed. Run: pip install google-cloud-compute google-cloud-alloydb google-auth google-api-python-client"
            )

        self.sqladmin_service = None
        self.monitoring_client = None

    def authenticate(self) -> bool:
        """Authenticate with GCP."""
        if not HAS_GCP_LIBS:
            return False

        try:
            if self.config.get("service_account_key"):
                # Use service account key file
                key_path = self.config["service_account_key"]
                if not os.path.exists(key_path):
                    self.add_error(f"Service account key file not found: {key_path}")
                    return False

                self.credentials = (
                    service_account.Credentials.from_service_account_file(key_path)
                )
                self.logger.info(
                    f"Authenticated with GCP using service account key: {key_path}"
                )

            elif self.config.get("application_default"):
                # Use Application Default Credentials
                self.credentials, project = default()
                self.logger.info(
                    "Authenticated with GCP using Application Default Credentials"
                )

            else:
                # Try to use Application Default Credentials as fallback
                self.credentials, project = default()
                self.logger.info("Using Application Default Credentials")

            # Build SQL Admin API service
            self.sqladmin_service = discovery.build(
                "sqladmin", "v1", credentials=self.credentials, cache_discovery=False
            )

            # Test authentication with a simple list call
            try:
                self.sqladmin_service.instances().list(
                    project=self.project_id
                ).execute()
            except HttpError as e:
                if e.resp.status == 403:
                    # API might not be enabled, but auth worked
                    self.add_warning("SQL Admin API may not be enabled in this project")
                    pass

            # Initialize Cloud Monitoring client for metrics queries
            try:
                self.monitoring_client = monitoring_v3.MetricServiceClient(
                    credentials=self.credentials
                )
            except Exception as e:
                self.add_warning(f"Failed to initialize Cloud Monitoring client: {e}")

            self.logger.info("Successfully authenticated and initialized SQL Admin API")
            return True

        except DefaultCredentialsError as e:
            self.add_error(
                "GCP authentication failed - no valid credentials found. "
                "Configure credentials via Application Default Credentials "
                "or a service account key file.\n"
                "  Quick start: gcloud auth application-default login\n"
                "  Or set: export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json\n"
                "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/gcp.md",
                e,
            )
            return False
        except Exception as e:
            self.add_error(
                "GCP authentication failed.\n"
                "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/gcp.md",
                e,
            )
            return False

    def discover_resources(self) -> List[str]:
        """Discover GCP database resources."""
        resources = []

        if not HAS_GCP_LIBS:
            return resources

        try:
            # Discover AlloyDB clusters
            try:
                alloydb_client = alloydb_v1.AlloyDBAdminClient(
                    credentials=self.credentials
                )

                for region in self.regions:
                    parent = f"projects/{self.project_id}/locations/{region}"
                    request = alloydb_v1.ListClustersRequest(parent=parent)

                    for cluster in alloydb_client.list_clusters(request=request):
                        resources.append(
                            f"alloydb-cluster:{region}:{cluster.name.split('/')[-1]}"
                        )

            except Exception as e:
                self.add_warning(f"Failed to discover AlloyDB clusters: {e}")

        except Exception as e:
            self.add_error("Failed to discover GCP resources", e)

        return resources

    def analyze(self) -> Dict[str, Any]:
        """Analyze GCP cloud database environment."""
        analysis_results = {
            "provider": "gcp",
            "timestamp": generate_timestamp(),
            "project_id": self.project_id,
            "regions_analyzed": self.regions,
            "resources": {},
            "networking": {},
            "security": {},
            "operations": {},
            "costs": {},
            "summary": {},
            "complexity_factors": {},
            "metadata": self.get_analysis_metadata(),
        }

        if not HAS_GCP_LIBS:
            return analysis_results

        for region in self.regions:
            self.logger.info(f"Analyzing GCP resources in region {region}")

            try:
                region_analysis = self._analyze_region(region)
                analysis_results["resources"][region] = region_analysis

            except Exception as e:
                self.add_error(f"Failed to analyze region {region}", e)

        # Generate summary and complexity assessment
        analysis_results["summary"] = self._generate_gcp_summary(
            analysis_results["resources"]
        )
        analysis_results["complexity_factors"] = self._assess_complexity(
            analysis_results["resources"]
        )

        return analysis_results

    def _analyze_region(self, region: str) -> Dict[str, Any]:
        """Analyze GCP resources in a specific region."""
        region_analysis = {
            "cloud_sql_instances": [],
            "alloydb_clusters": [],
            "vpc_networks": [],
            "firewall_rules": [],
            "networking_summary": {},
            "security_summary": {},
        }

        # Analyze Cloud SQL instances
        region_analysis["cloud_sql_instances"] = self._analyze_cloud_sql_instances(
            region
        )

        # Analyze AlloyDB clusters
        region_analysis["alloydb_clusters"] = self._analyze_alloydb_clusters(region)

        # Analyze networking (VPC networks)
        region_analysis["vpc_networks"] = self._analyze_vpc_networks(region)

        # Analyze security (firewall rules)
        region_analysis["firewall_rules"] = self._analyze_firewall_rules(region)

        return region_analysis

    def _analyze_cloud_sql_instances(self, region: str) -> List[Dict[str, Any]]:
        """Analyze Cloud SQL instances in a region."""
        instances = []

        if not self.sqladmin_service:
            self.add_error("SQL Admin service not initialized")
            return instances

        try:
            # List all Cloud SQL instances in the project
            request = self.sqladmin_service.instances().list(project=self.project_id)
            response = request.execute()

            if "items" not in response:
                self.logger.info(
                    f"No Cloud SQL instances found in project {self.project_id}"
                )
                return instances

            for instance in response.get("items", []):
                # Skip instances not in this region
                instance_region = instance.get("region", "")
                if instance_region and instance_region != region:
                    continue

                # Extract basic instance information
                instance_analysis = {
                    "name": instance.get("name", ""),
                    "database_version": instance.get("databaseVersion", ""),
                    "backend_type": instance.get("backendType", "SECOND_GEN"),
                    "instance_type": instance.get("instanceType", "CLOUD_SQL_INSTANCE"),
                    "state": instance.get("state", "UNKNOWN"),
                    "region": instance_region,
                    "gce_zone": instance.get("gceZone", ""),
                    "project": instance.get("project", self.project_id),
                    "connection_name": instance.get("connectionName", ""),
                    "current_disk_size": instance.get("currentDiskSize", 0),
                    "max_disk_size": instance.get("maxDiskSize", 0),
                    "settings": {},
                    "ip_addresses": instance.get("ipAddresses", []),
                    "server_ca_cert": instance.get("serverCaCert", {}),
                }

                # Analyze instance settings
                if "settings" in instance and instance["settings"]:
                    settings = instance["settings"]
                    instance_analysis["settings"] = {
                        "tier": settings.get("tier", ""),
                        "pricing_plan": settings.get("pricingPlan", "UNKNOWN"),
                        "replication_type": settings.get("replicationType", "UNKNOWN"),
                        "activation_policy": settings.get(
                            "activationPolicy", "UNKNOWN"
                        ),
                        "storage_auto_resize": settings.get("storageAutoResize", False),
                        "storage_auto_resize_limit": settings.get(
                            "storageAutoResizeLimit", 0
                        ),
                        "data_disk_type": settings.get("dataDiskType", "UNKNOWN"),
                        "data_disk_size_gb": settings.get("dataDiskSizeGb", 0),
                        "availability_type": settings.get("availabilityType", "ZONAL"),
                        "backup_configuration": {},
                        "database_flags": [],
                        "ip_configuration": {},
                        "location_preference": {},
                        "maintenance_window": {},
                    }

                    # Backup configuration
                    if "backupConfiguration" in settings:
                        backup_config = settings["backupConfiguration"]
                        instance_analysis["settings"]["backup_configuration"] = {
                            "enabled": backup_config.get("enabled", False),
                            "start_time": backup_config.get("startTime", ""),
                            "location": backup_config.get("location", ""),
                            "point_in_time_recovery_enabled": backup_config.get(
                                "pointInTimeRecoveryEnabled", False
                            ),
                            "transaction_log_retention_days": backup_config.get(
                                "transactionLogRetentionDays", 0
                            ),
                            "backup_retention_settings": backup_config.get(
                                "backupRetentionSettings", {}
                            ),
                        }

                    # Database flags
                    if "databaseFlags" in settings:
                        instance_analysis["settings"]["database_flags"] = [
                            {
                                "name": flag.get("name", ""),
                                "value": flag.get("value", ""),
                            }
                            for flag in settings.get("databaseFlags", [])
                        ]

                    # IP configuration
                    if "ipConfiguration" in settings:
                        ip_config = settings["ipConfiguration"]
                        instance_analysis["settings"]["ip_configuration"] = {
                            "ipv4_enabled": ip_config.get("ipv4Enabled", True),
                            "private_network": ip_config.get("privateNetwork", ""),
                            "require_ssl": ip_config.get("requireSsl", False),
                            "authorized_networks": [
                                {
                                    "value": net.get("value", ""),
                                    "name": net.get("name", ""),
                                    "expiration_time": net.get("expirationTime", ""),
                                }
                                for net in ip_config.get("authorizedNetworks", [])
                            ],
                        }

                    # Maintenance window
                    if "maintenanceWindow" in settings:
                        maint_window = settings["maintenanceWindow"]
                        instance_analysis["settings"]["maintenance_window"] = {
                            "hour": maint_window.get("hour", 0),
                            "day": maint_window.get("day", 0),
                            "update_track": maint_window.get("updateTrack", "UNKNOWN"),
                        }

                instances.append(instance_analysis)

        except Exception as e:
            self.add_error(f"Failed to analyze Cloud SQL instances in {region}", e)

        return instances

    def _analyze_alloydb_clusters(self, region: str) -> List[Dict[str, Any]]:
        """Analyze AlloyDB clusters in a region."""
        clusters = []

        try:
            alloydb_client = alloydb_v1.AlloyDBAdminClient(credentials=self.credentials)
            parent = f"projects/{self.project_id}/locations/{region}"
            request = alloydb_v1.ListClustersRequest(parent=parent)

            for cluster in alloydb_client.list_clusters(request=request):
                cluster_analysis = {
                    "name": cluster.name.split("/")[-1],
                    "full_name": cluster.name,
                    "display_name": getattr(cluster, "display_name", ""),
                    "state": (
                        cluster.state.name if hasattr(cluster, "state") else "unknown"
                    ),
                    "cluster_type": (
                        cluster.cluster_type.name
                        if hasattr(cluster, "cluster_type")
                        else "unknown"
                    ),
                    "database_type": (
                        cluster.database_type.name
                        if hasattr(cluster, "database_type")
                        else "unknown"
                    ),
                    "network": getattr(cluster, "network", ""),
                    "etag": getattr(cluster, "etag", ""),
                    "labels": dict(getattr(cluster, "labels", {})),
                    "backup_source": {},
                    "migration_source": {},
                    "encryption_config": {},
                    "continuous_backup_config": {},
                    "primary_config": {},
                    "secondary_config": {},
                    "instances": [],
                }

                # Encryption configuration
                if hasattr(cluster, "encryption_config") and cluster.encryption_config:
                    enc_config = cluster.encryption_config
                    cluster_analysis["encryption_config"] = {
                        "kms_key_name": getattr(enc_config, "kms_key_name", "")
                    }

                # Continuous backup configuration
                if (
                    hasattr(cluster, "continuous_backup_config")
                    and cluster.continuous_backup_config
                ):
                    backup_config = cluster.continuous_backup_config
                    cluster_analysis["continuous_backup_config"] = {
                        "enabled": getattr(backup_config, "enabled", False),
                        "recovery_window_days": getattr(
                            backup_config, "recovery_window_days", 0
                        ),
                        "encryption_config": {},
                    }

                # Get cluster instances
                cluster_analysis["instances"] = self._get_alloydb_instances(
                    cluster.name
                )

                # Get storage usage from Cloud Monitoring
                cluster_name = cluster.name.split("/")[-1]
                storage_usage = self._get_alloydb_storage_usage(cluster_name, region)
                if storage_usage:
                    cluster_analysis["storage"] = storage_usage

                clusters.append(cluster_analysis)

        except Exception as e:
            self.add_error(f"Failed to analyze AlloyDB clusters in {region}", e)

        return clusters

    def _analyze_vpc_networks(self, region: str) -> List[Dict[str, Any]]:
        """Analyze VPC networks."""
        networks = []

        try:
            compute_client = compute_v1.NetworksClient(credentials=self.credentials)
            request = compute_v1.ListNetworksRequest(project=self.project_id)

            for network in compute_client.list(request=request):
                network_analysis = {
                    "name": network.name,
                    "description": getattr(network, "description", ""),
                    "self_link": getattr(network, "self_link", ""),
                    "auto_create_subnetworks": getattr(
                        network, "auto_create_subnetworks", False
                    ),
                    "mtu": getattr(network, "mtu", 0),
                    "routing_config": {},
                    "subnets": [],
                }

                # Routing configuration
                if hasattr(network, "routing_config") and network.routing_config:
                    routing_config = network.routing_config
                    network_analysis["routing_config"] = {
                        "routing_mode": (
                            routing_config.routing_mode
                            if hasattr(routing_config, "routing_mode")
                            else "unknown"
                        )
                    }

                # Get subnets for this network
                network_analysis["subnets"] = self._get_network_subnets(
                    network.self_link, region
                )

                networks.append(network_analysis)

        except Exception as e:
            self.add_error(f"Failed to analyze VPC networks in {region}", e)

        return networks

    def _analyze_firewall_rules(self, region: str) -> List[Dict[str, Any]]:
        """Analyze firewall rules."""
        firewall_rules = []

        try:
            compute_client = compute_v1.FirewallsClient(credentials=self.credentials)
            request = compute_v1.ListFirewallsRequest(project=self.project_id)

            for rule in compute_client.list(request=request):
                rule_analysis = {
                    "name": rule.name,
                    "description": getattr(rule, "description", ""),
                    "network": getattr(rule, "network", ""),
                    "priority": getattr(rule, "priority", 0),
                    "direction": (
                        rule.direction if hasattr(rule, "direction") else "unknown"
                    ),
                    "disabled": getattr(rule, "disabled", False),
                    "source_ranges": list(getattr(rule, "source_ranges", [])),
                    "destination_ranges": list(getattr(rule, "destination_ranges", [])),
                    "source_tags": list(getattr(rule, "source_tags", [])),
                    "target_tags": list(getattr(rule, "target_tags", [])),
                    "source_service_accounts": list(
                        getattr(rule, "source_service_accounts", [])
                    ),
                    "target_service_accounts": list(
                        getattr(rule, "target_service_accounts", [])
                    ),
                    "allowed": [],
                    "denied": [],
                }

                # Allowed rules
                if hasattr(rule, "allowed"):
                    for allow_rule in rule.allowed:
                        rule_analysis["allowed"].append(  # type: ignore[union-attr]
                            {
                                "ip_protocol": getattr(allow_rule, "ip_protocol", ""),
                                "ports": list(getattr(allow_rule, "ports", [])),
                            }
                        )

                # Denied rules
                if hasattr(rule, "denied"):
                    for deny_rule in rule.denied:
                        rule_analysis["denied"].append(  # type: ignore[union-attr]
                            {
                                "ip_protocol": getattr(deny_rule, "ip_protocol", ""),
                                "ports": list(getattr(deny_rule, "ports", [])),
                            }
                        )

                firewall_rules.append(rule_analysis)

        except Exception as e:
            self.add_error(f"Failed to analyze firewall rules in {region}", e)

        return firewall_rules

    def _get_cloud_sql_details(self, instance_name: str) -> Dict[str, Any]:
        """Get additional Cloud SQL instance details."""
        details = {}

        try:
            # Get monitoring metrics if available
            # monitoring_client = monitoring_v3.MetricServiceClient(
            #     credentials=self.credentials
            # )

            # This would require more complex metric querying
            # For now, just return empty details
            pass

        except Exception as e:
            self.add_warning(
                f"Failed to get metrics for Cloud SQL instance {instance_name}: {e}"
            )

        return details

    def _get_alloydb_instances(self, cluster_name: str) -> List[Dict[str, Any]]:
        """Get AlloyDB instances for a cluster."""
        instances = []

        try:
            alloydb_client = alloydb_v1.AlloyDBAdminClient(credentials=self.credentials)
            request = alloydb_v1.ListInstancesRequest(parent=cluster_name)

            for instance in alloydb_client.list_instances(request=request):
                instance_data = {
                    "name": instance.name.split("/")[-1],
                    "full_name": instance.name,
                    "display_name": getattr(instance, "display_name", ""),
                    "state": (
                        instance.state.name if hasattr(instance, "state") else "unknown"
                    ),
                    "instance_type": (
                        instance.instance_type.name
                        if hasattr(instance, "instance_type")
                        else "unknown"
                    ),
                    "machine_config": {},
                    "availability_type": (
                        instance.availability_type.name
                        if hasattr(instance, "availability_type")
                        else "unknown"
                    ),
                }

                # Machine configuration
                if hasattr(instance, "machine_config") and instance.machine_config:
                    machine_config = instance.machine_config
                    instance_data["machine_config"] = {
                        "cpu_count": getattr(machine_config, "cpu_count", 0)
                    }

                # Read pool configuration (node count matters for cost calculations)
                if hasattr(instance, "read_pool_config") and instance.read_pool_config:
                    instance_data["read_pool_config"] = {
                        "node_count": getattr(
                            instance.read_pool_config, "node_count", 0
                        )
                    }

                instances.append(instance_data)

        except Exception as e:
            self.add_warning(
                f"Failed to get AlloyDB instances for cluster {cluster_name}: {e}"
            )

        return instances

    def _get_alloydb_storage_usage(
        self, cluster_id: str, region: str
    ) -> Optional[Dict[str, Any]]:
        """Get AlloyDB cluster storage usage from Cloud Monitoring API.

        Queries the alloydb.googleapis.com/cluster/storage/usage metric
        for the given cluster. Returns storage usage in bytes and GB,
        or None if the metric is unavailable.
        """
        if not self.monitoring_client:
            return None

        try:
            now = datetime.now(timezone.utc)
            one_hour_ago = now - timedelta(hours=1)

            interval = monitoring_v3.TimeInterval(
                {
                    "end_time": {"seconds": int(now.timestamp())},
                    "start_time": {"seconds": int(one_hour_ago.timestamp())},
                }
            )

            request = monitoring_v3.ListTimeSeriesRequest(
                name=f"projects/{self.project_id}",
                filter=(
                    f'metric.type = "alloydb.googleapis.com/cluster/storage/usage"'
                    f' AND resource.labels.cluster_id = "{cluster_id}"'
                    f' AND resource.labels.location = "{region}"'
                ),
                interval=interval,
                view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            )

            results = self.monitoring_client.list_time_series(request=request)

            for time_series in results:
                # Take the most recent data point
                for point in time_series.points:
                    storage_bytes = point.value.int64_value
                    return {
                        "storage_usage_bytes": storage_bytes,
                        "storage_usage_gb": round(storage_bytes / (1024**3), 2),
                    }

            return None

        except Exception as e:
            self.add_warning(
                f"Failed to get storage usage for AlloyDB cluster {cluster_id}: {e}"
            )
            return None

    def _get_network_subnets(
        self, network_self_link: str, region: str
    ) -> List[Dict[str, Any]]:
        """Get subnets for a network in a specific region."""
        subnets = []

        try:
            compute_client = compute_v1.SubnetworksClient(credentials=self.credentials)
            request = compute_v1.ListSubnetworksRequest(
                project=self.project_id, region=region
            )

            for subnet in compute_client.list(request=request):
                # Check if this subnet belongs to the network
                if hasattr(subnet, "network") and subnet.network == network_self_link:
                    subnets.append(
                        {
                            "name": subnet.name,
                            "description": getattr(subnet, "description", ""),
                            "ip_cidr_range": getattr(subnet, "ip_cidr_range", ""),
                            "gateway_address": getattr(subnet, "gateway_address", ""),
                            "region": getattr(subnet, "region", region),
                            "private_ip_google_access": getattr(
                                subnet, "private_ip_google_access", False
                            ),
                            "private_ipv6_google_access": getattr(
                                subnet, "private_ipv6_google_access", "unknown"
                            ),
                            "purpose": (
                                subnet.purpose
                                if hasattr(subnet, "purpose")
                                else "unknown"
                            ),
                            "role": (
                                subnet.role if hasattr(subnet, "role") else "unknown"
                            ),
                        }
                    )

        except Exception as e:
            self.add_warning(
                f"Failed to get subnets for network in region {region}: {e}"
            )

        return subnets

    def _generate_gcp_summary(self, resources: Dict[str, Any]) -> Dict[str, Any]:
        """Generate GCP analysis summary."""
        summary = {
            "regions_analyzed": len(resources),
            "cloud_sql_instances": 0,
            "alloydb_clusters": 0,
            "vpc_networks": 0,
            "firewall_rules": 0,
            "high_availability_instances": 0,
            "encrypted_instances": 0,
            "private_ip_instances": 0,
            "backup_enabled_instances": 0,
        }

        for region_data in resources.values():
            summary["cloud_sql_instances"] += len(
                region_data.get("cloud_sql_instances", [])
            )
            summary["alloydb_clusters"] += len(region_data.get("alloydb_clusters", []))
            summary["vpc_networks"] += len(region_data.get("vpc_networks", []))
            summary["firewall_rules"] += len(region_data.get("firewall_rules", []))

            # Analyze Cloud SQL instances
            for instance in region_data.get("cloud_sql_instances", []):
                settings = instance.get("settings", {})

                # High availability
                if settings.get("availability_type") == "REGIONAL":
                    summary["high_availability_instances"] += 1

                # Encryption (implicitly encrypted in GCP)
                summary["encrypted_instances"] += 1

                # Private IP
                ip_config = settings.get("ip_configuration", {})
                if ip_config.get("private_network") and not ip_config.get(
                    "ipv4_enabled", True
                ):
                    summary["private_ip_instances"] += 1

                # Backup enabled
                backup_config = settings.get("backup_configuration", {})
                if backup_config.get("enabled"):
                    summary["backup_enabled_instances"] += 1

        return summary

    def _assess_complexity(self, resources: Dict[str, Any]) -> Dict[str, Any]:
        """Assess infrastructure complexity factors."""
        complexity = {
            "high_availability_deployments": 0,
            "read_replicas": 0,
            "cross_region_replicas": 0,
            "custom_vpc_configuration": False,
            "private_ip_only_instances": 0,
            "custom_firewall_rules": 0,
            "custom_database_flags": 0,
            "backup_enabled": False,
            "point_in_time_recovery_enabled": False,
        }

        for region_data in resources.values():
            # Analyze Cloud SQL instances
            for instance in region_data.get("cloud_sql_instances", []):
                settings = instance.get("settings", {})

                # High availability
                if settings.get("availability_type") == "REGIONAL":
                    complexity["high_availability_deployments"] += 1

                # Custom database flags
                db_flags = settings.get("database_flags", {})
                if db_flags:
                    complexity["custom_database_flags"] += len(db_flags)

                # Backup and PITR
                backup_config = settings.get("backup_configuration", {})
                if backup_config.get("enabled"):
                    complexity["backup_enabled"] = True
                if backup_config.get("point_in_time_recovery_enabled"):
                    complexity["point_in_time_recovery_enabled"] = True

                # Private IP configuration
                ip_config = settings.get("ip_configuration", {})
                if ip_config.get("private_network") and not ip_config.get(
                    "ipv4_enabled", True
                ):
                    complexity["private_ip_only_instances"] += 1

            # Analyze AlloyDB clusters
            alloydb_clusters = region_data.get("alloydb_clusters", [])
            for cluster in alloydb_clusters:
                instances = cluster.get("instances", [])
                for instance in instances:
                    # Count read pool nodes as read replicas
                    if instance.get("instance_type") == "READ_POOL":
                        node_count = instance.get("read_pool_config", {}).get(
                            "node_count", 1
                        )
                        complexity["read_replicas"] += node_count

            # Check for custom VPC configuration
            vpc_networks = region_data.get("vpc_networks", [])
            if vpc_networks:
                complexity["custom_vpc_configuration"] = True

            # Count custom firewall rules (non-default)
            firewall_rules = region_data.get("firewall_rules", [])
            for rule in firewall_rules:
                if not rule.get("name", "").startswith("default-"):
                    complexity["custom_firewall_rules"] += 1

        return complexity
