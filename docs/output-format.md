# Output Format

The tool generates reports in the specified output directory (default: `./discovery_output/`).

## Generated Files

- **`planetscale_discovery_results_<timestamp>.json`**: Complete analysis results in JSON format
- **`ps_discovery_<timestamp>.md`**: Local debugging summary (only when `--local-summary` flag is used)

The `<timestamp>` is the discovery start time, for example
`20260901T173146`. Each run writes new files and overwrites nothing.

## JSON Report Structure

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "discovery_version": "1.1.0",
  "database_results": {
    "connection_info": { ... },
    "analysis_results": {
      "config": { ... },
      "schema": { ... },
      "performance": { ... },
      "security": { ... },
      "features": { ... },
      "citus": { ... }
    }
  },
  "cloud_results": { ... },
  "summary": { ... }
}
```

`analysis_results.citus` appears only when the database has the Citus
extension. See [Citus tables](#citus-tables).

### Citus tables

On a database with the Citus extension, the existing fields keep their names,
types and positions, and a `citus` object is added to the rows that describe
tables. On a database without Citus, no `citus` key appears anywhere. See
[Citus Extension Support](citus.md) for how each value is computed.

Every entry in `schema.table_analysis` receives `citus.table_type`: one of
`distributed`, `reference`, `local`, `schema` or `regular`. For a regular
table, nothing else changes. For a Citus table, the existing fields hold the
logical value for the table as a whole, read from its shards:

| Section | Fields that hold logical values |
| --- | --- |
| `schema.table_analysis` | `estimated_rows`, `table_size_bytes`, `total_size_bytes`, their `_human` forms, `statistics` |
| `schema.index_analysis` | `estimated_rows`, `index_size_bytes`, `index_size_human`, `usage_stats` |
| `schema.partition_analysis` | `partition_size_bytes`, `partition_size_human`, `estimated_rows` |
| `schema.database_catalog` (current database) | `size_bytes`, `size_human` |
| `performance.table_statistics` | Every counter and `last_*` time |
| `performance.index_usage` | `idx_scan`, `idx_tup_read`, `idx_tup_fetch`, `index_size_bytes`, `index_size_human`, and the unused index lists computed from them |
| `performance.resource_utilization` | `largest_tables`, ranked by logical size, and `database_sizes` for the current database |

The `citus` object on those rows holds `source`, always `"shards"`, and
`coordinator`, the values the coordinator reported for the same fields. The
other keys depend on the section:

- `schema.table_analysis`: `table_type`, `distribution_column`,
  `colocation_id`, `shard_count`, `partmethod`, `replication_model`,
  `autoconverted`, `access_method`, `placements_per_shard` (`min` and `max`),
  and `shard_statistics`. `shard_statistics` holds `shards_read`,
  `shards_never_analyzed`, `inactive_placements`, `placement_size_bytes`, and a
  `columns` list. Each `columns` entry holds `attname`, `inherited`,
  `n_distinct_method`, and a `shards` list with each shard's `reltuples`,
  `n_distinct`, `null_frac` and `correlation`.
- `schema.index_analysis` and `performance.index_usage`: `shards_read`.
- `performance.table_statistics`: `table_type`, `shards_read`, and
  `shards_without_event`, which counts, for each `last_*` time, the shards that
  have not recorded that event.
- `performance.resource_utilization.largest_tables`: no other key. A Citus
  table that the coordinator's own ranking left out, because its shell is
  small, has `coordinator: null`.
- `schema.partition_analysis` and the current database's row: no other key.

An invalid index of a Citus table, such as one left by a failed `CREATE INDEX
CONCURRENTLY`, can exist on only some shards. In `schema.index_analysis` and
`performance.index_usage`, its fields hold the values summed from the shards
where it was found, and its `citus` object also holds `reason:
"invalid_index"` and `shard_count`. A `shards_read` below `shard_count` means
the index is missing on the other shards.

`n_distinct_method` is one of `single_placement`, `distribution_column_sum`,
`weighted_fraction`, `max_absolute` or `mixed_max_absolute`. The last two give
a lower bound rather than an exact count.

`analysis_results.citus` reports the cluster:

| Key | Contents |
| --- | --- |
| `version` | `extension_version`, `citus_version`, `node_role` |
| `nodes` | Each `pg_dist_node` entry, with `database_size_bytes` |
| `colocation_groups` | Each colocation group, with its shard count, replication factor, distribution column type and tables |
| `tables` | Each Citus table, with its type, distribution column, `estimated_rows`, `logical_size_bytes` (one placement per shard) and `placement_size_bytes` (every placement) |
| `shard_placement` | Placement counts and bytes `by_node` and `by_table` |
| `distributed_schemas` | Each distributed schema and its colocation id |
| `distributed_functions` | Each distributed function, with its distribution argument and colocation id |
| `summary` | `node_count`, `table_counts` by type (including `regular`), `citus_table_logical_size_bytes`, `cluster_storage_bytes` |

On a database with the extension installed and no Citus table, no node is
contacted, so each node's `database_size_bytes` and `cluster_storage_bytes` are
`null`.

`database_results.connection_info.citus` describes the connection only: the
extension version, the Citus version, and the node role (`coordinator` or
`worker`). Node and table counts are reported once, in
`analysis_results.citus.summary`, which `summary.database_summary.citus` copies
unchanged. There, `table_counts` holds every table type, `regular` included.

### Cloud results

`cloud_results.providers` holds one entry per provider that ran, keyed by
provider name (`aws`, `gcp`, `azure`, `supabase`, `heroku`, `neon`,
`planetscale`). Each entry carries the provider's own scope identifier, the
regions analyzed, per-region resources, a summary, and complexity factors.

## Markdown Summary Sections

When `--local-summary` is used, the markdown file includes:

1. **Overview**: Which modules ran and their status
2. **Database Summary**: PostgreSQL version, table count, size, extensions, users, and on a Citus database the Citus version, node count, tables by type and cluster storage
3. **Cloud Summary**: Per-provider instance and cluster counts
4. **Information Not Collected**: Any gaps due to permissions or missing extensions
5. **Errors**: Any errors encountered during discovery
