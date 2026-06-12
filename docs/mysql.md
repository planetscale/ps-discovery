# MySQL Database Discovery Setup

## Overview

The PlanetScale Discovery CLI can analyze MySQL and MySQL-compatible databases including standard MySQL, MariaDB, Percona Server, Amazon Aurora MySQL, Google Cloud SQL for MySQL, Azure Database for MySQL, and PlanetScale (Vitess). The tool collects metadata about database configuration, schema structure, performance characteristics, replication topology, and feature usage without accessing actual table data.

> **Cloud provider support:** MySQL discovery is supported on AWS RDS MySQL, Aurora MySQL, and GCP Cloud SQL MySQL — both cloud analyzers recognize the MySQL engine and report it accordingly. The Supabase, Heroku Postgres, and Neon providers in this repo are PostgreSQL-only.

## Prerequisites

- MySQL-compatible database server (MySQL 5.7+, MySQL 8.0+, MariaDB 10.x, or compatible)
- Database user with read-only access to `information_schema` and system tables
- Python package: `pip install "ps-discovery[mysql]"` (or select MySQL during `./setup.sh`)

## Quick Start

```bash
# Install with MySQL support
pip install "ps-discovery[mysql]"

# Run discovery against a MySQL server
ps-discovery database --engine mysql --host db.example.com -u root -W

# Run discovery with a config file
ps-discovery database --engine mysql --config mysql-config.yaml

# Generate a MySQL configuration template
ps-discovery config-template --output mysql-config.yaml --engines mysql
```

## Authentication Setup

### Option 1: Command-Line Flags

```bash
ps-discovery database --engine mysql \
  --host db.example.com \
  --port 3306 \
  --username discovery_user \
  -W  # prompts for password
```

### Option 2: Configuration File

```yaml
engine: mysql

mysql:
  host: db.example.com
  port: 3306
  database: ""  # Leave empty to discover all databases
  username: discovery_user
  password: your_password
  ssl_mode: disabled
```

Then run:

```bash
ps-discovery database --engine mysql --config mysql-config.yaml
```

### Option 3: Environment Variables

```bash
export MYSQL_HOST=db.example.com
export MYSQL_PORT=3306
export MYSQL_USER=discovery_user
export MYSQL_PASSWORD=your_password
export MYSQL_SSL_MODE=disabled
export DISCOVERY_ENGINE=mysql
```

Then run with minimal flags:

```bash
ps-discovery database --engine mysql
```

## Required Privileges

### Standard MySQL

Create a dedicated read-only user for discovery:

```sql
-- Create the discovery user
CREATE USER 'planetscale_discovery'@'%' IDENTIFIED BY 'secure_password_here';

-- Grant read access to all databases (for schema analysis)
GRANT SELECT ON *.* TO 'planetscale_discovery'@'%';

-- Grant process privilege (for performance analysis, SHOW PROCESSLIST)
GRANT PROCESS ON *.* TO 'planetscale_discovery'@'%';

-- Grant replication client (for binary log and replica status)
GRANT REPLICATION CLIENT ON *.* TO 'planetscale_discovery'@'%';

-- Apply changes
FLUSH PRIVILEGES;
```

### Amazon RDS / Aurora MySQL

The same grants apply. Create the user through the RDS master user:

```sql
CREATE USER 'planetscale_discovery'@'%' IDENTIFIED BY 'secure_password_here';
GRANT SELECT ON *.* TO 'planetscale_discovery'@'%';
GRANT PROCESS ON *.* TO 'planetscale_discovery'@'%';
GRANT REPLICATION CLIENT ON *.* TO 'planetscale_discovery'@'%';
```

Note: Some RDS-specific status variables may not be accessible. The tool handles this gracefully and reports any gaps.

### Google Cloud SQL for MySQL

Same grants as standard MySQL. If using Cloud SQL Auth Proxy, configure the proxy connection details as the host:

```yaml
mysql:
  host: 127.0.0.1  # Cloud SQL Proxy listens locally
  port: 3306
  username: planetscale_discovery
  password: your_password
```

### PlanetScale (Vitess)

For PlanetScale databases, your existing database credentials are sufficient. The discovery tool automatically detects PlanetScale/Vitess environments and adapts its queries accordingly.

```bash
ps-discovery database --engine mysql \
  --host aws.connect.psdb.cloud \
  --username your_branch_username \
  -W \
  --ssl-mode required
```

Or via config:

```yaml
engine: mysql

mysql:
  host: aws.connect.psdb.cloud
  port: 3306
  username: your_branch_username
  password: your_branch_password
  ssl_mode: required
```

**PlanetScale-specific behavior:**

- The tool detects scoped `information_schema` (Vitess) and automatically falls back to per-database iteration
- System databases (`_vt`, `mysql`, `performance_schema`) are excluded
- Features not supported by Vitess (e.g., foreign keys in older versions, stored procedures) are detected and reported

## SSL Configuration

The `ssl_mode` setting controls how the connection to MySQL is secured:

| Mode | Description |
|------|-------------|
| `disabled` | No SSL (default for local connections) |
| `preferred` | Use SSL if available, fall back to unencrypted |
| `required` | Require SSL, but don't verify the server certificate |
| `verify-ca` | Require SSL and verify the server certificate against the CA |
| `verify-identity` | Require SSL, verify the certificate, and verify the hostname |

For PlanetScale and most cloud-hosted MySQL instances, use `required` or higher.

## What Data Is Collected

### Configuration Analysis
- MySQL version, distribution (MySQL, MariaDB, Percona, Aurora), and build information
- Server variables (`SHOW GLOBAL VARIABLES`)
- Cloud platform detection (RDS, Aurora, Cloud SQL, PlanetScale, Azure, on-premise)

### Schema Analysis
- Database list (excluding system databases)
- Table metadata: row counts, data size, index size, storage engine, collation, row format
- Column details: data types, nullability, defaults, auto-increment, generated columns
- Indexes: type (BTREE, HASH, FULLTEXT, SPATIAL), uniqueness, cardinality, prefix lengths
- Views: definitions, updatability, definer, security type
- Stored procedures and functions: return types, definer, deterministic flag
- Triggers: events, timing, action statements
- Foreign key constraints with referential actions (CASCADE, SET NULL, etc.)
- CHECK constraints (MySQL 8.0.16+)
- Partitioning details: method, expression, partition sizes
- Per-database aggregates: object counts, storage engine distribution, index types, column types

### Performance Analysis
- Global status counter deltas (per-second and per-day rates)
- Process list summary by command, user, host, database, and state
- InnoDB lock counters and current lock waits
- Deadlock detection from InnoDB status

### Replication Analysis
- Replica status (supports both modern `SHOW REPLICA STATUS` and legacy `SHOW SLAVE STATUS`)
- Binary log inventory and current position
- Binary log retention settings
- Binary log format (ROW, STATEMENT, MIXED)

### Security Analysis
- Aggregate user counts (total users, accounts without passwords, wildcard host accounts, locked accounts)
- Authentication plugin distribution (mysql_native_password, caching_sha2_password, etc.)
- SSL/TLS status (availability, required transport, TLS versions, users requiring SSL)
- Privilege summary (counts of users with broad privileges, SUPER, replication grants, database-level grants)
- Password validation policy settings

No individual usernames or specific grant details are collected.

### Feature/Technology Detection
- Full-text indexing usage
- Geospatial data types
- Foreign key constraints
- Table partitioning
- InnoDB compression
- SSL/TLS configuration
- Explicit table locking patterns
- XA transactions
- Prepared statements
- Galera Cluster (wsrep) detection

## What Is NOT Collected

- Table contents or row data
- Query text or slow query log entries
- User passwords or authentication credentials
- Connection strings or URIs
- Application code or business logic
- Billing or account information

## Configuration Examples

### Discover All Databases

Leave the `database` field empty to discover every database the user can access:

```yaml
engine: mysql

mysql:
  host: db.example.com
  port: 3306
  database: ""
  username: planetscale_discovery
  password: your_password
```

### Target a Specific Database

Set the `database` field to focus on one database:

```yaml
engine: mysql

mysql:
  host: db.example.com
  port: 3306
  database: my_application_db
  username: planetscale_discovery
  password: your_password
```

### Combined MySQL + Cloud Discovery

```yaml
engine: mysql

mysql:
  host: db.example.com
  port: 3306
  username: planetscale_discovery
  password: your_password

providers:
  aws:
    enabled: true
    regions:
      - us-east-1

output:
  output_dir: ./mysql_discovery_output
```

Run with:

```bash
ps-discovery both --engine mysql --config combined-config.yaml
```

### Select Specific Analyzers

Run only the analyzers relevant to your assessment:

```bash
# Schema and features only
ps-discovery database --engine mysql --host db.example.com -u root -W \
  --analyzers schema,features

# Performance and replication only
ps-discovery database --engine mysql --host db.example.com -u root -W \
  --analyzers performance,replication
```

Available MySQL analyzers: `config`, `schema`, `performance`, `replication`, `security`, `features`

## Troubleshooting

### "PyMySQL is not installed" Error

**Problem:** MySQL dependencies are not installed.

**Solution:**
```bash
pip install "ps-discovery[mysql]"
# Or re-run setup.sh and select MySQL when prompted
```

### "Can't connect to MySQL server" Error

**Problem:** Cannot reach the MySQL server.

**Solution:**
1. Verify the host and port are correct
2. Check that the MySQL server is running and accepting connections
3. Verify firewall rules allow connections from your machine
4. For cloud databases, ensure the IP allowlist includes your address

### "Access denied" Error

**Problem:** Authentication failed.

**Solution:**
1. Verify the username and password are correct
2. Check that the user has the required grants (see [Required Privileges](#required-privileges))
3. For PlanetScale, verify the branch credentials are active

### Incomplete Results or Gaps

**Problem:** Some analysis modules report errors or missing data.

**Solution:**
- Check the `analysis_gaps` section of the JSON report for specific details
- Ensure the user has `PROCESS` and `REPLICATION CLIENT` privileges
- Some managed services restrict access to certain system tables — the tool reports these as gaps and continues with available data

### PlanetScale/Vitess-Specific Issues

**Problem:** Schema analysis seems slow or incomplete.

**Solution:**
- PlanetScale uses scoped `information_schema`. The tool detects this and iterates per-database, which takes longer with many databases.
- Verify your branch credentials have access to the databases you want to analyze.
- Some Vitess features (e.g., stored procedures, triggers) may not be available — the tool reports these as gaps.

## Support

For issues with the discovery tool:
- Report bugs: https://github.com/planetscale/ps-discovery/issues
- Documentation: See main [README.md](../README.md)

For MySQL or PlanetScale-specific questions:
- PlanetScale Documentation: https://planetscale.com/docs
- MySQL Documentation: https://dev.mysql.com/doc/
