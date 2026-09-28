# Snowflake Postgres Cloud Discovery Setup

## Overview

The PlanetScale Discovery CLI inventories Snowflake Postgres instances and, when you also configure a database connection, analyzes the PostgreSQL catalogs they host.

The product path is the same as AWS RDS/Aurora, GCP Cloud SQL/AlloyDB, and Neon:

1. Install the extra: `pip install "ps-discovery[snowflake]"`
2. Enable `providers.snowflake` and set account authentication
3. Run `ps-discovery`

You do not need the Snowflake CLI or Snowsight to produce a report. The CLI authenticates to the Snowflake account and collects instance metadata itself.

Do **not** enable `providers.aws` or `providers.gcp` because a Snowflake hostname contains `aws` or `azure`. Those tokens are location labels. The inventory provider is Snowflake.

Snowflake discovery has two inputs you can use together or separately:

1. **Account inventory** — `providers.snowflake` lists Snowflake Postgres instances. Results land under `cloud_results.providers.snowflake`.
2. **Database discovery** — the existing Postgres analyzer uses the `database:` block. It never reads table row contents.

## Prerequisites

- A Snowflake account with one or more Snowflake Postgres instances
- A Snowflake user and a least-privilege role with the inventory permissions below
- Network access from the machine running discovery to the Snowflake account endpoint
- Python package: `pip install "ps-discovery[snowflake]"` (or `./setup.sh` and select Snowflake)

For catalog-level database discovery you also need a read-only Postgres user and network access to the instance hostname on port `5432` (SSL required). See [Database discovery](#database-discovery-optional).

## Authentication Options

Account inventory uses Snowflake **account** credentials. These are not the Postgres instance password.

### Option 1: Key pair (recommended)

Keep the private key outside the config file. Point `private_key_path` at a local path, or set `SNOWFLAKE_PRIVATE_KEY_PATH`. An optional passphrase is read from `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`.

```yaml
providers:
  snowflake:
    enabled: true
    account: ${SNOWFLAKE_ACCOUNT}
    user: ${SNOWFLAKE_USER}
    role: DISCOVERY_READONLY
    authentication: key_pair
    private_key_path: ${SNOWFLAKE_PRIVATE_KEY_PATH}
    discover_all: true
    account_inventory: true
```

### Option 2: Password (environment variable)

Supply the Snowflake account password through `SNOWFLAKE_PASSWORD`. Do not commit it as a YAML literal.

```bash
export SNOWFLAKE_ACCOUNT=your-account-locator
export SNOWFLAKE_USER=planetscale_discovery
export SNOWFLAKE_PASSWORD='use-a-local-secret'
```

```yaml
providers:
  snowflake:
    enabled: true
    account: ${SNOWFLAKE_ACCOUNT}
    user: ${SNOWFLAKE_USER}
    role: DISCOVERY_READONLY
    authentication: password
    discover_all: true
    account_inventory: true
```

`${VAR}` placeholders in YAML are treated as unset. The tool then reads the matching environment variable.

### Option 3: SSO (interactive only)

```yaml
authentication: sso
```

This uses Snowflake's `externalbrowser` authenticator. It needs an operator session and is not a fit for unattended discovery. Prefer key pair.

## Required Permissions

Grant a dedicated discovery role only the account privileges needed to inventory Postgres instances. These are permissions the role needs so the CLI can run, the same way AWS discovery needs `rds:DescribeDBInstances`. You do not open Snowsight or the Snowflake CLI and paste those statements as the discovery path.

| Privilege the role needs | Used for |
| --- | --- |
| `SHOW POSTGRES INSTANCES` | List Snowflake Postgres instances visible to the role |
| `DESCRIBE POSTGRES INSTANCE` | Collect details for each instance in scope |

Do not grant account-admin or broad warehouse or compute privileges for discovery. A warehouse is optional. Current inventory does not require one.

The tool only reads instance metadata. It does not create, alter, or drop Snowflake objects.

## Database discovery (optional)

The `database:` block is separate from account inventory. It uses a read-only Postgres user on one Snowflake Postgres hostname. Create that user with the grants in the [README](../../README.md) PostgreSQL privilege section. Do not grant superuser access.

```yaml
database:
  host: your-instance.your-org.us-east-1.aws.postgres.snowflake.app
  port: 5432
  database: your_database
  username: planetscale_discovery
  password: ${DISCOVERY_DB_PASSWORD}
  ssl_mode: require
```

Hostname shape:

```text
{id}.{account}.{region}.{aws|azure}.postgres.snowflake.app
```

SSL is required. Azure uses `azure` in place of `aws`. Snowflake does not currently advertise Postgres on GCP. The analyzer will parse a `gcp` token if one appears in a hostname, but do not invent a GCP Snowflake hostname.

## Configuration Examples

### Complete configuration

```yaml
engine: postgres

database:
  host: your-instance.your-org.us-east-1.aws.postgres.snowflake.app
  port: 5432
  database: your_database
  username: planetscale_discovery
  password: ${DISCOVERY_DB_PASSWORD}
  ssl_mode: require
  connection_timeout: 30
  statement_timeout: 300s

providers:
  snowflake:
    enabled: true
    account: ${SNOWFLAKE_ACCOUNT}
    user: ${SNOWFLAKE_USER}
    role: DISCOVERY_READONLY
    authentication: key_pair
    private_key_path: ${SNOWFLAKE_PRIVATE_KEY_PATH}
    discover_all: true
    account_inventory: true
    # Optional focused inventory when discover_all is false:
    # resources:
    #   postgres_instances:
    #     - your_primary_instance
    #     - your_replica_instance

  aws:
    enabled: false
  gcp:
    enabled: false

output:
  output_dir: ./snowflake_discovery_output

log_level: INFO
```

A sample file lives at `config-examples/config.snowflake.sample.yaml`. Generate a starting file with:

```bash
ps-discovery config-template --output snowflake-config.yaml --providers snowflake
```

You may enable Snowflake alongside other providers only when you are assessing **separate** fleets in one run (for example RDS elsewhere plus Snowflake Postgres). Never point `providers.aws` or `providers.gcp` at the Snowflake-hosted instances themselves.

### Environment variables

```bash
export SNOWFLAKE_ACCOUNT=your-account-locator
export SNOWFLAKE_USER=planetscale_discovery
export SNOWFLAKE_PRIVATE_KEY_PATH=$HOME/.ssh/snowflake_discovery.p8
# or, for password auth:
# export SNOWFLAKE_PASSWORD=your-local-secret
```

### CLI overrides

```bash
ps-discovery cloud --providers snowflake \
  --snowflake-account your-account-locator \
  --snowflake-user planetscale_discovery \
  --snowflake-role DISCOVERY_READONLY \
  --snowflake-private-key-path "$HOME/.ssh/snowflake_discovery.p8"
```

## Data Collected

### Instance inventory

Each SHOW/DESCRIBE row is one instance. The tool does not build a cluster, a shared HA topology, or a replica list on the primary.

| Field | Meaning |
| --- | --- |
| `name` | SHOW instance name (`your_primary_instance`). Use this as the display name. |
| `type` | `PRIMARY`, or `READ_REPLICA` when Snowflake returns `REPLICA` |
| `host` | Connection hostname. Use this as the endpoint. |
| `compute_family` | Instance size / compute family when Snowflake returns it (for example `STANDARD_M`) |
| `storage_size_gb` | Allocated storage when Snowflake returns it |
| `is_ha` | High availability flag when Snowflake returns it |
| `state` | Lifecycle state (`READY`, `CREATING`, …) |
| `origin` | Snowflake `origin` on that replica row only |
| `postgres_version` | PostgreSQL version when Snowflake returns it |
| `csp` / `region` | From SHOW/DESCRIBE when present. Otherwise parsed from the hostname |

Use `name` for the instance display name. Use `host` for the connection endpoint. Do not use the first label of the hostname as the name.

The tool never invents a SKU from Postgres settings such as `shared_buffers`, and never adds CPU, IOPS, or other metrics that Snowflake did not return on that row.

### What is not collected

- Table contents or row data
- Application code
- Passwords, private keys, or other credentials (used for the connection, never written to the report)
- Metrics, SKUs, or replica coupling that Snowflake did not return on that inventory row

## Running Discovery

```bash
pip install "ps-discovery[snowflake]"
# or, from a checkout:
# ./setup.sh   # select Snowflake Postgres

ps-discovery --config config.yaml
ps-discovery both --config config.yaml
ps-discovery cloud --providers snowflake --config config.yaml

# Database-only (no account inventory)
ps-discovery database --config config.yaml
```

Reports write under `output.output_dir`:

1. **JSON report** — `planetscale_discovery_results_<UTC-timestamp>.json`
2. **Optional local Markdown summary** — with `--local-summary` (stays on the machine)

Inventory path inside the JSON:

```text
cloud_results.providers.snowflake.resources[<region>].instances[]
```

## Troubleshooting

### "Snowflake libraries not installed"

Install the extra: `pip install "ps-discovery[snowflake]"` or re-run `./setup.sh` and select Snowflake.

### "Authentication failed" (Snowflake account)

**Problem:** The CLI could not connect to the Snowflake account.

**Solution:**

1. Verify account locator, user, and role.
2. Confirm the key-pair path and passphrase, or that `SNOWFLAKE_PASSWORD` is set for password auth.
3. Confirm with a successful run:

   ```bash
   ps-discovery cloud --providers snowflake --config config.yaml
   ```

If an administrator needs to confirm the role can list instances, that is an optional admin check. It is not the customer discovery path. Prefer the CLI run above.

### "Authentication failed" / SSL errors (Postgres `database:` block)

**Problem:** The Postgres analyzer could not connect to the instance hostname.

**Solution:**

1. Confirm hostname, port `5432`, and `ssl_mode: require`.
2. Confirm network policy allows the discovery client.
3. Confirm the Postgres user password and `CONNECT` / schema grants.

### "No instances found"

**Problem:** Account inventory completed but reported zero instances.

**Solution:**

1. Confirm Snowflake Postgres instances exist in the account.
2. Confirm the configured role is the one granted inventory privileges.
3. Re-run `ps-discovery cloud --providers snowflake` after the role grant.
4. Try `discover_all: true`, or list instance names under `resources.postgres_instances`.

If an administrator needs to confirm the role can see instances, that is an optional admin check. It is not the customer discovery path.

### Wrong provider enabled

If `providers.aws` was enabled because the host contains `aws.postgres.snowflake.app`, disable AWS and use `providers.snowflake`. AWS RDS APIs will not inventory Snowflake Postgres instances.

## Limitations

- **SSO is interactive.** `authentication: sso` opens a browser. Unattended runs should use key pair or password-from-env.
- **DESCRIBE column names can vary** by Snowflake release. The analyzer accepts SHOW-shaped rows and property/value listings. If a release uses a third shape, the SHOW row is still emitted and the DESCRIBE miss is recorded as a warning.
- **Warehouse is unused** for current inventory. Set `warehouse:` only if your account requires one.
- **No invented metrics.** Inventory is per-row only. The report does not assume replicas share monitoring or HA topology beyond the `origin` column Snowflake put on that replica row.
- A warehouse-backed SQL session, private-link-only accounts, and organization-level listing beyond the account `SHOW POSTGRES INSTANCES` privilege are not implemented.

## Query Workload Capture (optional)

Query workload capture uses the Postgres `database:` connection, not the Snowflake account inventory. Enable it only when a PlanetScale engineer asks for it. See [Workload Capture](../workload_capture.md).

## Additional Resources

- [PlanetScale Discovery Tool](https://planetscale.com/docs/postgres/imports/discovery-tool)
- [Migrate from Snowflake Postgres](https://planetscale.com/docs/postgres/imports/snowflake)
- Peer provider pages: [AWS](aws.md), [GCP](gcp.md), [Neon](neon.md), [Heroku](heroku.md), [Supabase](supabase.md), [PlanetScale](planetscale.md)

## Support

For issues with the discovery tool:

- Report bugs: https://github.com/planetscale/ps-discovery/issues
- Documentation: See the main [README.md](../../README.md)
