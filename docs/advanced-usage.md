# Advanced Usage

## Advanced Installation Options

For users who want more control over the installation:

```bash
# Clone the repository (for development)
git clone <repository-url>
cd planetscale-discovery-cli-dev

# Option 1: Install with pipx (isolates dependencies)
pipx install -e ".[mysql]"     # Add MySQL support
pipx install -e ".[aws]"       # AWS cloud discovery support
pipx install -e ".[gcp]"       # GCP support
pipx install -e ".[supabase]"  # Supabase support
pipx install -e ".[heroku]"    # Heroku support
pipx install -e ".[neon]"      # Neon support
pipx install -e ".[all]"       # All engines and cloud providers
pipx install -e .              # PostgreSQL database discovery only

# If you don't have pipx:
brew install pipx  # macOS
pipx ensurepath

# Option 2: Manual virtual environment setup
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e ".[all]"   # Install with all optional dependencies
```

## Focused Database Analysis

For analysis that focuses on a specific target database:

```bash
# Focus analysis on a specific database identifier
ps-discovery --config config.yaml --target-database my-rds-instance
```

This **focused approach**:
- Analyzes only the specified target database and its related infrastructure
- Captures complete networking stack (VPC, subnets, security groups, NACLs, route tables)
- Includes comprehensive database operational details (backup/maintenance windows)
- Limits data exposure to only infrastructure related to the target database

## CLI Reference

Running `ps-discovery` with no subcommand auto-discovers `./config.yaml` in the current directory (or the file given by `--config`) and runs whatever the config declares. The `database`, `cloud`, and `both` subcommands and the `--engine` flag are also available to override the config from the command line.

### Selecting What Runs

By default `ps-discovery` decides what to run from the contents of your config. A configured database block runs database discovery, and any cloud provider with `enabled: true` runs cloud discovery. You normally do not need to set anything to control this.

To restrict a run, add an optional top-level `modules:` list containing `database`, `cloud`, or both. Its main use is forcing a cloud-only scan when the same config also has a database configured (or, conversely, a database-only scan when cloud providers are enabled):

```yaml
# Only run cloud discovery, even though a database is configured
modules:
  - cloud
```

The `database`, `cloud`, and `both` subcommands do the same thing from the command line for a one-off run, without editing your config.

### Engine Selection

| Flag | Description | Default |
|------|-------------|---------|
| `--engine` | Override the config's database engine (`postgres` or `mysql`) | from config (`postgres` if unset) |

The engine is normally set in the config file with the top-level `engine:` key. The `--engine` flag overrides it and is available on the `database` and `both` subcommands. PostgreSQL is the default when neither is specified.

### Connection Parameters

These flags apply to both PostgreSQL and MySQL. The values are routed to the appropriate engine configuration based on the `--engine` flag.

| Flag | Description | Default (PG) | Default (MySQL) |
|------|-------------|:---:|:---:|
| `--host` | Database server host | `localhost` | `localhost` |
| `-p, --port` | Database server port | `5432` | `3306` |
| `-d, --database` | Database name | (required) | (optional — empty discovers all) |
| `-u, --username` | Database username | | `root` |
| `-W, --password` | Prompt for password | | |
| `--ssl-mode` | SSL connection mode | `require` | `disabled` |
| `--config` | YAML configuration file | `./config.yaml` | |

### Analysis Options

| Flag | Description |
|------|-------------|
| `--engine` | Database engine: `postgres` (default) or `mysql` |
| `--analyzers` | Comma-separated list of analyzers to run (see engine-specific lists below) |
| `--target-database` | Focus cloud analysis on a specific database identifier |

**PostgreSQL analyzers:** `config`, `schema`, `performance`, `security`, `features`, `data_size`

**MySQL analyzers:** `config`, `schema`, `performance`, `replication`, `security`, `features`

### Output Options

| Flag | Description | Default |
|------|-------------|---------|
| `--output-dir` | Output directory for reports | `./discovery_output` |
| `--local-summary` | Generate a local markdown summary | off |
| `--log-level` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `--log-file` | Path to log file | |

### Configuration Template Options

| Flag | Description | Default |
|------|-------------|---------|
| `--output` | Output file path, `.yaml` or `.yml` (required) | |
| `--engines` | Comma-separated engines: `postgres`, `mysql` | `postgres` |
| `--providers` | Comma-separated cloud providers: `aws`, `gcp`, `supabase`, `heroku`, `neon` | |

## Automating Discovery

### PostgreSQL — Multiple Databases

```bash
#!/bin/bash
# discover_pg_databases.sh

DATABASES=("db1" "db2" "db3")
HOST="localhost"
USER="planetscale_discovery"

for db in "${DATABASES[@]}"; do
    echo "Analyzing PostgreSQL database: $db"
    ps-discovery database \
        --host $HOST \
        -d $db \
        -u $USER \
        -W \
        --output-dir "./reports/pg_$db"
done
```

### MySQL — Multiple Hosts

```bash
#!/bin/bash
# discover_mysql_hosts.sh

HOSTS=("db-primary.example.com" "db-replica.example.com")
USER="planetscale_discovery"

for host in "${HOSTS[@]}"; do
    echo "Analyzing MySQL host: $host"
    ps-discovery database \
        --engine mysql \
        --host $host \
        -u $USER \
        -W \
        --output-dir "./reports/mysql_${host}"
done
```

### Combined — Both Engines with Cloud

```bash
#!/bin/bash
# full_discovery.sh

# PostgreSQL discovery
ps-discovery both \
    --host pg.example.com \
    -d mydb -u postgres -W \
    --providers aws \
    --output-dir "./reports/postgres"

# MySQL discovery
ps-discovery both \
    --engine mysql \
    --host mysql.example.com \
    -u root -W \
    --providers aws \
    --output-dir "./reports/mysql"
```

## Multi-Provider Cloud Discovery

You can discover multiple cloud providers simultaneously:

```yaml
engine: postgres

providers:
  aws:
    enabled: true
    regions: [us-east-1]

  gcp:
    enabled: true
    project_id: my-project

  supabase:
    enabled: true
    access_token: "sbp_xxxxx"

  heroku:
    enabled: true
    api_key: "your-heroku-api-key"

  neon:
    enabled: true
    api_key: "your-neon-api-key"

output:
  output_dir: ./multi_cloud_discovery
```
