# PlanetScale Discovery Tools

Discovery tools for analyzing PostgreSQL databases and cloud infrastructure environments. These tools provide a complete picture of your source environment including database configuration, schema structure, performance characteristics, and cloud infrastructure topology.

## Overview

The PlanetScale Discovery Tools consist of two main components:

1. **Database Discovery** - Comprehensive PostgreSQL environment analysis
2. **Cloud Discovery** - Multi-cloud database infrastructure analysis (AWS RDS/Aurora, GCP Cloud SQL/AlloyDB, Supabase, Heroku Postgres)

Both tools can be used independently or together for a complete environment assessment.

## Quick Start

### 1. Extract and Run Setup

```bash
# Download and extract the release
tar -xzf ps-discovery-1.1.0.tar.gz
cd ps-discovery-1.1.0

# Run the interactive setup script
./setup.sh
```

The setup script will:
- Verify your Python version (3.9+ required)
- Create a virtual environment
- Install required dependencies
- Prompt you to optionally install cloud provider dependencies (AWS, GCP, Supabase, Heroku)
- Generate a customized `sample-config.yaml` based on your selections

### 2. Configure Your Credentials

Edit `sample-config.yaml` with your database and cloud credentials:

```bash
nano sample-config.yaml
```

**Required configuration:**
- **Database section**: Set `host`, `port`, `database`, `username`, and `password`

**Optional configuration (if you installed cloud providers):**
- **AWS**: Configure your AWS profile or access keys and regions
- **GCP**: Set `project_id`, `service_account_key` path, and regions
- **Supabase**: Set your `access_token` from [app.supabase.com/account/tokens](https://app.supabase.com/account/tokens)
- **Heroku**: Set your `api_key` from [dashboard.heroku.com/account](https://dashboard.heroku.com/account) or set `HEROKU_API_KEY` env var

### 3. Run Discovery

```bash
# Run database discovery only
./ps-discovery database --config sample-config.yaml

# OR run both database and cloud discovery
./ps-discovery both --config sample-config.yaml
```

No virtual environment activation required — the wrapper script handles it automatically.

### 4. Review Results

Results are saved to `./discovery_output/` by default:

- **`planetscale_discovery_results.json`** - Complete structured data containing all discovery information

If you're working with the PlanetScale team, send the JSON report file to your point of contact. The JSON report does not contain any actual data from your database — only metadata about structure, configuration, and infrastructure.

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

-- For advanced performance analysis (if pg_stat_statements is enabled)
GRANT SELECT ON pg_stat_statements TO planetscale_discovery;

-- For replication analysis
GRANT SELECT ON pg_stat_wal_receiver TO planetscale_discovery;
GRANT SELECT ON pg_stat_subscription TO planetscale_discovery;

-- For PostgreSQL 10+ enhanced privileges (recommended)
GRANT pg_read_all_stats TO planetscale_discovery;
GRANT pg_read_all_settings TO planetscale_discovery;
```

Alternatively, you can use an existing superuser account for complete analysis.

## Configuration File Format

```yaml
modules:
  - database
  - cloud

database:
  host: localhost
  port: 5432
  database: mydb
  username: postgres
  password: secret
  sslmode: require

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

output:
  output_dir: ./reports
```

You can also generate a configuration template: `ps-discovery config-template --output config.yaml`

## Cloud Provider Setup

| Provider | Resources Analyzed | Documentation |
|----------|-------------------|---------------|
| **AWS** | RDS instances, Aurora clusters, VPC networking | [AWS Setup Guide](docs/providers/aws.md) |
| **GCP** | Cloud SQL instances, AlloyDB clusters, VPC networks | [GCP Setup Guide](docs/providers/gcp.md) |
| **Supabase** | Managed PostgreSQL projects, connection pooling | [Supabase Setup Guide](docs/providers/supabase.md) |
| **Heroku** | Postgres add-ons, PgBouncer pooling, followers | [Heroku Setup Guide](docs/providers/heroku.md) |

## Security & Data Privacy

This tool collects **metadata only** — never actual data from your tables.

**What is collected:** Schema metadata (table names, column types, constraints), database configuration (version, settings, extensions), usage statistics (table sizes, row counts, cache ratios), infrastructure topology (cloud resources, networking), and user/role names.

**What is NOT collected:** Table contents, customer records, SQL queries, application code, or credentials. Passwords are used only for connection and are never stored in output.

All analysis runs locally — no data is sent to external services.

## Additional Documentation

- [Output Format](docs/output-format.md) - JSON report structure and markdown summary details
- [Troubleshooting](docs/troubleshooting.md) - Python installation, common errors, managed database environments
- [Advanced Usage](docs/advanced-usage.md) - CLI reference, focused analysis, automation, pipx installation
- [Cleanup Procedures](docs/cleanup.md) - Removing discovery users and verifying cleanup
- [Data Size Analysis](docs/data_size_analysis.md) - Optional large column and LOB analysis
- [Performance Considerations](docs/performance_considerations.md) - Impact and timing guidance

## Support

If you encounter issues or have questions:

1. Check the [Troubleshooting Guide](docs/troubleshooting.md)
2. Review the PostgreSQL logs for connection or permission errors
3. Ensure all required privileges are granted to the discovery user
4. Contact your PlanetScale point of contact
