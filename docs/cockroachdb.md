# CockroachDB discovery

Discovery reads a CockroachDB cluster's schema, sizes, jobs, zone
configurations, and CockroachDB-only features, then reports what has to change
before a move to PlanetScale Postgres. Use it to plan a migration. It does not
copy any data.

Run discovery with `engine: postgres` and the usual PostgreSQL `database:`
block. Nothing else needs to be set: discovery detects CockroachDB from
`version()` and switches to CockroachDB's own catalogs. It does not rely on
`server_version_num`, because CockroachDB reports the PostgreSQL compatibility
value `130000` there.

## Prerequisites

- A network path to a SQL node of the cluster (default port 26257).
- For a secure cluster, the TLS settings in [TLS](#tls).
- A dedicated read-only role, not an application user. Run the following as
  an admin, and repeat the `CONNECT`, `USAGE`, and `SELECT` grants for each
  application database and schema:

```sql
CREATE ROLE discovery_ro;
GRANT CONNECT ON DATABASE app_db TO discovery_ro;
GRANT USAGE ON SCHEMA public TO discovery_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO discovery_ro;
GRANT SYSTEM VIEWACTIVITYREDACTED TO discovery_ro;
GRANT SYSTEM VIEWCLUSTERSETTING TO discovery_ro;
GRANT SYSTEM VIEWJOB TO discovery_ro;
ALTER ROLE discovery_ro SET default_transaction_read_only = on;
```

`VIEWACTIVITYREDACTED` lets discovery read `tenant_span_stats` and
`SHOW RANGES`. Without `VIEWJOB`, `SHOW JOBS` returns 0 rows and no error.
`default_transaction_read_only` is recommended because `public` allows
`CREATE` by default.

A missing privilege is an analysis gap. Discovery never reports 0 jobs, 0
sizes, or "none found" when it could not read the catalog.

Gaps that belong to a module (`cockroach_unassessed` for schema,
`no_logical_replication` for features) are recorded on a default full run,
including when those catalogs come back empty or unread. They are omitted
only when that module was deliberately excluded (`--analyzers config`).

## TLS

Secure clusters need an SSL mode and the cluster CA. The CA by itself is not enough. Set them in config.yaml, on the command line, or with libpq environment variables:

| config.yaml        | CLI                 | env             |
|--------------------|---------------------|-----------------|
| `ssl_mode`         | `--ssl-mode`        | `PGSSLMODE`     |
| `ssl_root_cert` / `sslrootcert` | `--ssl-root-cert` | `PGSSLROOTCERT` |
| `ssl_cert` / `sslcert`         | `--ssl-cert`      | `PGSSLCERT`     |
| `ssl_key` / `sslkey`           | `--ssl-key`       | `PGSSLKEY`      |

Use `ssl_mode: verify-full` with the cluster certificate authority (CA) file.
A client certificate and key are also required when the cluster uses
certificate authentication.

## Which databases are analyzed

Discovery analyzes the database you connect to, plus every other application
database the role can read. Every CockroachDB cluster ships empty `defaultdb`
and `postgres` databases, so discovery skips those two unless one of them is
the connected database. The `system` database is always skipped.

Run discovery once for each application database. If the application stores
its tables in `defaultdb`, run one pass connected to `defaultdb` and list it
with the other application databases.

## Sizes

Sizes come from `crdb_internal.tenant_span_stats` (`total_stats.live_bytes`).
That is one logical copy of the data, uncompressed, including indexes. It is
not the replicated on-disk size.

## Version gaps

v24.3 has no vector index (`USING cspann` fails, but a bare `VECTOR` column
works) and no row-level security (RLS) or `CREATE POLICY`. On v24.3 those
checks return empty results, not a "could not read" gap.

Discovery flags zone configurations whose `num_replicas` is higher than the
node count, for example `app_db` asking for 5 replicas on a 3-node cluster.

## Migration path

The supported path is a planned downtime cutover. Prepare the schema and the
application before the window. During the window, pause writes, copy each
table once, validate, and then point the application at PlanetScale. Do not
use pgcopydb or pg_dump for the schema or the data.

### Before the window

#### Export and convert the schema

Run `SHOW CREATE ALL TYPES` and `SHOW CREATE ALL TABLES` in each application
database. Collect triggers with `SHOW TRIGGERS` and `SHOW CREATE TRIGGER`.
Collect functions and procedures from `information_schema.routines`.

Convert the schema before you create it on PlanetScale:

- Replace every `unique_rowid()` default with an identity column or a UUID
  default, and give tables that rely on the hidden `rowid` an explicit
  primary key.
- Replace hash-sharded indexes with standard indexes on the same columns.
- Remove `REGIONAL BY ROW` and other multi-region table settings.

Create the converted tables on PlanetScale. Leave secondary indexes and
foreign keys for the window.

#### Rewrite the application

Replace `AS OF SYSTEM TIME` reads, and review any code that depends on
CockroachDB transaction retries or isolation. Test the application against
the converted schema on PlanetScale before the window.

#### Rehearse the copy

Run the whole window once against a copy of production: the COPY, the index
and foreign key builds, the sequence settings, and ANALYZE. Time each step.
The rehearsal total is the downtime estimate. Book a window longer than that
total, with time left for validation and the go/no-go call.

### In the window

#### Copy the data

1. Pause application writes on CockroachDB and confirm that no writes are
   arriving.
2. Copy each table once, in full, with text or CSV `COPY (SELECT ...)`. There
   is no incremental copy and no catch-up step. Binary COPY is not
   implemented. Cast collated strings with `::STRING` in the `SELECT`.
   Computed columns are included in COPY output, so give the loader an
   explicit column list.
3. Load each file into PlanetScale Postgres.

`CREATE CHANGEFEED ... WITH initial_scan='only'` can replace `COPY` for the
one full export of a table. It is a one-time export, not an ongoing sync, and
it does not shorten the window. Discovery does not provide a
changefeed-to-Postgres applier.

#### Build indexes and foreign keys

Build secondary indexes and foreign keys after the data is loaded.

#### Set sequences

After writes are paused and every table is copied, set each sequence and
identity column to the highest copied ID plus one:

```sql
SELECT setval('app_table_id_seq', (SELECT max(id) FROM app_table) + 1, false);
```

#### Validate

Run `ANALYZE` on the PlanetScale database. Compare row counts for every table,
and checksums on key tables, between CockroachDB and PlanetScale.

#### Cut over

Make an explicit go/no-go call from the validation results. On go, point the
application at PlanetScale and resume writes.

### Roll back

Keep CockroachDB intact and paused until the cutover is accepted.

- Before writes resume on PlanetScale: point the application back at
  CockroachDB and resume writes there.
- After writes resume on PlanetScale: CockroachDB no longer has the new
  writes, so rolling back needs a reverse copy from PlanetScale to
  CockroachDB.

The default after that point is to fix forward on PlanetScale.

## Limitations

From 26.1, most `crdb_internal` reads fail with `Access to crdb_internal and
system is restricted` (SQLSTATE 42501) unless the session sets
`allow_unsafe_internals`. Discovery sets that for the analyzer session on
every CockroachDB connection. On 26.1 and later the report records
`connection_info.allow_unsafe_internals = on`. CockroachDB audits each of
those reads as `unsafe_internals_accessed`. On 25.x and earlier the variable
does not exist, the SET fails, and discovery continues without a gap.

If the role cannot set it, table and size catalogs come back empty, the
report looks unassessed (0 tables, 0 B), and a high
`cockroach_internals_blocked` gap says so. A DBA can allow it once with
`ALTER ROLE discovery_ro SET allow_unsafe_internals = on`.
