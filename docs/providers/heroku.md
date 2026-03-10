# Heroku Postgres Cloud Discovery Setup

## Overview

The PlanetScale Discovery CLI can analyze Heroku-hosted PostgreSQL databases, providing insights into add-on configuration, connection pooling (PgBouncer), follower/replica databases, cross-app attachments, and plan-based specifications.

Heroku Postgres databases are managed as "add-ons" on the Heroku Platform API. The discovery tool identifies all apps with Heroku Postgres add-ons and collects infrastructure metadata without accessing actual database contents.

## Prerequisites

- Heroku account with access to the apps you want to analyze
- Heroku API key (Platform API authorization token)
- Python package: `pip install "ps-discovery[heroku]"`

## Authentication Setup

### Option 1: Heroku API Key from Dashboard (Recommended)

Heroku API keys provide access to the Platform API for managing and inspecting apps, add-ons, and configuration.

**How to obtain:**

1. Log in to [Heroku Dashboard](https://dashboard.heroku.com)
2. Click your avatar in the top-right corner and select **Account Settings**
3. Scroll down to the **API Key** section
4. Click **Reveal** to view your existing key, or **Regenerate API Key** to create a new one
5. Copy the key

**Configure in YAML:**

```yaml
providers:
  heroku:
    enabled: true
    api_key: "your-heroku-api-key"
```

### Option 2: Heroku CLI Authorization Token

If you have the [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) installed and authenticated, you can retrieve your current token:

```bash
heroku auth:token
```

This token can be used in your configuration or set as an environment variable.

### Option 3: Environment Variables

For security, store credentials in environment variables rather than config files:

```bash
export HEROKU_API_KEY="your-heroku-api-key"

# Optional: target a specific app
export HEROKU_TARGET_APP="my-production-app"
```

Then use a minimal config:

```yaml
providers:
  heroku:
    enabled: true
    # API key will be read from HEROKU_API_KEY
```

## Required Permissions

The Heroku API key must belong to a user (or team member) with access to the apps being analyzed. The discovery tool uses the following API endpoints:

**Platform API Endpoints Used** (`api.heroku.com`):

| Endpoint | Purpose | Permission Required |
|----------|---------|-------------------|
| `GET /account` | Verify authentication | Any authenticated user |
| `GET /apps` | List accessible apps | App access (member, collaborator, or admin) |
| `GET /apps/{app}/addons` | List add-ons per app | App access |
| `GET /apps/{app}/config-vars` | Detect pooling and follower config vars | App access |
| `GET /apps/{app}/addon-attachments` | Detect cross-app database sharing | App access |

**Data API Endpoint Used** (`api.data.heroku.com`):

| Endpoint | Purpose | Permission Required |
|----------|---------|-------------------|
| `GET /client/v11/databases/{addon_id}` | Live database details (size, connections, PG version, maintenance) | Same API key — add-on access |

**Key notes on permissions:**

- The API key inherits the permissions of the user it belongs to. If you can see an app in the Heroku Dashboard, the discovery tool can analyze it.
- For **team/organization apps**, you need to be a member of the team with at least `view` access.
- For **personal apps**, you need to be the owner or a collaborator.
- Config var **values** are fetched to detect pooling and follower patterns, but **only the key names** are stored in discovery results. No credentials or connection strings are ever persisted.

## Configuration Examples

### Complete Configuration Template

```yaml
# PlanetScale Discovery - Heroku Provider Configuration Template

# Modules to enable
modules:
  - cloud

# Provider configuration
providers:
  heroku:
    enabled: true

    # Required: Heroku API key
    # Get key from: https://dashboard.heroku.com/account
    # Or set HEROKU_API_KEY environment variable
    api_key: "your-heroku-api-key"

    # Optional: Target a specific app (leave commented to discover all apps)
    # target_app: "my-production-app"

    # Discover all accessible apps with Heroku Postgres add-ons
    discover_all: true

# Output configuration
output:
  output_dir: ./heroku_discovery_output
  mask_sensitive_data: true

# Logging settings
log_level: INFO
# log_file: ./discovery.log
```

### Basic Configuration (All Apps)

Discover all apps with Heroku Postgres that your API key can access:

```yaml
modules:
  - cloud

providers:
  heroku:
    enabled: true
    api_key: "your-heroku-api-key"
    discover_all: true

output:
  output_dir: ./heroku_discovery_output
```

### Single App Discovery

Target a specific app by name:

```yaml
modules:
  - cloud

providers:
  heroku:
    enabled: true
    api_key: "your-heroku-api-key"
    target_app: "my-production-app"

output:
  output_dir: ./heroku_discovery_output
```

### Multi-Provider Configuration

Combine Heroku with other cloud providers:

```yaml
modules:
  - cloud

providers:
  heroku:
    enabled: true
    api_key: "your-heroku-api-key"
    discover_all: true

  aws:
    enabled: true
    regions:
      - us-east-1

output:
  output_dir: ./multi_cloud_discovery
```

### CLI-Only Usage (No Config File)

You can run Heroku discovery entirely from the command line:

```bash
# Discover all apps
ps-discovery cloud --providers heroku --heroku-api-key "your-key"

# Target a specific app
ps-discovery cloud --providers heroku \
  --heroku-api-key "your-key" \
  --heroku-target-app "my-production-app"

# Using environment variable for the key
export HEROKU_API_KEY="your-key"
ps-discovery cloud --providers heroku
```

## Data Collected

The Heroku analyzer collects the following information:

### App Information
- App name
- Region (us, eu)
- Postgres add-on details

### Database Configuration (per add-on)
- Add-on name and ID
- Plan name (e.g., `standard-0`, `premium-2`)
- Plan specifications (vCPU, RAM, storage, connection limits, provisioned IOPS, HA, fork/follow support)
- Provisioning state
- Config var names associated with the database (names only, never values)

### Live Database Details (via Heroku Data API)

For each Postgres add-on, the tool also queries the [Heroku Data API](https://api.data.heroku.com) — the same internal API that powers `heroku pg:info` — to collect live operational data:

- **Data size**: Current usage vs plan limit with percentage (e.g., `66.1 GB / 512 GB (12.92%)`)
- **Raw byte count** (`num_bytes`) for programmatic use
- **PostgreSQL version**
- **Active connections** and waiting connections
- **Table count**
- **Continuous Protection** status
- **Data Encryption** status
- **Maintenance window** schedule
- **Rollback** availability and earliest restore point
- **Fork/Follow** availability

This data is fetched using the same Heroku API key — no separate credentials or direct database connections are needed.

### Connection Pooling Detection
- Whether PgBouncer connection pooling is active (detected via `*_CONNECTION_POOL_URL` config vars)
- Pooling mode (transaction mode on Heroku)
- Pool-related config var names

### Follower/Replica Detection
- Follower databases detected via multiple `HEROKU_POSTGRESQL_*_URL` config vars
- Color-coded database identifiers (e.g., AMBER, GREEN, COPPER)

### Cross-App Attachments
- Add-on attachments shared across multiple apps
- Identifies which apps share the same database

### Plan Specifications

The tool maps Heroku Postgres plan names to known specifications. vCPU and provisioned IOPS data is sourced from the [Heroku Postgres Production Tier Technical Characterization](https://devcenter.heroku.com/articles/heroku-postgres-production-tier-technical-characterization).

| Tier | Plans | vCPU | RAM | Storage | Connections | HA | Fork/Follow |
|------|-------|------|-----|---------|-------------|-----|-------------|
| **Essential** | essential-0 through essential-2 | Shared | Shared | 1-32 GB | 20-40 | No | No |
| **Standard** | standard-0 through standard-10 | 2-128 | 4 GB-1 TB | 64 GB-8 TB | 120-500 | No | Yes |
| **Premium** | premium-0 through premium-10 | 2-128 | 4 GB-1 TB | 64 GB-8 TB | 120-500 | Yes | Yes |
| **Private** | private-0 through private-10 | 2-128 | 4 GB-1 TB | 64 GB-8 TB | 120-500 | Yes | Yes |
| **Shield** | shield-0 through shield-10 | 2-128 | 4 GB-1 TB | 64 GB-8 TB | 120-500 | Yes | Yes |

**Note:** Only Premium, Private, and Shield tiers include [High Availability](https://devcenter.heroku.com/articles/heroku-postgres-ha). Standard tier does not.

## Security Considerations

### What Is Collected

- **Config var key names only** (e.g., `DATABASE_URL`, `HEROKU_POSTGRESQL_COPPER_URL`). Values are read to detect patterns but are **never stored** in discovery results.
- **Live database metadata** from the Heroku Data API (size, connections, PG version, maintenance schedule). The Data API response includes credential fields (`resource_url`, `database_password`) — these are **explicitly excluded** and never stored.
- **No database contents** are accessed. The tool uses the Heroku Platform API and Data API, not direct database connections.
- **No application code** or business logic is accessed.

### What Is NOT Collected

- Config var values (connection strings, passwords, secrets)
- Database credentials returned by the Data API (`resource_url`, `database_password`, `database_user`)
- Database table contents or row data
- Application source code or environment details beyond Postgres add-ons
- Billing or payment information

### API Key Security

- **Never commit API keys to version control.** Use environment variables or a `.gitignore`'d config file.
- **Rotate API keys** after running discovery, especially if the key was shared or stored temporarily.
- **Use the minimum-access account** needed. If you only need to analyze specific apps, consider using a collaborator account with limited access rather than an admin account.

### Report Handling

- Reports may contain app names and add-on identifiers. Review before sharing externally.
- Use `mask_sensitive_data: true` in output config to sanitize outputs.
- Store reports securely and delete when no longer needed.

## Running Discovery

### Command Line

```bash
# Using configuration file
ps-discovery cloud --config heroku-config.yaml

# With specific output directory
ps-discovery cloud --config heroku-config.yaml --output-dir ./output

# Combined with database discovery
ps-discovery both --config full-config.yaml
```

### Expected Output

The tool generates two report files:

1. **JSON Report** (`cloud_discovery_results.json`)
   - Complete structured data with all app and database details
   - Programmatically accessible for further analysis

2. **Markdown Report** (`cloud_discovery_summary.md`)
   - Human-readable summary with tables
   - Per-app database details, pooling, and follower information
   - Recommendations specific to Heroku

## Limitations

### API Rate Limits

- The Heroku Platform API enforces rate limits (approximately 4,500 requests per hour)
- The discovery tool uses pagination and handles 429 (rate limited) responses gracefully
- For accounts with many apps, discovery may take longer due to per-app API calls

### Read-Only Access

- The tool only reads app, add-on, and database metadata
- No modifications are made to apps, add-ons, or configuration
- No direct database connections are established (the Data API provides live details like size and connections without a direct Postgres connection)

### Plan Specification Accuracy

- Plan specs (vCPU, RAM, storage, IOPS) are based on a static mapping sourced from [Heroku's documentation](https://devcenter.heroku.com/articles/heroku-postgres-production-tier-technical-characterization) and may not reflect the latest changes
- Live database details (actual data size, connection count, PG version) are fetched from the Data API and are always current
- Unknown or new plan names will be reported with `"unknown"` specifications
- The plan tier is always extracted from the plan name even for unrecognized plans

## Troubleshooting

### "No Heroku API key provided" Error

**Problem:** No API key found in config or environment

**Solution:**
1. Set `api_key` in your config file under `providers.heroku`
2. Or set the `HEROKU_API_KEY` environment variable
3. Verify the key is not empty or whitespace

### "Invalid or expired Heroku API key" Error

**Problem:** API returned 401 Unauthorized

**Solution:**
1. Verify the API key is correct (check for copy/paste errors)
2. Regenerate the key at [dashboard.heroku.com/account](https://dashboard.heroku.com/account)
3. Ensure the account is active and not suspended

### "Heroku API rate limit reached" Warning

**Problem:** Too many API requests in a short period

**Solution:**
1. Wait and retry (the tool will continue with data collected so far)
2. Use `target_app` to analyze specific apps instead of all apps
3. Run discovery during off-peak hours

### No Apps Found

**Problem:** Discovery completes but reports zero apps

**Solution:**
1. Verify your account has apps with Heroku Postgres add-ons
2. Check that the API key belongs to an account with app access
3. For team apps, verify team membership and access level
4. Try targeting a specific app with `target_app` to confirm access

## Recommendations

### Connection Pooling (PgBouncer)

Heroku Postgres uses PgBouncer in **transaction mode** for connection pooling:

- Review application compatibility with transaction-mode pooling
- Check for prepared statement usage (not compatible with transaction mode)

### Follower Databases

Heroku followers are read replicas that follow a primary database:

- Document follower databases alongside the primary
- Review application read/write splitting logic

### Essential Plan Limitations

Essential-tier databases lack HA, fork, and follow capabilities:

- Single-instance architecture (simpler topology)
- Limited backup options
- No replica lag considerations

### Cross-App Attachments

If databases are shared across multiple apps via add-on attachments:

- Identify all dependent applications
- Document connection string usage across all apps
- Review shared access patterns

## Example Workflow

```bash
# 1. Install with Heroku support
pip install "ps-discovery[heroku]"

# 2. Set your API key
export HEROKU_API_KEY="your-heroku-api-key"

# 3. Generate a config template
ps-discovery config-template --output heroku-config.yaml --providers heroku

# 4. Run discovery
ps-discovery cloud --config heroku-config.yaml

# 5. Review reports
ls ./discovery_output/
cat ./discovery_output/cloud_discovery_summary.md
```

## Additional Resources

- [Heroku Platform API Reference](https://devcenter.heroku.com/articles/platform-api-reference)
- [Heroku Postgres Documentation](https://devcenter.heroku.com/categories/heroku-postgres)
- [Heroku Postgres Plans](https://devcenter.heroku.com/articles/heroku-postgres-plans)
- [Heroku PgBouncer Connection Pooling](https://devcenter.heroku.com/articles/postgres-connection-pooling)
- [Heroku Postgres Followers](https://devcenter.heroku.com/articles/heroku-postgres-follower-databases)
## Support

For issues with the discovery tool:
- Report bugs: https://github.com/planetscale/ps-discovery/issues
- Documentation: See main [README.md](../../README.md)

For Heroku-specific questions:
- Heroku Support: https://help.heroku.com
- Heroku Dev Center: https://devcenter.heroku.com
