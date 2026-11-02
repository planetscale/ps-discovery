-- Citus coverage dataset for discovery testing.
--
-- Creates every table kind that discovery labels, the regular PostgreSQL
-- variants it must leave unchanged, and the Citus cases it handles specially,
-- with enough data (about 6 GB logical) for sizes, row estimates, column
-- statistics and index counters to be measurable. It needs no other table on
-- the cluster.
--
-- Citus tables:
--   cov.orders_hash          hash distributed table, 8 shards
--   cov.orders_replicated    hash distributed table with 2 placements per shard
--                            (citus.shard_replication_factor = 2), with updates
--                            and deletes, so write counters differ from reads
--   cov.currencies           reference table (a foreign key target)
--   cov.invoices             Citus local table, converted automatically by a
--                            foreign key to a reference table (autoconverted)
--   cov.app_settings         Citus local table, added explicitly
--   cov.audit_log            single-shard distributed table, no distribution column
--   cov.events               partitioned distributed table, 3 monthly partitions
--   cov_tenant_acme.*        tables in a distributed schema (schema-based sharding)
--   cov_tenant_globex.*      a second distributed schema, smaller
--
-- Statistics edge states:
--   cov.empty_analyzed       distributed, analyzed while empty: 0 rows
--   cov.never_analyzed       distributed, autovacuum off, never analyzed: -1 rows
--   cov.partly_analyzed      distributed, autovacuum off, one shard analyzed
--
-- Identifiers that Citus must quote or shorten in shard names:
--   "Cov Mixed"."Order Lines"  quoted, mixed case, with a space, and a quoted
--                              distribution column and index
--   cov.measurement_rollups_by_region_and_hour_for_discovery_tests
--                              a 58-character name, and a 62-character index
--                              name, which exceed 63 characters with a shard id
--
-- Other Citus objects:
--   cov.tenant_order_count() a distributed function
--
-- Regular PostgreSQL tables, which discovery must report unchanged:
--   cov.regular_notes        a regular table in a schema with Citus tables
--   cov.audit_log_999999     a regular table named like a shard of cov.audit_log
--   cov.log_20261029101112123456
--                            a regular table whose digit suffix exceeds bigint
--   cov_plain.metrics        a regular partitioned table, 2 partitions and a default
--   cov_plain.legacy_child   a regular table inheriting from cov_plain.legacy_parent
--
-- Optional sections at the end, commented out: an invalid index from a failed
-- CREATE INDEX CONCURRENTLY, an index created with DDL propagation off (which
-- stops discovery on purpose), a foreign table added to the Citus metadata
-- (which needs postgres_fdw on every node), and a columnar table.
--
-- Run it on the coordinator as a superuser. It needs no extension besides
-- citus, and the replicated table needs at least two worker nodes:
--
--   psql "<coordinator DSN>" -f test-data/citus-coverage.sql
--
-- It is idempotent: the schemas it creates, and the server of the optional
-- foreign table, are dropped first.
-- The teardown at the end of the file removes everything it created.

\set ON_ERROR_STOP on
\timing on

DROP SCHEMA IF EXISTS cov CASCADE;
DROP SCHEMA IF EXISTS cov_tenant_acme CASCADE;
DROP SCHEMA IF EXISTS cov_tenant_globex CASCADE;
DROP SCHEMA IF EXISTS cov_plain CASCADE;
DROP SCHEMA IF EXISTS "Cov Mixed" CASCADE;
DROP SERVER IF EXISTS cov_loopback CASCADE;

-- Every schema below is a regular schema unless it is created with schema-based
-- sharding turned on explicitly.
SET citus.enable_schema_based_sharding TO off;
CREATE SCHEMA cov;
CREATE SCHEMA cov_plain;
CREATE SCHEMA "Cov Mixed";

-- ---------------------------------------------------------------------------
-- Hash distributed table: the common case, 8 shards spread across the workers.
-- About 2,000,000 rows.
-- ---------------------------------------------------------------------------

CREATE TABLE cov.orders_hash (
    tenant_id int NOT NULL,
    order_id bigint NOT NULL,
    status text NOT NULL,
    total numeric(12, 2) NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, order_id)
);
SELECT create_distributed_table('cov.orders_hash', 'tenant_id',
                                shard_count => 8, colocate_with => 'none');

INSERT INTO cov.orders_hash
SELECT 1 + (g % 5000), g,
       (ARRAY['open', 'paid', 'shipped', 'cancelled'])[1 + g % 4],
       round((random() * 1000)::numeric, 2),
       timestamptz '2026-01-01' + g * interval '12 seconds'
FROM generate_series(1, 2000000) AS g;

CREATE INDEX orders_hash_status_idx ON cov.orders_hash (status);

-- ---------------------------------------------------------------------------
-- Replicated distributed table: 2 placements per shard. A write is applied to
-- both placements and a read runs on one, so discovery takes each shard's
-- largest write counter, sums read counters over both placements, and takes
-- rows and sizes from one placement per shard. About 1,000,000 rows, then
-- updates and deletes.
-- ---------------------------------------------------------------------------

SET citus.shard_replication_factor TO 2;
CREATE TABLE cov.orders_replicated (
    tenant_id int NOT NULL,
    order_id bigint NOT NULL,
    status text NOT NULL,
    total numeric(12, 2) NOT NULL,
    PRIMARY KEY (tenant_id, order_id)
);
SELECT create_distributed_table('cov.orders_replicated', 'tenant_id',
                                shard_count => 8, colocate_with => 'none');
RESET citus.shard_replication_factor;

INSERT INTO cov.orders_replicated
SELECT 1 + (g % 2000), g,
       (ARRAY['open', 'paid', 'shipped'])[1 + g % 3],
       round((random() * 500)::numeric, 2)
FROM generate_series(1, 1000000) AS g;

UPDATE cov.orders_replicated SET status = 'refunded' WHERE order_id % 10 = 0;
DELETE FROM cov.orders_replicated WHERE order_id % 50 = 0;

CREATE INDEX orders_replicated_status_idx ON cov.orders_replicated (status);

-- ---------------------------------------------------------------------------
-- Reference table: the target of the foreign key that converts cov.invoices.
-- ---------------------------------------------------------------------------

CREATE TABLE cov.currencies (
    code char(3) PRIMARY KEY,
    name text NOT NULL
);
SELECT create_reference_table('cov.currencies');

INSERT INTO cov.currencies
SELECT upper(substr(md5(g::text), 1, 3)), 'Currency ' || g
FROM generate_series(1, 400) AS g
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- Citus local table, converted automatically (autoconverted = true): a
-- foreign key from a regular table to a reference table converts it, because
-- citus.enable_local_reference_table_foreign_keys is on by default. About
-- 2,000,000 rows, 20% NULL memo.
-- ---------------------------------------------------------------------------

CREATE TABLE cov.invoices (
    invoice_id bigint PRIMARY KEY,
    currency char(3) NOT NULL,
    amount numeric(12, 2) NOT NULL,
    issued_at timestamptz NOT NULL,
    memo text
);

INSERT INTO cov.invoices
SELECT g,
       c.codes[1 + (g % array_length(c.codes, 1))],
       round((random() * 10000)::numeric, 2),
       timestamptz '2026-01-01' + g * interval '15 seconds',
       CASE WHEN g % 5 = 0 THEN NULL ELSE repeat(md5(g::text), 3) END
FROM generate_series(1, 2000000) AS g,
     (SELECT array_agg(code ORDER BY code) AS codes FROM cov.currencies) AS c;

CREATE INDEX invoices_currency_idx ON cov.invoices (currency);

ALTER TABLE cov.invoices
    ADD CONSTRAINT invoices_currency_fk
    FOREIGN KEY (currency) REFERENCES cov.currencies (code);

-- ---------------------------------------------------------------------------
-- Citus local table, added explicitly (autoconverted = false). About
-- 1,000,000 rows. Its data stays on the coordinator.
-- ---------------------------------------------------------------------------

CREATE TABLE cov.app_settings (
    setting_id bigint PRIMARY KEY,
    scope text NOT NULL,
    setting_key text NOT NULL,
    setting_value text
);

INSERT INTO cov.app_settings
SELECT g,
       (ARRAY['global', 'team', 'user'])[1 + g % 3],
       'key_' || (g % 5000),
       repeat(md5(g::text), 2)
FROM generate_series(1, 1000000) AS g;

CREATE INDEX app_settings_key_idx ON cov.app_settings (setting_key);

SELECT citus_add_local_table_to_metadata('cov.app_settings');

-- ---------------------------------------------------------------------------
-- Single-shard distributed table: no distribution column, one shard on a
-- worker. Labeled 'distributed', with distribution_column null and
-- shard_count 1. About 3,000,000 rows.
-- ---------------------------------------------------------------------------

CREATE TABLE cov.audit_log (
    log_id bigint PRIMARY KEY,
    actor text NOT NULL,
    action text NOT NULL,
    logged_at timestamptz NOT NULL,
    detail text
);
SELECT create_distributed_table('cov.audit_log', NULL, colocate_with => 'none');

INSERT INTO cov.audit_log
SELECT g,
       'user_' || (g % 1000),
       (ARRAY['create', 'update', 'delete', 'login'])[1 + g % 4],
       timestamptz '2026-01-01' + g * interval '10 seconds',
       repeat(md5(g::text), 2)
FROM generate_series(1, 3000000) AS g;

CREATE INDEX audit_log_actor_idx ON cov.audit_log (actor);

-- ---------------------------------------------------------------------------
-- Partitioned distributed table: distributed on device_id, range partitioned
-- by month. Each partition is a distributed table of its own, with 16 shards.
-- About 12,000,000 rows, loaded in four batches so that the coordinator
-- routes each batch without holding all rows at once.
--
-- Columns are chosen to exercise each n_distinct method:
--   device_id   distribution column           -> distribution_column_sum
--   event_id    unique per shard              -> weighted_fraction
--   kind        5 values in every shard       -> max_absolute
--   score       10% NULL                      -> weighted null_frac
-- ---------------------------------------------------------------------------

CREATE TABLE cov.events (
    event_id bigint NOT NULL,
    device_id int NOT NULL,
    created_at timestamptz NOT NULL,
    kind text NOT NULL,
    score double precision,
    payload text,
    PRIMARY KEY (device_id, event_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE cov.events_2026_08 PARTITION OF cov.events
    FOR VALUES FROM ('2026-08-01 00:00:00+00') TO ('2026-09-01 00:00:00+00');
CREATE TABLE cov.events_2026_09 PARTITION OF cov.events
    FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');
CREATE TABLE cov.events_2026_10 PARTITION OF cov.events
    FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');

SELECT create_distributed_table('cov.events', 'device_id', shard_count => 16);

CREATE INDEX events_kind_idx ON cov.events (kind);

-- 92 days from 2026-08-01 00:00 UTC ends at 2026-11-01 00:00 UTC, so every
-- generated time falls in a partition.
INSERT INTO cov.events
SELECT g,
       1 + (g % 10000),
       timestamptz '2026-08-01 00:00:00+00'
           + ((g::bigint * 7919) % (92 * 86400)) * interval '1 second',
       (ARRAY['click', 'view', 'purchase', 'error', 'scroll'])[1 + g % 5],
       CASE WHEN g % 10 = 0 THEN NULL ELSE random() * 100 END,
       repeat(md5(g::text), 2)
FROM generate_series(1, 3000000) AS g;

INSERT INTO cov.events
SELECT g,
       1 + (g % 10000),
       timestamptz '2026-08-01 00:00:00+00'
           + ((g::bigint * 7919) % (92 * 86400)) * interval '1 second',
       (ARRAY['click', 'view', 'purchase', 'error', 'scroll'])[1 + g % 5],
       CASE WHEN g % 10 = 0 THEN NULL ELSE random() * 100 END,
       repeat(md5(g::text), 2)
FROM generate_series(3000001, 6000000) AS g;

INSERT INTO cov.events
SELECT g,
       1 + (g % 10000),
       timestamptz '2026-08-01 00:00:00+00'
           + ((g::bigint * 7919) % (92 * 86400)) * interval '1 second',
       (ARRAY['click', 'view', 'purchase', 'error', 'scroll'])[1 + g % 5],
       CASE WHEN g % 10 = 0 THEN NULL ELSE random() * 100 END,
       repeat(md5(g::text), 2)
FROM generate_series(6000001, 9000000) AS g;

INSERT INTO cov.events
SELECT g,
       1 + (g % 10000),
       timestamptz '2026-08-01 00:00:00+00'
           + ((g::bigint * 7919) % (92 * 86400)) * interval '1 second',
       (ARRAY['click', 'view', 'purchase', 'error', 'scroll'])[1 + g % 5],
       CASE WHEN g % 10 = 0 THEN NULL ELSE random() * 100 END,
       repeat(md5(g::text), 2)
FROM generate_series(9000001, 12000000) AS g;

-- ---------------------------------------------------------------------------
-- Schema-based sharding: every table created in a distributed schema is a
-- single-shard table, colocated with the schema's other tables. Labeled
-- 'schema'. Two tenants of different sizes, so their shards differ.
-- ---------------------------------------------------------------------------

SET citus.enable_schema_based_sharding TO on;
CREATE SCHEMA cov_tenant_acme;
CREATE SCHEMA cov_tenant_globex;
SET citus.enable_schema_based_sharding TO off;

CREATE TABLE cov_tenant_acme.orders (
    order_id bigint PRIMARY KEY,
    customer_id int NOT NULL,
    status text NOT NULL,
    total numeric(12, 2) NOT NULL,
    placed_at timestamptz NOT NULL,
    notes text
);
CREATE TABLE cov_tenant_acme.order_items (
    order_id bigint NOT NULL REFERENCES cov_tenant_acme.orders (order_id),
    line_no int NOT NULL,
    sku text NOT NULL,
    quantity int NOT NULL,
    price numeric(10, 2) NOT NULL,
    PRIMARY KEY (order_id, line_no)
);

CREATE TABLE cov_tenant_globex.orders (LIKE cov_tenant_acme.orders INCLUDING ALL);
CREATE TABLE cov_tenant_globex.order_items (
    LIKE cov_tenant_acme.order_items INCLUDING ALL,
    FOREIGN KEY (order_id) REFERENCES cov_tenant_globex.orders (order_id)
);

INSERT INTO cov_tenant_acme.orders
SELECT g,
       1 + (g % 50000),
       (ARRAY['open', 'paid', 'shipped', 'cancelled'])[1 + g % 4],
       round((random() * 500)::numeric, 2),
       timestamptz '2026-01-01' + g * interval '20 seconds',
       CASE WHEN g % 3 = 0 THEN repeat(md5(g::text), 2) END
FROM generate_series(1, 1500000) AS g;

INSERT INTO cov_tenant_acme.order_items
SELECT o, l, 'SKU-' || ((o * 31 + l) % 20000), 1 + (o + l) % 5,
       round((random() * 100)::numeric, 2)
FROM generate_series(1, 1500000) AS o, generate_series(1, 3) AS l;

INSERT INTO cov_tenant_globex.orders
SELECT g,
       1 + (g % 8000),
       (ARRAY['open', 'paid', 'shipped', 'cancelled'])[1 + g % 4],
       round((random() * 500)::numeric, 2),
       timestamptz '2026-01-01' + g * interval '60 seconds',
       CASE WHEN g % 3 = 0 THEN repeat(md5(g::text), 2) END
FROM generate_series(1, 500000) AS g;

INSERT INTO cov_tenant_globex.order_items
SELECT o, l, 'SKU-' || ((o * 31 + l) % 20000), 1 + (o + l) % 5,
       round((random() * 100)::numeric, 2)
FROM generate_series(1, 500000) AS o, generate_series(1, 3) AS l;

-- ---------------------------------------------------------------------------
-- Statistics edge states. reltuples is -1 until a shard is first analyzed or
-- vacuumed, and 0 once an empty shard is analyzed. autovacuum is turned off on
-- the last two tables, and Citus copies that setting to their shards, so
-- nothing analyzes them behind this file's back. They have no index, because
-- building an index also sets reltuples.
-- ---------------------------------------------------------------------------

CREATE TABLE cov.empty_analyzed (tenant_id int NOT NULL, note text);
SELECT create_distributed_table('cov.empty_analyzed', 'tenant_id',
                                shard_count => 4, colocate_with => 'none');

CREATE TABLE cov.never_analyzed (tenant_id int NOT NULL, note text)
    WITH (autovacuum_enabled = false);
SELECT create_distributed_table('cov.never_analyzed', 'tenant_id',
                                shard_count => 4, colocate_with => 'none');
INSERT INTO cov.never_analyzed
SELECT 1 + (g % 1000), md5(g::text) FROM generate_series(1, 200000) AS g;

CREATE TABLE cov.partly_analyzed (tenant_id int NOT NULL, note text)
    WITH (autovacuum_enabled = false);
SELECT create_distributed_table('cov.partly_analyzed', 'tenant_id',
                                shard_count => 4, colocate_with => 'none');
INSERT INTO cov.partly_analyzed
SELECT 1 + (g % 1000), md5(g::text) FROM generate_series(1, 200000) AS g;

-- Analyze one shard only. ANALYZE of a relation that a node does not hold
-- fails on that node, which run_command_on_workers() reports instead of
-- raising.
SELECT nodename, success
FROM run_command_on_workers(
    (SELECT format('ANALYZE %s', shard_name('cov.partly_analyzed'::regclass, min(shardid)))
     FROM pg_dist_shard WHERE logicalrelid = 'cov.partly_analyzed'::regclass));

-- ---------------------------------------------------------------------------
-- Identifiers that need quoting or shortening. A shard name is the table's
-- name followed by _<shardid>; past 63 characters Citus shortens it and adds a
-- hash, and a name with capitals or spaces is quoted.
-- ---------------------------------------------------------------------------

CREATE TABLE "Cov Mixed"."Order Lines" (
    "Tenant Id" int NOT NULL,
    "Line No" int NOT NULL,
    "Amount" numeric(10, 2) NOT NULL,
    PRIMARY KEY ("Tenant Id", "Line No")
);
SELECT create_distributed_table('"Cov Mixed"."Order Lines"', 'Tenant Id',
                                shard_count => 4, colocate_with => 'none');
INSERT INTO "Cov Mixed"."Order Lines"
SELECT 1 + (g % 3000), g, round((random() * 100)::numeric, 2)
FROM generate_series(1, 600000) AS g;
CREATE INDEX "Amount Lookup" ON "Cov Mixed"."Order Lines" ("Amount");

CREATE TABLE cov.measurement_rollups_by_region_and_hour_for_discovery_tests (
    region_id int NOT NULL,
    hour timestamptz NOT NULL,
    reading double precision,
    PRIMARY KEY (region_id, hour)
);
SELECT create_distributed_table(
    'cov.measurement_rollups_by_region_and_hour_for_discovery_tests', 'region_id',
    shard_count => 4, colocate_with => 'none');
INSERT INTO cov.measurement_rollups_by_region_and_hour_for_discovery_tests
SELECT 1 + (g % 500), timestamptz '2026-01-01' + (g / 500) * interval '1 hour',
       random() * 100
FROM generate_series(0, 599999) AS g;
CREATE INDEX measurement_rollups_by_region_and_hour_for_discovery_tests_idx
    ON cov.measurement_rollups_by_region_and_hour_for_discovery_tests (reading);

-- ---------------------------------------------------------------------------
-- A distributed function, colocated with cov.orders_hash and routed by its
-- argument.
-- ---------------------------------------------------------------------------

CREATE FUNCTION cov.tenant_order_count(p_tenant int) RETURNS bigint
    LANGUAGE sql STABLE
    AS $$ SELECT count(*) FROM cov.orders_hash WHERE tenant_id = p_tenant $$;
SELECT create_distributed_function('cov.tenant_order_count(int)', 'p_tenant',
                                   colocate_with => 'cov.orders_hash');

-- ---------------------------------------------------------------------------
-- Regular PostgreSQL tables. None references a Citus table, so none is
-- converted, and discovery must report each with its own values.
-- ---------------------------------------------------------------------------

-- In a schema that also holds Citus tables.
CREATE TABLE cov.regular_notes (
    note_id bigint PRIMARY KEY,
    author text NOT NULL,
    body text
);
INSERT INTO cov.regular_notes
SELECT g, 'author_' || (g % 300), repeat(md5(g::text), 4)
FROM generate_series(1, 500000) AS g;

-- Named like a shard of cov.audit_log. 999999 is no shard id, so Citus does
-- not treat it as a shard, and neither may discovery.
CREATE TABLE cov.audit_log_999999 (log_id bigint PRIMARY KEY, actor text NOT NULL);
INSERT INTO cov.audit_log_999999
SELECT g, 'user_' || (g % 100) FROM generate_series(1, 100000) AS g;

-- A name whose 20-digit suffix exceeds bigint, so a shard id parsed from every
-- relation name on a node would fail the run.
CREATE TABLE cov.log_20261029101112123456 (entry_id bigint PRIMARY KEY, line text);
INSERT INTO cov.log_20261029101112123456
SELECT g, md5(g::text) FROM generate_series(1, 10000) AS g;

-- A regular partitioned table, with an index on the parent.
CREATE TABLE cov_plain.metrics (
    metric_id bigint NOT NULL,
    measured_on date NOT NULL,
    name text NOT NULL,
    value double precision,
    PRIMARY KEY (metric_id, measured_on)
) PARTITION BY RANGE (measured_on);
CREATE TABLE cov_plain.metrics_2026_h1 PARTITION OF cov_plain.metrics
    FOR VALUES FROM ('2026-01-01') TO ('2026-07-01');
CREATE TABLE cov_plain.metrics_2026_h2 PARTITION OF cov_plain.metrics
    FOR VALUES FROM ('2026-07-01') TO ('2027-01-01');
CREATE TABLE cov_plain.metrics_default PARTITION OF cov_plain.metrics DEFAULT;
CREATE INDEX metrics_name_idx ON cov_plain.metrics (name);
INSERT INTO cov_plain.metrics
SELECT g, date '2026-01-01' + (g % 500), 'metric_' || (g % 40), random() * 100
FROM generate_series(1, 1000000) AS g;

-- Table inheritance, which Citus cannot distribute.
CREATE TABLE cov_plain.legacy_parent (item_id bigint NOT NULL, label text);
CREATE TABLE cov_plain.legacy_child (extra text) INHERITS (cov_plain.legacy_parent);
INSERT INTO cov_plain.legacy_parent
SELECT g, 'parent_' || g FROM generate_series(1, 100000) AS g;
INSERT INTO cov_plain.legacy_child
SELECT g, 'child_' || g, md5(g::text) FROM generate_series(1, 200000) AS g;

-- ---------------------------------------------------------------------------
-- Statistics and activity. ANALYZE on the coordinator propagates to every
-- shard placement. The loop then makes index and sequential scans, so that
-- the scan counters are not all zero. cov.never_analyzed and
-- cov.partly_analyzed are left out on purpose.
-- ---------------------------------------------------------------------------

ANALYZE cov.orders_hash;
ANALYZE cov.orders_replicated;
ANALYZE cov.currencies;
ANALYZE cov.invoices;
ANALYZE cov.app_settings;
ANALYZE cov.audit_log;
ANALYZE cov.events;
ANALYZE cov_tenant_acme.orders;
ANALYZE cov_tenant_acme.order_items;
ANALYZE cov_tenant_globex.orders;
ANALYZE cov_tenant_globex.order_items;
ANALYZE cov.empty_analyzed;
ANALYZE "Cov Mixed"."Order Lines";
ANALYZE cov.measurement_rollups_by_region_and_hour_for_discovery_tests;
ANALYZE cov.regular_notes;
ANALYZE cov.audit_log_999999;
ANALYZE cov.log_20261029101112123456;
ANALYZE cov_plain.metrics;
ANALYZE cov_plain.legacy_parent;
ANALYZE cov_plain.legacy_child;

DO $$
DECLARE
    i int;
BEGIN
    FOR i IN 1..200 LOOP
        PERFORM count(*) FROM cov.events WHERE device_id = 1 + (i * 37) % 10000;
        PERFORM 1 FROM cov.audit_log WHERE log_id = i * 1000;
        PERFORM 1 FROM cov.invoices WHERE invoice_id = i * 500;
        PERFORM 1 FROM cov.app_settings WHERE setting_id = i * 250;
        PERFORM 1 FROM cov_tenant_acme.orders WHERE order_id = i * 700;
        PERFORM 1 FROM cov_tenant_globex.order_items WHERE order_id = i * 200;
        PERFORM 1 FROM cov.orders_hash WHERE tenant_id = 1 + i % 5000 AND order_id = i;
        PERFORM 1 FROM cov.orders_replicated
            WHERE tenant_id = 1 + i % 2000 AND order_id = i;
        PERFORM 1 FROM "Cov Mixed"."Order Lines"
            WHERE "Tenant Id" = 1 + i % 3000 AND "Line No" = i;
        PERFORM 1 FROM cov.regular_notes WHERE note_id = i * 100;
    END LOOP;
    PERFORM count(*) FROM cov.events WHERE kind = 'error';
    PERFORM count(*) FROM cov.audit_log WHERE actor = 'user_7';
    PERFORM count(*) FROM cov.orders_replicated WHERE status = 'refunded';
    PERFORM cov.tenant_order_count(42);
END
$$;

-- ---------------------------------------------------------------------------
-- What discovery should now see. Compare with the report.
-- ---------------------------------------------------------------------------

SELECT table_name, citus_table_type, distribution_column, colocation_id,
       shard_count, table_size
FROM citus_tables
WHERE table_name::text ILIKE '%cov%'
ORDER BY table_name::text;

SELECT logicalrelid, autoconverted
FROM pg_dist_partition
WHERE logicalrelid::text IN ('cov.invoices', 'cov.app_settings', 'cov.remote_currencies');

SELECT logicalrelid, shardid, count(*) AS placements
FROM pg_dist_shard JOIN pg_dist_placement USING (shardid)
WHERE logicalrelid = 'cov.orders_replicated'::regclass
GROUP BY logicalrelid, shardid
ORDER BY shardid;

SELECT objid::regprocedure AS function, distribution_argument_index, colocationid
FROM pg_dist_object
WHERE classid = 'pg_proc'::regclass AND objid = 'cov.tenant_order_count(int)'::regprocedure;

SELECT n.nspname, c.relname, c.relkind, c.relispartition
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('cov', 'cov_plain') AND c.relkind IN ('r', 'p', 'f')
  AND NOT EXISTS (SELECT 1 FROM pg_dist_partition p WHERE p.logicalrelid = c.oid)
ORDER BY 1, 2;

-- ---------------------------------------------------------------------------
-- Optional: an invalid index. A unique index fails on the one shard that holds
-- a duplicate, which leaves the coordinator's index invalid and shard indexes
-- on only some shards. Discovery reports it with the shards where it was
-- found, flagged invalid_index, and does not stop. Uncomment to run.
-- ---------------------------------------------------------------------------

-- CREATE TABLE cov.cic_probe (tenant_id int NOT NULL, k int NOT NULL, payload text);
-- SELECT create_distributed_table('cov.cic_probe', 'tenant_id',
--                                 shard_count => 8, colocate_with => 'none');
-- INSERT INTO cov.cic_probe
-- SELECT t, k, md5((t * 100 + k)::text)
-- FROM generate_series(1, 20000) AS t, generate_series(1, 10) AS k;
-- INSERT INTO cov.cic_probe VALUES (1, 1, 'duplicate');
-- ANALYZE cov.cic_probe;
-- \set ON_ERROR_STOP off
-- CREATE UNIQUE INDEX CONCURRENTLY cic_probe_tenant_k ON cov.cic_probe (tenant_id, k);
-- \set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Optional: an index created with DDL propagation off. It exists only on the
-- coordinator, so discovery stops with exit code 1 and the propagation
-- remedy. Run only test_an_unpropagated_index_stops_with_its_remedy while it
-- exists, then drop it with propagation off, because its shards have no index
-- to drop. Uncomment to run.
-- ---------------------------------------------------------------------------

-- SET citus.enable_ddl_propagation TO off;
-- CREATE INDEX orders_hash_unpropagated_idx ON cov.orders_hash (created_at);
-- RESET citus.enable_ddl_propagation;
--
-- To remove it:
-- SET citus.enable_ddl_propagation TO off;
-- DROP INDEX cov.orders_hash_unpropagated_idx;
-- RESET citus.enable_ddl_propagation;

-- ---------------------------------------------------------------------------
-- Optional: a foreign table added to the Citus metadata. Its rows stay on its
-- server, so discovery reports it as a foreign table, not as a Citus table.
-- Creating it opens no connection to the server. Citus copies the extension,
-- the server and the table to every node, so postgres_fdw must be available on
-- each of them. Uncomment to run.
-- ---------------------------------------------------------------------------

-- CREATE EXTENSION IF NOT EXISTS postgres_fdw;
-- DO $$
-- BEGIN
--     EXECUTE format(
--         'CREATE SERVER cov_loopback FOREIGN DATA WRAPPER postgres_fdw'
--         ' OPTIONS (host %L, dbname %L)', 'localhost', current_database());
-- END
-- $$;
-- CREATE FOREIGN TABLE cov.remote_currencies (code char(3), name text)
--     SERVER cov_loopback OPTIONS (schema_name 'cov', table_name 'currencies');
-- SELECT citus_add_local_table_to_metadata('cov.remote_currencies');

-- ---------------------------------------------------------------------------
-- Optional: a columnar table. Installs the citus_columnar extension, which
-- also adds its own schemas and objects for discovery to exclude. Uncomment
-- to run.
-- ---------------------------------------------------------------------------

-- CREATE EXTENSION IF NOT EXISTS citus_columnar;
-- CREATE TABLE cov.metrics_archive (
--     device_id int NOT NULL,
--     measured_at timestamptz NOT NULL,
--     metric text NOT NULL,
--     value double precision
-- ) USING columnar;
-- SELECT create_distributed_table('cov.metrics_archive', 'device_id',
--                                 colocate_with => 'cov.events');
-- INSERT INTO cov.metrics_archive
-- SELECT 1 + (g % 10000), timestamptz '2026-08-01' + g * interval '1 second',
--        (ARRAY['cpu', 'mem', 'disk'])[1 + g % 3], random() * 100
-- FROM generate_series(1, 3000000) AS g;
-- ANALYZE cov.metrics_archive;

-- ---------------------------------------------------------------------------
-- Teardown. Uncomment to remove everything this file created. Drop the
-- postgres_fdw extension only if nothing else on the cluster uses it.
-- ---------------------------------------------------------------------------

-- DROP SCHEMA IF EXISTS cov CASCADE;
-- DROP SCHEMA IF EXISTS cov_tenant_acme CASCADE;
-- DROP SCHEMA IF EXISTS cov_tenant_globex CASCADE;
-- DROP SCHEMA IF EXISTS cov_plain CASCADE;
-- DROP SCHEMA IF EXISTS "Cov Mixed" CASCADE;
-- DROP SERVER IF EXISTS cov_loopback CASCADE;
-- DROP EXTENSION IF EXISTS citus_columnar;
-- DROP EXTENSION IF EXISTS postgres_fdw;
