# Advanced Usage

## Advanced Installation Options

For users who want more control over the installation:

```bash
# Clone the repository (for development)
git clone <repository-url>
cd ps-discovery

# Option 1: Install with pipx (isolates dependencies)
pipx install -e ".[aws]"       # AWS cloud discovery support
# OR
pipx install -e ".[gcp]"       # GCP support
# OR
pipx install -e ".[supabase]"  # Supabase support
# OR
pipx install -e ".[heroku]"    # Heroku support
# OR
pipx install -e ".[all]"       # All cloud providers
# OR
pipx install -e .              # Database discovery only

# If you don't have pipx:
brew install pipx  # macOS
pipx ensurepath

# Option 2: Manual virtual environment setup
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e ".[aws]"
```

## Focused Database Analysis

For analysis that focuses on a specific target database:

```bash
# Focus analysis on a specific database identifier
ps-discovery cloud --config config.yaml --target-database my-rds-instance

# Combined focused analysis (database + targeted cloud infrastructure)
ps-discovery both --config config.yaml --target-database my-rds-instance
```

This **focused approach**:
- Analyzes only the specified target database and its related infrastructure
- Captures complete networking stack (VPC, subnets, security groups, NACLs, route tables)
- Includes comprehensive database operational details (backup/maintenance windows)
- Limits data exposure to only infrastructure related to the target database

## CLI Reference

### Connection Parameters

| Flag | Description | Default |
|------|-------------|---------|
| `--host` | PostgreSQL server host | `localhost` |
| `-p, --port` | PostgreSQL server port | `5432` |
| `-d, --database` | PostgreSQL database name | (required) |
| `-u, --username` | PostgreSQL username | |
| `-W, --password` | Prompt for password | |
| `--config` | YAML or JSON configuration file | |

### Analysis Options

| Flag | Description |
|------|-------------|
| `--analyzers` | Comma-separated list of analyzers to run: `config`, `schema`, `performance`, `security`, `features` |
| `--target-database` | Focus cloud analysis on a specific database identifier |

### Output Options

| Flag | Description | Default |
|------|-------------|---------|
| `--output-dir` | Output directory for reports | `./discovery_output` |
| `--local-summary` | Generate a local markdown summary | off |
| `--log-level` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `--log-file` | Path to log file | |

## Automating Discovery

Create a shell script for automated discovery across multiple databases:

```bash
#!/bin/bash
# discover_all_databases.sh

DATABASES=("db1" "db2" "db3")
HOST="localhost"
USER="planetscale_discovery"

for db in "${DATABASES[@]}"; do
    echo "Analyzing database: $db"
    ps-discovery database \
        --host $HOST \
        -d $db \
        -u $USER \
        -W \
        --output-dir "./reports/$db"
done
```

## Multi-Provider Cloud Discovery

You can discover multiple cloud providers simultaneously:

```yaml
modules:
  - cloud

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

output:
  output_dir: ./multi_cloud_discovery
```
