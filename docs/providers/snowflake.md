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

## Required Permissions

Inventory needs one privilege: `OPERATE` on each Postgres instance. Snowflake has no narrower privilege for `SHOW POSTGRES INSTANCES` / `DESCRIBE POSTGRES INSTANCE`, and both commands only return instances the role holds `OPERATE` or `OWNERSHIP` on. Read replicas do not inherit grants from their primary, so grant every instance.

Create the role once as `SECURITYADMIN`, or have `USERADMIN` create the role and the role that owns the instances grant `OPERATE`. `ACCOUNTADMIN` is not needed.

```sql
USE ROLE SECURITYADMIN;
CREATE ROLE IF NOT EXISTS PS_DISCOVERY;
GRANT OPERATE ON POSTGRES INSTANCE "your_primary_instance" TO ROLE PS_DISCOVERY;
GRANT OPERATE ON POSTGRES INSTANCE "your_replica_instance" TO ROLE PS_DISCOVERY;
```

Snowflake has no `GRANT ... ON ALL` or `ON FUTURE` form for Postgres instances, so grant each one by name. `SECURITYADMIN` sees every instance in the account, so list them first with `SHOW POSTGRES INSTANCES;`. With a warehouse active, this prints one `GRANT` per instance:

```sql
SHOW POSTGRES INSTANCES
  ->> SELECT 'GRANT OPERATE ON POSTGRES INSTANCE "' || "name" || '" TO ROLE PS_DISCOVERY;' FROM $1;
```

Instances created after the grant are not covered. Any instance without a grant is missing from the report.

The next section grants this role to the user that runs discovery.

`OPERATE` also allows suspend, resume, and Postgres setting changes. The inventory queries are `SHOW POSTGRES INSTANCES` and `DESCRIBE POSTGRES INSTANCE`. After connect, the tool also runs `USE SECONDARY ROLES NONE` (unless `use_secondary_roles: true`) and `SELECT CURRENT_ACCOUNT()`, `CURRENT_ACCOUNT_NAME()`, `CURRENT_ORGANIZATION_NAME()`, `CURRENT_REGION()`, `CURRENT_ROLE()`, and `CURRENT_SECONDARY_ROLES()` for the account block. Drop the role once the report is produced:

```sql
USE ROLE SECURITYADMIN;
DROP ROLE IF EXISTS PS_DISCOVERY;
```

The tool runs `USE SECONDARY ROLES NONE` after connecting, so a user with `DEFAULT_SECONDARY_ROLES = ('ALL')` still sees only what `PS_DISCOVERY` can see. The tool runs this only when a role is set, in `role:` or `SNOWFLAKE_ROLE`. Set `use_secondary_roles: true` to keep secondary roles active.

Do not grant `ACCOUNTADMIN` or `SNOWFLAKE` database roles for this inventory. The tool does not create, alter, or drop Snowflake objects.

## Authentication Options

Account inventory uses Snowflake **account** credentials. These are not the Postgres instance password.

Snowflake is [retiring single-factor password sign-in](https://docs.snowflake.com/en/user-guide/security-mfa-rollout): service users cannot use passwords, and human users need MFA with one. Use key pair, or SSO if your account already has a SAML identity provider.

### Option 1: Key pair on a service user (recommended)

A dedicated `TYPE = SERVICE` user that holds only `PS_DISCOVERY`. It has no password, cannot use SSO or MFA, and is not affected by the password deprecation.

Generate an encrypted key on the machine that runs discovery:

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -v2 des3 -inform PEM -out rsa_key.p8
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
chmod 600 rsa_key.p8
```

The first command asks you to set a passphrase for the private key. The `openssl rsa` command asks for that passphrase again to read the key.

Create the user as `SECURITYADMIN`, after you create the `PS_DISCOVERY` role in [Required Permissions](#required-permissions). Paste the body of `rsa_key.pub` without the `BEGIN` / `END` lines:

```sql
USE ROLE SECURITYADMIN;
CREATE USER PS_DISCOVERY_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = PS_DISCOVERY
  DEFAULT_SECONDARY_ROLES = ()
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqh...';
GRANT ROLE PS_DISCOVERY TO USER PS_DISCOVERY_SVC;
```

Keep the private key outside the config file. Point `private_key_path` at it, or set `SNOWFLAKE_PRIVATE_KEY_PATH`. The passphrase is read from `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`:

```bash
export SNOWFLAKE_PRIVATE_KEY_PATH=$PWD/rsa_key.p8
read -s -p "Key passphrase: " SNOWFLAKE_PRIVATE_KEY_PASSPHRASE && export SNOWFLAKE_PRIVATE_KEY_PASSPHRASE
```

```yaml
providers:
  snowflake:
    enabled: true
    # Or set SNOWFLAKE_ACCOUNT
    account: ""
    user: PS_DISCOVERY_SVC
    role: PS_DISCOVERY
    authentication: key_pair
    # Or set SNOWFLAKE_PRIVATE_KEY_PATH
    private_key_path: ""
    discover_all: true
```

After the run, drop the user and delete the key:

```sql
USE ROLE SECURITYADMIN;
DROP USER IF EXISTS PS_DISCOVERY_SVC;
```

### Option 2: SSO

```yaml
authentication: sso
```

This uses Snowflake's `externalbrowser` authenticator: a browser opens and the person running discovery signs in through the account's identity provider (Okta, Entra ID, and so on). Nothing is stored, so it fits a one-off run by a person. It only works if the account has SAML SSO set up, needs a browser, and does not work for `TYPE = SERVICE` users.

Grant the role to the existing user that signs in:

```sql
USE ROLE SECURITYADMIN;
GRANT ROLE PS_DISCOVERY TO USER your_user;
```

### Option 3: Password

Use key pair on a service user, or `authentication: sso`. Snowflake is retiring password-only sign-in. If the account still allows a password, grant `PS_DISCOVERY` to the user as in [Option 2](#option-2-sso), then set `SNOWFLAKE_PASSWORD`. Do not put the password in the config file. `read -s` keeps it out of your shell history:

```bash
export SNOWFLAKE_ACCOUNT=your-account-locator
export SNOWFLAKE_USER=planetscale_discovery
read -s -p "Snowflake password: " SNOWFLAKE_PASSWORD && export SNOWFLAKE_PASSWORD
```

```yaml
providers:
  snowflake:
    enabled: true
    # Or set SNOWFLAKE_ACCOUNT
    account: ""
    # Or set SNOWFLAKE_USER
    user: ""
    role: PS_DISCOVERY
    authentication: password
    discover_all: true
```

## Database discovery (optional)

The `database:` block is separate from account inventory. It uses a read-only Postgres user on one Snowflake Postgres hostname instead of `snowflake_admin`. Connect as `snowflake_admin` once to create it:

```sql
CREATE ROLE planetscale_discovery LOGIN PASSWORD 'choose-a-strong-password';
GRANT CONNECT ON DATABASE your_database TO planetscale_discovery;
GRANT pg_read_all_stats, pg_read_all_settings, pg_monitor TO planetscale_discovery;
```

This login cannot read table rows. Discovery still reports the same tables, indexes, extensions, and settings as `snowflake_admin`. The one difference is per-column `n_distinct` / `correlation`, which Postgres only shows for tables the user can `SELECT`. Drop the login after the run:

```sql
REVOKE pg_read_all_stats, pg_read_all_settings, pg_monitor FROM planetscale_discovery;
REVOKE CONNECT ON DATABASE your_database FROM planetscale_discovery;
DROP ROLE planetscale_discovery;
```

Leave the password out of the config file and pass `-W` to be prompted for it. `PGPASSWORD` is not read when a config file is used.

```yaml
database:
  host: your-instance.your-org.us-east-1.aws.postgres.snowflake.app
  port: 5432
  database: your_database
  username: planetscale_discovery
  ssl_mode: require
```

```bash
ps-discovery --config config.yaml both -W
```

Hostname shape:

```text
{id}.{account}.{region}.{aws|azure}.postgres.snowflake.app
```

SSL is required. Azure uses `azure` in place of `aws`. Snowflake does not currently advertise Postgres on GCP. A `gcp` token in a hostname is parsed if one appears.

## Configuration Examples

### Complete configuration

```yaml
engine: postgres

database:
  host: your-instance.your-org.us-east-1.aws.postgres.snowflake.app
  port: 5432
  database: your_database
  username: planetscale_discovery
  ssl_mode: require  # password comes from the -W prompt
  connection_timeout: 30
  statement_timeout: 300s

providers:
  snowflake:
    enabled: true
    # Or set SNOWFLAKE_ACCOUNT
    account: ""
    user: PS_DISCOVERY_SVC
    role: PS_DISCOVERY
    authentication: key_pair
    # Or set SNOWFLAKE_PRIVATE_KEY_PATH
    private_key_path: ""
    discover_all: true
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

Generate a starting file with:

```bash
ps-discovery config-template --output snowflake-config.yaml --providers snowflake
```

You may enable Snowflake alongside other providers only when you are assessing **separate** fleets in one run (for example RDS elsewhere plus Snowflake Postgres). Never point `providers.aws` or `providers.gcp` at the Snowflake-hosted instances themselves.

### Environment variables

```bash
export SNOWFLAKE_ACCOUNT=your-account-locator
export SNOWFLAKE_USER=PS_DISCOVERY_SVC
export SNOWFLAKE_ROLE=PS_DISCOVERY
export SNOWFLAKE_PRIVATE_KEY_PATH=$HOME/.ssh/snowflake_discovery.p8
read -s -p "Key passphrase: " SNOWFLAKE_PRIVATE_KEY_PASSPHRASE && export SNOWFLAKE_PRIVATE_KEY_PASSPHRASE
# When there is no YAML authentication: field (env-only load), key_pair is the default.
# export SNOWFLAKE_AUTHENTICATION=sso
```

### CLI overrides

```bash
ps-discovery cloud --providers snowflake \
  --snowflake-account your-account-locator \
  --snowflake-user PS_DISCOVERY_SVC \
  --snowflake-role PS_DISCOVERY \
  --snowflake-private-key-path "$HOME/.ssh/snowflake_discovery.p8"
```

## Data Collected

### Instance inventory

Each SHOW row is one instance. DESCRIBE returns `property` / `value` rows for that instance, and those are merged onto the SHOW row. `origin` is copied from the replica row when Snowflake returns it.

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
| `network_policy` | Attached network policy name. Only visible to the owner role |
| `network_policy_redacted` | `true` when Snowflake redacted the policy name for this role |
| `privatelink_service_identifier` | Private Link service id, when configured |
| `postgres_settings` | Custom Postgres settings set on the instance |
| `maintenance_window_start`, `instance_protection`, `retention_time` | Instance settings when Snowflake returns them |
| `pending_operations` | In-flight operations, only when non-empty |
| `created_on`, `comment` | Instance metadata |

The report also has an `account` block: account locator and name, organization, account region (`AWS_US_EAST_1` split into `csp` / `region`), the effective role, and its secondary roles.

Use `name` for the instance display name. Use `host` for the connection endpoint. The first label of the hostname is not the instance name.

### What is not collected

- Table contents or row data
- Application code
- Passwords, password hashes, private keys, or other credentials (used for the connection, never written to the report). Postgres roles appear only as names with a `has_password` flag
- Column values. Column statistics are limited to `n_distinct` and `correlation`

The report does include hostnames, instance names, the account locator, Postgres role names, and table, column, and index names.

`postgres_settings` is the instance's custom settings map. Keys whose names contain `password`, `secret`, `key`, or `token` are dropped before the report is written.

## Running Discovery

```bash
pip install "ps-discovery[snowflake]"
# or, from a checkout:
# ./setup.sh   # select Snowflake Postgres

# Database + account inventory. -W prompts for the Postgres password.
ps-discovery --config config.yaml both -W

# Account inventory only (no Postgres password needed)
ps-discovery --config config.yaml cloud --providers snowflake

# Database only
ps-discovery --config config.yaml database -W
```

Send PlanetScale only the JSON report. Never send the config file or any password or key.

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
2. For key pair: `Failed to load Snowflake private key` means a wrong path or passphrase. `JWT token is invalid` means the public key on the user does not match; check `DESCRIBE USER PS_DISCOVERY_SVC` shows an `RSA_PUBLIC_KEY_FP`.
3. For password: an error mentioning MFA or a blocked password means Snowflake's password-only sign-in retirement applies to the user. Switch to key pair or SSO.
4. Confirm with a successful run:

   ```bash
   ps-discovery cloud --providers snowflake --config config.yaml
   ```

To check what the role holds, run `SHOW GRANTS TO ROLE PS_DISCOVERY;` and confirm `PS_DISCOVERY` is granted to the user in `SHOW GRANTS TO USER your_user;`.

### "Authentication failed" / SSL errors (Postgres `database:` block)

**Problem:** The Postgres analyzer could not connect to the instance hostname.

**Solution:**

1. Confirm hostname, port `5432`, and `ssl_mode: require`.
2. Confirm network policy allows the discovery client.
3. Confirm the Postgres user password, and that the user has `CONNECT` on the database plus `pg_read_all_stats`, `pg_read_all_settings`, and `pg_monitor`.
4. Put `-W` after the subcommand: `ps-discovery --config config.yaml both -W`. Without `-W` the tool connects with no password.

### "No instances found"

**Problem:** Account inventory completed but reported zero instances.

**Solution:**

1. Confirm Snowflake Postgres instances exist in the account.
2. Confirm the configured role has `OPERATE` on every instance, read replicas included. Snowflake hides instances the role cannot operate.
3. Re-run `ps-discovery cloud --providers snowflake` after the role grant.
4. Try `discover_all: true`, or list instance names under `resources.postgres_instances`.
   `discover_all: false` with an empty list inventories nothing and logs a warning.

`SHOW GRANTS TO ROLE PS_DISCOVERY;` should list `OPERATE` on every Postgres instance. Any instance missing from that list is missing from the report.

### Wrong provider enabled

If `providers.aws` was enabled because the host contains `aws.postgres.snowflake.app`, disable AWS and use `providers.snowflake`. AWS RDS APIs will not inventory Snowflake Postgres instances.

## Limitations

- **SSO is interactive.** `authentication: sso` opens a browser and needs SAML SSO on the account. Unattended runs should use key pair.
- **Password-only sign-in is being retired.** Snowflake blocks passwords for service users and requires MFA for human users. The tool does not send an MFA passcode. If the account still allows a password, set `SNOWFLAKE_PASSWORD`. Do not put the password in the config file.
- **DESCRIBE is a property/value listing.** Those rows are merged onto the SHOW row. A SHOW-shaped DESCRIBE row is also accepted. Any other shape is skipped, the SHOW row is still emitted, and the miss is recorded as a warning.
- **Partial grants.** Snowflake hides instances the role lacks `OPERATE` on, including from a primary's replica list. Grant every instance.
- Private-link-only accounts and organization-level listing across accounts are not implemented. Each run inventories one Snowflake account.

## Query Workload Capture (optional)

Query workload capture uses the Postgres `database:` connection, not the Snowflake account inventory. Enable it only when a PlanetScale engineer asks for it. See [Workload Capture](../workload_capture.md).

## Additional Resources

- [PlanetScale Discovery Tool](https://planetscale.com/docs/postgres/imports/discovery-tool)
- Peer provider pages: [AWS](aws.md), [GCP](gcp.md), [Neon](neon.md), [Heroku](heroku.md), [Supabase](supabase.md), [PlanetScale](planetscale.md)

## Support

For issues with the discovery tool:

- Report bugs: https://github.com/planetscale/ps-discovery/issues
- Documentation: See the main [README.md](../../README.md)
