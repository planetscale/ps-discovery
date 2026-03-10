-- PlanetScale Discovery Tool - SQL Script
-- This script collects comprehensive database metadata for environment analysis
-- Run this in your PostgreSQL database using psql or any PostgreSQL client
--
-- Usage: psql -h hostname -U username -d database -f planetscale_discovery.sql -o discovery_output.txt
--
-- IMPORTANT: This script only collects metadata - no actual data is queried
-- All queries are read-only and require minimal permissions

\echo '=================================================================================='
\echo 'PlanetScale PostgreSQL Discovery Analysis'
\echo 'Generated at: ' :CURRENT_TIMESTAMP
\echo '=================================================================================='
\echo ''

-- Set output format for better readability
\pset border 2
\pset format wrapped

-- ==================================================================================
-- SECTION 1: VERSION AND CONFIGURATION
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 1: VERSION AND CONFIGURATION'
\echo '=================================================================================='
\echo ''

\echo '--- PostgreSQL Version Information ---'
SELECT version() AS postgresql_version;

\echo ''
\echo '--- Server Version Details ---'
SELECT
    current_setting('server_version') AS version,
    current_setting('server_version_num') AS version_num;

\echo ''
\echo '--- Key Configuration Settings (Modified from defaults) ---'
SELECT
    name,
    setting,
    unit,
    category,
    source,
    context
FROM pg_settings
WHERE source != 'default'
ORDER BY category, name;

\echo ''
\echo '--- Memory Configuration ---'
SELECT
    name,
    setting,
    unit,
    source
FROM pg_settings
WHERE name IN (
    'shared_buffers',
    'work_mem',
    'maintenance_work_mem',
    'effective_cache_size',
    'temp_buffers',
    'max_connections'
)
ORDER BY name;

\echo ''
\echo '--- WAL and Replication Configuration ---'
SELECT
    name,
    setting,
    unit,
    source
FROM pg_settings
WHERE name IN (
    'wal_level',
    'max_wal_senders',
    'max_replication_slots',
    'archive_mode',
    'synchronous_commit',
    'max_wal_size',
    'min_wal_size'
)
ORDER BY name;

-- ==================================================================================
-- SECTION 2: DATABASE CATALOG
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 2: DATABASE CATALOG'
\echo '=================================================================================='
\echo ''

\echo '--- All Databases ---'
SELECT
    d.datname AS database_name,
    pg_encoding_to_char(d.encoding) AS encoding,
    d.datcollate AS collation,
    d.datctype AS ctype,
    pg_size_pretty(pg_database_size(d.datname)) AS size,
    r.rolname AS owner
FROM pg_database d
JOIN pg_roles r ON d.datdba = r.oid
WHERE d.datname NOT IN ('template0', 'template1')
ORDER BY pg_database_size(d.datname) DESC;

\echo ''
\echo '--- Current Database Name ---'
SELECT current_database() AS current_database;

-- ==================================================================================
-- SECTION 3: SCHEMA ANALYSIS
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 3: SCHEMA ANALYSIS'
\echo '=================================================================================='
\echo ''

\echo '--- Schema Inventory ---'
SELECT
    n.nspname AS schema_name,
    r.rolname AS owner,
    CASE
        WHEN n.nspname LIKE 'pg_%' OR n.nspname = 'information_schema'
        THEN 'system'
        ELSE 'user'
    END AS schema_type,
    COUNT(c.oid) FILTER (WHERE c.relkind = 'r') AS table_count,
    COUNT(c.oid) FILTER (WHERE c.relkind = 'v') AS view_count,
    COUNT(c.oid) FILTER (WHERE c.relkind = 'm') AS mat_view_count,
    COUNT(c.oid) FILTER (WHERE c.relkind = 'S') AS sequence_count
FROM pg_namespace n
JOIN pg_roles r ON n.nspowner = r.oid
LEFT JOIN pg_class c ON c.relnamespace = n.oid
GROUP BY n.nspname, r.rolname
ORDER BY schema_type, n.nspname;

\echo ''
\echo '--- Table Summary by Schema ---'
SELECT
    schemaname,
    COUNT(*) AS table_count,
    pg_size_pretty(SUM(pg_total_relation_size(schemaname||'.'||tablename))) AS total_size
FROM pg_tables
WHERE schemaname NOT IN ('information_schema', 'pg_catalog')
GROUP BY schemaname
ORDER BY SUM(pg_total_relation_size(schemaname||'.'||tablename)) DESC;

-- ==================================================================================
-- SECTION 4: TABLE DETAILS
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 4: TABLE DETAILS'
\echo '=================================================================================='
\echo ''

\echo '--- Table Inventory (Top 50 by size) ---'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    r.rolname AS owner,
    c.reltuples::bigint AS estimated_rows,
    pg_size_pretty(pg_relation_size(c.oid)) AS table_size,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size_with_indexes,
    CASE c.relpersistence
        WHEN 'p' THEN 'permanent'
        WHEN 'u' THEN 'unlogged'
        WHEN 't' THEN 'temporary'
    END AS persistence,
    c.relhasindex AS has_indexes,
    c.relhastriggers AS has_triggers
FROM pg_class c
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_roles r ON c.relowner = r.oid
WHERE c.relkind IN ('r', 'p')
AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 50;

\echo ''
\echo '--- Column Details for User Tables (Sample) ---'
\echo 'Note: Showing details for largest tables only to reduce output size'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    a.attname AS column_name,
    a.attnum AS column_number,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
    a.attnotnull AS not_null,
    a.atthasdef AS has_default,
    pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default_value
FROM pg_attribute a
JOIN pg_class c ON a.attrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
LEFT JOIN pg_attrdef d ON (a.attrelid = d.adrelid AND a.attnum = d.adnum)
WHERE a.attnum > 0
AND NOT a.attisdropped
AND c.relkind = 'r'
AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
AND c.oid IN (
    SELECT c2.oid FROM pg_class c2
    JOIN pg_namespace n2 ON c2.relnamespace = n2.oid
    WHERE c2.relkind = 'r'
    AND n2.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
    ORDER BY pg_total_relation_size(c2.oid) DESC
    LIMIT 20
)
ORDER BY n.nspname, c.relname, a.attnum;

-- ==================================================================================
-- SECTION 5: INDEX ANALYSIS
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 5: INDEX ANALYSIS'
\echo '=================================================================================='
\echo ''

\echo '--- Index Inventory ---'
SELECT
    n.nspname AS schema_name,
    t.relname AS table_name,
    i.relname AS index_name,
    am.amname AS index_type,
    pg_size_pretty(pg_relation_size(i.oid)) AS index_size,
    ix.indisunique AS is_unique,
    ix.indisprimary AS is_primary,
    pg_get_indexdef(i.oid) AS index_definition
FROM pg_class i
JOIN pg_index ix ON i.oid = ix.indexrelid
JOIN pg_class t ON ix.indrelid = t.oid
JOIN pg_namespace n ON i.relnamespace = n.oid
JOIN pg_am am ON i.relam = am.oid
WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY pg_relation_size(i.oid) DESC;

\echo ''
\echo '--- Index Usage Statistics ---'
SELECT
    schemaname,
    tablename,
    indexrelname AS index_name,
    idx_scan AS scans,
    idx_tup_read AS tuples_read,
    idx_tup_fetch AS tuples_fetched,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC, pg_relation_size(indexrelid) DESC
LIMIT 50;

\echo ''
\echo '--- Potentially Unused Indexes (0 scans) ---'
SELECT
    schemaname,
    tablename,
    indexrelname AS index_name,
    pg_size_pretty(pg_relation_size(indexrelid)) AS wasted_size
FROM pg_stat_user_indexes
WHERE idx_scan = 0
AND indexrelname NOT LIKE '%pkey'
ORDER BY pg_relation_size(indexrelid) DESC;

-- ==================================================================================
-- SECTION 6: CONSTRAINTS AND RELATIONSHIPS
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 6: CONSTRAINTS AND RELATIONSHIPS'
\echo '=================================================================================='
\echo ''

\echo '--- Constraint Summary ---'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    con.conname AS constraint_name,
    CASE con.contype
        WHEN 'c' THEN 'CHECK'
        WHEN 'f' THEN 'FOREIGN KEY'
        WHEN 'p' THEN 'PRIMARY KEY'
        WHEN 'u' THEN 'UNIQUE'
        WHEN 't' THEN 'TRIGGER'
        WHEN 'x' THEN 'EXCLUSION'
    END AS constraint_type,
    pg_get_constraintdef(con.oid) AS constraint_definition
FROM pg_constraint con
JOIN pg_class c ON con.conrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY n.nspname, c.relname, con.contype;

\echo ''
\echo '--- Foreign Key Relationships ---'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    con.conname AS fk_name,
    f_n.nspname AS referenced_schema,
    f_c.relname AS referenced_table,
    pg_get_constraintdef(con.oid) AS fk_definition
FROM pg_constraint con
JOIN pg_class c ON con.conrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
LEFT JOIN pg_class f_c ON con.confrelid = f_c.oid
LEFT JOIN pg_namespace f_n ON f_c.relnamespace = f_n.oid
WHERE con.contype = 'f'
AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY n.nspname, c.relname;

-- ==================================================================================
-- SECTION 7: FUNCTIONS, TRIGGERS, AND VIEWS
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 7: FUNCTIONS, TRIGGERS, AND VIEWS'
\echo '=================================================================================='
\echo ''

\echo '--- Function/Procedure Inventory ---'
SELECT
    n.nspname AS schema_name,
    p.proname AS function_name,
    l.lanname AS language,
    p.pronargs AS num_args,
    pg_get_function_arguments(p.oid) AS arguments,
    pg_get_function_result(p.oid) AS return_type,
    CASE p.provolatile
        WHEN 'i' THEN 'IMMUTABLE'
        WHEN 's' THEN 'STABLE'
        WHEN 'v' THEN 'VOLATILE'
    END AS volatility,
    r.rolname AS owner
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
JOIN pg_language l ON p.prolang = l.oid
JOIN pg_roles r ON p.proowner = r.oid
WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY n.nspname, p.proname;

\echo ''
\echo '--- Trigger Inventory ---'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    t.tgname AS trigger_name,
    p.proname AS function_name,
    CASE
        WHEN t.tgenabled = 'O' THEN 'enabled'
        WHEN t.tgenabled = 'D' THEN 'disabled'
        WHEN t.tgenabled = 'R' THEN 'replica'
        WHEN t.tgenabled = 'A' THEN 'always'
    END AS status,
    pg_get_triggerdef(t.oid) AS trigger_definition
FROM pg_trigger t
JOIN pg_class c ON t.tgrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_proc p ON t.tgfoid = p.oid
WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
AND NOT t.tgisinternal
ORDER BY n.nspname, c.relname, t.tgname;

\echo ''
\echo '--- View Inventory ---'
SELECT
    n.nspname AS schema_name,
    c.relname AS view_name,
    CASE c.relkind
        WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'materialized view'
    END AS view_type,
    r.rolname AS owner,
    CASE
        WHEN c.relkind = 'm' THEN pg_size_pretty(pg_relation_size(c.oid))
        ELSE 'N/A'
    END AS size
FROM pg_class c
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_roles r ON c.relowner = r.oid
WHERE c.relkind IN ('v', 'm')
AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY n.nspname, c.relname;

\echo ''
\echo '--- Sequence Inventory ---'
SELECT
    schemaname AS schema_name,
    sequencename AS sequence_name,
    start_value,
    min_value,
    max_value,
    increment_by,
    cycle AS is_cycle,
    cache_size,
    last_value
FROM pg_sequences
WHERE schemaname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY schemaname, sequencename;

-- ==================================================================================
-- SECTION 8: EXTENSIONS AND ADVANCED FEATURES
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 8: EXTENSIONS AND ADVANCED FEATURES'
\echo '=================================================================================='
\echo ''

\echo '--- Installed Extensions ---'
SELECT
    e.extname AS extension_name,
    e.extversion AS version,
    n.nspname AS schema_name,
    c.description
FROM pg_extension e
JOIN pg_namespace n ON e.extnamespace = n.oid
LEFT JOIN pg_description c ON c.objoid = e.oid
    AND c.classoid = 'pg_extension'::regclass
ORDER BY e.extname;

\echo ''
\echo '--- Custom Data Types (Enums) ---'
SELECT
    n.nspname AS schema_name,
    t.typname AS type_name,
    array_agg(e.enumlabel ORDER BY e.enumsortorder) AS enum_values
FROM pg_type t
JOIN pg_namespace n ON t.typnamespace = n.oid
LEFT JOIN pg_enum e ON t.oid = e.enumtypid
WHERE t.typtype = 'e'
AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
GROUP BY n.nspname, t.typname
ORDER BY n.nspname, t.typname;

\echo ''
\echo '--- Custom Data Types (Composite) ---'
SELECT
    n.nspname AS schema_name,
    t.typname AS type_name,
    r.rolname AS owner
FROM pg_type t
JOIN pg_namespace n ON t.typnamespace = n.oid
JOIN pg_roles r ON t.typowner = r.oid
WHERE t.typtype = 'c'
AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY n.nspname, t.typname;

\echo ''
\echo '--- Tables with JSON/JSONB Columns ---'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    a.attname AS column_name,
    format_type(a.atttypid, a.atttypmod) AS data_type
FROM pg_attribute a
JOIN pg_class c ON a.attrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_type t ON a.atttypid = t.oid
WHERE t.typname IN ('json', 'jsonb')
AND NOT a.attisdropped
AND c.relkind = 'r'
AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY n.nspname, c.relname, a.attname;

\echo ''
\echo '--- Tables with Array Columns ---'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    a.attname AS column_name,
    format_type(a.atttypid, a.atttypmod) AS data_type,
    a.attndims AS array_dimensions
FROM pg_attribute a
JOIN pg_class c ON a.attrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE a.attndims > 0
AND NOT a.attisdropped
AND c.relkind = 'r'
AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY n.nspname, c.relname, a.attname;

\echo ''
\echo '--- Foreign Data Wrappers ---'
SELECT
    fdwname AS fdw_name,
    fdwowner::regrole AS owner,
    fdwhandler::regproc AS handler,
    fdwoptions AS options
FROM pg_foreign_data_wrapper
ORDER BY fdwname;

\echo ''
\echo '--- Foreign Tables ---'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    s.srvname AS server_name
FROM pg_class c
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_foreign_table ft ON c.oid = ft.ftrelid
JOIN pg_foreign_server s ON ft.ftserver = s.oid
WHERE c.relkind = 'f'
ORDER BY n.nspname, c.relname;

-- ==================================================================================
-- SECTION 9: PARTITIONING
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 9: PARTITIONING'
\echo '=================================================================================='
\echo ''

\echo '--- Partitioned Tables (PostgreSQL 10+) ---'
\echo 'Note: Will show empty results for PostgreSQL versions before 10'
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    pg_get_partkeydef(c.oid) AS partition_key,
    c.reltuples::bigint AS estimated_rows
FROM pg_class c
JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE c.relkind = 'p'
AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY n.nspname, c.relname;

\echo ''
\echo '--- Partition Details ---'
SELECT
    parent_ns.nspname AS parent_schema,
    parent_class.relname AS parent_table,
    child_ns.nspname AS partition_schema,
    child_class.relname AS partition_name,
    pg_size_pretty(pg_relation_size(child_class.oid)) AS partition_size,
    child_class.reltuples::bigint AS estimated_rows
FROM pg_inherits i
JOIN pg_class parent_class ON i.inhparent = parent_class.oid
JOIN pg_namespace parent_ns ON parent_class.relnamespace = parent_ns.oid
JOIN pg_class child_class ON i.inhrelid = child_class.oid
JOIN pg_namespace child_ns ON child_class.relnamespace = child_ns.oid
WHERE parent_ns.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
ORDER BY parent_ns.nspname, parent_class.relname, child_ns.nspname, child_class.relname;

-- ==================================================================================
-- SECTION 10: PERFORMANCE STATISTICS
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 10: PERFORMANCE STATISTICS'
\echo '=================================================================================='
\echo ''

\echo '--- Database Activity Statistics ---'
SELECT
    datname AS database_name,
    numbackends AS active_connections,
    xact_commit AS transactions_committed,
    xact_rollback AS transactions_rolled_back,
    blks_read AS blocks_read_from_disk,
    blks_hit AS blocks_hit_in_cache,
    ROUND(100.0 * blks_hit / NULLIF(blks_hit + blks_read, 0), 2) AS cache_hit_ratio_pct,
    tup_returned AS tuples_returned,
    tup_fetched AS tuples_fetched,
    tup_inserted AS tuples_inserted,
    tup_updated AS tuples_updated,
    tup_deleted AS tuples_deleted,
    conflicts,
    deadlocks
FROM pg_stat_database
WHERE datname IS NOT NULL
ORDER BY datname;

\echo ''
\echo '--- Table Access Statistics (Top 50) ---'
SELECT
    schemaname,
    relname AS tablename,
    seq_scan AS sequential_scans,
    seq_tup_read AS seq_tuples_read,
    idx_scan AS index_scans,
    idx_tup_fetch AS idx_tuples_fetched,
    n_tup_ins AS tuples_inserted,
    n_tup_upd AS tuples_updated,
    n_tup_del AS tuples_deleted,
    n_live_tup AS live_tuples,
    n_dead_tup AS dead_tuples,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
ORDER BY seq_scan + COALESCE(idx_scan, 0) DESC
LIMIT 50;

\echo ''
\echo '--- Connection Summary ---'
SELECT
    state,
    COUNT(*) AS connection_count
FROM pg_stat_activity
GROUP BY state
ORDER BY connection_count DESC;

\echo ''
\echo '--- Active Connections Detail ---'
SELECT
    pid,
    usename AS username,
    datname AS database,
    application_name,
    client_addr,
    state,
    query_start,
    state_change,
    wait_event_type,
    wait_event
FROM pg_stat_activity
WHERE state != 'idle'
AND pid != pg_backend_pid()
ORDER BY query_start;

\echo ''
\echo '--- Long Running Queries (if any) ---'
SELECT
    pid,
    usename AS username,
    datname AS database,
    state,
    EXTRACT(EPOCH FROM (now() - query_start))::int AS duration_seconds,
    LEFT(query, 100) AS query_preview
FROM pg_stat_activity
WHERE state != 'idle'
AND query_start IS NOT NULL
AND EXTRACT(EPOCH FROM (now() - query_start)) > 60
AND pid != pg_backend_pid()
ORDER BY query_start;

-- ==================================================================================
-- SECTION 11: REPLICATION STATUS
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 11: REPLICATION STATUS'
\echo '=================================================================================='
\echo ''

\echo '--- Server Role (Primary or Standby) ---'
SELECT
    CASE WHEN pg_is_in_recovery()
        THEN 'STANDBY'
        ELSE 'PRIMARY'
    END AS server_role,
    pg_is_in_recovery() AS is_in_recovery;

\echo ''
\echo '--- Replication Slots (Primary only) ---'
SELECT
    slot_name,
    slot_type,
    database,
    active,
    active_pid,
    restart_lsn,
    confirmed_flush_lsn
FROM pg_replication_slots
ORDER BY slot_name;

\echo ''
\echo '--- Active Replication Connections (Primary only) ---'
SELECT
    pid,
    usename,
    application_name,
    client_addr,
    client_hostname,
    state,
    sent_lsn,
    write_lsn,
    flush_lsn,
    replay_lsn,
    sync_state,
    sync_priority
FROM pg_stat_replication
ORDER BY application_name;

-- ==================================================================================
-- SECTION 12: SECURITY AND USERS
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 12: SECURITY AND USERS'
\echo '=================================================================================='
\echo ''

\echo '--- User/Role Inventory ---'
SELECT
    rolname AS role_name,
    rolsuper AS is_superuser,
    rolinherit AS can_inherit,
    rolcreaterole AS can_create_role,
    rolcreatedb AS can_create_db,
    rolcanlogin AS can_login,
    rolreplication AS is_replication,
    rolconnlimit AS connection_limit,
    rolvaliduntil AS valid_until
FROM pg_roles
ORDER BY rolname;

\echo ''
\echo '--- Row Level Security Policies ---'
SELECT
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd AS command,
    qual AS using_expression
FROM pg_policies
ORDER BY schemaname, tablename, policyname;

-- ==================================================================================
-- SECTION 13: QUERY PERFORMANCE (if pg_stat_statements available)
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'SECTION 13: QUERY PERFORMANCE (requires pg_stat_statements extension)'
\echo '=================================================================================='
\echo ''

\echo '--- Check if pg_stat_statements is available ---'
SELECT
    CASE WHEN EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')
        THEN 'INSTALLED'
        ELSE 'NOT INSTALLED'
    END AS pg_stat_statements_status;

\echo ''
\echo '--- Top Queries by Total Time (if extension available) ---'
\echo 'Note: Will error if pg_stat_statements is not installed - this is expected'
SELECT
    queryid,
    LEFT(query, 100) AS query_preview,
    calls,
    ROUND(total_exec_time::numeric, 2) AS total_time_ms,
    ROUND(mean_exec_time::numeric, 2) AS mean_time_ms,
    ROUND(100.0 * shared_blks_hit / NULLIF(shared_blks_hit + shared_blks_read, 0), 2) AS cache_hit_pct
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;

-- ==================================================================================
-- COMPLETION
-- ==================================================================================

\echo ''
\echo '=================================================================================='
\echo 'DISCOVERY COMPLETE'
\echo '=================================================================================='
\echo ''
\echo 'Discovery analysis completed successfully.'
\echo 'Results have been saved to the output file.'
\echo ''
\echo 'Generated at: ' :CURRENT_TIMESTAMP
\echo '=================================================================================='
