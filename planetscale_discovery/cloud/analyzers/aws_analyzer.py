"""
AWS Cloud Database Environment Analyzer

Analyzes AWS RDS, Aurora, and related infrastructure.
"""

from typing import Dict, Any, List
import logging
from datetime import datetime, timedelta

try:
    import boto3
    from botocore.exceptions import (
        ClientError,
        NoCredentialsError,
        PartialCredentialsError,
    )

    HAS_AWS_LIBS = True
except ImportError:
    HAS_AWS_LIBS = False

from ...common.base_analyzer import CloudAnalyzer
from ...common.utils import generate_timestamp


class AWSAnalyzer(CloudAnalyzer):
    """AWS cloud database environment analyzer."""

    def __init__(self, config: Any, logger: logging.Logger = None):
        """Initialize AWS analyzer."""
        # Handle config properly - preserve target_database if it exists
        if hasattr(config, "__dict__"):
            config_dict = config.__dict__.copy()
            # Also check for dynamically added attributes
            if hasattr(config, "target_database"):
                config_dict["target_database"] = config.target_database
        else:
            config_dict = config

        super().__init__(config_dict, logger)
        self.session = None
        # Extract regions from config dict or attribute
        if hasattr(config, "regions"):
            self.regions = config.regions if config.regions else ["us-east-1"]
        elif isinstance(config, dict) and "aws" in config:
            self.regions = config["aws"].get("regions", ["us-east-1"])
        else:
            self.regions = ["us-east-1"]

        # Extract target_database from config
        if hasattr(config, "target_database"):
            self.target_database = config.target_database
        elif isinstance(config, dict) and "aws" in config:
            self.target_database = config["aws"].get("target_database")
        else:
            self.target_database = None

    def authenticate(self) -> bool:
        """Authenticate with AWS."""
        if not HAS_AWS_LIBS:
            self.add_error(
                "AWS libraries not installed. Install with: pip install 'planetscale-discovery-tools[aws]'"
            )
            return False

        try:
            # Create session based on configuration
            if self.config.get("profile"):
                self.session = boto3.Session(profile_name=self.config["profile"])
            elif self.config.get("role_arn"):
                # Assume role authentication
                sts_client = boto3.client("sts")
                assumed_role = sts_client.assume_role(
                    RoleArn=self.config["role_arn"],
                    RoleSessionName="ps-discovery",
                    ExternalId=self.config.get("external_id"),
                )
                credentials = assumed_role["Credentials"]
                self.session = boto3.Session(
                    aws_access_key_id=credentials["AccessKeyId"],
                    aws_secret_access_key=credentials["SecretAccessKey"],
                    aws_session_token=credentials["SessionToken"],
                )
            elif self.config.get("access_key_id") and self.config.get(
                "secret_access_key"
            ):
                # Direct credentials
                self.session = boto3.Session(
                    aws_access_key_id=self.config["access_key_id"],
                    aws_secret_access_key=self.config["secret_access_key"],
                    aws_session_token=self.config.get("session_token"),
                )
            else:
                # Use default credential chain
                self.session = boto3.Session()

            # Test authentication
            sts = self.session.client("sts")
            identity = sts.get_caller_identity()
            self.logger.info(
                f"Authenticated as AWS user: {identity.get('Arn', 'Unknown')}"
            )

            return True

        except (NoCredentialsError, PartialCredentialsError) as e:
            self.add_error(
                "AWS authentication failed - no valid credentials found. "
                "Configure credentials via AWS CLI profile, environment variables "
                "(AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY), IAM role, or config file.\n"
                "  Quick start: aws configure --profile discovery\n"
                "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/aws.md",
                e,
            )
            return False
        except ClientError as e:
            self.add_error(
                "AWS authentication failed - insufficient permissions. "
                "The configured credentials could not call sts:GetCallerIdentity. "
                "Verify the IAM user/role has the required permissions.\n"
                "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/aws.md",
                e,
            )
            return False
        except Exception as e:
            self.add_error(
                "AWS authentication failed.\n"
                "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/aws.md",
                e,
            )
            return False

    def discover_resources(self) -> List[str]:
        """Discover AWS database resources."""
        resources = []

        for region in self.regions:
            try:
                # Discover RDS instances
                rds_client = self.session.client("rds", region_name=region)
                instances = rds_client.describe_db_instances()
                for instance in instances.get("DBInstances", []):
                    resources.append(
                        f"rds-instance:{region}:{instance['DBInstanceIdentifier']}"
                    )

                # Discover Aurora clusters
                clusters = rds_client.describe_db_clusters()
                for cluster in clusters.get("DBClusters", []):
                    resources.append(
                        f"aurora-cluster:{region}:{cluster['DBClusterIdentifier']}"
                    )

            except ClientError as e:
                self.add_warning(
                    f"Failed to discover resources in region {region}: {e}"
                )

        return resources

    def analyze(self) -> Dict[str, Any]:
        """Analyze AWS cloud database environment."""
        analysis_results = {
            "provider": "aws",
            "timestamp": generate_timestamp(),
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

        # Check if we have a target database for focused analysis
        target_database = (
            self.config.get("target_database")
            if isinstance(self.config, dict)
            else getattr(self.config, "target_database", None)
        )

        if target_database:
            self.logger.info(
                f"Starting focused analysis for database: {target_database}"
            )
            analysis_results["focused_analysis"] = True
            analysis_results["target_database"] = target_database

            # Find and analyze the specific database and its infrastructure
            target_analysis = self._analyze_target_database(target_database)
            analysis_results["target_database_analysis"] = target_analysis

            # Also populate the regular regional analysis but filtered
            for region in self.regions:
                try:
                    region_analysis = self._analyze_region_filtered(
                        region, target_database
                    )
                    if (
                        region_analysis
                    ):  # Only include regions where the target was found
                        analysis_results["resources"][region] = region_analysis
                except Exception as e:
                    self.add_error(f"Failed to analyze region {region}", e)
        else:
            # Regular comprehensive analysis
            analysis_results["focused_analysis"] = False
            for region in self.regions:
                self.logger.info(f"Analyzing AWS resources in region {region}")

                try:
                    region_analysis = self._analyze_region(region)
                    analysis_results["resources"][region] = region_analysis

                except Exception as e:
                    self.add_error(f"Failed to analyze region {region}", e)

        # Generate summary and complexity assessment
        analysis_results["summary"] = self._generate_aws_summary(
            analysis_results["resources"]
        )
        analysis_results["complexity_factors"] = self._assess_complexity(
            analysis_results["resources"]
        )

        return analysis_results

    def _analyze_region(self, region: str) -> Dict[str, Any]:
        """Analyze AWS resources in a specific region."""
        region_analysis = {
            "rds_instances": [],
            "aurora_clusters": [],
            "vpcs": [],
            "security_groups": [],
            "db_subnet_groups": [],
            "networking_summary": {},
            "security_summary": {},
        }

        # Analyze RDS instances
        region_analysis["rds_instances"] = self._analyze_rds_instances(region)

        # Analyze Aurora clusters
        region_analysis["aurora_clusters"] = self._analyze_aurora_clusters(region)

        # Analyze networking
        region_analysis["vpcs"] = self._analyze_vpcs(region)
        region_analysis["db_subnet_groups"] = self._analyze_db_subnet_groups(region)

        # Analyze security
        region_analysis["security_groups"] = self._analyze_security_groups(region)

        return region_analysis

    def _get_rds_instances(self, region: str) -> List[Dict[str, Any]]:
        """Legacy method name - calls _analyze_rds_instances"""
        return self._analyze_rds_instances(region)

    def _analyze_rds_instances(self, region: str) -> List[Dict[str, Any]]:
        """Analyze RDS instances in a region."""
        instances = []

        try:
            rds_client = self.session.client("rds", region_name=region)
            response = rds_client.describe_db_instances()

            for db_instance in response.get("DBInstances", []):
                instance_analysis = {
                    "db_instance_identifier": db_instance["DBInstanceIdentifier"],
                    "engine": db_instance["Engine"],
                    "engine_version": db_instance["EngineVersion"],
                    "db_instance_class": db_instance["DBInstanceClass"],
                    "allocated_storage": db_instance.get("AllocatedStorage", 0),
                    "storage_type": db_instance.get("StorageType", "unknown"),
                    "iops": db_instance.get("Iops"),
                    "multi_az": db_instance.get("MultiAZ", False),
                    "publicly_accessible": db_instance.get("PubliclyAccessible", False),
                    "vpc_id": db_instance.get("DBSubnetGroup", {}).get("VpcId"),
                    "db_subnet_group": (
                        {
                            "name": db_instance.get("DBSubnetGroup", {}).get(
                                "DBSubnetGroupName"
                            ),
                            "vpc_id": db_instance.get("DBSubnetGroup", {}).get("VpcId"),
                        }
                        if db_instance.get("DBSubnetGroup")
                        else None
                    ),
                    "vpc_security_groups": [
                        {
                            "vpc_security_group_id": sg.get(
                                "VpcSecurityGroupId", sg.get("GroupId", "unknown")
                            ),
                            "status": sg.get("Status", "unknown"),
                        }
                        for sg in db_instance.get("VpcSecurityGroups", [])
                    ],
                    "parameter_group": db_instance.get("DBParameterGroups", [{}])[
                        0
                    ].get("DBParameterGroupName"),
                    "option_group": db_instance.get("OptionGroupMemberships", [{}])[
                        0
                    ].get("OptionGroupName"),
                    "backup_retention_period": db_instance.get(
                        "BackupRetentionPeriod", 0
                    ),
                    "preferred_backup_window": db_instance.get("PreferredBackupWindow"),
                    "preferred_maintenance_window": db_instance.get(
                        "PreferredMaintenanceWindow"
                    ),
                    "deletion_protection": db_instance.get("DeletionProtection", False),
                    "storage_encrypted": db_instance.get("StorageEncrypted", False),
                    "kms_key_id": db_instance.get("KmsKeyId"),
                    "performance_insights_enabled": db_instance.get(
                        "PerformanceInsightsEnabled", False
                    ),
                    "monitoring_interval": db_instance.get("MonitoringInterval", 0),
                    "status": db_instance.get("DBInstanceStatus"),
                    "availability_zone": db_instance.get("AvailabilityZone"),
                    "region": region,
                }

                # Get additional details
                instance_analysis.update(
                    self._get_rds_instance_details(
                        rds_client, db_instance["DBInstanceIdentifier"]
                    )
                )

                instances.append(instance_analysis)

        except ClientError as e:
            self.add_error(f"Failed to analyze RDS instances in {region}", e)

        return instances

    def _get_aurora_clusters(self, region: str) -> List[Dict[str, Any]]:
        """Legacy method name - calls _analyze_aurora_clusters"""
        return self._analyze_aurora_clusters(region)

    def _analyze_aurora_clusters(self, region: str) -> List[Dict[str, Any]]:
        """Analyze Aurora clusters in a region."""
        clusters = []

        try:
            rds_client = self.session.client("rds", region_name=region)
            response = rds_client.describe_db_clusters()

            for db_cluster in response.get("DBClusters", []):
                cluster_analysis = {
                    "identifier": db_cluster["DBClusterIdentifier"],
                    "engine": db_cluster["Engine"],
                    "engine_version": db_cluster["EngineVersion"],
                    "engine_mode": db_cluster.get("EngineMode", "provisioned"),
                    "database_name": db_cluster.get("DatabaseName"),
                    "master_username": db_cluster.get("MasterUsername"),
                    "port": db_cluster.get("Port"),
                    "vpc_id": None,  # Aurora clusters store VPC ID differently, need to look up via subnet group
                    "db_subnet_group": db_cluster.get("DBSubnetGroup"),
                    "security_groups": [
                        sg.get("VpcSecurityGroupId", sg.get("GroupId", "unknown"))
                        for sg in db_cluster.get("VpcSecurityGroups", [])
                    ],
                    "cluster_parameter_group": db_cluster.get(
                        "DBClusterParameterGroup"
                    ),
                    "backup_retention_period": db_cluster.get(
                        "BackupRetentionPeriod", 0
                    ),
                    "preferred_backup_window": db_cluster.get("PreferredBackupWindow"),
                    "preferred_maintenance_window": db_cluster.get(
                        "PreferredMaintenanceWindow"
                    ),
                    "deletion_protection": db_cluster.get("DeletionProtection", False),
                    "storage_encrypted": db_cluster.get("StorageEncrypted", False),
                    "kms_key_id": db_cluster.get("KmsKeyId"),
                    "status": db_cluster.get("Status"),
                    "multi_az": db_cluster.get("MultiAZ", False),
                    # Storage information
                    "allocated_storage": db_cluster.get("AllocatedStorage"),
                    "storage_type": db_cluster.get("StorageType"),
                    "iops": db_cluster.get("Iops"),
                    "storage_throughput": db_cluster.get("StorageThroughput"),
                    "cluster_members": [],
                    "global_cluster_identifier": db_cluster.get(
                        "GlobalClusterIdentifier"
                    ),
                    "backtrack_window": db_cluster.get("BacktrackWindow", 0),
                    "region": region,
                }

                # Get cluster members
                for member in db_cluster.get("DBClusterMembers", []):
                    cluster_analysis["cluster_members"].append(
                        {
                            "instance_identifier": member["DBInstanceIdentifier"],
                            "is_cluster_writer": member.get("IsClusterWriter", False),
                            "promotion_tier": member.get("PromotionTier", 0),
                        }
                    )

                # Get additional details for Aurora Serverless (v1 and v2)
                # v1: engine_mode == "serverless"
                # v2: engine_mode == "provisioned" but has ServerlessV2ScalingConfiguration
                if cluster_analysis["engine_mode"] == "serverless" or db_cluster.get(
                    "ServerlessV2ScalingConfiguration"
                ):
                    cluster_analysis.update(
                        self._get_aurora_serverless_details(rds_client, db_cluster)
                    )

                clusters.append(cluster_analysis)

        except ClientError as e:
            self.add_error(f"Failed to analyze Aurora clusters in {region}", e)

        return clusters

    def _get_vpcs(self, region: str) -> List[Dict[str, Any]]:
        """Legacy method name - calls _analyze_vpcs"""
        return self._analyze_vpcs(region)

    def _analyze_vpcs(self, region: str) -> List[Dict[str, Any]]:
        """Analyze VPCs in a region."""
        vpcs = []

        try:
            ec2_client = self.session.client("ec2", region_name=region)
            response = ec2_client.describe_vpcs()

            for vpc in response.get("Vpcs", []):
                vpc_analysis = {
                    "vpc_id": vpc["VpcId"],
                    "cidr_block": vpc["CidrBlock"],
                    "additional_cidr_blocks": [
                        assoc["CidrBlock"]
                        for assoc in vpc.get("CidrBlockAssociationSet", [])
                        if assoc["CidrBlockState"]["State"] == "associated"
                    ],
                    "is_default": vpc.get("IsDefault", False),
                    "state": vpc.get("State"),
                    "dns_hostnames": vpc.get("EnableDnsHostnames", False),
                    "dns_resolution": vpc.get("EnableDnsSupport", False),
                    "tags": {tag["Key"]: tag["Value"] for tag in vpc.get("Tags", [])},
                    "subnets": [],
                    "internet_gateways": [],
                    "nat_gateways": [],
                    "vpc_endpoints": [],
                }

                # Get subnets
                vpc_analysis["subnets"] = self._get_vpc_subnets(
                    ec2_client, vpc["VpcId"]
                )

                # Get internet gateways
                vpc_analysis["internet_gateways"] = self._get_internet_gateways(
                    ec2_client, vpc["VpcId"]
                )

                # Get NAT gateways
                vpc_analysis["nat_gateways"] = self._get_nat_gateways(
                    ec2_client, vpc["VpcId"]
                )

                # Get VPC endpoints
                vpc_analysis["vpc_endpoints"] = self._get_vpc_endpoints(
                    ec2_client, vpc["VpcId"]
                )

                vpcs.append(vpc_analysis)

        except ClientError as e:
            self.add_error(f"Failed to analyze VPCs in {region}", e)

        return vpcs

    def _get_security_groups(self, region: str) -> List[Dict[str, Any]]:
        """Legacy method name - calls _analyze_security_groups"""
        return self._analyze_security_groups(region)

    def _analyze_security_groups(self, region: str) -> List[Dict[str, Any]]:
        """Analyze security groups in a region."""
        security_groups = []

        try:
            ec2_client = self.session.client("ec2", region_name=region)
            response = ec2_client.describe_security_groups()

            for sg in response.get("SecurityGroups", []):
                sg_analysis = {
                    "group_id": sg["GroupId"],
                    "group_name": sg["GroupName"],
                    "description": sg["Description"],
                    "vpc_id": sg.get("VpcId"),
                    "owner_id": sg["OwnerId"],
                    "inbound_rules": [],
                    "outbound_rules": [],
                    "tags": {tag["Key"]: tag["Value"] for tag in sg.get("Tags", [])},
                }

                # Analyze inbound rules
                for rule in sg.get("IpPermissions", []):
                    sg_analysis["inbound_rules"].append(
                        self._parse_security_group_rule(rule, "inbound")
                    )

                # Analyze outbound rules
                for rule in sg.get("IpPermissionsEgress", []):
                    sg_analysis["outbound_rules"].append(
                        self._parse_security_group_rule(rule, "outbound")
                    )

                security_groups.append(sg_analysis)

        except ClientError as e:
            self.add_error(f"Failed to analyze security groups in {region}", e)

        return security_groups

    def _analyze_db_subnet_groups(self, region: str) -> List[Dict[str, Any]]:
        """Analyze DB subnet groups in a region."""
        subnet_groups = []

        try:
            rds_client = self.session.client("rds", region_name=region)
            response = rds_client.describe_db_subnet_groups()

            for sg in response.get("DBSubnetGroups", []):
                sg_analysis = {
                    "name": sg["DBSubnetGroupName"],
                    "description": sg["DBSubnetGroupDescription"],
                    "vpc_id": sg["VpcId"],
                    "status": sg["SubnetGroupStatus"],
                    "subnets": [],
                }

                for subnet in sg.get("Subnets", []):
                    sg_analysis["subnets"].append(
                        {
                            "subnet_id": subnet["SubnetIdentifier"],
                            "availability_zone": subnet["SubnetAvailabilityZone"][
                                "Name"
                            ],
                            "status": subnet["SubnetStatus"],
                        }
                    )

                subnet_groups.append(sg_analysis)

        except ClientError as e:
            self.add_error(f"Failed to analyze DB subnet groups in {region}", e)

        return subnet_groups

    def _get_rds_instance_details(self, rds_client, instance_id: str) -> Dict[str, Any]:
        """Get additional RDS instance details."""
        details = {}

        try:
            # Get metrics from CloudWatch
            cloudwatch = self.session.client(
                "cloudwatch", region_name=rds_client.meta.region_name
            )

            # Get recent CPU utilization
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=7)

            metrics_response = cloudwatch.get_metric_statistics(
                Namespace="AWS/RDS",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": instance_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,  # Daily
                Statistics=["Average", "Maximum"],
            )

            if metrics_response["Datapoints"]:
                cpu_data = sorted(
                    metrics_response["Datapoints"], key=lambda x: x["Timestamp"]
                )
                details["cpu_utilization_avg"] = sum(
                    d["Average"] for d in cpu_data
                ) / len(cpu_data)
                details["cpu_utilization_max"] = max(d["Maximum"] for d in cpu_data)

        except Exception as e:
            self.add_warning(f"Failed to get metrics for instance {instance_id}: {e}")

        return details

    def _get_aurora_serverless_details(
        self, rds_client, cluster: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get Aurora Serverless specific details (v1 and v2)."""
        details = {}

        # Aurora Serverless v1 configuration
        if cluster.get("ScalingConfigurationInfo"):
            scaling = cluster["ScalingConfigurationInfo"]
            details["serverless_v1_config"] = {
                "min_capacity": scaling.get("MinCapacity"),
                "max_capacity": scaling.get("MaxCapacity"),
                "auto_pause": scaling.get("AutoPause"),
                "seconds_until_auto_pause": scaling.get("SecondsUntilAutoPause"),
                "timeout_action": scaling.get("TimeoutAction"),
                "seconds_before_timeout": scaling.get("SecondsBeforeTimeout"),
            }

        # Aurora Serverless v2 configuration
        if cluster.get("ServerlessV2ScalingConfiguration"):
            scaling = cluster["ServerlessV2ScalingConfiguration"]
            details["serverless_v2_config"] = {
                "min_capacity": scaling.get("MinCapacity"),
                "max_capacity": scaling.get("MaxCapacity"),
            }

        return details

    def _parse_security_group_rule(
        self, rule: Dict[str, Any], direction: str
    ) -> Dict[str, Any]:
        """Parse a security group rule."""
        parsed_rule = {
            "direction": direction,
            "protocol": rule.get("IpProtocol", "unknown"),
            "from_port": rule.get("FromPort"),
            "to_port": rule.get("ToPort"),
            "sources": [],
        }

        # Parse IP ranges
        for ip_range in rule.get("IpRanges", []):
            parsed_rule["sources"].append(
                {
                    "type": "ip_range",
                    "value": ip_range["CidrIp"],
                    "description": ip_range.get("Description", ""),
                }
            )

        # Parse security group references
        for sg_ref in rule.get("UserIdGroupPairs", []):
            parsed_rule["sources"].append(
                {
                    "type": "security_group",
                    "value": sg_ref["GroupId"],
                    "description": sg_ref.get("Description", ""),
                }
            )

        # Parse prefix lists
        for prefix_list in rule.get("PrefixListIds", []):
            parsed_rule["sources"].append(
                {
                    "type": "prefix_list",
                    "value": prefix_list["PrefixListId"],
                    "description": prefix_list.get("Description", ""),
                }
            )

        return parsed_rule

    def _get_vpc_subnets(self, ec2_client, vpc_id: str) -> List[Dict[str, Any]]:
        """Get subnets for a VPC with detailed networking information."""
        subnets = []

        try:
            response = ec2_client.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )

            for subnet in response.get("Subnets", []):
                subnet_info = {
                    "subnet_id": subnet["SubnetId"],
                    "cidr_block": subnet["CidrBlock"],
                    "availability_zone": subnet["AvailabilityZone"],
                    "available_ip_count": subnet["AvailableIpAddressCount"],
                    "map_public_ip_on_launch": subnet.get("MapPublicIpOnLaunch", False),
                    "state": subnet.get("State"),
                    "tags": {
                        tag["Key"]: tag["Value"] for tag in subnet.get("Tags", [])
                    },
                    "route_tables": [],
                    "network_acls": [],
                }

                # Get route tables for this subnet
                try:
                    subnet_info["route_tables"] = self._get_subnet_route_tables(
                        ec2_client, subnet["SubnetId"]
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Could not get route tables for subnet {subnet['SubnetId']}: {e}"
                    )

                # Get network ACLs for this subnet
                try:
                    subnet_info["network_acls"] = self._get_subnet_network_acls(
                        ec2_client, subnet["SubnetId"]
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Could not get NACLs for subnet {subnet['SubnetId']}: {e}"
                    )

                subnets.append(subnet_info)

        except ClientError as e:
            self.add_warning(f"Failed to describe subnets for VPC: {e}")

        return subnets

    def _get_internet_gateways(self, ec2_client, vpc_id: str) -> List[Dict[str, Any]]:
        """Get internet gateways for a VPC."""
        igws = []

        try:
            response = ec2_client.describe_internet_gateways(
                Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
            )

            for igw in response.get("InternetGateways", []):
                igws.append(
                    {
                        "gateway_id": igw["InternetGatewayId"],
                        "state": igw.get("Attachments", [{}])[0].get(
                            "State", "unknown"
                        ),
                        "tags": {
                            tag["Key"]: tag["Value"] for tag in igw.get("Tags", [])
                        },
                    }
                )

        except ClientError as e:
            self.add_warning(
                f"Failed to describe internet gateways for VPC {vpc_id}: {e}"
            )

        return igws

    def _get_nat_gateways(self, ec2_client, vpc_id: str) -> List[Dict[str, Any]]:
        """Get NAT gateways for a VPC."""
        nat_gws = []

        try:
            response = ec2_client.describe_nat_gateways(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )

            for nat_gw in response.get("NatGateways", []):
                nat_gws.append(
                    {
                        "gateway_id": nat_gw["NatGatewayId"],
                        "subnet_id": nat_gw["SubnetId"],
                        "state": nat_gw.get("State"),
                        "type": nat_gw.get("NatGatewayType", "Gateway"),
                        "tags": {
                            tag["Key"]: tag["Value"] for tag in nat_gw.get("Tags", [])
                        },
                    }
                )

        except ClientError as e:
            self.add_warning(f"Failed to describe NAT gateways for VPC {vpc_id}: {e}")

        return nat_gws

    def _get_vpc_endpoints(self, ec2_client, vpc_id: str) -> List[Dict[str, Any]]:
        """Get VPC endpoints for a VPC."""
        endpoints = []

        try:
            response = ec2_client.describe_vpc_endpoints(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )

            for endpoint in response.get("VpcEndpoints", []):
                endpoints.append(
                    {
                        "endpoint_id": endpoint["VpcEndpointId"],
                        "service_name": endpoint["ServiceName"],
                        "endpoint_type": endpoint.get("VpcEndpointType"),
                        "state": endpoint.get("State"),
                        "route_table_ids": endpoint.get("RouteTableIds", []),
                        "subnet_ids": endpoint.get("SubnetIds", []),
                    }
                )

        except ClientError as e:
            self.add_warning(f"Failed to describe VPC endpoints for VPC {vpc_id}: {e}")

        return endpoints

    def _generate_aws_summary(self, resources: Dict[str, Any]) -> Dict[str, Any]:
        """Generate AWS analysis summary."""
        summary = {
            "regions_analyzed": len(resources),
            "database_instances": 0,
            "database_clusters": 0,
            "vpcs": 0,
            "security_groups": 0,
            "multi_az_deployments": 0,
            "read_replicas": 0,
            "encrypted_databases": 0,
            "performance_insights_enabled": 0,
        }

        for region_data in resources.values():
            summary["database_instances"] += len(region_data.get("rds_instances", []))
            summary["database_clusters"] += len(region_data.get("aurora_clusters", []))
            summary["vpcs"] += len(region_data.get("vpcs", []))
            summary["security_groups"] += len(region_data.get("security_groups", []))

            # Analyze RDS instances
            for instance in region_data.get("rds_instances", []):
                if instance.get("multi_az"):
                    summary["multi_az_deployments"] += 1
                if instance.get("storage_encrypted"):
                    summary["encrypted_databases"] += 1
                if instance.get("performance_insights_enabled"):
                    summary["performance_insights_enabled"] += 1

            # Analyze Aurora clusters
            for cluster in region_data.get("aurora_clusters", []):
                if cluster.get("storage_encrypted"):
                    summary["encrypted_databases"] += 1

                # Count read replicas (non-writer cluster members)
                for member in cluster.get("cluster_members", []):
                    if not member.get("is_cluster_writer", False):
                        summary["read_replicas"] += 1

        return summary

    def _assess_complexity(self, resources: Dict[str, Any]) -> Dict[str, Any]:
        """Assess infrastructure complexity factors."""
        complexity = {
            "multi_az_deployments": 0,
            "read_replicas": 0,
            "cross_region_replicas": 0,
            "custom_vpc_configuration": False,
            "vpc_peering_connections": 0,
            "private_subnets_only": False,
            "custom_security_groups": 0,
            "custom_parameter_groups": 0,
            "performance_insights_enabled": False,
            "enhanced_monitoring": False,
        }

        total_instances = 0
        total_clusters = 0

        for region_data in resources.values():
            # Count instances and clusters
            instances = region_data.get("rds_instances", [])
            clusters = region_data.get("aurora_clusters", [])
            total_instances += len(instances)
            total_clusters += len(clusters)

            # Analyze complexity factors
            for instance in instances:
                if instance.get("multi_az"):
                    complexity["multi_az_deployments"] += 1
                if instance.get("performance_insights_enabled"):
                    complexity["performance_insights_enabled"] = True
                if instance.get("monitoring_interval", 0) > 0:
                    complexity["enhanced_monitoring"] = True

            for cluster in clusters:
                # Count read replicas
                for member in cluster.get("cluster_members", []):
                    if not member.get("is_cluster_writer", False):
                        complexity["read_replicas"] += 1

                # Check for global clusters (cross-region)
                if cluster.get("global_cluster_identifier"):
                    complexity["cross_region_replicas"] += 1

            # Analyze VPC configuration
            vpcs = region_data.get("vpcs", [])
            if vpcs:
                complexity["custom_vpc_configuration"] = True

            # Count custom security groups (non-default)
            security_groups = region_data.get("security_groups", [])
            for sg in security_groups:
                if sg.get("group_name") != "default":
                    complexity["custom_security_groups"] += 1

        return complexity

    def _analyze_target_database(self, target_identifier: str) -> Dict[str, Any]:
        """Analyze a specific database and build its infrastructure story."""
        target_analysis = {
            "identifier": target_identifier,
            "found": False,
            "type": None,  # 'rds' or 'aurora-cluster'
            "region": None,
            "database_details": {},
            "infrastructure_story": {
                "vpc": {},
                "subnets": [],
                "security_groups": [],
                "networking": {},
                "dependencies": [],
                "recommendations": [],
            },
        }

        # Search across all regions for the target database
        for region in self.regions:
            try:
                rds_client = self.session.client("rds", region_name=region)

                # Check RDS instances first
                try:
                    response = rds_client.describe_db_instances(
                        DBInstanceIdentifier=target_identifier
                    )
                    if response["DBInstances"]:
                        instance = response["DBInstances"][0]
                        target_analysis.update(
                            {
                                "found": True,
                                "type": "rds",
                                "region": region,
                                "database_details": self._extract_database_details(
                                    instance, "rds"
                                ),
                            }
                        )

                        # Build infrastructure story for RDS instance
                        target_analysis["infrastructure_story"] = (
                            self._build_infrastructure_story(instance, "rds", region)
                        )
                        break

                except ClientError as e:
                    if e.response["Error"]["Code"] != "DBInstanceNotFound":
                        self.logger.debug(f"Error checking RDS in {region}: {e}")

                # Check Aurora clusters if not found as RDS
                if not target_analysis["found"]:
                    try:
                        response = rds_client.describe_db_clusters(
                            DBClusterIdentifier=target_identifier
                        )
                        if response["DBClusters"]:
                            cluster = response["DBClusters"][0]
                            target_analysis.update(
                                {
                                    "found": True,
                                    "type": "aurora-cluster",
                                    "region": region,
                                    "database_details": self._extract_database_details(
                                        cluster, "aurora"
                                    ),
                                }
                            )

                            # Build infrastructure story for Aurora cluster
                            target_analysis["infrastructure_story"] = (
                                self._build_infrastructure_story(
                                    cluster, "aurora", region
                                )
                            )
                            break

                    except ClientError as e:
                        if e.response["Error"]["Code"] != "DBClusterNotFound":
                            self.logger.debug(f"Error checking Aurora in {region}: {e}")

            except Exception as e:
                self.logger.error(
                    f"Error searching for {target_identifier} in {region}: {e}"
                )

        return target_analysis

    def _extract_database_details(
        self, db_resource: Dict, db_type: str
    ) -> Dict[str, Any]:
        """Extract comprehensive operational details from a database resource."""
        if db_type == "rds":
            return {
                "identifier": db_resource["DBInstanceIdentifier"],
                "engine": db_resource["Engine"],
                "engine_version": db_resource["EngineVersion"],
                "instance_class": db_resource["DBInstanceClass"],
                "allocated_storage": db_resource.get("AllocatedStorage", 0),
                "storage_type": db_resource.get("StorageType", "unknown"),
                "iops": db_resource.get("Iops"),
                "storage_throughput": db_resource.get("StorageThroughput"),
                "multi_az": db_resource.get("MultiAZ", False),
                "publicly_accessible": db_resource.get("PubliclyAccessible", False),
                "endpoint": db_resource.get("Endpoint", {}).get("Address"),
                "port": db_resource.get("Endpoint", {}).get("Port"),
                "status": db_resource["DBInstanceStatus"],
                "availability_zone": db_resource.get("AvailabilityZone"),
                "vpc_security_groups": db_resource.get("VpcSecurityGroups", []),
                "db_subnet_group_name": db_resource.get("DBSubnetGroup", {}).get(
                    "DBSubnetGroupName"
                ),
                "parameter_groups": [
                    pg.get("DBParameterGroupName")
                    for pg in db_resource.get("DBParameterGroups", [])
                ],
                "option_groups": [
                    og.get("OptionGroupName")
                    for og in db_resource.get("OptionGroupMemberships", [])
                ],
                # Backup configuration
                "backup_retention_period": db_resource.get("BackupRetentionPeriod", 0),
                "preferred_backup_window": db_resource.get("PreferredBackupWindow"),
                "backup_target": db_resource.get("BackupTarget", "region"),
                "automated_backup_arn": db_resource.get(
                    "AutomatedBackupReplicationDestinationDBInstanceArn"
                ),
                # Maintenance configuration
                "preferred_maintenance_window": db_resource.get(
                    "PreferredMaintenanceWindow"
                ),
                "auto_minor_version_upgrade": db_resource.get(
                    "AutoMinorVersionUpgrade", False
                ),
                # Security and encryption
                "storage_encrypted": db_resource.get("StorageEncrypted", False),
                "kms_key_id": db_resource.get("KmsKeyId"),
                "deletion_protection": db_resource.get("DeletionProtection", False),
                # Monitoring and performance
                "performance_insights_enabled": db_resource.get(
                    "PerformanceInsightsEnabled", False
                ),
                "performance_insights_kms_key_id": db_resource.get(
                    "PerformanceInsightsKMSKeyId"
                ),
                "performance_insights_retention_period": db_resource.get(
                    "PerformanceInsightsRetentionPeriod"
                ),
                "monitoring_interval": db_resource.get("MonitoringInterval", 0),
                "monitoring_role_arn": db_resource.get("MonitoringRoleArn"),
                "enhanced_monitoring_resource_arn": db_resource.get(
                    "EnhancedMonitoringResourceArn"
                ),
                # Network and connectivity
                "db_name": db_resource.get("DBName"),
                "master_username": db_resource.get("MasterUsername"),
                "ca_certificate_identifier": db_resource.get("CACertificateIdentifier"),
                # Licensing and edition
                "license_model": db_resource.get("LicenseModel"),
                "character_set_name": db_resource.get("CharacterSetName"),
                "nchar_character_set_name": db_resource.get("NcharCharacterSetName"),
            }
        else:  # aurora
            return {
                "identifier": db_resource["DBClusterIdentifier"],
                "engine": db_resource["Engine"],
                "engine_version": db_resource["EngineVersion"],
                "engine_mode": db_resource.get("EngineMode", "provisioned"),
                "database_name": db_resource.get("DatabaseName"),
                "port": db_resource.get("Port"),
                "status": db_resource["Status"],
                "multi_az": db_resource.get("MultiAZ", False),
                "endpoint": db_resource.get("Endpoint"),
                "reader_endpoint": db_resource.get("ReaderEndpoint"),
                "custom_endpoints": [
                    ep.get("Endpoint") for ep in db_resource.get("CustomEndpoints", [])
                ],
                "cluster_members": db_resource.get("DBClusterMembers", []),
                "vpc_security_groups": db_resource.get("VpcSecurityGroups", []),
                "db_subnet_group_name": db_resource.get("DBSubnetGroup"),
                "cluster_parameter_group": db_resource.get("DBClusterParameterGroup"),
                # Backup configuration
                "backup_retention_period": db_resource.get("BackupRetentionPeriod", 0),
                "preferred_backup_window": db_resource.get("PreferredBackupWindow"),
                "backup_target": db_resource.get("BackupTarget", "region"),
                # Maintenance configuration
                "preferred_maintenance_window": db_resource.get(
                    "PreferredMaintenanceWindow"
                ),
                "auto_minor_version_upgrade": db_resource.get(
                    "AutoMinorVersionUpgrade", False
                ),
                # Security and encryption
                "storage_encrypted": db_resource.get("StorageEncrypted", False),
                "kms_key_id": db_resource.get("KmsKeyId"),
                "deletion_protection": db_resource.get("DeletionProtection", False),
                # Serverless configuration (if applicable)
                "serverless_v2_scaling_configuration": db_resource.get(
                    "ServerlessV2ScalingConfiguration"
                ),
                "capacity": db_resource.get("Capacity"),
                # Global cluster information
                "global_cluster_identifier": db_resource.get("GlobalClusterIdentifier"),
                # Multi-region setup
                "availability_zones": db_resource.get("AvailabilityZones", []),
                "cross_backup_supported": db_resource.get(
                    "CrossBackupSupported", False
                ),
                # Network and connectivity
                "master_username": db_resource.get("MasterUsername"),
                "iam_database_authentication_enabled": db_resource.get(
                    "IAMDatabaseAuthenticationEnabled", False
                ),
            }

    def _build_infrastructure_story(
        self, db_resource: Dict, db_type: str, region: str
    ) -> Dict[str, Any]:
        """Build the complete infrastructure story around a database."""
        story = {
            "vpc": {},
            "subnets": [],
            "security_groups": [],
            "networking": {},
            "dependencies": [],
            "recommendations": [],
        }

        try:
            ec2_client = self.session.client("ec2", region_name=region)
            rds_client = self.session.client("rds", region_name=region)

            # Get DB Subnet Group details
            subnet_group_name = None
            if db_type == "rds":
                subnet_group_name = db_resource.get("DBSubnetGroup", {}).get(
                    "DBSubnetGroupName"
                )
            else:  # aurora
                subnet_group_name = db_resource.get("DBSubnetGroup")

            if subnet_group_name:
                # Get subnet group details
                subnet_groups = rds_client.describe_db_subnet_groups(
                    DBSubnetGroupName=subnet_group_name
                )
                if subnet_groups["DBSubnetGroups"]:
                    subnet_group = subnet_groups["DBSubnetGroups"][0]
                    vpc_id = subnet_group["VpcId"]

                    # Analyze VPC
                    story["vpc"] = self._analyze_target_vpc(vpc_id, ec2_client)

                    # Analyze subnets
                    story["subnets"] = self._analyze_target_subnets(
                        [
                            subnet["SubnetIdentifier"]
                            for subnet in subnet_group["Subnets"]
                        ],
                        ec2_client,
                    )

            # Analyze security groups
            vpc_security_groups = db_resource.get("VpcSecurityGroups", [])
            if vpc_security_groups:
                sg_ids = [
                    sg.get("VpcSecurityGroupId", sg.get("GroupId", ""))
                    for sg in vpc_security_groups
                ]
                story["security_groups"] = self._analyze_target_security_groups(
                    sg_ids, ec2_client
                )

            # Build networking analysis
            story["networking"] = self._analyze_target_networking(story)

            # Generate dependencies and recommendations
            story["dependencies"] = self._identify_dependencies(
                db_resource, db_type, story
            )
            story["recommendations"] = []

        except Exception as e:
            self.logger.error(f"Error building infrastructure story: {e}")

        return story

    def _analyze_target_vpc(self, vpc_id: str, ec2_client) -> Dict[str, Any]:
        """Analyze the VPC containing the target database."""
        try:
            vpc_response = ec2_client.describe_vpcs(VpcIds=[vpc_id])
            if vpc_response["Vpcs"]:
                vpc = vpc_response["Vpcs"][0]

                # Get additional VPC details
                igw_response = ec2_client.describe_internet_gateways(
                    Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
                )
                nat_response = ec2_client.describe_nat_gateways(
                    Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
                )

                return {
                    "vpc_id": vpc_id,
                    "cidr_block": vpc.get("CidrBlock"),
                    "is_default": vpc.get("IsDefault", False),
                    "state": vpc.get("State"),
                    "tags": {tag["Key"]: tag["Value"] for tag in vpc.get("Tags", [])},
                    "internet_gateways": len(igw_response["InternetGateways"]),
                    "nat_gateways": len(nat_response["NatGateways"]),
                    "has_internet_access": len(igw_response["InternetGateways"]) > 0,
                }
        except Exception as e:
            self.logger.error(f"Error analyzing VPC {vpc_id}: {e}")

        return {"vpc_id": vpc_id, "error": "Could not analyze VPC"}

    def _analyze_target_subnets(
        self, subnet_ids: List[str], ec2_client
    ) -> List[Dict[str, Any]]:
        """Analyze subnets used by the target database with complete networking details."""
        subnets = []
        try:
            response = ec2_client.describe_subnets(SubnetIds=subnet_ids)
            for subnet in response["Subnets"]:
                # Get route tables for this subnet
                route_tables = ec2_client.describe_route_tables(
                    Filters=[
                        {
                            "Name": "association.subnet-id",
                            "Values": [subnet["SubnetId"]],
                        }
                    ]
                )

                # Get Network ACLs for this subnet
                network_acls = ec2_client.describe_network_acls(
                    Filters=[
                        {
                            "Name": "association.subnet-id",
                            "Values": [subnet["SubnetId"]],
                        }
                    ]
                )

                # Process route tables
                route_table_details = []
                is_public = False
                for rt in route_tables["RouteTables"]:
                    routes = []
                    for route in rt.get("Routes", []):
                        if route.get("GatewayId", "").startswith("igw-"):
                            is_public = True

                        route_info = {
                            "destination": route.get(
                                "DestinationCidrBlock",
                                route.get("DestinationIpv6CidrBlock", "Unknown"),
                            ),
                            "target": route.get("GatewayId")
                            or route.get("InstanceId")
                            or route.get("NetworkInterfaceId")
                            or route.get("VpcPeeringConnectionId")
                            or route.get("NatGatewayId")
                            or "local",
                            "state": route.get("State", "unknown"),
                        }
                        routes.append(route_info)

                    route_table_details.append(
                        {
                            "route_table_id": rt["RouteTableId"],
                            "routes": routes,
                            "tags": {
                                tag["Key"]: tag["Value"] for tag in rt.get("Tags", [])
                            },
                        }
                    )

                # Process Network ACLs
                nacl_details = []
                for nacl in network_acls["NetworkAcls"]:
                    entries = []
                    for entry in nacl.get("Entries", []):
                        port_range = "All"
                        if entry.get("PortRange"):
                            port_from = entry["PortRange"].get("From", "")
                            port_to = entry["PortRange"].get("To", "")
                            if port_from == port_to:
                                port_range = str(port_from)
                            else:
                                port_range = f"{port_from}-{port_to}"

                        entries.append(
                            {
                                "rule_number": entry.get("RuleNumber"),
                                "protocol": str(entry.get("Protocol", "All")),
                                "rule_action": entry.get("RuleAction", "unknown"),
                                "cidr_block": entry.get(
                                    "CidrBlock", entry.get("Ipv6CidrBlock", "Unknown")
                                ),
                                "port_range": port_range,
                                "egress": entry.get("Egress", False),
                            }
                        )

                    nacl_details.append(
                        {
                            "network_acl_id": nacl["NetworkAclId"],
                            "entries": entries,
                            "is_default": nacl.get("IsDefault", False),
                            "tags": {
                                tag["Key"]: tag["Value"] for tag in nacl.get("Tags", [])
                            },
                        }
                    )

                subnets.append(
                    {
                        "subnet_id": subnet["SubnetId"],
                        "availability_zone": subnet["AvailabilityZone"],
                        "cidr_block": subnet["CidrBlock"],
                        "is_public": is_public,
                        "available_ip_count": subnet.get("AvailableIpAddressCount", 0),
                        "route_tables": route_table_details,
                        "network_acls": nacl_details,
                        "tags": {
                            tag["Key"]: tag["Value"] for tag in subnet.get("Tags", [])
                        },
                    }
                )
        except Exception as e:
            self.logger.error(f"Error analyzing subnets with networking details: {e}")

        return subnets

    def _analyze_target_security_groups(
        self, sg_ids: List[str], ec2_client
    ) -> List[Dict[str, Any]]:
        """Analyze security groups protecting the target database."""
        security_groups = []
        try:
            response = ec2_client.describe_security_groups(
                GroupIds=[sg for sg in sg_ids if sg]
            )
            for sg in response["SecurityGroups"]:
                # Analyze ingress rules
                ingress_summary = []
                for rule in sg.get("IpPermissions", []):
                    port_range = (
                        f"{rule.get('FromPort', 'Any')}-{rule.get('ToPort', 'Any')}"
                    )
                    if rule.get("FromPort") == rule.get("ToPort"):
                        port_range = str(rule.get("FromPort", "Any"))

                    protocol = rule.get("IpProtocol", "Unknown")
                    sources = []

                    for ip_range in rule.get("IpRanges", []):
                        sources.append(ip_range.get("CidrIp", "Unknown"))
                    for sg_ref in rule.get("UserIdGroupPairs", []):
                        sources.append(f"sg-{sg_ref.get('GroupId', 'Unknown')}")

                    ingress_summary.append(
                        {
                            "protocol": protocol,
                            "port_range": port_range,
                            "sources": sources,
                        }
                    )

                security_groups.append(
                    {
                        "group_id": sg["GroupId"],
                        "group_name": sg.get("GroupName", "Unknown"),
                        "description": sg.get("Description", ""),
                        "ingress_rules": ingress_summary,
                        "egress_rules_count": len(sg.get("IpPermissionsEgress", [])),
                        "tags": {
                            tag["Key"]: tag["Value"] for tag in sg.get("Tags", [])
                        },
                    }
                )
        except Exception as e:
            self.logger.error(f"Error analyzing security groups: {e}")

        return security_groups

    def _analyze_target_networking(self, story: Dict) -> Dict[str, Any]:
        """Analyze networking configuration and access patterns."""
        networking = {
            "access_pattern": "unknown",
            "connectivity": [],
            "security_summary": {},
            "considerations": [],
        }

        # Determine access pattern
        vpc = story.get("vpc", {})
        subnets = story.get("subnets", [])

        has_public_subnets = any(subnet.get("is_public", False) for subnet in subnets)
        has_private_subnets = any(
            not subnet.get("is_public", True) for subnet in subnets
        )
        has_internet_gateway = vpc.get("has_internet_access", False)

        if has_public_subnets and has_internet_gateway:
            networking["access_pattern"] = "public_with_internet"
            networking["connectivity"].append("Direct internet access possible")
        elif has_private_subnets and vpc.get("nat_gateways", 0) > 0:
            networking["access_pattern"] = "private_with_nat"
            networking["connectivity"].append("Outbound internet via NAT Gateway")
        elif has_private_subnets:
            networking["access_pattern"] = "private_isolated"
            networking["connectivity"].append("No direct internet access")

        # Security summary
        security_groups = story.get("security_groups", [])
        networking["security_summary"] = {
            "total_security_groups": len(security_groups),
            "total_ingress_rules": sum(
                len(sg.get("ingress_rules", [])) for sg in security_groups
            ),
            "allows_public_access": any(
                any(
                    "0.0.0.0/0" in rule.get("sources", [])
                    for rule in sg.get("ingress_rules", [])
                )
                for sg in security_groups
            ),
        }

        return networking

    def _identify_dependencies(
        self, db_resource: Dict, db_type: str, story: Dict
    ) -> List[str]:
        """Identify key dependencies for the target database."""
        dependencies = []

        # VPC dependency
        vpc = story.get("vpc", {})
        if not vpc.get("is_default", True):
            dependencies.append(f"Custom VPC: {vpc.get('vpc_id', 'Unknown')}")

        # Subnet dependencies
        subnets = story.get("subnets", [])
        if len(subnets) > 1:
            dependencies.append(
                f"Multi-AZ subnet configuration ({len(subnets)} subnets)"
            )

        # Security group dependencies
        security_groups = story.get("security_groups", [])
        custom_sgs = [sg for sg in security_groups if sg.get("group_name") != "default"]
        if custom_sgs:
            dependencies.append(f"Custom security groups ({len(custom_sgs)} groups)")

        # Parameter group dependencies
        if db_type == "rds":
            param_groups = db_resource.get("DBParameterGroups", [])
            custom_params = [
                pg
                for pg in param_groups
                if not pg.get("DBParameterGroupName", "").startswith("default.")
            ]
            if custom_params:
                dependencies.append(
                    f"Custom parameter groups ({len(custom_params)} groups)"
                )

        return dependencies

    def _analyze_region_filtered(
        self, region: str, target_identifier: str
    ) -> Dict[str, Any]:
        """Analyze a region but only include resources related to the target database."""
        try:
            rds_client = self.session.client("rds", region_name=region)
            ec2_client = self.session.client("ec2", region_name=region)

            # Try to find the target database first
            target_db = None
            target_subnet_group = None

            # Check if it's an RDS instance
            try:
                response = rds_client.describe_db_instances(
                    DBInstanceIdentifier=target_identifier
                )
                target_db = response["DBInstances"][0]
                target_subnet_group = target_db.get("DBSubnetGroup", {}).get(
                    "DBSubnetGroupName"
                )
            except ClientError as e:
                self.add_warning(
                    f"Failed to describe RDS instance '{target_identifier}': {e}"
                )
                # Check if it's an Aurora cluster
                try:
                    response = rds_client.describe_db_clusters(
                        DBClusterIdentifier=target_identifier
                    )
                    target_db = response["DBClusters"][0]
                    target_subnet_group = target_db.get("DBSubnetGroup")
                except ClientError as e:
                    self.add_warning(
                        f"Failed to describe Aurora cluster '{target_identifier}': {e}"
                    )
                    return None

            if not target_db or not target_subnet_group:
                return None

            # Get the DB subnet group details to find subnets
            subnet_group_response = rds_client.describe_db_subnet_groups(
                DBSubnetGroupName=target_subnet_group
            )
            subnet_group = subnet_group_response["DBSubnetGroups"][0]
            target_subnets = [
                subnet["SubnetIdentifier"] for subnet in subnet_group["Subnets"]
            ]

            # Get VPC ID from the first subnet (all subnets in a DB subnet group are in same VPC)
            if target_subnets:
                subnet_response = ec2_client.describe_subnets(
                    SubnetIds=[target_subnets[0]]
                )
                target_vpc_id = subnet_response["Subnets"][0]["VpcId"]
            else:
                return None

            # Now build focused analysis with only related resources
            region_analysis = {
                "rds_instances": [],
                "aurora_clusters": [],
                "vpcs": [],
                "security_groups": [],
                "db_subnet_groups": [],
                "networking_summary": {},
                "security_summary": {},
            }

            # Add only the target database
            if "DBInstanceIdentifier" in target_db:
                region_analysis["rds_instances"] = [
                    self._analyze_single_rds_instance(target_db, region)
                ]
            else:
                region_analysis["aurora_clusters"] = [
                    self._analyze_single_aurora_cluster(target_db, region)
                ]

            # Add only the related DB subnet group
            region_analysis["db_subnet_groups"] = [
                self._analyze_single_db_subnet_group(subnet_group)
            ]

            # Add only the target VPC and its related networking
            region_analysis["vpcs"] = [
                self._analyze_single_vpc(ec2_client, target_vpc_id)
            ]

            # Add only security groups related to the target database
            if "VpcSecurityGroups" in target_db:
                sg_ids = [
                    sg["VpcSecurityGroupId"] for sg in target_db["VpcSecurityGroups"]
                ]
                region_analysis["security_groups"] = (
                    self._analyze_specific_security_groups(ec2_client, sg_ids)
                )

            return region_analysis

        except Exception as e:
            self.logger.error(f"Error in filtered region analysis for {region}: {e}")
            return None

    def _analyze_single_vpc(self, ec2_client, vpc_id: str) -> Dict[str, Any]:
        """Analyze a single specific VPC and its related networking components."""
        try:
            response = ec2_client.describe_vpcs(VpcIds=[vpc_id])
            vpc = response["Vpcs"][0]

            vpc_analysis = {
                "vpc_id": vpc["VpcId"],
                "cidr_block": vpc["CidrBlock"],
                "additional_cidr_blocks": [
                    assoc["CidrBlock"]
                    for assoc in vpc.get("CidrBlockAssociationSet", [])
                    if assoc["CidrBlockState"]["State"] == "associated"
                ],
                "is_default": vpc.get("IsDefault", False),
                "state": vpc.get("State"),
                "dns_hostnames": vpc.get("EnableDnsHostnames", False),
                "dns_resolution": vpc.get("EnableDnsSupport", False),
                "tags": {tag["Key"]: tag["Value"] for tag in vpc.get("Tags", [])},
                "subnets": [],
                "internet_gateways": [],
                "nat_gateways": [],
                "vpc_endpoints": [],
            }

            # Get only subnets in this VPC
            vpc_analysis["subnets"] = self._get_vpc_subnets(ec2_client, vpc_id)

            # Get only internet gateways for this VPC
            vpc_analysis["internet_gateways"] = self._get_internet_gateways(
                ec2_client, vpc_id
            )

            # Get only NAT gateways for this VPC
            vpc_analysis["nat_gateways"] = self._get_nat_gateways(ec2_client, vpc_id)

            # Get only VPC endpoints for this VPC
            vpc_analysis["vpc_endpoints"] = self._get_vpc_endpoints(ec2_client, vpc_id)

            return vpc_analysis

        except Exception as e:
            self.logger.error(f"Error analyzing VPC {vpc_id}: {e}")
            return {}

    def _analyze_single_rds_instance(
        self, instance_data: Dict, region: str
    ) -> Dict[str, Any]:
        """Analyze a single RDS instance that we already have data for."""
        try:
            cloudwatch = self.session.client("cloudwatch", region_name=region)

            # Build analysis similar to existing RDS analysis
            analysis = {
                "db_instance_identifier": instance_data.get("DBInstanceIdentifier"),
                "engine": instance_data.get("Engine"),
                "engine_version": instance_data.get("EngineVersion"),
                "db_instance_class": instance_data.get("DBInstanceClass"),
                "allocated_storage": instance_data.get("AllocatedStorage"),
                "storage_type": instance_data.get("StorageType"),
                "storage_encrypted": instance_data.get("StorageEncrypted", False),
                "kms_key_id": instance_data.get("KmsKeyId"),
                "multi_az": instance_data.get("MultiAZ", False),
                "availability_zone": instance_data.get("AvailabilityZone"),
                "vpc_security_groups": [
                    {
                        "vpc_security_group_id": sg.get("VpcSecurityGroupId"),
                        "status": sg.get("Status"),
                    }
                    for sg in instance_data.get("VpcSecurityGroups", [])
                ],
                "db_subnet_group": (
                    {
                        "name": instance_data.get("DBSubnetGroup", {}).get(
                            "DBSubnetGroupName"
                        ),
                        "vpc_id": instance_data.get("DBSubnetGroup", {}).get("VpcId"),
                    }
                    if instance_data.get("DBSubnetGroup")
                    else None
                ),
                "publicly_accessible": instance_data.get("PubliclyAccessible", False),
                "backup_retention_period": instance_data.get("BackupRetentionPeriod"),
                "preferred_backup_window": instance_data.get("PreferredBackupWindow"),
                "preferred_maintenance_window": instance_data.get(
                    "PreferredMaintenanceWindow"
                ),
                "deletion_protection": instance_data.get("DeletionProtection", False),
                "performance_insights_enabled": instance_data.get(
                    "PerformanceInsightsEnabled", False
                ),
                "monitoring_interval": instance_data.get("MonitoringInterval", 0),
            }

            # Get CloudWatch metrics for this instance
            try:
                analysis["cloudwatch_metrics"] = self._get_rds_cloudwatch_metrics(
                    cloudwatch, instance_data.get("DBInstanceIdentifier"), region
                )
            except Exception as e:
                self.logger.warning(
                    f"Could not get CloudWatch metrics for {instance_data.get('DBInstanceIdentifier')}: {e}"
                )
                analysis["cloudwatch_metrics"] = {}

            return analysis

        except Exception as e:
            self.logger.error(f"Error analyzing RDS instance: {e}")
            return {}

    def _analyze_single_aurora_cluster(
        self, cluster_data: Dict, region: str
    ) -> Dict[str, Any]:
        """Analyze a single Aurora cluster that we already have data for."""
        try:
            # Build analysis similar to existing Aurora analysis
            return {
                "db_cluster_identifier": cluster_data.get("DBClusterIdentifier"),
                "engine": cluster_data.get("Engine"),
                "engine_version": cluster_data.get("EngineVersion"),
                "engine_mode": cluster_data.get("EngineMode"),
                "status": cluster_data.get("Status"),
                "allocated_storage": cluster_data.get("AllocatedStorage", 0),
                "storage_encrypted": cluster_data.get("StorageEncrypted", False),
                "kms_key_id": cluster_data.get("KmsKeyId"),
                "multi_az": cluster_data.get("MultiAZ", False),
                "availability_zones": cluster_data.get("AvailabilityZones", []),
                "vpc_security_groups": [
                    {
                        "vpc_security_group_id": sg.get("VpcSecurityGroupId"),
                        "status": sg.get("Status"),
                    }
                    for sg in cluster_data.get("VpcSecurityGroups", [])
                ],
                "db_subnet_group": cluster_data.get("DBSubnetGroup"),
                "backup_retention_period": cluster_data.get("BackupRetentionPeriod"),
                "preferred_backup_window": cluster_data.get("PreferredBackupWindow"),
                "preferred_maintenance_window": cluster_data.get(
                    "PreferredMaintenanceWindow"
                ),
                "deletion_protection": cluster_data.get("DeletionProtection", False),
                "cluster_members": [
                    {
                        "db_instance_identifier": member.get("DBInstanceIdentifier"),
                        "is_cluster_writer": member.get("IsClusterWriter", False),
                        "db_cluster_parameter_group_status": member.get(
                            "DBClusterParameterGroupStatus"
                        ),
                        "promotion_tier": member.get("PromotionTier", 0),
                    }
                    for member in cluster_data.get("DBClusterMembers", [])
                ],
            }
        except Exception as e:
            self.logger.error(f"Error analyzing Aurora cluster: {e}")
            return {}

    def _analyze_single_db_subnet_group(
        self, subnet_group_data: Dict
    ) -> Dict[str, Any]:
        """Analyze a single DB subnet group that we already have data for."""
        return {
            "name": subnet_group_data.get("DBSubnetGroupName"),
            "description": subnet_group_data.get("DBSubnetGroupDescription", ""),
            "vpc_id": subnet_group_data.get("VpcId"),
            "status": subnet_group_data.get("SubnetGroupStatus"),
            "subnets": [
                {
                    "subnet_id": subnet.get("SubnetIdentifier"),
                    "availability_zone": subnet.get("SubnetAvailabilityZone", {}).get(
                        "Name"
                    ),
                    "status": subnet.get("SubnetStatus"),
                }
                for subnet in subnet_group_data.get("Subnets", [])
            ],
            "tags": {
                tag["Key"]: tag["Value"] for tag in subnet_group_data.get("Tags", [])
            },
        }

    def _analyze_specific_security_groups(
        self, ec2_client, sg_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Analyze specific security groups by their IDs."""
        if not sg_ids:
            return []

        try:
            # Use existing method if available
            return self._analyze_target_security_groups(sg_ids, ec2_client)
        except Exception as e:
            self.logger.error(f"Error analyzing security groups {sg_ids}: {e}")
            return []

    def _get_subnet_route_tables(
        self, ec2_client, subnet_id: str
    ) -> List[Dict[str, Any]]:
        """Get route tables associated with a subnet."""
        route_tables = []

        try:
            # Get route tables that are explicitly associated with this subnet
            response = ec2_client.describe_route_tables(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
            )

            for rt in response.get("RouteTables", []):
                route_table_info = {
                    "route_table_id": rt["RouteTableId"],
                    "routes": [],
                    "tags": {tag["Key"]: tag["Value"] for tag in rt.get("Tags", [])},
                }

                # Parse routes
                for route in rt.get("Routes", []):
                    route_info = {
                        "destination": route.get(
                            "DestinationCidrBlock",
                            route.get("DestinationIpv6CidrBlock", "Unknown"),
                        ),
                        "state": route.get("State", "Unknown"),
                    }

                    # Determine target
                    if route.get("GatewayId"):
                        route_info["target"] = route["GatewayId"]
                    elif route.get("NatGatewayId"):
                        route_info["target"] = route["NatGatewayId"]
                    elif route.get("NetworkInterfaceId"):
                        route_info["target"] = route["NetworkInterfaceId"]
                    elif route.get("VpcPeeringConnectionId"):
                        route_info["target"] = route["VpcPeeringConnectionId"]
                    elif route.get("TransitGatewayId"):
                        route_info["target"] = route["TransitGatewayId"]
                    else:
                        route_info["target"] = "local"

                    route_table_info["routes"].append(route_info)

                route_tables.append(route_table_info)

            # If no explicit association, get the main route table for the VPC
            if not route_tables:
                subnet_info = ec2_client.describe_subnets(SubnetIds=[subnet_id])[
                    "Subnets"
                ][0]
                vpc_id = subnet_info["VpcId"]

                main_rt_response = ec2_client.describe_route_tables(
                    Filters=[
                        {"Name": "vpc-id", "Values": [vpc_id]},
                        {"Name": "association.main", "Values": ["true"]},
                    ]
                )

                for rt in main_rt_response.get("RouteTables", []):
                    route_table_info = {
                        "route_table_id": rt["RouteTableId"],
                        "routes": [],
                        "tags": {
                            tag["Key"]: tag["Value"] for tag in rt.get("Tags", [])
                        },
                    }

                    # Parse routes (same logic as above)
                    for route in rt.get("Routes", []):
                        route_info = {
                            "destination": route.get(
                                "DestinationCidrBlock",
                                route.get("DestinationIpv6CidrBlock", "Unknown"),
                            ),
                            "state": route.get("State", "Unknown"),
                        }

                        if route.get("GatewayId"):
                            route_info["target"] = route["GatewayId"]
                        elif route.get("NatGatewayId"):
                            route_info["target"] = route["NatGatewayId"]
                        elif route.get("NetworkInterfaceId"):
                            route_info["target"] = route["NetworkInterfaceId"]
                        elif route.get("VpcPeeringConnectionId"):
                            route_info["target"] = route["VpcPeeringConnectionId"]
                        elif route.get("TransitGatewayId"):
                            route_info["target"] = route["TransitGatewayId"]
                        else:
                            route_info["target"] = "local"

                        route_table_info["routes"].append(route_info)

                    route_tables.append(route_table_info)

        except Exception as e:
            self.logger.error(f"Error getting route tables for subnet {subnet_id}: {e}")

        return route_tables

    def _get_subnet_network_acls(
        self, ec2_client, subnet_id: str
    ) -> List[Dict[str, Any]]:
        """Get network ACLs associated with a subnet."""
        network_acls = []

        try:
            response = ec2_client.describe_network_acls(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
            )

            for nacl in response.get("NetworkAcls", []):
                nacl_info = {
                    "network_acl_id": nacl["NetworkAclId"],
                    "entries": [],
                    "is_default": nacl.get("IsDefault", False),
                    "tags": {tag["Key"]: tag["Value"] for tag in nacl.get("Tags", [])},
                }

                # Parse ACL entries
                for entry in nacl.get("Entries", []):
                    entry_info = {
                        "rule_number": entry.get("RuleNumber"),
                        "protocol": entry.get("Protocol"),
                        "rule_action": entry.get("RuleAction"),
                        "cidr_block": entry.get(
                            "CidrBlock", entry.get("Ipv6CidrBlock", "Unknown")
                        ),
                        "egress": entry.get("Egress", False),
                    }

                    # Handle port ranges
                    if entry.get("PortRange"):
                        port_range = entry["PortRange"]
                        if port_range.get("From") == port_range.get("To"):
                            entry_info["port_range"] = str(port_range["From"])
                        else:
                            entry_info["port_range"] = (
                                f"{port_range.get('From', '')}-{port_range.get('To', '')}"
                            )
                    else:
                        entry_info["port_range"] = "All"

                    nacl_info["entries"].append(entry_info)

                network_acls.append(nacl_info)

        except Exception as e:
            self.logger.error(f"Error getting network ACLs for subnet {subnet_id}: {e}")

        return network_acls
