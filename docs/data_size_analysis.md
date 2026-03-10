# Data Size Analysis

## Overview

The Data Size Analyzer is an **opt-in** module that analyzes actual data sizes within columns to identify LOBs (Large Objects) and large text/bytea/json fields. This analyzer helps identify tables and columns that may require special handling.

⚠️ **IMPORTANT**: This analyzer requires table scans and can be **very expensive** on large databases. It is disabled by default and should be used carefully.

## When to Use

Use the Data Size Analyzer when you need to:

- Identify tables with LOB (Large Object) data
- Find columns with values exceeding specific size thresholds (1KB, 64KB)
- Understand maximum and average column sizes
- Plan for row size limitations in the target database
- Assess performance implications for replication

## Why This Matters

### PostgreSQL Considerations

- **TOAST Storage**: PostgreSQL automatically compresses and moves large values (>2KB by default) to TOAST tables
- **Performance Impact**: Large columns with TOAST storage can impact query performance and replication
- **Replication Lag**: Large text/bytea fields can slow down logical replication
- **Index Limitations**: Cannot index full large text columns, only prefixes
- **Target Database Compatibility**: Some target databases may have different size limitations

### Detection Thresholds

The analyzer checks for two default thresholds:

- **1KB threshold**: Values > 1,024 bytes (may use TOAST storage, impact performance)
- **64KB threshold**: Values > 65,536 bytes (very large values that may need special handling)

## Configuration

### Basic Configuration (Disabled by Default)

```yaml
database:
  host: localhost
  port: 5432
  database: mydb
  username: postgres
  password: secret

  # Data size analysis - OPT-IN ONLY
  data_size:
    enabled: false  # Must explicitly enable
```

### Recommended Configuration (With Sampling)

```yaml
database:
  host: localhost
  port: 5432
  database: mydb
  username: postgres
  password: secret

  data_size:
    enabled: true

    # Sample only 10% of rows (much faster!)
    sample_percent: 10

    # Skip tables larger than 10GB
    max_table_size_gb: 10

    # Only analyze these schemas
    target_schemas:
      - public
      - app_data

    # Check these column types
    check_column_types:
      - text
      - bytea
      - json
      - jsonb
      - character varying

    # Size thresholds to check
    size_thresholds:
      1kb: 1024      # 1 KB
      64kb: 65536    # 64 KB
```

### Targeted Analysis (Specific Tables)

```yaml
database:
  host: localhost
  port: 5432
  database: mydb
  username: postgres
  password: secret

  data_size:
    enabled: true
    sample_percent: 100  # Full scan for small tables

    # Only analyze specific tables
    target_tables:
      - public.emails
      - public.export_processes
      - app_data.documents
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `false` | Whether to run data size analysis |
| `sample_percent` | integer | `10` | Percentage of rows to sample (1-100) |
| `max_table_size_gb` | integer | `10` | Skip tables larger than this (GB) |
| `target_tables` | list | `[]` | Specific tables to analyze (empty = all) |
| `target_schemas` | list | `["public"]` | Schemas to include in analysis |
| `check_column_types` | list | See below | Column data types to check |
| `size_thresholds` | dict | `{1kb: 1024, 64kb: 65536}` | Size thresholds in bytes |

**Default `check_column_types`:**
- `text`
- `bytea`
- `json`
- `jsonb`
- `character varying`

## Performance Considerations

### Full Table Scans

The analyzer uses `pg_column_size()` which requires scanning rows:

```sql
-- Example query used internally
SELECT
  COUNT(*) as total_rows,
  COUNT(*) FILTER (WHERE pg_column_size(body_html) > 1024) as count_gt_1kb,
  MAX(pg_column_size(body_html)) as max_size_bytes
FROM public.emails TABLESAMPLE BERNOULLI (10);
```

### Sampling Strategy

**TABLESAMPLE BERNOULLI** is used for efficient sampling:

- **10% sampling**: Scans ~10% of rows randomly
- **Much faster** than full table scan
- **Statistical representation** of data
- Only applied to tables with > 1,000 rows

### Performance Example

For a table with 500,000 rows:

| Sampling | Rows Scanned | Approximate Time |
|----------|--------------|------------------|
| 100% (full) | 500,000 | 30-60 seconds |
| 10% (default) | ~50,000 | 3-6 seconds |
| 5% | ~25,000 | 1.5-3 seconds |

## PostgreSQL TOAST Storage Strategies

The analyzer captures the TOAST (The Oversized-Attribute Storage Technique) storage strategy for each column:

| Strategy | Description | When Used |
|----------|-------------|-----------|
| **PLAIN** | No compression or out-of-line storage | Small fixed-length types (integers, etc.) |
| **EXTENDED** | Compression first, then out-of-line storage | Default for text, bytea, json, jsonb |
| **EXTERNAL** | Out-of-line storage without compression | Pre-compressed data (e.g., already compressed JSON) |
| **MAIN** | Compression first, avoid out-of-line if possible | Data that should stay inline when possible |

**What this means:**
- **EXTENDED** columns with large values will be compressed and moved to TOAST tables
- **EXTERNAL** columns skip compression (good for already compressed data)
- **MAIN** columns try to stay inline, which can slow down table scans
- **PLAIN** columns cannot use TOAST (always inline, may cause issues if large)

## Output Format

### JSON Output Structure

```json
{
  "data_size": {
    "enabled": true,
    "configuration": {
      "sample_percent": 10,
      "max_table_size_gb": 10,
      "target_schemas": ["public"],
      "target_tables": "all"
    },
    "tables_analyzed": [
      {
        "schema": "public",
        "table": "emails",
        "estimated_rows": 514603,
        "size_gb": 2.4,
        "sampled": true,
        "sample_percent": 10,
        "columns": [
          {
            "column_name": "body_html",
            "data_type": "text",
            "toast_storage_strategy": "EXTENDED",
            "total_rows_checked": 51234,
            "count_gt_1kb": 12784,
            "count_gt_64kb": 0,
            "max_size_bytes": 22528,
            "avg_size_bytes": 284,
            "max_size_human": "22 kB",
            "avg_size_human": "284 bytes",
            "percent_gt_1kb": 24.95,
            "percent_gt_64kb": 0.0
          }
        ],
        "has_large_columns": true
      }
    ],
    "tables_skipped": [
      {
        "schema": "public",
        "table": "audit_logs",
        "size_gb": 15.2,
        "reason": "exceeds_size_limit"
      }
    ],
    "summary": {
      "total_tables_analyzed": 5,
      "total_tables_skipped": 2,
      "tables_with_large_columns": 3,
      "columns_exceeding_1kb": 8,
      "columns_exceeding_64kb": 1
    }
  }
}
```

### Markdown Report Section

The data size analysis is included in the database discovery report:

```markdown
## Data Size Analysis

**Configuration:**
- Sampling: 10% of rows
- Tables analyzed: 5
- Tables skipped: 2 (exceeding size limits)

### Tables with Large Columns

#### public.emails (514,603 rows, 2.4 GB)

| Column | Max Size | Avg Size | > 1KB | > 64KB |
|--------|----------|----------|-------|--------|
| body_html | 22 kB | 284 bytes | 24.95% | 0% |
| vars_map | 512 bytes | 0 bytes | 0% | 0% |

**Considerations:**
- body_html column has 12,784 rows (24.95%) exceeding 1KB
- Maximum observed size: 22 kB (within MySQL limits)
```

## Usage Examples

### Example 1: Quick Assessment (Sampled)

Check all tables with minimal performance impact:

```yaml
database:
  host: localhost
  database: mydb
  username: postgres
  password: secret

  data_size:
    enabled: true
    sample_percent: 5  # Only 5% sampling
    max_table_size_gb: 20
```

Run:
```bash
ps-discovery database --config config.yaml
```

### Example 2: Detailed Analysis (Specific Tables)

Full scan of known problematic tables:

```yaml
database:
  host: localhost
  database: mydb
  username: postgres
  password: secret

  data_size:
    enabled: true
    sample_percent: 100  # Full scan
    target_tables:
      - public.emails
      - public.documents
      - public.attachments
```

### Example 3: Production-Safe Analysis

Conservative settings for production databases:

```yaml
database:
  host: production-replica  # Use read replica!
  database: mydb
  username: readonly_user
  password: secret

  data_size:
    enabled: true
    sample_percent: 1  # Only 1% sampling
    max_table_size_gb: 5  # Skip large tables
    target_schemas:
      - public  # Only public schema
```

## Best Practices

### 1. Use Read Replicas

**Always run against read replicas when possible:**

```yaml
database:
  host: db-replica.example.com  # Read replica
  database: mydb
  username: readonly_user
```

### 2. Start with Small Sampling

Begin with low sampling percentages:

```yaml
data_size:
  sample_percent: 5  # Start small
```

Increase if results are needed:

```yaml
data_size:
  sample_percent: 25  # More thorough
```

### 3. Target Specific Tables

If you know which tables have large columns:

```yaml
data_size:
  target_tables:
    - public.emails
    - public.documents
```

### 4. Set Conservative Size Limits

Skip very large tables:

```yaml
data_size:
  max_table_size_gb: 10  # Skip > 10GB tables
```

### 5. Run During Off-Peak Hours

Schedule analysis during low-traffic periods:

```bash
# Cron job example - 2 AM daily
0 2 * * * ps-discovery database --config config.yaml
```

## Interpreting Results

### Critical Findings

🚨 **Columns exceeding 64KB:**
- Very large values stored in TOAST tables
- May impact replication and backup performance
- Check target database compatibility
- High priority concern

### Warning Findings

⚠️ **Columns exceeding 1KB:**
- Values likely using TOAST storage
- May impact query performance
- Consider optimization opportunities
- Monitor replication lag

### Example Output

```
export_processes table:
- resource_ids column: Max 14 MB, Avg 358 KB
- 1,105 rows (75%) exceed 1KB
- 653 rows (44%) exceed 64KB
- WARNING: Very large values using TOAST storage
- May cause replication lag and slow queries

emails table:
- body_html column: Max 22 KB, Avg 284 bytes
- 12,784 rows (2.5%) exceed 1KB
- 0 rows exceed 64KB
- OK: Moderate sizes, within normal range
```

## Troubleshooting

### "Permission denied for table"

**Solution:** Ensure database user has SELECT permission:

```sql
GRANT SELECT ON ALL TABLES IN SCHEMA public TO discovery_user;
```

### "Analysis taking too long"

**Solutions:**
1. Reduce `sample_percent`
2. Add specific `target_tables`
3. Increase `max_table_size_gb` to skip more tables
4. Use read replica

### "TABLESAMPLE not supported"

**Issue:** PostgreSQL < 9.5

**Solution:** Disable sampling:

```yaml
data_size:
  sample_percent: 100  # Full scan (slow!)
```

## Recommendations

Based on data size analysis results:

### If columns exceed 64KB

1. **Check target database limits**: Verify target database can handle large values
2. **Redesign schema**: Consider moving large columns to separate tables
3. **Use external storage**: Store large objects in S3/object storage with references in database
4. **Compress data**: Apply application-level compression before storing
5. **Chunk data**: Split large values into multiple rows with versioning
6. **Test replication**: Monitor logical replication lag with large TOAST values

### If many columns exceed 1KB

1. **Review TOAST strategy**: Check `pg_attribute.attstorage` settings (PLAIN, EXTENDED, EXTERNAL, MAIN)
2. **Optimize queries**: Avoid `SELECT *` on tables with large columns
3. **Consider indexes**: Use GIN/GiST indexes for text search instead of full-text indexing
4. **Test replication**: Monitor replication slot lag
5. **Evaluate compression**: PostgreSQL TOAST compression may reduce wire transfer size
6. **Plan for backups**: Large TOAST tables increase backup time and size

## Additional Resources

- [PostgreSQL pg_column_size Documentation](https://www.postgresql.org/docs/current/functions-admin.html)
- [PostgreSQL TOAST Storage](https://www.postgresql.org/docs/current/storage-toast.html)
- [TABLESAMPLE Documentation](https://www.postgresql.org/docs/current/sql-select.html#SQL-FROM)
- [PostgreSQL Large Objects](https://www.postgresql.org/docs/current/largeobjects.html)
- [Logical Replication Performance](https://www.postgresql.org/docs/current/logical-replication.html)
