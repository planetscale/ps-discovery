# Cloud Provider Documentation

This directory contains setup guides and configuration documentation for each supported cloud provider.

## Supported Providers

### [AWS (RDS/Aurora)](aws.md)
Amazon Web Services RDS and Aurora database discovery.

**Key Features:**
- RDS instance discovery
- Aurora cluster analysis
- VPC and networking configuration
- Security group analysis
- IAM authentication support

**Quick Start:**
```yaml
providers:
  aws:
    enabled: true
    regions:
      - us-east-1
    credentials:
      profile: default
```

### [GCP (Cloud SQL/AlloyDB)](gcp.md)
Google Cloud Platform Cloud SQL and AlloyDB database discovery.

**Key Features:**
- Cloud SQL instance discovery
- AlloyDB cluster analysis
- VPC network configuration
- Firewall rule analysis
- Service account authentication

**Quick Start:**
```yaml
providers:
  gcp:
    enabled: true
    project_id: my-project-123
    regions:
      - us-central1
    credentials:
      service_account_key: path/to/key.json
```

### [Supabase](supabase.md)
Supabase-hosted PostgreSQL project discovery.

**Key Features:**
- Project metadata discovery
- Database configuration analysis
- PgBouncer pooling details
- Network and SSL configuration
- API-based authentication

**Quick Start:**
```yaml
providers:
  supabase:
    enabled: true
    access_token: "sbp_xxxxxxxxxxxx"
    discover_all: true
```

### [Heroku](heroku.md)
Heroku Postgres add-on discovery.

**Key Features:**
- Postgres add-on discovery across all apps
- Live database details via Heroku Data API (data size, PG version, connections, maintenance)
- PgBouncer connection pooling detection
- Follower/replica database detection
- Cross-app attachment identification
- Plan-based specification mapping (vCPU, RAM, storage, connections, provisioned IOPS, HA)

**Quick Start:**
```yaml
providers:
  heroku:
    enabled: true
    api_key: "your-heroku-api-key"
    discover_all: true
```

## Common Setup Patterns

### Authentication

Each provider has different authentication methods:

- **AWS**: IAM profiles, access keys, session tokens, instance roles
- **GCP**: Service account keys, application default credentials
- **Supabase**: Personal access tokens, service role keys
- **Heroku**: Platform API keys (from Dashboard or CLI)

See individual provider documentation for detailed authentication setup.

### Multi-Provider Discovery

You can discover multiple cloud providers simultaneously:

```yaml
modules:
  - cloud

providers:
  aws:
    enabled: true
    regions:
      - us-east-1

  gcp:
    enabled: true
    project_id: my-project

  supabase:
    enabled: true
    access_token: "sbp_xxxxx"

  heroku:
    enabled: true
    api_key: "your-heroku-api-key"

output:
  output_dir: ./multi_cloud_discovery
```

### Environment Variables

All providers support environment variable configuration:

**AWS:**
```bash
export AWS_PROFILE=migration-discovery
export AWS_REGION=us-east-1
```

**GCP:**
```bash
export GCP_PROJECT_ID=my-project-123
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

**Supabase:**
```bash
export SUPABASE_ACCESS_TOKEN=sbp_xxxxxxxxxxxx
export SUPABASE_PROJECT_REF=abcdefghijklmnop
```

**Heroku:**
```bash
export HEROKU_API_KEY=your-heroku-api-key
export HEROKU_TARGET_APP=my-production-app  # Optional: target specific app
```

### Discovery Scope

Control what resources are discovered:

**Specific Resources:**
```yaml
providers:
  aws:
    enabled: true
    discover_all: false
    resources:
      rds_instances:
        - db-prod-1
        - db-staging-1
```

**All Resources (Default):**
```yaml
providers:
  aws:
    enabled: true
    discover_all: true  # Discover everything accessible
```

**Targeted Discovery:**
```yaml
providers:
  aws:
    enabled: true
    discover_all: true
    target_database: db-prod-1  # Focus on specific database
```

## Installation

Install provider-specific dependencies:

```bash
# AWS only
pipx install -e ".[aws]"

# GCP only
pipx install -e ".[gcp]"

# Supabase only
pipx install -e ".[supabase]"

# Heroku only
pipx install -e ".[heroku]"

# All providers
pipx install -e ".[all]"
```

## Output Format

All providers generate consistent output:

### JSON Report Structure
```json
{
  "providers": {
    "aws": { /* AWS discoveries */ },
    "gcp": { /* GCP discoveries */ },
    "supabase": { /* Supabase discoveries */ },
    "heroku": { /* Heroku discoveries */ }
  },
  "summary": {
    "providers_discovered": ["aws", "gcp", "supabase", "heroku"],
    "total_databases": 10,
    "total_clusters": 3,
    "total_regions": 5
  }
}
```

### Markdown Report Sections
- Executive Summary
- Provider-specific infrastructure details
- Networking analysis
- Security configuration
- Recommendations

## Security Best Practices

### Credential Management
- Use environment variables for sensitive data
- Never commit credentials to version control
- Rotate access tokens/keys regularly
- Use least-privilege access

### Report Handling
- Enable `mask_sensitive_data: true` in output config
- Store reports securely
- Review reports before sharing
- Sanitize before sending to third parties

### Access Control
- Use read-only credentials when possible
- Limit API token scopes to discovery needs
- Monitor API usage and access logs
- Audit credential access regularly

## Troubleshooting

### Authentication Issues

**Problem:** "Authentication failed" errors

**Solutions:**
1. Verify credentials are correct
2. Check token/key hasn't expired
3. Ensure proper permissions/scopes
4. Test credentials with provider's CLI tools

### Rate Limiting

**Problem:** API rate limit errors

**Solutions:**
1. Reduce discovery scope
2. Increase delay between API calls
3. Use provider-specific rate limit handling
4. Spread discovery across time windows

### Missing Data

**Problem:** Some data not appearing in reports

**Solutions:**
1. Verify credentials have sufficient permissions
2. Check if resources exist in specified regions
3. Review logs for warnings about skipped resources
4. Ensure API endpoints are accessible

## Provider Comparison

| Feature | AWS | GCP | Supabase | Heroku |
|---------|-----|-----|----------|--------|
| Database Types | RDS, Aurora | Cloud SQL, AlloyDB | PostgreSQL | PostgreSQL (add-ons) |
| Authentication | IAM, Access Keys | Service Accounts | Access Tokens | API Keys |
| Networking | VPC, Security Groups | VPC, Firewalls | SSL, IPv6 | Managed (no VPC config) |
| Pricing Tiers | Multiple | Multiple | Free to Enterprise | Essential to Shield |
| API Maturity | Mature | Mature | Growing | Mature |
| Documentation | Extensive | Extensive | Good | Good |

## Getting Help

- [Main README](../../README.md)
- [GitHub Issues](https://github.com/planetscale/ps-discovery/issues)
- Provider-specific documentation (see links above)

## Contributing

When adding a new cloud provider:

1. Create analyzer in `planetscale_discovery/cloud/analyzers/`
2. Extend `CloudAnalyzer` base class
3. Add provider configuration to `config_manager.py`
4. Update report generators
5. Add comprehensive documentation here
6. Include unit tests
7. Update main README.md

See existing providers for implementation patterns.
