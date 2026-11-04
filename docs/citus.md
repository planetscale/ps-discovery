# Citus Extension Support

Discovery has initial, limited support for PostgreSQL databases that use the
Citus extension. This page describes what is collected from such a database,
how the values are computed, and what the cluster must provide for a run to
succeed.

## Scope

Support covers database discovery only. No setting is needed: when the
`citus` extension is installed in the database you connect to, it is detected,
and additional Citus analysis is run alongside the regular PostgreSQL analysis.
Regular tables are discovered next to the tables that Citus manages, in the same
report.

A *Citus table* is a table recorded in Citus's metadata. For a Citus table, the
coordinator stores only an empty *shell*. The rows, the column statistics and
the activity counters are stored on the table's *shards*, and each shard is
stored on one or more nodes as a *placement*. Values read from the shell alone
do not describe the table. A 154 GB distributed table, for example, reports 0
rows and 8 kB on the coordinator.

## What is collected

For every table, `citus.table_type` is reported with the label that Citus itself
uses:

| `table_type` | Meaning |
| --- | --- |
| `distributed` | Rows are spread across shards by a distribution column. A table with one shard and no distribution column also has this type. |
| `reference` | One shard, with a placement on every node. |
| `local` | A Citus local table, whose data stays on the coordinator. |
| `schema` | A table in a distributed schema (schema-based sharding). |
| `regular` | A PostgreSQL table that Citus does not manage. |

For Citus tables, the following are also collected:

- The distribution column, the colocation group, the shard count, the
  replication model and the number of placements per shard.
- Row estimates, table and index sizes, column statistics, and table and index
  activity counters, read from every shard placement.
- The cluster's nodes, the database size on each node, the colocation groups,
  the placements per node, distributed schemas and distributed functions. These
  are reported under `analysis_results.citus`.

No table rows are read. Every value comes from catalogs and statistics views,
read on the coordinator, and on each placement through
`run_command_on_all_nodes()`. Most common values and histograms are never read.

Shards are hidden from discovery's own session, whatever
`citus.show_shards_for_app_name_prefixes` is set to for the discovery role, so
a shard placement on the coordinator is never reported as a table. The setting
is changed for that session only.

## How values are computed

The existing fields of the report keep their names, types and positions. For a
Citus table, each existing field holds the logical value for the table as a
whole, so a reader of the report needs no change. The coordinator's own values
are kept under `citus.coordinator`, and the detail for each shard under
`citus.shard_statistics`. See [Output Format](output-format.md#citus-tables).

Values are combined per shard, following how Citus runs queries. A read runs on
one placement, and a write is applied to every placement:

- **Row estimates, sizes and live tuples** are summed across shards, using one
  placement per shard, the one analyzed most recently. A reference table on
  three nodes is therefore counted once, not three times.
- **Write counters** (`n_tup_ins`, `n_tup_upd`, `n_tup_del`) are summed across
  shards, using each shard's largest placement value, so counters reset on one
  node do not lower the total.
- **Read counters** (`seq_scan`, `idx_scan` and related counters) are summed
  over every placement.
- **`null_frac`** is a mean weighted by each shard's row estimate. The
  combination adds no error of its own, so the result is as accurate as the
  shards' statistics.
- **`correlation`** is a mean weighted by each shard's row estimate. The query
  planner uses each shard's own correlation, so the value describes how ordered
  a typical shard is.
- **`n_distinct`** keeps PostgreSQL's convention: a positive value is a count,
  and a negative value is a fraction of the rows. The method used is recorded in
  `n_distinct_method`. Each shard's value is itself an estimate from ANALYZE,
  so the Result column describes the combination only:

  | Method | Used for | Result |
  | --- | --- | --- |
  | `single_placement` | A table with one shard | The shard's value, unchanged |
  | `distribution_column_sum` | The distribution column of a hash or range distributed table, whose values are each stored in one shard | A sum, which adds no error |
  | `weighted_fraction` | Every shard reports a fraction | An estimate, which assumes the fraction holds across shards |
  | `max_absolute` | Every shard reports a count | A lower bound |
  | `mixed_max_absolute` | Shards disagree | A lower bound |

  A combined count above 10% of the table's rows is stored as a negative
  fraction, as ANALYZE does.

A shard with no statistics, because it was never analyzed, is counted under
`shards_never_analyzed` and left out of the sums. A table whose every shard is
unanalyzed reports `estimated_rows: -1`, the same value PostgreSQL reports for
an unanalyzed regular table.

The "Total Size" in the summary is the logical size: regular tables plus each
Citus table counted once. A shard that a move left on the coordinator, until
Citus's deferred cleanup drops it, is not counted. The summary also reports
cluster storage, the sum of the database size on every node, which includes
every placement and any shard not yet cleaned up.

## Requirements

A Citus run either reads every shard or stops. A partial result is never
written for the database. Before any analysis runs, the following are checked:

- **Connect to the coordinator.** A connection to a worker stops the run.
- **Every active primary node must be reachable**, whether or not it holds a
  placement, and must accept a connection from the discovery role, because
  `run_command_on_all_nodes()` connects to each of them as that role. Inactive
  nodes and secondaries are not contacted. A node added during the run stops
  it, because its shards would be read under the wrong node.
- **Every shard must have an active placement on an active primary node.** A
  shard whose placements are only on inactive nodes or secondaries stops the
  run.
- **Every worker that holds placements must have the Citus metadata.** A
  worker recognizes its shards from its own copy of the metadata. Citus 11 and
  later sync it to every node, but a cluster upgraded from Citus 10 has none on
  its workers until `citus_finalize_upgrade_to_citus11()` is run.
- **The discovery role needs the Citus privileges.** It needs `EXECUTE` on
  `run_command_on_all_nodes`, `relation_is_a_known_shard`, `shard_name`,
  `column_to_column_name` and `citus_version`, and `SELECT` on the `pg_dist_*`
  metadata tables. All of them are granted to `PUBLIC` by default.
- **`citus.use_secondary_nodes` must be `never`** for the discovery role, which
  is the default.

The node checks, the `run_command_on_all_nodes` privilege and the
`citus.use_secondary_nodes` setting apply only when the database holds at least
one Citus table. A database where the extension is installed but no table is
distributed contacts no node, so it cannot stop the run for those reasons. The
coordinator check and the metadata privileges always apply. Without them, a
database with no Citus table cannot be told apart from one whose Citus tables
cannot be read.

During the shard read, every valid index of a Citus table must also be found on
every shard of that table. A valid index missing on a shard stops the run,
because its size would be partial and its scan counts would drop it from the
list of unused indexes. There are two usual causes, and the message gives the
remedy for each:

1. On a partition's index, created from its parent's index, the shard indexes
   can lack their shard id after PostgreSQL named them automatically. `SELECT
   fix_all_partition_shard_index_names();` on the coordinator renames them.
2. An index created with `citus.enable_ddl_propagation` off exists only on the
   coordinator. Recreate it with DDL propagation on, or drop it.

An invalid index never stops the run. A failed `CREATE INDEX CONCURRENTLY`
leaves the coordinator's index invalid, with shard indexes on only some shards.
A shard index that the build completed can be valid on its node and take
scans, whatever the coordinator records. An invalid index therefore reports
the values summed from the shards where it was found, and is flagged with
`citus.reason: "invalid_index"`. Compare `citus.shards_read` with
`citus.shard_count` to see how many shards hold it.

When any check fails, database discovery stops, no database result is written,
and the message names each node or privilege at fault. Database discovery alone
then exits with code 1. A combined run with a cloud provider continues with
cloud discovery, writes the report with `database_results` set to `null`, and
exits with code 0, as for any other database failure. Unlike other permission errors, which are
reported as warnings, a missing Citus privilege stops the run: without it,
every Citus table would be reported with its shell's values. See
[Troubleshooting](troubleshooting.md#citus-discovery-stopped).

Run the privilege SQL from the [README](../README.md#required-postgresql-privileges)
on the coordinator. With `citus.enable_create_role_propagation` on, which is the
default, `CREATE ROLE` and `DROP ROLE` on the coordinator are propagated to
every node.

Column statistics follow the same rule as on a regular table: `pg_stats`
returns a column only to a role that may `SELECT` it, and on a node that rule
applies to the shard. If `statistics` is empty for a Citus table whose shards
were analyzed, confirm that the discovery role's `SELECT` grant reached the
shards.

## Limitations

- **Workload capture** reads only the coordinator's catalogs. On a Citus
  database, its row counts and column statistics do not describe Citus tables.
- **Secondary nodes** are not read.
- **Foreign tables** added to Citus's metadata with
  `citus_add_local_table_to_metadata()` are reported as foreign tables, as on
  regular PostgreSQL. Their rows are stored on their own server, so they have
  no shard values to read, and they are not counted among the Citus tables.
- **Data size analysis** skips Citus tables, with the reason `citus_table`.
  Reading their rows would scan every shard in full. See
  [Data Size Analysis](data_size_analysis.md).
- **Statement statistics** in the performance report come from the
  coordinator's `pg_stat_statements` only.
- The support was tested with Citus 14.1 on PostgreSQL 18. A Citus version that
  lacks a function the read needs is reported as unsupported.
