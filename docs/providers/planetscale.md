# PlanetScale Postgres Cloud Discovery Setup

## Overview

The PlanetScale provider records the current state of a PlanetScale account. It reads the PlanetScale API and collects organizations, databases,
branches and the cluster size each branch runs.

**This provider covers PlanetScale Postgres only.**

## Prerequisites

- A PlanetScale organization with at least one Postgres database.
- A PlanetScale service token with read access. See
  [Authentication Setup](#authentication-setup).
- The `requests` library:

## Authentication Setup

The provider authenticates with a **service token**. A service token has two
parts: a token ID and the token itself. Both are required.

### Option 1: Create a service token in the web app

1. Open [Service tokens](https://app.planetscale.com/settings/service-tokens)
   in your organization settings.
2. Select **New service token** and give it a name.
3. Copy the token ID and the token. The token is shown one time only.
4. Open the token. The page holds two separate permission areas.
5. Under **Organization access**, open the organization menu and select
   **Manage permissions**. Select `read_organization` and `read_databases`,
   then select **Save permissions**.
6. Under **Database access**, select **Edit permissions** in the
   **Permissions for all databases** panel. Open the **branch** group, select
   `read_branch`, then save. To grant one database at a time instead, select
   **Add a database** and set `read_branch` on each database.

### Option 2: Create a service token with the CLI

```bash
pscale service-token create
pscale service-token add-access <token-id> read_organization read_databases
pscale service-token add-access <token-id> read_branch --database <database>
```

Repeat the last command for each database. The CLI grants a database access one
database at a time. Use the web app to grant `read_branch` on all databases at
once.

### Option 3: Environment variables

Set both values in the environment and leave them out of the config file:

```bash
export PLANETSCALE_SERVICE_TOKEN_ID=your_service_token_id
export PLANETSCALE_SERVICE_TOKEN=your_service_token

# Optional: limit discovery to one organization
export PLANETSCALE_ORGANIZATION=your-org-slug
```

## Required Permissions

The service token needs these accesses:

| Access | Where to grant it | Needed for |
| --- | --- | --- |
| `read_organization` | Organization access | The organization list, plan and cluster size SKUs |
| `read_databases` | Organization access | The database list, region and state |
| `read_branch` | Database access, in the **branch** group | The branch list, cluster size, replica flags and storage metrics |

`read_organization` and `read_databases` are organization accesses. Grant them
in the **Organization access permissions** dialog. `read_branch` is a database
access. Grant it in the **Permissions for all databases** dialog, or on each
database.

Grant no write access. The provider issues `GET` requests only.

A token without `read_organization` cannot list organizations. In that case set
`organization` in the config, or set `PLANETSCALE_ORGANIZATION`.

## Configuration Examples

### Complete Configuration Template

Generate a starting template:

```bash
ps-discovery config-template --output planetscale.yaml --providers planetscale
```

```yaml
# PlanetScale Discovery - PlanetScale Provider Configuration Template

# Database engine (PlanetScale Postgres databases only)
engine: postgres

# Provider configuration
providers:
  planetscale:
    enabled: true
    # Create a service token at:
    # https://app.planetscale.com/settings/service-tokens
    service_token_id: your_service_token_id
    service_token: your_service_token
    # Optional: limit to one organization (otherwise all readable orgs)
    # organization: your-org-slug
    # Optional: analyze a specific database only
    # target_database: my-database
    discover_all: true  # Discover all Postgres databases.

# Output configuration
output:
  output_dir: ./discovery_output

# Logging settings
log_level: INFO
# log_file: ./discovery.log
```

### Single Organization Discovery

```yaml
engine: postgres

providers:
  planetscale:
    enabled: true
    organization: acme
    # Credentials come from PLANETSCALE_SERVICE_TOKEN_ID and
    # PLANETSCALE_SERVICE_TOKEN.

output:
  output_dir: ./discovery_output
```

### Multi-Provider Configuration

```yaml
engine: postgres

providers:
  aws:
    enabled: true
    regions:
      - us-east-1
  planetscale:
    enabled: true
    organization: acme

output:
  output_dir: ./discovery_output
```

## Data Collected

### Organization Information

- Organization name and creation date
- Plan tier
- Total database count, for all engines
- Single tenancy flag

### Database Information

- Database ID, name and engine kind
- Region slug and cloud provider
- State and readiness
- Branch counts: total, production and development
- Creation date

### Branch Topology

- Branch ID, name and engine kind
- Production flag
- State and readiness
- Parent branch
- Replica flags: `has_replicas` and `has_read_only_replicas`
- Replica count
- Region
- Creation date

### Cluster Sizing

- Cluster size name for each branch, for example `PS_80`
- Provisioned IOPS
- The matching SKU spec: vCPU, RAM, storage, cloud provider and metal flag

Cluster size specs come from the API for each organization, so they stay
current as PlanetScale changes its SKUs.

### Storage

Each branch records a `storage` block and a `storage_config` block.

`storage` holds the current consumption, in bytes, from the branch instant
metrics endpoint: `bytes_used`, `bytes_capacity` and `usage_percentage`. The
endpoint reports one value per pod, so the block also keeps the `pods` list and
takes the branch figures from the primary pod.

`storage_config` holds the settings, not the consumption: `autoscaling`,
`shrinking`, `minimum_bytes`, `maximum_bytes`, `type`, `iops` and
`throughput_mibs`.

A non-Metal SKU reports no storage, and a Metal branch leaves every
`storage_config` field empty because the disk comes with the instance size. So
read consumption from `storage` and never from the SKU.

## Security Considerations

### What Is Collected

- Organization, database and branch names
- Region and cloud provider names
- Cluster sizes, SKU specs and replica flags
- Storage usage and capacity in bytes, and the storage settings
- Plan tier, tenancy and state flags
- Creation dates

### What Is NOT Collected

- Table data of any kind
- Schema or DDL
- Query text
- Branch passwords or connection strings. The provider never calls the
  passwords endpoint.
- The service token. It is used for the request header only and never reaches
  the report.

### Service Token Security

- Store the token in the environment, not in a config file you commit.
- Grant read access only.
- Delete the token when the assessment is complete.
- Rotate the token if it is shared.

### Report Handling

- Reports are written with `0600` permissions.
- Store reports in encrypted storage.
- Do not commit reports to version control.
- Delete reports securely when the migration is complete.

## Running Discovery

### Command Line

```bash
# Run discovery using your configuration file
ps-discovery cloud --config planetscale.yaml

# Include a local markdown summary
ps-discovery cloud --config planetscale.yaml --local-summary
```

### Expected Output

```
Starting cloud database environment discovery
Starting PlanetScale discovery
Successfully authenticated with PlanetScale
PlanetScale analysis completed. Found 3 Postgres database(s) across 1 organization(s)
PlanetScale discovery completed. Found 1 organization(s)
Cloud discovery completed successfully
```

Results appear in the JSON report under
`cloud_results.providers.planetscale`.

## Limitations

### API Rate Limits

The provider stops paginating and records a warning when the API returns
HTTP 429. Results are then incomplete. Re-run the discovery later.

### Read-Only Access

The provider issues `GET` requests only. It creates, changes and deletes
nothing.

### Engine Coverage

The provider analyzes PlanetScale Postgres databases only. It skips databases
of any other engine.

### Call Volume

The provider makes one call for the organization details, one for the cluster
size SKUs, one page set for the database list, and one page set of branches per
database. A large organization produces many calls. Set `organization` or
`target_database` to narrow the run.

## Troubleshooting

### "No PlanetScale service token provided" Error

Both the token ID and the token are required. Set them in the config file under
`providers.planetscale.service_token_id` and
`providers.planetscale.service_token`, or set
`PLANETSCALE_SERVICE_TOKEN_ID` and `PLANETSCALE_SERVICE_TOKEN`.

### "Invalid or expired PlanetScale service token" Error

The API returned HTTP 401. Confirm the token ID and the token are a matching
pair and that the token still exists in
[Service tokens](https://app.planetscale.com/settings/service-tokens).

### "PlanetScale service token lacks the read_organization access" Error

The API returned HTTP 403 for the organization list. Grant
`read_organization`, or set `organization` in the config to skip the list call.

### "PlanetScale API rate limit reached" Warning

The API returned HTTP 429. Results are incomplete. Narrow the run with
`organization` or `target_database`, then re-run.

### No databases in the report

Confirm the organization holds Postgres databases. The provider analyzes
Postgres databases only, so an organization with no Postgres database produces
an empty report.

## Additional Resources

- [PlanetScale API reference](https://planetscale.com/docs/api/planetscale-api-oauth-applications)
- [PlanetScale OpenAPI specification](https://planetscale.com/docs/api/openapi-spec)
- [PlanetScale service tokens](https://planetscale.com/docs/cli/service-tokens)
- [Provider index](README.md)

## Support

Contact the PlanetScale Migration Engineering team for help with a discovery
run.
