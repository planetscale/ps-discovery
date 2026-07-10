# Neon Serverless Postgres Cloud Discovery Setup

## Overview

The PlanetScale Discovery CLI can analyze Neon-hosted PostgreSQL projects, providing insights into project configuration, branch topology, compute endpoint autoscaling, connection pooling, and database metadata.

Neon is a serverless Postgres provider that organizes databases into projects with branching, autoscaling compute, and connection pooling built in. The discovery tool uses the [Neon API](https://api-docs.neon.tech/reference/getting-started) to collect infrastructure metadata without accessing actual database contents or establishing direct database connections.

## Prerequisites

- Neon account with access to the projects you want to analyze
- Neon API key (personal, organization, or project-scoped)
- Python package: `pip install "ps-discovery[neon]"`

## Authentication Setup

### Option 1: Personal API Key (Recommended)

A personal API key provides access to all projects owned by your Neon account.

**How to obtain:**

1. Log in to the [Neon Console](https://console.neon.tech)
2. Click your avatar in the bottom-left corner and select **Account Settings**
3. Navigate to the **API Keys** section
4. Click **Generate new API key**
5. Give the key a name and click **Create**
6. Copy the key (it is only shown once)

**Configure in YAML:**

```yaml
providers:
  neon:
    enabled: true
    api_key: "your-neon-api-key"
```

### Option 2: Organization API Key

Organization API keys provide access to all projects within a Neon organization. These are useful when multiple team members share projects under an organization.

Generate organization API keys from the Neon Console under **Organization Settings > API Keys**.

### Option 3: Project-Scoped API Key

Project-scoped keys are limited to a single project. Use these for targeted analysis when you only need to assess one project.

Generate project-scoped keys from the Neon Console under **Project Settings > API Keys**.

### Option 4: Environment Variables

For security, store credentials in environment variables rather than config files:

```bash
export NEON_API_KEY="your-neon-api-key"

# Optional: target a specific project
export NEON_TARGET_PROJECT="project-id-here"
```

Then use a minimal config:

```yaml
providers:
  neon:
    enabled: true
    # API key will be read from NEON_API_KEY
```

## Required Permissions

The API key must have access to the projects being analyzed. The discovery tool uses the following API endpoints:

| Endpoint | Purpose | Permission Required |
|----------|---------|-------------------|
| `GET /projects` | List accessible projects | Personal or org key |
| `GET /projects/{id}` | Get project details | Project access |
| `GET /projects/{id}/branches` | List branches | Project access |
| `GET /projects/{id}/endpoints` | List compute endpoints | Project access |
| `GET /projects/{id}/branches/{branch_id}/databases` | List databases | Project access |

**Key notes on permissions:**

- Personal API keys inherit access to all projects owned by the account.
- Organization API keys provide access to all projects within the organization.
- Project-scoped keys are limited to their specific project.
- The tool only performs read operations. No modifications are made to projects, branches, or endpoints.

## Configuration Examples

### Complete Configuration Template

```yaml
# PlanetScale Discovery - Neon Provider Configuration Template

# Database engine (Neon is PostgreSQL)
engine: postgres

# Provider configuration
providers:
  neon:
    enabled: true

    # Required: Neon API key
    # Get key from: https://console.neon.tech/app/settings/api-keys
    # Or set NEON_API_KEY environment variable
    api_key: "your-neon-api-key"

    # Optional: Target a specific project (leave commented to discover all projects)
    # target_project: "project-abc-123"

    # Optional: Filter projects to a specific organization
    # org_id: "org-abc-123"

    # Discover all accessible projects
    discover_all: true

# Output configuration
output:
  output_dir: ./neon_discovery_output

# Logging settings
log_level: INFO
# log_file: ./discovery.log
```

### Basic Configuration (All Projects)

Discover all projects your API key can access:

```yaml
engine: postgres

providers:
  neon:
    enabled: true
    api_key: "your-neon-api-key"
    discover_all: true

output:
  output_dir: ./neon_discovery_output
```

### Single Project Discovery

Target a specific project by ID:

```yaml
engine: postgres

providers:
  neon:
    enabled: true
    api_key: "your-neon-api-key"
    target_project: "project-abc-123"

output:
  output_dir: ./neon_discovery_output
```

### Multi-Provider Configuration

Combine Neon with other cloud providers:

```yaml
engine: postgres

providers:
  neon:
    enabled: true
    api_key: "your-neon-api-key"
    discover_all: true

  aws:
    enabled: true
    regions:
      - us-east-1

output:
  output_dir: ./multi_cloud_discovery
```

### CLI-Only Usage (No Config File)

You can run Neon discovery entirely from the command line:

```bash
# Discover all projects
ps-discovery cloud --providers neon --neon-api-key "your-key"

# Target a specific project
ps-discovery cloud --providers neon \
  --neon-api-key "your-key" \
  --neon-target-project "project-abc-123"

# Filter by organization
ps-discovery cloud --providers neon \
  --neon-api-key "your-key" \
  --neon-org-id "org-abc-123"

# Using environment variable for the key
export NEON_API_KEY="your-key"
ps-discovery cloud --providers neon
```

## Data Collected

The Neon analyzer collects the following information:

### Project Information
- Project name and ID
- Region (e.g., `aws-us-east-2`, `aws-eu-central-1`)
- PostgreSQL version
- Creation and last update timestamps
- Owner subscription type (free, launch, scale, business, enterprise)

### Branch Topology
- Branch names and IDs
- Default branch identification
- Parent-child relationships between branches
- Logical size (data size) and physical size (storage used)
- Branch state (ready, init_compute, etc.)
- Protected branch status

### Compute Endpoints
- Endpoint type (read-write or read-only/read replica)
- Autoscaling configuration (minimum and maximum compute units)
- Compute specifications mapped from CU size (vCPU, RAM, estimated max connections)
- Connection pooling status and mode (transaction, session)
- Suspend timeout (seconds of inactivity before auto-suspend)
- Current state (active, idle, suspended)

### Databases
- Database names on the default branch
- Database owner

### Compute Specifications

The tool maps Neon Compute Units (CU) to hardware specifications:

| CU Size | vCPU | RAM | Est. Max Connections |
|---------|------|-----|---------------------|
| 0.25 | 0.25 | 1 GB | 112 |
| 0.5 | 0.5 | 2 GB | 225 |
| 1 | 1 | 4 GB | 450 |
| 2 | 2 | 8 GB | 901 |
| 3 | 3 | 12 GB | 1,351 |
| 4 | 4 | 16 GB | 1,802 |
| 5 | 5 | 20 GB | 2,252 |
| 6 | 6 | 24 GB | 2,703 |
| 7 | 7 | 28 GB | 3,153 |
| 8 | 8 | 32 GB | 3,604 |

## Security Considerations

### What Is Collected

- **Project metadata only**: names, regions, versions, timestamps, and configuration settings.
- **Branch topology**: sizes, states, and parent-child relationships.
- **Endpoint configuration**: autoscaling limits, pooling settings, and suspend timeouts.
- **Database names and owners** on the default branch.
- **No database contents** are accessed. The tool uses the Neon Management API, not direct database connections.
- **No application code** or business logic is accessed.

### What Is NOT Collected

- Connection strings or URIs
- Database passwords or credentials
- Database table contents or row data
- Application source code
- Billing or payment information

### API Key Security

- **Never commit API keys to version control.** Use environment variables or a `.gitignore`'d config file.
- **Rotate API keys** after running discovery, especially if the key was shared or stored temporarily.
- **Use project-scoped keys** when you only need to analyze a single project, following the principle of least privilege.

### Report Handling

- Reports may contain project names and region identifiers. Review before sharing externally.
- Store reports securely and delete when no longer needed.

## Running Discovery

### Command Line

```bash
# Run discovery using your configuration file
ps-discovery --config neon-config.yaml

# Save reports to a specific directory
ps-discovery --config neon-config.yaml --output-dir ./output
```

### Expected Output

The tool generates two report files:

1. **JSON Report** (`cloud_discovery_results.json`)
   - Complete structured data with all project, branch, endpoint, and database details
   - Programmatically accessible for further analysis

2. **Markdown Report** (`cloud_discovery_summary.md`)
   - Human-readable summary with tables
   - Per-project branch and endpoint details
   - Summary statistics across all projects

## Limitations

### API Rate Limits

- The Neon API enforces rate limits (700 requests per minute baseline, burst up to 40 requests per second)
- The discovery tool handles 429 (rate limited) responses gracefully and reports a warning
- For accounts with many projects, discovery may take longer due to per-project API calls

### Read-Only Access

- The tool only reads project, branch, endpoint, and database metadata
- No modifications are made to projects, branches, endpoints, or databases
- No direct database connections are established

### Branch Coverage

- Databases are currently listed for the **default branch only** to avoid excessive API calls across many branches
- Branch sizes and states are collected for all branches

## Troubleshooting

### "No Neon API key provided" Error

**Problem:** No API key found in config or environment

**Solution:**
1. Set `api_key` in your config file under `providers.neon`
2. Or set the `NEON_API_KEY` environment variable
3. Verify the key is not empty or whitespace

### "Invalid or expired Neon API key" Error

**Problem:** API returned 401 Unauthorized

**Solution:**
1. Verify the API key is correct (check for copy/paste errors)
2. Generate a new key at [console.neon.tech/app/settings/api-keys](https://console.neon.tech/app/settings/api-keys)
3. Ensure the account is active and the key has not been revoked

### "Neon API rate limit reached" Warning

**Problem:** Too many API requests in a short period

**Solution:**
1. Wait and retry (the tool will continue with data collected so far)
2. Use `target_project` to analyze specific projects instead of all projects
3. Run discovery during off-peak hours

### No Projects Found

**Problem:** Discovery completes but reports zero projects

**Solution:**
1. Verify your account has Neon projects
2. Check that the API key has access to the projects you expect
3. For organization keys, verify the `org_id` is correct
4. For project-scoped keys, verify the key matches the target project
5. Try targeting a specific project with `target_project` to confirm access

## Example Workflow

```bash
# 1. Install with Neon support
pip install "ps-discovery[neon]"

# 2. Set your API key
export NEON_API_KEY="your-neon-api-key"

# 3. Generate a config template
ps-discovery config-template --output neon-config.yaml --providers neon

# 4. Run discovery
ps-discovery --config neon-config.yaml

# 5. Review reports
ls ./discovery_output/
cat ./discovery_output/cloud_discovery_summary.md
```

## Additional Resources

- [Neon API Reference](https://api-docs.neon.tech/reference/getting-started)
- [Neon Documentation](https://neon.tech/docs)
- [Neon Branching](https://neon.tech/docs/introduction/branching)
- [Neon Autoscaling](https://neon.tech/docs/introduction/autoscaling)
- [Neon Connection Pooling](https://neon.tech/docs/connect/connection-pooling)

## Support

For issues with the discovery tool:
- Report bugs: https://github.com/planetscale/planetscale-discovery-cli-dev/issues
- Documentation: See main [README.md](../../README.md)

For Neon-specific questions:
- Neon Support: https://neon.tech/docs/introduction/support
- Neon Community: https://community.neon.tech
