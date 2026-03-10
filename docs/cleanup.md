# Cleanup Procedures

After completing the discovery analysis, follow these steps to clean up temporary resources created on your PostgreSQL environment.

## Remove Discovery User

If you created a dedicated discovery user, remove it after analysis:

```sql
-- Connect as a superuser or user with appropriate privileges

-- Drop the discovery user
DROP USER IF EXISTS planetscale_discovery;

-- If the user owns any objects, you may need to reassign ownership first
-- REASSIGN OWNED BY planetscale_discovery TO postgres;
-- DROP OWNED BY planetscale_discovery;
-- DROP USER planetscale_discovery;
```

## Verify No Temporary Objects Remain

Check that no temporary objects were left behind:

```sql
-- Check for any remaining objects owned by the discovery user
SELECT
    schemaname,
    tablename,
    tableowner
FROM pg_tables
WHERE tableowner = 'planetscale_discovery';

-- Check for any remaining functions owned by the discovery user
SELECT
    schemaname,
    proname,
    proowner::regrole
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE proowner::regrole::text = 'planetscale_discovery';
```

## Review Analysis Impact

The discovery tool is designed to be completely non-intrusive, but you should verify:

- No temporary tables were created
- No database connections remain open
- No long-running queries are still executing
- No locks are held by discovery processes

## Connection Cleanup

Verify all discovery tool connections have been properly closed:

```sql
-- Check for any remaining connections from the discovery tool
SELECT
    pid,
    usename,
    application_name,
    client_addr,
    state,
    query_start
FROM pg_stat_activity
WHERE usename = 'planetscale_discovery'
   OR application_name LIKE '%discovery%';

-- If any connections remain, they can be terminated:
-- SELECT pg_terminate_backend(pid) FROM pg_stat_activity
-- WHERE usename = 'planetscale_discovery';
```

## Extension Cleanup

If you installed any extensions specifically for the discovery analysis (like `pg_stat_statements` if it wasn't already present), consider whether to remove them:

```sql
-- Only remove if it was installed specifically for discovery
-- and is not needed for normal operations
-- DROP EXTENSION IF EXISTS pg_stat_statements;
```

**Important**: Only remove extensions if you're certain they were installed solely for the discovery process and are not used by applications or other processes.

## Security Audit

After cleanup, perform a final security check:

```sql
-- Verify the discovery user is completely removed
SELECT rolname FROM pg_roles WHERE rolname = 'planetscale_discovery';

-- Check for any remaining elevated privileges that might have been granted
SELECT
    grantee,
    table_schema,
    table_name,
    privilege_type
FROM information_schema.table_privileges
WHERE grantee = 'planetscale_discovery';
```

## Documentation

Record the following for audit purposes:

- Date and time of discovery analysis
- User who performed the analysis
- Cleanup completion confirmation
- Any issues encountered during cleanup
