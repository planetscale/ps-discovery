# AWS Cloud Discovery Setup

## Overview

The PlanetScale Discovery CLI can analyze AWS RDS instances, Aurora clusters, and related networking infrastructure.

## Prerequisites

- AWS account with RDS/Aurora instances
- AWS credentials configured (IAM user, role, or profile)
- Python package: `pip install "ps-discovery[aws]"`

## Required AWS Services Access

The cloud discovery feature requires access to the following AWS services:

- **Amazon RDS** - For analyzing RDS instances and Aurora clusters
- **Amazon EC2** - For VPC, subnet, security group, and gateway analysis
- **Amazon CloudWatch** - For performance metrics and monitoring data
- **AWS STS** - For credential validation and cross-account access

## Required AWS Permissions

Create an IAM policy with the following comprehensive permissions for complete infrastructure analysis:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PlanetScaleDiscoveryRDS",
            "Effect": "Allow",
            "Action": [
                "rds:DescribeDBInstances",
                "rds:DescribeDBClusters",
                "rds:DescribeDBSubnetGroups",
                "rds:DescribeDBClusterParameterGroups",
                "rds:DescribeDBParameterGroups",
                "rds:DescribeOptionGroups"
            ],
            "Resource": "*"
        },
        {
            "Sid": "PlanetScaleDiscoveryEC2",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeVpcs",
                "ec2:DescribeSubnets",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeInternetGateways",
                "ec2:DescribeNatGateways",
                "ec2:DescribeVpcEndpoints",
                "ec2:DescribeRouteTables",
                "ec2:DescribeNetworkAcls",
                "ec2:DescribeVpcPeeringConnections",
                "ec2:DescribeTransitGateways",
                "ec2:DescribeVpnGateways"
            ],
            "Resource": "*"
        },
        {
            "Sid": "PlanetScaleDiscoveryCloudWatch",
            "Effect": "Allow",
            "Action": [
                "cloudwatch:GetMetricStatistics",
                "cloudwatch:ListMetrics"
            ],
            "Resource": "*"
        },
        {
            "Sid": "PlanetScaleDiscoverySTS",
            "Effect": "Allow",
            "Action": [
                "sts:GetCallerIdentity"
            ],
            "Resource": "*"
        }
    ]
}
```

## Authentication Options

### Option 1: AWS Profile (Recommended)

```bash
# Configure AWS CLI profile
aws configure --profile migration-discovery

# Use in discovery tool
ps-discovery cloud --aws-profile migration-discovery
```

Or in configuration file:

```yaml
providers:
  aws:
    enabled: true
    credentials:
      profile: migration-discovery
    regions:
      - us-east-1
      - us-west-2
```

### Option 2: IAM Role (For Cross-Account Access)

```yaml
providers:
  aws:
    enabled: true
    credentials:
      role_arn: "arn:aws:iam::123456789012:role/PlanetScaleDiscoveryRole"
      external_id: "unique-external-id"  # Optional but recommended
    regions:
      - us-east-1
```

### Option 3: Environment Variables

```bash
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_DEFAULT_REGION=us-east-1

ps-discovery cloud --config config.yaml
```

### Option 4: IAM Instance Profile

When running on EC2, the tool can automatically use the instance's IAM role. No additional configuration needed.

## Configuration Examples

### Basic Configuration

```yaml
modules:
  - cloud

providers:
  aws:
    enabled: true
    regions:
      - us-east-1
      - us-west-2
    discover_all: true

output:
  output_dir: ./aws_discovery_output
```

### Focused Discovery (Specific Database)

```yaml
providers:
  aws:
    enabled: true
    regions:
      - us-east-1
    discover_all: true
    target_database: my-rds-instance  # Focus on specific database

output:
  output_dir: ./focused_discovery
```

### Multi-Account Discovery

```yaml
providers:
  aws:
    enabled: true
    credentials:
      role_arn: "arn:aws:iam::ACCOUNT-ID:role/DiscoveryRole"
      external_id: "unique-id"
    regions:
      - us-east-1
      - eu-west-1
    discover_all: true
```

### Resource-Specific Discovery

```yaml
providers:
  aws:
    enabled: true
    discover_all: false
    resources:
      rds_instances:
        - production-db-1
        - staging-db-1
      aurora_clusters:
        - prod-cluster
    regions:
      - us-east-1
```

## Setting Up IAM Credentials

### Create IAM Policy

1. **Via AWS Console:**
   - Navigate to IAM → Policies → Create Policy
   - Choose JSON tab and paste the permissions JSON above
   - Name it `PlanetScaleDiscoveryPolicy`
   - Click Create Policy

2. **Via AWS CLI:**
   ```bash
   aws iam create-policy \
       --policy-name PlanetScaleDiscoveryPolicy \
       --policy-document file://discovery-policy.json
   ```

### Create IAM Role (Cross-Account or EC2)

```bash
# Create trust policy file
cat > trust-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::YOUR-ACCOUNT:root"
            },
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {
                    "sts:ExternalId": "unique-external-id"
                }
            }
        }
    ]
}
EOF

# Create the role
aws iam create-role \
    --role-name PlanetScaleDiscoveryRole \
    --assume-role-policy-document file://trust-policy.json

# Attach the policy
aws iam attach-role-policy \
    --role-name PlanetScaleDiscoveryRole \
    --policy-arn arn:aws:iam::YOUR-ACCOUNT:policy/PlanetScaleDiscoveryPolicy
```

### Create IAM User (Access Key Method)

```bash
# Create user
aws iam create-user --user-name ps-discovery

# Attach policy
aws iam attach-user-policy \
    --user-name ps-discovery \
    --policy-arn arn:aws:iam::YOUR-ACCOUNT:policy/PlanetScaleDiscoveryPolicy

# Create access key
aws iam create-access-key --user-name ps-discovery
```

## Regional Considerations

### Multi-Region Discovery

Specify multiple regions to analyze RDS instances across regions:

```yaml
providers:
  aws:
    enabled: true
    regions:
      - us-east-1
      - us-west-2
      - eu-west-1
      - ap-southeast-1
```

### Default Region

If not specified, the tool defaults to `us-east-1`.

### Regional Costs

- CloudWatch API calls are charged per region
- RDS and EC2 describe calls are generally free
- For large-scale discovery, monitor AWS API costs

## Data Collected

### RDS Instances
- Instance identifier and ARN
- Engine type and version
- Instance class and storage
- Multi-AZ configuration
- Backup retention and windows
- Maintenance windows
- Performance Insights status
- Enhanced monitoring
- Encryption status

### Aurora Clusters
- Cluster identifier and ARN
- Engine mode (provisioned/serverless)
- Reader/writer endpoints
- Cluster members
- Backup retention
- Global database configuration

### Networking
- VPC configuration
- Subnet groups and availability zones
- Security groups and rules
- Route tables
- Internet gateways
- NAT gateways
- VPC peering connections
- Transit gateway attachments

### Operational
- CloudWatch metrics
- CPU, memory, storage utilization
- Connection counts
- Replication lag (if applicable)

## Troubleshooting

### "Authentication failed" Error

**Problem:** AWS credentials not found or invalid

**Solutions:**
1. Verify AWS CLI is configured: `aws sts get-caller-identity`
2. Check profile name matches configuration
3. Ensure credentials have not expired
4. For role assumption, verify trust policy

### "Access Denied" Errors

**Problem:** IAM permissions insufficient

**Solutions:**
1. Verify IAM policy includes all required permissions
2. Check region-specific permissions
3. Confirm policy is attached to user/role
4. Review AWS CloudTrail for specific denied actions

### "No instances found" Warning

**Problem:** No RDS instances discovered

**Solutions:**
1. Verify instances exist in specified regions
2. Check region configuration
3. Ensure credentials have rds:DescribeDBInstances permission
4. Try with `discover_all: true`

### Rate Limiting

**Problem:** AWS API throttling

**Solutions:**
1. Reduce number of regions
2. Implement retry delays (built-in)
3. Request rate limit increase from AWS
4. Spread discovery over multiple time windows

## Security Best Practices

### Credential Management
- Use IAM roles instead of access keys when possible
- Rotate access keys regularly (90 days recommended)
- Never commit credentials to version control
- Use AWS Secrets Manager for credential storage

### Least Privilege
- Grant only required permissions
- Use resource-specific policies when possible
- Limit regions in IAM policies
- Review CloudTrail logs for unused permissions

### Audit and Monitoring
- Enable CloudTrail logging for API calls
- Set up CloudWatch alarms for unusual API activity
- Review IAM Access Analyzer findings
- Periodic access review (quarterly recommended)

## Advanced Configuration

### Custom Endpoint Configuration

For AWS GovCloud or custom endpoints:

```yaml
providers:
  aws:
    enabled: true
    regions:
      - us-gov-west-1
    endpoint_url: https://rds.us-gov-west-1.amazonaws.com
```

### Proxy Configuration

```bash
export HTTP_PROXY=http://proxy.example.com:8080
export HTTPS_PROXY=http://proxy.example.com:8080
export NO_PROXY=169.254.169.254  # For instance metadata

ps-discovery cloud --config config.yaml
```

### Session Token Authentication

For temporary credentials:

```yaml
providers:
  aws:
    enabled: true
    credentials:
      access_key_id: ASIA...
      secret_access_key: ...
      session_token: IQoJb3...
    regions:
      - us-east-1
```

## Recommendations

### High Availability
- Document Multi-AZ deployments
- Review failover procedures

### Backup Strategy
- Catalog current backup retention
- Evaluate point-in-time recovery needs

### Network Architecture
- Document VPC peering connections
- Review security group rules

### Performance
- Baseline current performance metrics
- Identify workload patterns

## Additional Resources

- [AWS RDS Documentation](https://docs.aws.amazon.com/rds/)
- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [AWS VPC Documentation](https://docs.aws.amazon.com/vpc/)
## Support

For issues with AWS discovery:
- Report bugs: https://github.com/planetscale/ps-discovery/issues
- AWS Support: https://aws.amazon.com/support/
