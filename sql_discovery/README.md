# PlanetScale SQL-Based Discovery

This directory contains a standalone SQL script for customers who cannot run the full PlanetScale Discovery CLI tool directly in their environment but can execute SQL queries against their PostgreSQL database.

## Overview

The `planetscale_discovery.sql` script collects the same key metadata as the CLI tool, including:

- PostgreSQL version and configuration
- Database catalog and schema structure
- Table details, columns, and data types
- Index analysis and usage statistics
- Constraints and foreign key relationships
- Functions, triggers, views, and sequences
- Extensions and advanced features
- Partitioning information
- Performance statistics
- Replication status
- Security and user information
- Query performance (if pg_stat_statements is available)

**Important:** This script only collects metadata - no actual customer data is queried. All operations are read-only.

## Requirements

### Minimum PostgreSQL Version

- PostgreSQL 9.5 or later (recommended: 10+)
- Some features require specific versions (e.g., partitioning requires PostgreSQL 10+)

### Required Permissions

The script requires minimal read-only permissions. Ideally, run as a user with:

```sql
-- Create a dedicated discovery user
CREATE USER planetscale_discovery WITH PASSWORD 'secure_password';

-- Grant connect permission
GRANT CONNECT ON DATABASE your_database TO planetscale_discovery;

-- Grant usage on schemas
GRANT USAGE ON SCHEMA public TO planetscale_discovery;
GRANT USAGE ON SCHEMA your_schema TO planetscale_discovery;

-- Grant read access to system catalogs (automatically available to all users)
-- No additional grants needed for pg_catalog views

-- Optional: For pg_stat_statements access
GRANT pg_read_all_stats TO planetscale_discovery; -- PostgreSQL 10+
```

**Note:** Most system catalog queries work without elevated privileges. Some advanced features (like `pg_stat_statements` or replication views) may require additional permissions depending on your PostgreSQL configuration.

### Managed PostgreSQL Services

This script works with managed PostgreSQL services:

- **AWS RDS/Aurora PostgreSQL**: Works with standard RDS permissions
- **GCP Cloud SQL/AlloyDB**: Works with standard CloudSQL permissions
- **Azure Database for PostgreSQL**: Works with standard Azure permissions
- **Heroku Postgres**: Works with standard Heroku permissions
- **Self-hosted PostgreSQL**: Works with any user that has database read access

Some replication and advanced statistics views may not be accessible on managed services - the script handles this gracefully.

## Important: Multiple Databases

⚠️ **Critical Limitation:** PostgreSQL clusters can contain multiple databases, but this SQL script can only analyze **one database at a time** - the database you connect to.

### How to Check Which Databases Exist

Before running the discovery script, check what databases exist in your cluster:

```bash
# List all databases
psql -h your-hostname -U your-username -d postgres -c "\l"

# Or with SQL
psql -h your-hostname -U your-username -d postgres -c "SELECT datname, pg_size_pretty(pg_database_size(datname)) AS size FROM pg_database WHERE datistemplate = false ORDER BY pg_database_size(datname) DESC;"
```

### Running Discovery on Multiple Databases

If your cluster has multiple databases with application data, you need to run the script separately for each database:

```bash
# Run on the first database
psql -h your-hostname -U your-username -d database1 \
     -f planetscale_discovery.sql -o database1_discovery.txt

# Run on the second database
psql -h your-hostname -U your-username -d database2 \
     -f planetscale_discovery.sql -o database2_discovery.txt
```

**Important:** The default `postgres` database is usually empty. Make sure to run the script against your actual application database(s)!

## Usage

### Using psql (Recommended)

The simplest method is to use the `psql` command-line tool:

```bash
# Basic usage - output to terminal
psql -h your-hostname \
     -p 5432 \
     -U your-username \
     -d your-database \
     -f planetscale_discovery.sql

# Recommended: Save output to a file
psql -h your-hostname \
     -p 5432 \
     -U your-username \
     -d your-database \
     -f planetscale_discovery.sql \
     -o discovery_output.txt

# With SSL/TLS (recommended for security)
psql "host=your-hostname port=5432 dbname=your-database user=your-username sslmode=require" \
     -f planetscale_discovery.sql \
     -o discovery_output.txt

# Using environment variable for password (avoid typing password interactively)
PGPASSWORD='your-password' psql -h your-hostname \
                                 -U your-username \
                                 -d your-database \
                                 -f planetscale_discovery.sql \
                                 -o discovery_output.txt
```

**Environment Variables:**

You can also use standard PostgreSQL environment variables:

```bash
export PGHOST=your-hostname
export PGPORT=5432
export PGDATABASE=your-database
export PGUSER=your-username
export PGPASSWORD=your-password

# Then simply run:
psql -f planetscale_discovery.sql -o discovery_output.txt
```

## Output Format

The script generates a comprehensive text report with clearly labeled sections:

```
==================================================================================
PlanetScale PostgreSQL Discovery Analysis
Generated at: 2024-01-15 10:30:00
==================================================================================

==================================================================================
SECTION 1: VERSION AND CONFIGURATION
==================================================================================
[Configuration details...]

==================================================================================
SECTION 2: DATABASE CATALOG
==================================================================================
[Database inventory...]

[...additional sections...]
```

## What to Do with the Output

### 1. Save the Output File

Save the complete output to a file:

```bash
# If you didn't use -o flag during execution
psql [...] -f planetscale_discovery.sql > discovery_output.txt 2>&1
```

### 2. Share with PlanetScale

Share the output file via:

- Email (if file size permits)
- Secure file sharing service

### 3. Compress Large Files

If the output file is large (common for databases with many tables):

```bash
# Create compressed archive
gzip discovery_output.txt

# Or zip format
zip discovery_output.zip discovery_output.txt
```

## Expected Output Size

The output size varies based on your database:

- **Small database** (< 50 tables): 50-200 KB
- **Medium database** (50-500 tables): 200 KB - 2 MB
- **Large database** (500+ tables): 2 MB - 20 MB+

The script limits detailed column information to the 20 largest tables to keep output manageable.

## Troubleshooting

### "permission denied" Errors

Some sections may fail with permission errors on managed databases. This is expected and the script continues:

```
ERROR: permission denied for table pg_stat_replication
```

These are gracefully handled - the sections that succeed provide sufficient information.

### pg_stat_statements Not Available

If you see:

```
ERROR: relation "pg_stat_statements" does not exist
```

This is expected if the extension isn't installed. The script continues with other sections.

### Connection Issues

If you have connection problems:

```bash
# Test basic connectivity first
psql -h your-hostname -U your-username -d your-database -c "SELECT version();"

# Check for firewall issues
nc -zv your-hostname 5432

# Verify SSL requirements
psql "host=your-hostname sslmode=require" -c "SELECT version();"
```

### Timeout Issues

For very large databases, some queries may timeout:

```bash
# Increase statement timeout
psql -h your-hostname \
     -U your-username \
     -d your-database \
     -c "SET statement_timeout = '5min';" \
     -f planetscale_discovery.sql
```

### Character Encoding Issues

If you see encoding errors in output:

```bash
# Explicitly set client encoding
psql --set client_encoding=UTF8 \
     -h your-hostname \
     -U your-username \
     -d your-database \
     -f planetscale_discovery.sql \
     -o discovery_output.txt
```

## Security Considerations

### What the Script Does NOT Collect

- ❌ Actual table data or row contents
- ❌ Query result sets
- ❌ Password hashes or credentials
- ❌ Application code or business logic
- ❌ Personal Identifiable Information (PII)
- ❌ Connection strings with passwords

### What the Script DOES Collect

- ✅ Table and column names
- ✅ Data types and constraints
- ✅ Index definitions
- ✅ Function and trigger code (if present)
- ✅ Configuration parameter names and values
- ✅ User/role names (not passwords)
- ✅ Database sizes and statistics

### Safe to Run in Production

Yes, this script is safe to run in production:

- All queries are read-only (`SELECT` statements only)
- No data modification (`INSERT`, `UPDATE`, `DELETE`, `DROP`, etc.)
- No schema changes (`CREATE`, `ALTER`, etc.)
- Minimal performance impact (reads from system catalogs)
- Does not hold locks or block operations

However, as a best practice:

- Run during off-peak hours if possible
- Test in a non-production environment first
- Monitor query execution time on very large databases

## Support

If you encounter issues with the SQL script:

1. Check the Troubleshooting section above
1. Contact PlanetScale migration support
1. Include:
   - PostgreSQL version
   - Database platform (RDS, Cloud SQL, self-hosted, etc.)
   - Error message or unexpected output
   - Approximate database size

## Version History

- **v1.0** (2024-01): Initial release with comprehensive discovery queries

## License

This script is part of the PlanetScale Discovery Tools suite. See the main repository LICENSE file for details.
