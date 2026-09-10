# Workload Bundle Format

## Overview

This document describes what `ps-discovery workload finalize` writes.

The bundle is the deliverable of a workload capture. Hand it to the PlanetScale
migration team, who use it to plan a sharding scheme for a migration to
[PlanetScale Neki](https://neki.dev). Sharding decisions need the query
workload, not only the schema: which tables a statement reads together, how
often each pattern runs, and how many rows each table holds.

The bundle holds captured data. It holds no conclusions. It recommends no shard
key and proposes no topology.

## The capture id

Every capture gets an eight-character id, and it appears in four places: the
bundle directory name, `manifest.json` as `capture_id`, and a
`-- neki:capture <id>` header at the top of `workload.sql` and `schema.sql`.

Files get separated. Someone unpacks two archives side by side, or attaches one
file to a ticket. The id ties them back together, and because it is derived
rather than random it also says *which* capture: the same database over the same
window always produces the same id, and any other pair produces a different one.
Re-running `finalize` on a session therefore writes the same directory rather
than a new one each time.

It is a truncated SHA-256 of the database name, its OID, and the window's start
and end. Nothing about the id is secret, and it identifies a capture rather than
a customer.

## Files

| File | Contents |
| --- | --- |
| `workload.sql` | the query log: one statement per record, `;`-terminated, with `-- neki:` metric headers |
| `schema.sql` | sequences, tables, indexes, constraints, views, and foreign keys last |
| `plantest_counts.json` | row count per table |
| `plantest_counts-card.json` | identical bytes, under the second name the planning tools open |
| `manifest.json` | when the capture ran, and over which intervals |
| `column_stats.json` | per-column value distribution, for judging a shard key |
| `table_activity.json` | writes, index use, size and growth per table, over the measured window |
| `README.md` | a summary of what was collected, regenerated for each bundle |

The first four are the planning tools' input. `manifest.json` and
`column_stats.json` are read by the migration tooling rather than the planning
tools: one records how the capture was taken, the others what the data looks
like and how it was used.
Nothing else is written, because a file nothing reads is a file someone has to
explain. Counts that describe the capture — how many statements were collected,
how many were left out and why — are printed by `finalize` and summarized in
`README.md`.

Every file is mode `0600`. The directory is mode `0700` and carries a
`.gitignore`, so you cannot commit the bundle by accident.

Keep the bundle a directory and hand it over whole. The files reference each
other by relative path, and the planning tools resolve some default paths inside
the bundle directory.

## Statement selection

`workload.sql` is the query log, so it holds query patterns only: `SELECT`,
`INSERT`, `UPDATE`, `DELETE` and `MERGE`. The filter is an allowlist rather than
a blocklist. A blocklist always misses the next case, such as DDL, `GRANT`,
`CHECKPOINT` or transaction control.

The tool also excludes statements that name no relation in `schema.sql`. These
are monitoring queries, `psql` queries and this tool's own catalog reads. They
are not the application's workload, and no planner can route a statement whose
relation is absent from the schema file.

A view is a relation for this purpose. An application that reads through a view
names only the view, so `schema.sql` carries the view definitions and the filter
counts the view names. An extension's views do not count, however, even when the
extension installed them into an ordinary schema: `public.pg_stat_statements`
belongs to `pg_stat_statements`, not to the application, and counting it would
return this tool's own reads to the query log.

`finalize` counts both kinds and names them in its summary. The tool ranks, caps
and samples nothing. It emits every statement that names a captured relation,
however rare. Deciding which statements matter needs a cost model and a
candidate topology, which the planning tools supply.

### Metric headers

Each record in `workload.sql` carries its counters as comments, so the file
holds the traffic weight as well as the statement:

    -- neki:stmt id=<statement id>
    -- neki:metrics calls=12904 total_exec_time_ms=88213.4 rows=1032320
    SELECT ... ;

A query log alone weights every pattern equally, so the weight travels with the
statement.

The planning tools do not read these headers yet. Their SQL loader supplies no
count, so every pattern arrives weighted 1 and a rare statement carries the
weight of a hot one. This is being addressed on the planning side. Until it is,
read a report's cost ranking as a ranking of patterns and not of traffic.

## Schema reconstruction

`schema.sql` is rendered from the catalog metadata that the schema analyzer
already collects, so the default path takes no table locks and needs no
`pg_dump`.

The tool does not render the hard parts by hand. It stores the output of
PostgreSQL's own `pg_get_indexdef` and `pg_get_constraintdef` verbatim, so
opclasses, `INCLUDE` columns, partial predicates and foreign-key actions come
back as strings that the server produced. The tool assembles only the
`CREATE TABLE` skeleton.

The renderer takes a dictionary rather than a connection, so it also works
against an archived discovery JSON.

The tool re-parses every statement before it writes the statement. Failures move
out, so `schema.sql` is guaranteed to parse, and the count of what was left out
is reported. Two notes on fidelity:

- The tool emits sequences before tables. A `serial` column's default is
  `nextval('seq'::regclass)`, which cannot resolve otherwise.
- Views come after the tables, and after any view they read. `pg_get_viewdef`
  supplies the body, so only the `CREATE VIEW` header is assembled here.
- `pg_get_constraintdef` is not a fixed point under re-parse. Feed PostgreSQL its
  own output and it re-renders some casts. `pg_dump` behaves the same way.
  Byte-identical catalog text is therefore not achievable. The tests assert
  idempotency instead.

## Manifest format

`manifest.json` says when the capture ran. It holds no findings: everything
about *what* was captured is readable from the files above, and a second copy
here would be a second source that can disagree with them.

```json
{
  "schema_version": 3,
  "capture_id": "b01d8e74",
  "generated_at": "2026-08-31T14:27:19Z",
  "discovery_version": "1.3.1",
  "window": { "start": "...", "end": "...", "tz": "UTC" },
  "intervals": [{ "start": "...", "end": "...", "calls": 146079 }],
  "intervals_skipped": [],
  "resets_observed": 0,
  "capped": false,
  "pgss": { "max": 5000, "evicted": false }
}
```

`schema_version` is a contract with the tool that reads the file, so it moves
when a field changes meaning, not when this tool changes.

`intervals` carries one entry per measured pair of snapshots, with that
interval's own call count. Nothing else in the bundle records it, and it is
what shows a quiet hour: an interval reporting `"calls": 0` is a gap in the
traffic that the total alone would hide. An interval the tool could not measure,
because the two snapshots are not ordered by the server clock, appears in
`intervals_skipped` with a reason instead.

Every timestamp comes from the database server rather than the host running
cron, so a clock difference between the two cannot shift the window.
`generated_at` is the exception: it is when `finalize` ran.

`capped` is true when any snapshot reached `workload.statement_row_limit`, which
means the tail of the workload is missing from that snapshot.

`pgss.evicted` reports whether PostgreSQL discarded statements **during this
window**, comparing `pg_stat_statements_info.dealloc` at the first and last
snapshot. A server that evicted statements last month and none during the
capture lost nothing from this result, and reports `false`. It is **`null`**
when the server cannot say: `pg_stat_statements_info` arrived in PostgreSQL 14,
so 12 and 13 report unknown rather than a `false` that would claim nothing was
lost. Treat `null` as unknown, not as falsy.

## Column statistics

Row counts say how big a table is. They cannot say whether a column can spread
it. A key whose most common value holds 40% of the rows puts 40% of that table
on one shard, however many shards there are, and no row count shows that.

`column_stats.json` carries, per column, `n_distinct`, `null_frac`,
`correlation`, and the first ten entries of `most_common_freqs` with the share
of the table the whole list accounts for. Read `top_share` first: it is the
fraction of rows holding the single most common value.

    {"table": "public.customer", "column": "c_credit",
     "n_distinct": 2.0, "null_frac": 0.0, "top_share": 0.9012,
     "top_frequencies": [0.9012, 0.0988], "mcv_coverage": 1.0,
     "first_seen": {"n_distinct": 2.0, "null_frac": 0.0, "top_share": 0.9012}}

A `top_share` of `null` means PostgreSQL kept no list of common values for that
column, which happens when the values are all but unique. That is a good sign
for a shard key rather than missing data.

Every snapshot records this, and the file keeps the first and last reading, so a
column that spreads evenly at the start of a capture and piles onto one value by
the end is visible as a change. `columns_whose_skew_moved` counts how many moved
by more than five percentage points.

**Frequencies, never values.** `most_common_vals` and `histogram_bounds` hold
sampled rows from your tables and are never read. This file says how often the
values in a column occur, and never what they are.

The figures come from the last `ANALYZE`, so they are a sample and an estimate,
and they describe one column at a time rather than a composite key.

## Table activity

`table_activity.json` answers four questions the query log cannot.

**Which tables absorbed the writes.** Statements are never parsed, so the query
log cannot attribute a write to a table. These counters can, and write
concentration is what decides whether a shard becomes a hotspot.

**Which indexes actually served the traffic**, and the column at the front of
each. That is evidence of the real access path, arrived at from use rather than
inferred from statement text, so it corroborates or contradicts a shard key
chosen from the workload.

**How big each table and index is**, and how much it grew over the window.
Shard count is decided in bytes, and a row count cannot give them.

**Which interval was the busiest**, because a burst and a steady rate produce
the same window average.

    {"table": "public.order_line3", "rows_inserted": 11826,
     "rows_updated": 13011, "rows_deleted": 0, "index_scans": 2821,
     "sequential_scans": 0, "rows_read_sequentially": 0,
     "total_size_bytes": 1073741824, "table_size_bytes": 644245094,
     "indexes_size_bytes": 429496730,
     "first_seen": {"total_size_bytes": 1069547520,
                    "table_size_bytes": 641728512,
                    "indexes_size_bytes": 427819008},
     "total_size_growth_bytes": 4194304,
     "peak_writes_per_second": 41.2,
     "peak_interval_start": "2026-08-25T03:00:00+00:00"}

    {"table": "public.stock6", "index": "stock6_pkey",
     "leading_column": "s_w_id", "is_unique": true, "is_primary": true,
     "scans": 63522, "rows_read": 127044, "rows_fetched": 63522,
     "size_bytes": 268435456, "first_seen": {"size_bytes": 266338304},
     "size_growth_bytes": 2097152}

`table_size_bytes` is the table alone. `total_size_bytes` adds TOAST and every
index, so it is the figure to size a shard from. `first_seen` holds the same
reading taken at the first snapshot, and the growth field is the difference. A
`null` means not measured, not empty.

Sizes are bytes on disk, so they include bloat, and a partitioned parent reads
`0` because its partitions hold the data. Over a short window a size change can
measure vacuum rather than growth; `rows_inserted - rows_deleted` is the
steadier estimate.

`peak_writes_per_second` is the highest write rate any one interval carried, and
`peak_interval_start` says which.

Every counter is for the measured window, differenced from the snapshots. Every
size is a reading, carried rather than differenced. The `window` block repeats
the same start, end, duration and interval count that
`manifest.json` carries, so the two files can be read together and a write rate
derived from them.

A table with `sequential_scans` above zero is read without an index, which fans
out to every shard once the table is split.

`indexes_not_scanned_in_window` counts indexes no query used during the capture.
The name is deliberate: over a short window that number says nothing about
whether an index is dead, only that nothing reached for it while you were
watching. A nightly report can be the sole user of an index and leave it at zero
in an eight-minute capture.

Index definitions are not repeated here. `schema.sql` holds them, and they were
most of the size of the file this replaces.

## Cardinality format

The file is a JSON object nested by schema, then table, then row count as a
number.

    {
      "public": { "orders": 812004993, "order_items": 3910244001 },
      "billing": { "invoices": 90210 }
    }

Row counts come from `reltuples`, which is PostgreSQL's own estimate for each
table rather than an exact count. This tool never runs `ANALYZE` to refresh them. `finalize` names
any table that has never been analyzed, whose estimate you should not rely on.

Four behaviours match the consumer exactly. The file holds ordinary tables only,
so it excludes partitioned parents and materialized views. It normalizes a
negative `reltuples` to `0`. It excludes system schemas. It carries no extra
keys, because a stray field risks a strict parse.

Both filenames carry identical bytes, because the planning tools open the
`-card` name.

## Verifying the format

`tests/integration/test_planner_contract.py` runs the planning tool against a
generated bundle and asserts the counters that the tool itself reports: it read
every record, it parsed every statement, and it lost no statement beyond
template deduplication. The test also compares this tool's cardinality file
against the same figures read independently from the database.

Every other test asserts that the output matches a format documented in this
repository, which cannot catch a misreading of the consumer. Run the contract
test before you trust a release. It needs the planner binary and a database, and
skips without either:

    PLANNER_BIN=/path/to/planner \
    WORKLOAD_E2E_DSN="host=... dbname=... user=..." \
      pytest tests/integration/test_planner_contract.py

## Related

- [Workload capture](workload_capture.md)
