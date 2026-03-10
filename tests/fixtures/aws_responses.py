"""
AWS API response fixtures for testing
"""

SAMPLE_RDS_INSTANCE = {
    "DBInstanceIdentifier": "test-postgres-instance",
    "DBInstanceClass": "db.r6g.large",
    "Engine": "postgres",
    "EngineVersion": "17.4",
    "DBInstanceStatus": "available",
    "MasterUsername": "postgres",
    "Endpoint": {
        "Address": "test-instance.abc123.us-east-1.rds.amazonaws.com",
        "Port": 5432,
    },
    "AllocatedStorage": 100,
    "StorageType": "gp3",
    "StorageEncrypted": True,
    "KmsKeyId": "arn:aws:kms:us-east-1:123456789012:key/test-key-id",
    "AvailabilityZone": "us-east-1a",
    "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-12345678", "Status": "active"}],
    "DBSubnetGroup": {
        "DBSubnetGroupName": "test-subnet-group",
        "DBSubnetGroupDescription": "Test subnet group",
        "VpcId": "vpc-12345678",
        "SubnetGroupStatus": "Complete",
        "Subnets": [
            {
                "SubnetIdentifier": "subnet-12345678",
                "SubnetAvailabilityZone": {"Name": "us-east-1a"},
                "SubnetStatus": "Active",
            }
        ],
    },
    "MultiAZ": False,
    "PubliclyAccessible": False,
    "BackupRetentionPeriod": 7,
    "PreferredBackupWindow": "03:00-04:00",
    "PreferredMaintenanceWindow": "sun:04:00-sun:05:00",
    "AutoMinorVersionUpgrade": True,
    "PerformanceInsightsEnabled": True,
    "DeletionProtection": False,
    "MonitoringInterval": 0,
}

SAMPLE_AURORA_CLUSTER = {
    "DBClusterIdentifier": "test-aurora-cluster",
    "Engine": "aurora-mysql",
    "EngineVersion": "5.7.mysql_aurora.2.11.5",
    "EngineMode": "provisioned",
    "Status": "available",
    "MasterUsername": "admin",
    "Endpoint": "test-cluster.cluster-abc123.us-east-1.rds.amazonaws.com",
    "Port": 3306,
    "AllocatedStorage": 1,
    "StorageEncrypted": True,
    "KmsKeyId": "arn:aws:kms:us-east-1:123456789012:key/test-key-id",
    "AvailabilityZones": ["us-east-1a", "us-east-1b", "us-east-1c"],
    "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-87654321", "Status": "active"}],
    "DBSubnetGroup": "test-aurora-subnet-group",
    "MultiAZ": True,
    "BackupRetentionPeriod": 7,
    "PreferredBackupWindow": "09:15-09:45",
    "PreferredMaintenanceWindow": "tue:04:15-tue:04:45",
    "DeletionProtection": True,
    "DBClusterMembers": [
        {
            "DBInstanceIdentifier": "test-aurora-instance-1",
            "IsClusterWriter": True,
            "DBClusterParameterGroupStatus": "in-sync",
            "PromotionTier": 1,
        },
        {
            "DBInstanceIdentifier": "test-aurora-instance-2",
            "IsClusterWriter": False,
            "DBClusterParameterGroupStatus": "in-sync",
            "PromotionTier": 1,
        },
    ],
}

SAMPLE_VPC = {
    "VpcId": "vpc-12345678",
    "State": "available",
    "CidrBlock": "10.0.0.0/16",
    "IsDefault": False,
    "Tags": [{"Key": "Name", "Value": "test-vpc"}],
}

SAMPLE_SUBNET = {
    "SubnetId": "subnet-12345678",
    "State": "available",
    "VpcId": "vpc-12345678",
    "CidrBlock": "10.0.1.0/24",
    "AvailabilityZone": "us-east-1a",
    "AvailableIpAddressCount": 250,
    "MapPublicIpOnLaunch": True,
    "Tags": [{"Key": "Name", "Value": "test-subnet"}],
}

SAMPLE_SECURITY_GROUP = {
    "GroupId": "sg-12345678",
    "GroupName": "test-security-group",
    "Description": "Test security group",
    "VpcId": "vpc-12345678",
    "IpPermissions": [
        {
            "IpProtocol": "tcp",
            "FromPort": 5432,
            "ToPort": 5432,
            "IpRanges": [{"CidrIp": "10.0.0.0/8", "Description": "Internal access"}],
        }
    ],
    "IpPermissionsEgress": [
        {"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    ],
    "Tags": [],
}

SAMPLE_ROUTE_TABLE = {
    "RouteTableId": "rtb-12345678",
    "VpcId": "vpc-12345678",
    "Routes": [
        {
            "DestinationCidrBlock": "10.0.0.0/16",
            "State": "active",
            "GatewayId": "local",
        },
        {
            "DestinationCidrBlock": "0.0.0.0/0",
            "State": "active",
            "GatewayId": "igw-12345678",
        },
    ],
    "Associations": [
        {
            "RouteTableAssociationId": "rtbassoc-12345678",
            "RouteTableId": "rtb-12345678",
            "SubnetId": "subnet-12345678",
            "Main": False,
        }
    ],
    "Tags": [],
}

SAMPLE_NETWORK_ACL = {
    "NetworkAclId": "acl-12345678",
    "VpcId": "vpc-12345678",
    "IsDefault": True,
    "Entries": [
        {
            "RuleNumber": 100,
            "Protocol": "-1",
            "RuleAction": "allow",
            "CidrBlock": "0.0.0.0/0",
            "Egress": False,
        },
        {
            "RuleNumber": 32767,
            "Protocol": "-1",
            "RuleAction": "deny",
            "CidrBlock": "0.0.0.0/0",
            "Egress": False,
        },
    ],
    "Associations": [
        {
            "NetworkAclAssociationId": "aclassoc-12345678",
            "NetworkAclId": "acl-12345678",
            "SubnetId": "subnet-12345678",
        }
    ],
    "Tags": [],
}

# Mock responses for AWS API calls
RDS_DESCRIBE_INSTANCES_RESPONSE = {"DBInstances": [SAMPLE_RDS_INSTANCE]}

RDS_DESCRIBE_CLUSTERS_RESPONSE = {"DBClusters": [SAMPLE_AURORA_CLUSTER]}

EC2_DESCRIBE_VPCS_RESPONSE = {"Vpcs": [SAMPLE_VPC]}

EC2_DESCRIBE_SUBNETS_RESPONSE = {"Subnets": [SAMPLE_SUBNET]}

EC2_DESCRIBE_SECURITY_GROUPS_RESPONSE = {"SecurityGroups": [SAMPLE_SECURITY_GROUP]}

EC2_DESCRIBE_ROUTE_TABLES_RESPONSE = {"RouteTables": [SAMPLE_ROUTE_TABLE]}

EC2_DESCRIBE_NETWORK_ACLS_RESPONSE = {"NetworkAcls": [SAMPLE_NETWORK_ACL]}
