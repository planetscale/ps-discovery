# PlanetScale Discovery Tools

Discovery tools for analyzing PostgreSQL and MySQL/Vitess database environments and cloud infrastructure. These tools provide a complete picture of your source environment including database configuration, schema structure, performance characteristics, and cloud infrastructure topology.

## Overview

The PlanetScale Discovery Tools consist of two main components:

1. **Database Discovery** - Comprehensive database environment analysis for PostgreSQL and MySQL/Vitess
2. **Cloud Discovery** - Multi-cloud database infrastructure analysis (AWS RDS/Aurora, GCP Cloud SQL/AlloyDB, Supabase, Heroku Postgres, Neon, PlanetScale Postgres)

Both tools can be used independently or together for a complete environment assessment.

## Quick Start

### 1. Extract and Run Setup

```bash
# Download and extract the release
tar -xzf ps-discovery-1.2.0.tar.gz
cd ps-discovery-1.2.0

# Run the interactive setup script
./setup.sh
```

The setup script will:
- Verify your Python version (3.10+ required)
- Create a virtual environment
- Install required dependencies (PostgreSQL support included by default)
- Prompt you to optionally install MySQL and cloud provider dependencies
- Generate a customized `config.yaml` based on your selections

### 2. Configure Your Credentials

Edit `config.yaml` with your database and cloud credentials:

```bash
nano config.yaml
```

**PostgreSQL configuration:**
- **Database section**: Set `host`, `port`, `database`, `username`, and `password`

**MySQL configuration (if installed):**
- **MySQL section**: Set `host`, `port`, `username`, and `password`. The `database` field can be left empty to discover all databases.

**Optional cloud configuration (if you installed cloud providers):**
- **AWS**: Configure your AWS profile or access keys and regions
- **GCP**: Set `project_id`, `service_account_key` path, and regions
- **Supabase**: Set your `access_token` from [app.supabase.com/account/tokens](https://app.supabase.com/account/tokens)
- **Heroku**: Set your `api_key` from [dashboard.heroku.com/account](https://dashboard.heroku.com/account) or set `HEROKU_API_KEY` env var
- **Neon**: Set your `api_key` from [console.neon.tech/app/settings/api-keys](https://console.neon.tech/app/settings/api-keys) or set `NEON_API_KEY` env var
- **PlanetScale**: Set `service_token_id` and `service_token` from [app.planetscale.com/settings/service-tokens](https://app.planetscale.com/settings/service-tokens) or set `PLANETSCALE_SERVICE_TOKEN_ID` and `PLANETSCALE_SERVICE_TOKEN` env vars

### 3. Run Discovery

`ps-discovery` reads everything it needs from your config file:

```bash
# Run using ./config.yaml in the current directory
./ps-discovery

# Or point at a specific config file
./ps-discovery --config config.yaml
```

No virtual environment activation required — the wrapper script handles it automatically.

### 4. Review Results

Results are saved to `./discovery_output/` by default:

- **`planetscale_discovery_results_<timestamp>.json`** - Complete structured data containing all discovery information

If you're working with the PlanetScale team, send the JSON report file to your point of contact. The JSON report does not contain any actual data from your database — only metadata about structure, configuration, and infrastructure.

## Database Engine Support

The discovery tool supports two database engines. Set the engine in your config file with the top-level `engine:` key (`postgres` or `mysql`). The `--engine` flag remains available as an explicit command-line override.

| Engine | Config / Flag | Analyzers | Documentation |
|--------|------|-----------|---------------|
| **PostgreSQL** (default) | `engine: postgres` / `--engine postgres` | config, schema, performance, security, features, data_size | See [PostgreSQL Privileges](#required-postgresql-privileges) below |
| **MySQL/Vitess** | `engine: mysql` / `--engine mysql` | config, schema, performance, replication, security, features | [MySQL Setup Guide](docs/mysql.md) |

### Quick Examples

```bash
# PostgreSQL (default engine — no flag needed)
ps-discovery database --host db.example.com -d mydb -u postgres -W

# MySQL
ps-discovery database --engine mysql --host db.example.com -u root -W

# MySQL on PlanetScale
ps-discovery database --engine mysql --host aws.connect.psdb.cloud -u username -W --ssl-mode required
```

## Required PostgreSQL Privileges

Create a dedicated discovery user with the necessary read-only privileges:

```sql
-- Create a dedicated user for database discovery
CREATE USER planetscale_discovery WITH PASSWORD 'secure_password_here';

-- Grant basic connection and usage permissions
GRANT CONNECT ON DATABASE your_database TO planetscale_discovery;
GRANT USAGE ON SCHEMA public TO planetscale_discovery;
GRANT USAGE ON SCHEMA information_schema TO planetscale_discovery;

-- Grant read access to all tables and views
GRANT SELECT ON ALL TABLES IN SCHEMA public TO planetscale_discovery;
GRANT SELECT ON ALL TABLES IN SCHEMA information_schema TO planetscale_discovery;
GRANT SELECT ON ALL TABLES IN SCHEMA pg_catalog TO planetscale_discovery;

-- Grant permissions for system catalogs and statistics
GRANT SELECT ON pg_stat_database TO planetscale_discovery;
GRANT SELECT ON pg_stat_user_tables TO planetscale_discovery;
GRANT SELECT ON pg_stat_user_indexes TO planetscale_discovery;
GRANT SELECT ON pg_stat_activity TO planetscale_discovery;
GRANT SELECT ON pg_stat_replication TO planetscale_discovery;
GRANT SELECT ON pg_settings TO planetscale_discovery;
GRANT SELECT ON pg_database TO planetscale_discovery;
GRANT SELECT ON pg_user TO planetscale_discovery;
GRANT SELECT ON pg_roles TO planetscale_discovery;
GRANT SELECT ON pg_user_mappings TO planetscale_discovery;

-- For foreign data wrapper analysis
GRANT SELECT ON pg_foreign_server TO planetscale_discovery;
GRANT SELECT ON pg_foreign_data_wrapper TO planetscale_discovery;

-- For advanced performance analysis. Skip this line if the extension is not
-- installed, or it will fail with "relation pg_stat_statements does not exist"
GRANT SELECT ON pg_stat_statements TO planetscale_discovery;

-- For replication analysis
GRANT SELECT ON pg_stat_wal_receiver TO planetscale_discovery;
GRANT SELECT ON pg_stat_subscription TO planetscale_discovery;

-- For PostgreSQL 10+ enhanced privileges (recommended)
GRANT pg_read_all_stats TO planetscale_discovery;
GRANT pg_read_all_settings TO planetscale_discovery;

-- Required for workload capture (see below)
GRANT pg_monitor TO planetscale_discovery;
```

Alternatively, you can use an existing superuser account for complete analysis.

Query workload capture needs `pg_monitor` and a readable `pg_stat_statements`.
It reads no user table, so it needs no `SELECT` on your data. See
[Workload Capture](docs/workload_capture.md#before-you-start) for a smaller
role and for how to enable `pg_stat_statements`.

## Required MySQL Privileges

For a detailed MySQL setup guide including PlanetScale/Vitess specifics, see [MySQL Setup Guide](docs/mysql.md).

The minimum privileges needed for MySQL discovery:

```sql
-- Create a dedicated discovery user
CREATE USER 'planetscale_discovery'@'%' IDENTIFIED BY 'secure_password_here';

-- Grant read-only access to metadata
GRANT SELECT ON *.* TO 'planetscale_discovery'@'%';
GRANT PROCESS ON *.* TO 'planetscale_discovery'@'%';
GRANT REPLICATION CLIENT ON *.* TO 'planetscale_discovery'@'%';
```

For PlanetScale databases, your existing database credentials are sufficient — the tool automatically detects and handles PlanetScale/Vitess environments.

## Configuration File Format

### PostgreSQL Configuration

```yaml
engine: postgres

database:
  host: localhost
  port: 5432
  database: mydb
  username: postgres
  password: secret
  ssl_mode: require

output:
  output_dir: ./reports
```

### MySQL Configuration

```yaml
engine: mysql

mysql:
  host: localhost
  port: 3306
  database: ""  # Leave empty to discover all databases
  username: root
  password: secret
  ssl_mode: disabled  # Options: disabled, preferred, required, verify-ca, verify-identity

output:
  output_dir: ./reports
```

### Combined Configuration (Database + Cloud)

```yaml
engine: postgres

database:
  host: localhost
  port: 5432
  database: mydb
  username: postgres
  password: secret

providers:
  aws:
    enabled: true
    regions:
      - us-east-1
  gcp:
    enabled: false
  supabase:
    enabled: false
  heroku:
    enabled: false
  neon:
    enabled: false
  planetscale:
    enabled: false

output:
  output_dir: ./reports
```

You can also generate a configuration template. The generated file includes the `engine:` key and is ready to run with `./ps-discovery --config config.yaml`:

```bash
# PostgreSQL config (default)
ps-discovery config-template --output config.yaml

# MySQL config
ps-discovery config-template --output config.yaml --engines mysql

# Both engines with cloud providers
ps-discovery config-template --output config.yaml --engines postgres,mysql --providers aws
```

## Cloud Provider Setup

| Provider | Resources Analyzed | Documentation |
|----------|-------------------|---------------|
| **AWS** | RDS instances, Aurora clusters, VPC networking | [AWS Setup Guide](docs/providers/aws.md) |
| **GCP** | Cloud SQL instances, AlloyDB clusters, VPC networks | [GCP Setup Guide](docs/providers/gcp.md) |
| **Supabase** | Managed PostgreSQL projects, connection pooling | [Supabase Setup Guide](docs/providers/supabase.md) |
| **Heroku** | Postgres add-ons, PgBouncer pooling, followers | [Heroku Setup Guide](docs/providers/heroku.md) |
| **Neon** | Serverless Postgres projects, branches, endpoints | [Neon Setup Guide](docs/providers/neon.md) |
| **PlanetScale** | Postgres databases, branches, cluster sizing (Postgres only) | [PlanetScale Setup Guide](docs/providers/planetscale.md) |

## Query Workload Capture (optional, for Neki sharding design)

Designing a sharding scheme for [PlanetScale Neki](https://neki.dev) needs the
query workload, not only the schema. This optional flow records
`pg_stat_statements` over a few days from cron. It writes the input files that a
sharding planner reads.

Nothing here runs during a normal discovery run, and nothing here writes into
the discovery output. `./setup.sh` installs what the flow needs, so there is
nothing extra to install.

```bash
# 1. Prepare a session directory and take the first snapshot
./ps-discovery workload init --session ./workload-session

# 2. Take one more snapshot. Run this repeatedly over a few days.
#    init prints a crontab line that runs it every hour.
./ps-discovery workload collect --session ./workload-session

# 3. Turn the snapshots into the bundle
./ps-discovery workload finalize --session ./workload-session
```

`--session` names a directory that this tool creates and owns. It holds the
snapshots and the captured schema. Give all four commands the same directory.
You can name it anything and put it anywhere. Delete it when you are done.
Nothing persists on the database server.

Each `collect` takes one snapshot and exits. It does no scheduling of its own.
Two snapshots are the minimum, because a window needs two readings to
difference. More snapshots, spread across a peak, give a better result.

Compress the bundle directory and send the archive to your PlanetScale migration
engineer, who uses it to plan the sharding scheme. The bundle holds a query log,
a schema file, table row counts, a `manifest.json` recording when the capture
ran, and a README summarizing what was collected.

`pg_stat_statements` is required: it is the only record of the query workload,
and that workload is what a sharding scheme is planned from. It is not a trusted
extension, so enabling it needs a superuser or the provider's admin role. The
capture role also needs `pg_monitor`. `workload init` checks both and stops with
exit code 5 when either is missing.

The flow reads catalogs and statistics views only. It reads no user table, never
runs `ANALYZE` and holds no table locks. Read
[Workload Capture](docs/workload_capture.md) before you enable it on a
production primary.

## Security & Data Privacy

This tool collects **metadata only** — never actual data from your tables.

**What is collected:** Schema metadata (table names, column types, constraints), database configuration (version, settings, extensions), usage statistics (table sizes, row counts, cache ratios), infrastructure topology (cloud resources, networking), and user/role names.

**What is NOT collected:** Table contents, customer records, literal values from queries, application code, or credentials. Passwords are used only for connection and are never stored in output.

**Query text.** Where the tool reports a statement — a long-running transaction, or the two sides of a lock — it records the statement with literal values replaced by `$N` placeholders, so `SELECT * FROM orders WHERE email = $1` rather than the address itself. Comments are removed. This captures the shape of a query rather than the data in it. Review the report before sharing it outside your organization.

All analysis runs locally — no data is sent to external services.

## Additional Documentation

- [MySQL Setup Guide](docs/mysql.md) - MySQL/Vitess-specific setup, privileges, and PlanetScale configuration
- [Output Format](docs/output-format.md) - JSON report structure and markdown summary details
- [Troubleshooting](docs/troubleshooting.md) - Python installation, common errors, managed database environments
- [Advanced Usage](docs/advanced-usage.md) - CLI reference, focused analysis, automation, pipx installation
- [Cleanup Procedures](docs/cleanup.md) - Removing discovery users and verifying cleanup
- [Data Size Analysis](docs/data_size_analysis.md) - Optional large column and LOB analysis
- [Performance Considerations](docs/performance_considerations.md) - Impact and timing guidance
- [Workload Capture](docs/workload_capture.md) - Optional query workload capture for Neki sharding design
- [Workload Bundle Format](docs/workload-bundle.md) - What the bundle contains and how to hand it over

## Support

If you encounter issues or have questions:

1. Check the [Troubleshooting Guide](docs/troubleshooting.md)
2. Review your database logs for connection or permission errors
3. Ensure all required privileges are granted to the discovery user
4. Contact your PlanetScale point of contact
