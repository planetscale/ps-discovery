# Performance Considerations for Discovery Analyzers

## Overview

This document outlines the performance characteristics of each analyzer and provides guidance on safe usage in production environments.

## Performance Safety Summary

| Analyzer | Performance Impact | Safe for Production | Notes |
|----------|-------------------|---------------------|-------|
| ConfigAnalyzer | ✅ Very Low | Yes | Only reads `pg_settings` and config files |
| SchemaAnalyzer | ✅ Low | Yes | Only queries system catalogs (`pg_*` tables) |
| PerformanceAnalyzer | ✅ Low | Yes | Only reads `pg_stat_*` views |
| SecurityAnalyzer | ✅ Low | Yes | Only queries system catalogs and roles |
| FeatureAnalyzer | ✅ Low | Yes | Only queries system catalogs + one COUNT on static table |
| **DataSizeAnalyzer** | ⚠️ **HIGH** | **Use Caution** | **Requires table scans - OPT-IN ONLY** |

## Detailed Analysis

### ✅ ConfigAnalyzer - SAFE

**What it does:**
- Reads `pg_settings` system view
- Parses `postgresql.conf` file (if accessible)
- Checks for configuration files

**Performance characteristics:**
- No user table access
- No table scans
- Reads only configuration metadata
- Minimal memory usage

**Production safety:** ✅ Always safe

---

### ✅ SchemaAnalyzer - SAFE

**What it does:**
- Queries system catalogs: `pg_class`, `pg_attribute`, `pg_index`, `pg_constraint`
- Analyzes table structure, columns, indexes, constraints
- Calculates table and index sizes using `pg_relation_size()`

**Performance characteristics:**
- All queries use system catalogs (already indexed)
- `pg_relation_size()` is efficient (reads metadata, not data)
- No user table data access
- Scales with number of tables/indexes (not data size)

**Example queries:**
```sql
-- Efficient: Uses system catalogs
SELECT c.relname, pg_relation_size(c.oid)
FROM pg_class c
WHERE c.relkind = 'r';

-- Efficient: Metadata only
SELECT attname, format_type(atttypid, atttypmod)
FROM pg_attribute
WHERE attrelid = table_oid;
```

**Production safety:** ✅ Always safe

**Caveat:** On databases with 10,000+ tables, may take 10-30 seconds

---

### ✅ PerformanceAnalyzer - SAFE

**What it does:**
- Queries `pg_stat_activity` for connection analysis
- Reads `pg_stat_statements` for query performance (if enabled)
- Analyzes `pg_stat_user_tables` and `pg_stat_user_indexes`
- Checks cache hit ratios, locks, and waits

**Performance characteristics:**
- All queries use `pg_stat_*` views (very efficient)
- Statistics are maintained in shared memory
- No table scans
- No user data access

**Example queries:**
```sql
-- Efficient: Statistics view
SELECT * FROM pg_stat_activity;

-- Efficient: Aggregated query stats
SELECT query, total_exec_time, calls
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;
```

**Production safety:** ✅ Always safe

**Note:** `pg_stat_statements` must be enabled for query analysis

---

### ✅ SecurityAnalyzer - SAFE

**What it does:**
- Analyzes roles, users, and permissions
- Queries `pg_roles`, `pg_user`, `pg_authid`
- Checks SSL configuration
- Analyzes password policies

**Performance characteristics:**
- Only queries system catalogs
- No user table access
- Minimal resource usage

**Production safety:** ✅ Always safe

---

### ✅ FeatureAnalyzer - SAFE

**What it does:**
- Checks for installed extensions (`pg_extension`)
- Analyzes foreign data wrappers
- Checks for PostGIS (including `COUNT(*) FROM spatial_ref_sys`)
- Checks for partitioning, inheritance features

**Performance characteristics:**
- Mostly system catalog queries
- One `COUNT(*)` on `spatial_ref_sys` (static table, ~8,000 rows)
- No user table scans

**Production safety:** ✅ Always safe

---

### ⚠️ DataSizeAnalyzer - USE CAUTION

**What it does:**
- Analyzes actual data sizes within columns using `pg_column_size()`
- **Requires scanning table rows** to measure column sizes
- Uses TABLESAMPLE for sampling (default 10%)

**Performance characteristics:**
- **Requires table scans** (expensive!)
- Uses `pg_column_size()` which must read actual data
- Default sampling (10%) reduces cost but still significant
- Large tables can take minutes even with sampling

**Example query:**
```sql
-- EXPENSIVE: Scans 10% of table rows
SELECT
  COUNT(*),
  MAX(pg_column_size(body_html))
FROM emails TABLESAMPLE BERNOULLI (10);
```

**Performance impact example:**

| Table Size | Sample % | Rows Scanned | Approximate Time |
|------------|----------|--------------|------------------|
| 100K rows | 10% | 10,000 | 1-2 seconds |
| 1M rows | 10% | 100,000 | 10-20 seconds |
| 10M rows | 10% | 1,000,000 | 2-3 minutes |
| 50M rows | 10% | 5,000,000 | 10-15 minutes |

**Production safety:** ⚠️ **OPT-IN ONLY - Use with caution**

**Recommendations:**

1. **Use Read Replicas**
   ```yaml
   database:
     host: db-replica.example.com  # Not primary!
   ```

2. **Start with Low Sampling**
   ```yaml
   data_size:
     sample_percent: 1  # Only 1%!
   ```

3. **Set Size Limits**
   ```yaml
   data_size:
     max_table_size_gb: 5  # Skip large tables
   ```

4. **Target Specific Tables**
   ```yaml
   data_size:
     target_tables:
       - public.emails
       - public.documents
   ```

5. **Run Off-Peak**
   - Schedule during low-traffic hours
   - Use cron: `0 2 * * * ps-discovery ...`

---

## Production Usage Guidelines

### Always Safe (Default Analyzers)

These analyzers can run anytime on production databases:

```yaml
modules:
  - database  # Safe: config, schema, performance, security, features
  - cloud     # Safe: Only queries cloud provider APIs
```

### Requires Caution (Opt-In Analyzers)

The DataSizeAnalyzer must be explicitly enabled and configured carefully:

```yaml
database:
  host: replica.db.example.com  # Use replica!

  data_size:
    enabled: true  # Explicit opt-in
    sample_percent: 5  # Low sampling
    max_table_size_gb: 10  # Skip huge tables
```

### Best Practices

#### ✅ DO:

1. **Use Read Replicas** for data size analysis
2. **Start with minimal sampling** (1-5%)
3. **Set table size limits** to skip very large tables
4. **Target specific tables** when possible
5. **Run during off-peak hours**
6. **Test on non-production first**
7. **Monitor replication lag** if using replicas

#### ❌ DON'T:

1. **Don't run data size analysis on primary databases** without careful planning
2. **Don't use 100% sampling** on large tables without testing
3. **Don't run during peak traffic** hours
4. **Don't analyze all tables** if you only need specific ones
5. **Don't run without size limits** on databases with very large tables

### Managed Database Environments

When running against managed PostgreSQL services (AWS RDS, Cloud SQL, Supabase, etc.):

- **Default analyzers are safe** - they only read metadata
- **Data size analyzer requires caution** - consult your cloud provider's best practices
- **Use read replicas** if available
- **Monitor CloudWatch/Cloud Monitoring** during discovery

### Troubleshooting Performance Issues

#### If discovery is slow:

1. **Check which analyzer is running:**
   - DataSizeAnalyzer? Expected if analyzing large tables
   - SchemaAnalyzer? May be slow if 10,000+ tables
   - Others? Should be fast (<30 seconds)

2. **For DataSizeAnalyzer:**
   - Reduce `sample_percent`
   - Increase `max_table_size_gb` to skip more tables
   - Add specific `target_tables`

3. **For SchemaAnalyzer:**
   - This is expected on databases with many tables
   - Consider filtering schemas if needed

4. **Check database resources:**
   - Monitor CPU, I/O during discovery
   - Check for locks or blocking queries
   - Review `pg_stat_activity`

## Configuration Examples

### Minimal Performance Impact

```yaml
# Fastest discovery - skip data size analysis
modules:
  - database

database:
  host: localhost
  database: mydb
  username: postgres
  password: secret

  data_size:
    enabled: false  # Skip expensive analysis
```

**Time:** 10-60 seconds depending on database size

### Balanced Approach

```yaml
# Moderate performance impact with sampling
modules:
  - database

database:
  host: db-replica.example.com  # Use replica
  database: mydb

  data_size:
    enabled: true
    sample_percent: 5  # Light sampling
    max_table_size_gb: 10  # Skip huge tables
```

**Time:** 1-5 minutes depending on table count and sizes

### Comprehensive Analysis

```yaml
# Detailed analysis - use during maintenance windows
modules:
  - database

database:
  host: db-replica.example.com
  database: mydb

  data_size:
    enabled: true
    sample_percent: 25  # More thorough
    max_table_size_gb: 50  # Analyze larger tables
    target_schemas:
      - public
      - app_data
```

**Time:** 5-30 minutes depending on data volume

## Monitoring Discovery Performance

### During Discovery

Monitor these metrics:

```sql
-- Active discovery queries
SELECT pid, query_start, state, query
FROM pg_stat_activity
WHERE application_name LIKE '%discovery%'
  OR query LIKE '%pg_column_size%';

-- Lock contention
SELECT * FROM pg_locks
WHERE NOT granted;

-- I/O activity
SELECT * FROM pg_stat_database
WHERE datname = current_database();
```

### After Discovery

Review performance impact:

```sql
-- Check if temp files were created (indicates spilling to disk)
SELECT temp_files, pg_size_pretty(temp_bytes)
FROM pg_stat_database
WHERE datname = current_database();

-- Review query statistics if pg_stat_statements enabled
SELECT query, calls, total_exec_time
FROM pg_stat_statements
WHERE query LIKE '%pg_column_size%'
ORDER BY total_exec_time DESC;
```

## Summary

**Default analyzers** (config, schema, performance, security, features) are **always safe for production** use. They only query system catalogs and statistics views.

**DataSizeAnalyzer** requires **careful configuration** and should use:
- Read replicas when possible
- Low sampling percentages (1-10%)
- Table size limits
- Off-peak scheduling

When in doubt, **start conservative** and increase scope as needed.
