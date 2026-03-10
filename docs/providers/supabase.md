# Supabase Cloud Discovery Setup

## Overview

The PlanetScale Discovery CLI can analyze Supabase-hosted PostgreSQL infrastructure, providing insights into database configuration, connection pooling, networking, and operational details.

## Prerequisites

- Supabase account with project access
- Personal Access Token or Service Role Key
- Python package: `pip install "ps-discovery[supabase]"`

## Authentication Setup

### Option 1: Personal Access Token (Recommended)

Personal Access Tokens provide read-only access to project metadata and are the recommended approach for discovery operations.

**How to obtain:**

1. Log in to [Supabase Dashboard](https://app.supabase.com)
2. Navigate to Account Settings → Access Tokens
3. Click "Generate New Token"
4. Give it a descriptive name (e.g., "PlanetScale Discovery")
5. Copy the token immediately (it won't be shown again)

**Configure in YAML:**

```yaml
providers:
  supabase:
    enabled: true
    access_token: "sbp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### Option 2: Service Role Key

Service Role Keys have elevated privileges and should be used with caution. Only use this if Personal Access Tokens don't provide sufficient access.

**How to obtain:**

1. Log in to [Supabase Dashboard](https://app.supabase.com)
2. Select your project
3. Navigate to Project Settings → API
4. Copy the "service_role" key (under "Project API keys")

**Configure in YAML:**

```yaml
providers:
  supabase:
    enabled: true
    access_token: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."  # Service role key
```

### Option 3: Environment Variables

For security, you can store credentials in environment variables:

```bash
export SUPABASE_ACCESS_TOKEN="sbp_xxxxxxxxxxxx"
export SUPABASE_PROJECT_REF="abcdefghijklmnop"  # Optional: target specific project
```

Then use a minimal config:

```yaml
providers:
  supabase:
    enabled: true
    # Token will be read from SUPABASE_ACCESS_TOKEN
```

## Required Permissions

**For Personal Access Tokens:**
- Read access to organization projects
- Read access to project metadata
- Read access to project settings

**API Endpoints Used:**
- `GET /v1/projects` - List accessible projects
- `GET /v1/projects/{ref}` - Get project details
- `GET /v1/projects/{ref}/config/database` - Get database configuration
- `GET /v1/projects/{ref}/config/pgbouncer` - Get pooler configuration

## Configuration Examples

### Complete Configuration Template

Here's a complete configuration template you can copy and paste into your config file:

```yaml
# PlanetScale Discovery - Supabase Provider Configuration Template

# Modules to enable
modules:
  - cloud

# Provider configuration
providers:
  supabase:
    enabled: true

    # Required: Personal Access Token from Supabase Dashboard
    # Get token from: https://app.supabase.com/account/tokens
    access_token: "sbp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"

    # Optional: Target a specific project (leave commented to discover all projects)
    # project_ref: "abcdefghijklmnop"

    # Optional: Filter projects by organization ID
    # organization_id: "org_xxxxx"

    # Discover all accessible projects (set to false if using project_ref)
    discover_all: true

# Output configuration
output:
  output_dir: ./supabase_discovery_output
  include_costs: true
  mask_sensitive_data: true
  generate_diagrams: false

# Logging settings
log_level: INFO
# log_file: ./discovery.log
```

### Basic Configuration (All Projects)

Discover all projects accessible with your token:

```yaml
modules:
  - cloud

providers:
  supabase:
    enabled: true
    access_token: "sbp_xxxxxxxxxxxx"
    discover_all: true

output:
  output_dir: ./supabase_discovery_output
```

### Single Project Discovery

Target a specific project by its reference ID:

```yaml
modules:
  - cloud

providers:
  supabase:
    enabled: true
    access_token: "sbp_xxxxxxxxxxxx"
    project_ref: "abcdefghijklmnop"  # Your project ref
    discover_all: false

output:
  output_dir: ./supabase_discovery_output
```

### Organization-Scoped Discovery

Filter projects by organization:

```yaml
providers:
  supabase:
    enabled: true
    access_token: "sbp_xxxxxxxxxxxx"
    organization_id: "org_xxxxx"  # Optional
    discover_all: true
```

### Multi-Provider Configuration

Combine Supabase with other cloud providers:

```yaml
modules:
  - cloud

providers:
  aws:
    enabled: true
    regions:
      - us-east-1

  supabase:
    enabled: true
    access_token: "sbp_xxxxxxxxxxxx"
    discover_all: true

output:
  output_dir: ./multi_cloud_discovery
```

## Data Collected

The Supabase analyzer collects the following information:

### Project Information
- Project reference ID
- Project name and organization
- Region/hosting location
- Project status and health
- Creation timestamp

### Database Configuration
- PostgreSQL version
- Instance size/pricing tier
- Database connection limits
- Storage capacity and usage
- Host and port information

### Connection Pooling
- PgBouncer configuration
- Pool mode (transaction/session)
- Connection pool size
- Max client connections
- IPv4/IPv6 support

### Networking
- Connection strings (direct vs pooled)
- SSL/TLS enforcement
- IPv6 availability
- Custom domain configuration

### Security
- SSL enforcement status
- Network policies
- Connection security settings

## Running Discovery

### Command Line

```bash
# Using configuration file
ps-discovery cloud --config supabase-config.yaml

# With specific output directory
ps-discovery cloud --config supabase-config.yaml --output-dir ./output

# Combined with database discovery
ps-discovery both --config full-config.yaml
```

### Expected Output

The tool generates two report files:

1. **JSON Report** (`cloud_discovery_results.json`)
   - Complete structured data
   - Programmatically accessible
   - All discovered details

2. **Markdown Report** (`cloud_discovery_summary.md`)
   - Human-readable summary
   - Project overview tables
   - Configuration details
   - Recommendations

## Limitations

### API Rate Limits
- Supabase Management API has rate limits
- Discovery tool includes basic retry logic
- For large numbers of projects, discovery may take time

### Read-Only Access
- Tool only reads project metadata
- No access to actual database data (by design)
- Cannot modify project configuration

### API Availability
- Some configuration endpoints may not be available on all pricing tiers
- Tool gracefully handles missing data
- Reports indicate when specific data is unavailable

## Troubleshooting

### "Authentication failed" Error

**Problem:** Invalid or expired access token

**Solution:**
1. Verify token is correct (check for copy/paste errors)
2. Regenerate token if expired
3. Ensure token has proper permissions
4. Check environment variables are set correctly

### "Failed to list projects" Error

**Problem:** API returned non-200 status code

**Solution:**
1. Verify network connectivity to `api.supabase.com`
2. Check if token has organization access
3. Confirm Supabase API is accessible (not behind firewall)

### "No projects found" Warning

**Problem:** No projects accessible with provided token

**Solution:**
1. Verify you have projects in your Supabase account
2. Check token belongs to correct organization
3. Ensure `project_ref` is correct if targeting specific project

### "Database config endpoint not available"

**Problem:** Some API endpoints return 404

**Solution:**
- This is expected for certain Supabase tiers
- Tool continues with available data
- Report will indicate which data is unavailable
- Not an error condition

## Security Considerations

### Token Storage
- **Never commit tokens to version control**
- Use environment variables for sensitive data
- Rotate tokens regularly
- Use Personal Access Tokens (not service role keys) when possible

### Access Scope
- Personal Access Tokens have read-only access to metadata
- Service Role Keys have elevated privileges - use carefully
- Tool does not access database contents, only configuration metadata

### Report Handling
- Reports may contain sensitive project information
- Use `mask_sensitive_data: true` to sanitize outputs
- Store reports securely
- Review reports before sharing

## Example Workflow

```bash
# 1. Install with Supabase support
pipx install -e ".[supabase]"

# 2. Create configuration file
ps-discovery config-template --output supabase-config.yaml

# 3. Edit the config file and add your Supabase token

# 4. Run discovery
ps-discovery cloud --config supabase-config.yaml

# 5. Review reports in output directory
ls ./supabase_discovery_output/
```

## Recommendations

### Connection Pooling
- Supabase uses PgBouncer in transaction mode by default
- Review application compatibility with transaction-mode pooling

### SSL Connections
- Supabase enforces SSL connections
- Review SSL certificate handling in applications

### PostgreSQL Extensions
- Catalog installed Supabase extensions

### Multi-Region Considerations
- If projects span multiple regions, plan regional strategy
- Consider data residency requirements
- Evaluate network latency impacts

## Additional Resources

- [Supabase Management API Documentation](https://supabase.com/docs/reference/api)
- [Supabase Architecture Overview](https://supabase.com/docs/guides/platform/architecture)
- [Connection Pooling Best Practices](https://supabase.com/docs/guides/database/connecting-to-postgres#connection-pooler)

## Support

For issues with the discovery tool:
- Report bugs: https://github.com/planetscale/ps-discovery/issues
- Documentation: See main [README.md](../../README.md)

For Supabase-specific questions:
- Supabase Support: https://supabase.com/support
- Supabase Documentation: https://supabase.com/docs
