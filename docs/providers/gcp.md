# GCP Cloud Discovery Setup

## Overview

The PlanetScale Discovery CLI can analyze Google Cloud SQL instances, AlloyDB clusters, and related networking infrastructure.

## Prerequisites

- GCP project with Cloud SQL or AlloyDB instances
- Service account with appropriate permissions
- Python package: `pip install "ps-discovery[gcp]"`

## Required GCP APIs

Enable the following APIs in your GCP project:

```bash
# Enable required APIs
gcloud services enable sqladmin.googleapis.com
gcloud services enable compute.googleapis.com
gcloud services enable monitoring.googleapis.com
gcloud services enable alloydb.googleapis.com
```

Or via Console:
1. Navigate to APIs & Services → Library
2. Search for and enable each API:
   - Cloud SQL Admin API
   - Compute Engine API
   - Cloud Monitoring API
   - AlloyDB API

## Required GCP Permissions

Create a custom IAM role with the following permissions:

```json
{
  "title": "PlanetScale Discovery Role",
  "description": "Permissions for PlanetScale database discovery tool",
  "stage": "GA",
  "includedPermissions": [
    "cloudsql.instances.list",
    "cloudsql.instances.get",
    "alloydb.clusters.list",
    "alloydb.clusters.get",
    "alloydb.instances.list",
    "alloydb.instances.get",
    "compute.networks.list",
    "compute.networks.get",
    "compute.subnetworks.list",
    "compute.subnetworks.get",
    "compute.firewalls.list",
    "monitoring.metricDescriptors.list",
    "monitoring.timeSeries.list"
  ]
}
```

## Authentication Options

### Option 1: Service Account Key (Recommended for Discovery)

1. **Create Service Account:**
   ```bash
   gcloud iam service-accounts create ps-discovery \
       --display-name="PlanetScale Discovery Tool"
   ```

2. **Grant Permissions:**
   ```bash
   gcloud projects add-iam-policy-binding PROJECT_ID \
       --member="serviceAccount:ps-discovery@PROJECT_ID.iam.gserviceaccount.com" \
       --role="roles/cloudsql.viewer"

   gcloud projects add-iam-policy-binding PROJECT_ID \
       --member="serviceAccount:ps-discovery@PROJECT_ID.iam.gserviceaccount.com" \
       --role="roles/compute.networkViewer"
   ```

3. **Create and Download Key:**
   ```bash
   gcloud iam service-accounts keys create ~/ps-discovery-key.json \
       --iam-account=ps-discovery@PROJECT_ID.iam.gserviceaccount.com
   ```

4. **Configure Discovery Tool:**
   ```yaml
   providers:
     gcp:
       enabled: true
       project_id: your-project-123
       credentials:
         service_account_key: /path/to/ps-discovery-key.json
       regions:
         - us-central1
   ```

### Option 2: Application Default Credentials

For interactive use or when running on GCP:

```bash
# Authenticate
gcloud auth application-default login

# Or set environment variable
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
```

Configuration:
```yaml
providers:
  gcp:
    enabled: true
    project_id: your-project-123
    credentials:
      application_default: true
    regions:
      - us-central1
```

### Option 3: Environment Variables

```bash
export GCP_PROJECT_ID=your-project-123
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
export GCP_REGIONS=us-central1,us-east1
```

Minimal config:
```yaml
providers:
  gcp:
    enabled: true
    # Reads from environment variables
```

## Configuration Examples

### Basic Configuration

```yaml
modules:
  - cloud

providers:
  gcp:
    enabled: true
    project_id: my-project-123
    regions:
      - us-central1
      - us-east1
    credentials:
      service_account_key: /path/to/key.json
    discover_all: true

output:
  output_dir: ./gcp_discovery_output
```

### Multi-Project Discovery

```yaml
# Note: Current version supports single project
# For multiple projects, run discovery separately for each
providers:
  gcp:
    enabled: true
    project_id: production-project-123
    regions:
      - us-central1
```

### Resource-Specific Discovery

```yaml
providers:
  gcp:
    enabled: true
    project_id: my-project
    discover_all: false
    resources:
      cloud_sql_instances:
        - prod-db-instance
        - staging-db-instance
      alloydb_clusters:
        - prod-alloydb-cluster
    regions:
      - us-central1
```

### Cross-Region Discovery

```yaml
providers:
  gcp:
    enabled: true
    project_id: global-project-123
    regions:
      - us-central1
      - us-east1
      - europe-west1
      - asia-southeast1
    discover_all: true
```

## Setting Up Service Account

### Via gcloud CLI

```bash
# 1. Create service account
PROJECT_ID="your-project-123"
SA_NAME="ps-discovery"

gcloud iam service-accounts create $SA_NAME \
    --display-name="PlanetScale Discovery" \
    --project=$PROJECT_ID

# 2. Create custom role with required permissions
cat > discovery-role.yaml << EOF
title: "PlanetScale Discovery Role"
description: "Custom role for database discovery"
stage: "GA"
includedPermissions:
- cloudsql.instances.list
- cloudsql.instances.get
- alloydb.clusters.list
- alloydb.clusters.get
- alloydb.instances.list
- alloydb.instances.get
- compute.networks.list
- compute.networks.get
- compute.subnetworks.list
- compute.firewalls.list
EOF

gcloud iam roles create planetscaleDiscovery \
    --project=$PROJECT_ID \
    --file=discovery-role.yaml

# 3. Bind role to service account
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="projects/$PROJECT_ID/roles/planetscaleDiscovery"

# 4. Create key
gcloud iam service-accounts keys create ~/planetscale-key.json \
    --iam-account=$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com
```

### Via GCP Console

1. **Create Service Account:**
   - Navigate to IAM & Admin → Service Accounts
   - Click "Create Service Account"
   - Name: `ps-discovery`
   - Click "Create and Continue"

2. **Grant Roles:**
   - Add Role: `Cloud SQL Viewer`
   - Add Role: `Compute Network Viewer`
   - Add Role: `Monitoring Viewer` (optional)
   - Click "Continue" then "Done"

3. **Create Key:**
   - Click on the service account
   - Go to "Keys" tab
   - Click "Add Key" → "Create New Key"
   - Choose JSON format
   - Download and save securely

## Regional Considerations

### Available Regions

GCP Cloud SQL supports numerous regions. Common regions:

- **Americas:** `us-central1`, `us-east1`, `us-west1`, `northamerica-northeast1`
- **Europe:** `europe-west1`, `europe-west2`, `europe-north1`
- **Asia Pacific:** `asia-southeast1`, `asia-northeast1`, `australia-southeast1`

### Multi-Region Discovery

Specify regions for comprehensive discovery:

```yaml
providers:
  gcp:
    enabled: true
    project_id: global-app
    regions:
      - us-central1    # Primary region
      - us-east1       # DR region
      - europe-west1   # European users
```

### Regional Quotas

- API quotas are per-project
- Monitor quota usage in GCP Console
- Request quota increases if needed

## Data Collected

### Cloud SQL Instances
- Instance name and self-link
- Database version (PostgreSQL, MySQL, SQL Server)
- Tier (instance size)
- Region and availability type (zonal/regional)
- Storage type and capacity
- Backup configuration
- High availability status
- Private IP configuration
- SSL requirements
- Maintenance windows
- Database flags

### AlloyDB Clusters
- Cluster name and network
- Primary and read pool instances
- Compute resources
- Storage allocation
- Backup configuration
- Encryption settings
- Private Service Connect configuration

### Networking
- VPC networks
- Subnet configuration
- Firewall rules
- Private service connections
- VPC peering
- IP address allocations

### Monitoring
- CPU utilization metrics
- Memory usage
- Storage utilization
- Connection counts
- Replication lag

## Troubleshooting

### "API not enabled" Error

**Problem:** Required GCP APIs not enabled

**Solutions:**
```bash
# Check enabled APIs
gcloud services list --enabled

# Enable missing APIs
gcloud services enable sqladmin.googleapis.com compute.googleapis.com
```

### "Permission Denied" Errors

**Problem:** Service account lacks permissions

**Solutions:**
1. Verify service account has required roles
2. Check IAM policy bindings:
   ```bash
   gcloud projects get-iam-policy PROJECT_ID \
       --flatten="bindings[].members" \
       --filter="bindings.members:serviceAccount:YOUR-SA"
   ```
3. Ensure custom role includes all permissions
4. Wait 1-2 minutes for permission changes to propagate

### "Project not found" Error

**Problem:** Invalid project ID or access

**Solutions:**
1. Verify project ID: `gcloud projects list`
2. Check service account has access to project
3. Ensure billing is enabled
4. Confirm project is not deleted

### "Quota Exceeded" Error

**Problem:** API quota limits reached

**Solutions:**
1. Check quota usage in Console → IAM & Admin → Quotas
2. Request quota increase
3. Reduce discovery frequency
4. Implement rate limiting

### Authentication Issues

**Problem:** Invalid credentials

**Solutions:**
1. Verify key file path is correct
2. Check key file permissions (readable)
3. Ensure key hasn't been deleted in GCP
4. Verify service account still exists
5. Test authentication:
   ```bash
   gcloud auth activate-service-account \
       --key-file=/path/to/key.json
   gcloud sql instances list --project=PROJECT_ID
   ```

## Security Best Practices

### Service Account Management
- Use separate service accounts per environment
- Rotate keys regularly (90 days recommended)
- Delete unused keys
- Never commit keys to version control
- Use Secret Manager for key storage

### Least Privilege
- Grant only required permissions
- Use custom roles instead of predefined roles when possible
- Limit to specific projects
- Review permissions quarterly

### Key Security
- Store keys in secure locations
- Encrypt keys at rest
- Use GCP Secret Manager or HashiCorp Vault
- Audit key usage with Cloud Logging

### Monitoring
- Enable Cloud Audit Logs
- Set up alerts for unusual API activity
- Review service account usage regularly
- Monitor for anomalous behavior

## Advanced Configuration

### VPC Service Controls

For organizations using VPC Service Controls:

```yaml
providers:
  gcp:
    enabled: true
    project_id: protected-project
    vpc_service_controls:
      enabled: true
      perimeter: accessPolicies/POLICY_ID/servicePerimeters/PERIMETER_NAME
```

### Shared VPC Configuration

For resources in shared VPC:

```yaml
providers:
  gcp:
    enabled: true
    project_id: service-project-123
    shared_vpc:
      host_project: vpc-host-project-123
```

### Custom API Endpoints

For Private Google Access:

```yaml
providers:
  gcp:
    enabled: true
    project_id: private-project
    endpoint_override:
      sqladmin: https://sqladmin.private.googleapis.com
```

## Recommendations

### High Availability
- Document regional/multi-regional setup
- Catalog read replicas

### Backup Strategy
- Review automated backup configuration
- Evaluate point-in-time recovery windows

### Network Architecture
- Document Private Service Connect
- Review VPC peering connections

### Database Flags
- Catalog custom database flags

## Managed Database Considerations

When running against managed PostgreSQL services (Cloud SQL, AlloyDB):

- **Expected Warnings:** May see warnings about missing schema privileges
- **Graceful Degradation:** Tool continues even with restricted features
- **Core Analysis:** Essential data still captured
- **Error Handling:** Errors logged but don't prevent completion

## Additional Resources

- [Cloud SQL Documentation](https://cloud.google.com/sql/docs)
- [AlloyDB Documentation](https://cloud.google.com/alloydb/docs)
- [GCP IAM Best Practices](https://cloud.google.com/iam/docs/best-practices)
- [VPC Documentation](https://cloud.google.com/vpc/docs)
## Support

For issues with GCP discovery:
- Report bugs: https://github.com/planetscale/ps-discovery/issues
- GCP Support: https://cloud.google.com/support
