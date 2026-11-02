"""Reading every shard placement, and combining the values per logical table."""

import re
from datetime import datetime, timezone

import pytest

from planetscale_discovery.database import citus
from planetscale_discovery.database.analyzers.performance_analyzer import (
    PerformanceAnalyzer,
)
from planetscale_discovery.database.analyzers.schema_analyzer import SchemaAnalyzer
from planetscale_discovery.database.citus import CitusClusterError, CitusTable
from tests.fixtures.database_responses import (
    CITUS_BRANCHES_OID,
    CITUS_PLACEMENTS,
    CITUS_REFERENCE_OID,
    CITUS_REFERENCE_PKEY_OID,
    CITUS_REMOTE_INDEX_ROWS,
    CITUS_REMOTE_TABLE_ROWS,
    CITUS_SHARDS,
    CITUS_TABLES,
    FakeConnection,
    citus_handlers,
    citus_index_row,
    citus_table_row,
)


def ready(**kwargs):
    connection = FakeConnection(citus_handlers(**kwargs))
    ctx = citus.detect_citus(connection)
    citus.preflight(connection, ctx)
    return connection, ctx


def keys_named(value, name, path="$"):
    """The path of every dict key called name, at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == name:
                yield f"{path}.{key}"
            yield from keys_named(item, name, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from keys_named(item, name, f"{path}[{i}]")


def table(**overrides):
    values = dict(
        oid=1,
        schema_name="public",
        table_name="orders",
        table_type="distributed",
        partmethod="h",
        repmodel="s",
        colocation_id=3,
        distribution_column="tenant_id",
        autoconverted=False,
        shard_count=2,
        access_method="heap",
    )
    values.update(overrides)
    return CitusTable(**values)


class TestPlacementRules:
    def test_a_reference_table_counts_its_rows_and_writes_once(self):
        """Catches a reference table on 3 nodes reported with 3x its rows."""
        connection, ctx = ready()
        combined = ctx.shard_statistics(connection).tables[CITUS_REFERENCE_OID]
        # The coordinator's placement was analyzed most recently.
        assert combined["estimated_rows"] == 49996560
        assert combined["counters"]["n_tup_ins"] == 50000000
        assert combined["total_size_bytes"] == 5801107456
        assert combined["placement_size_bytes"] == 3 * 5801107456
        assert combined["placements_per_shard"] == {"min": 3, "max": 3}

    def test_reads_are_summed_over_every_placement(self):
        """Catches a reference table's reads counted on one placement only, while each
        read runs on any one of them.
        """
        connection, ctx = ready()
        combined = ctx.shard_statistics(connection).tables[CITUS_REFERENCE_OID]
        assert combined["counters"]["seq_scan"] == 3

    def test_live_tuples_take_one_placement_per_shard(self):
        """Catches a reference table's live tuples counted on every node."""
        connection, ctx = ready()
        combined = ctx.shard_statistics(connection).tables[CITUS_REFERENCE_OID]
        assert combined["counters"]["n_live_tup"] == 49996560

    def test_an_inactive_placement_is_not_read_but_is_counted(self):
        """Catches a stale inactive placement added to the table's values."""
        placements = CITUS_PLACEMENTS + [
            {"shardid": 102137, "groupid": 2, "shardstate": 3}
        ]
        remote = {k: list(v) for k, v in CITUS_REMOTE_TABLE_ROWS.items()}
        remote[3] = remote[3] + [citus_table_row("pgbench_branches_102137", 9999)]
        connection, ctx = ready(placements=placements, remote_tables=remote)
        combined = ctx.shard_statistics(connection).tables[CITUS_BRANCHES_OID]
        assert combined["estimated_rows"] == 630
        assert combined["inactive_placements"] == 1
        assert combined["placements_per_shard"] == {"min": 1, "max": 1}
        assert combined["counters"]["seq_scan"] == 2

    def test_index_reads_sum_every_placement_and_size_counts_once(self):
        """Catches a reference index's size counted on every node, or its
        scans counted on one."""
        remote_indexes = {
            nodeid: [
                r
                for r in CITUS_REMOTE_INDEX_ROWS[nodeid]
                if not r["index_relname"].startswith("reference_t1")
            ]
            + [
                citus_index_row(
                    "reference_t1_pkey_102265", 5e7, idx_scan=nodeid, size=1577099264
                )
            ]
            for nodeid in (1, 2, 3)
        }
        connection, ctx = ready(remote_indexes=remote_indexes)
        index = ctx.shard_statistics(connection).indexes[CITUS_REFERENCE_PKEY_OID]
        assert index["idx_scan"] == 1 + 2 + 3
        assert index["index_size_bytes"] == 1577099264

    def test_a_distributed_table_sums_its_shards(self):
        """Catches a distributed table's rows, writes or size taken from one shard
        instead of all of them.
        """
        connection, ctx = ready()
        combined = ctx.shard_statistics(connection).tables[CITUS_BRANCHES_OID]
        assert combined["estimated_rows"] == 630
        assert combined["counters"]["n_tup_ins"] == 630
        assert combined["total_size_bytes"] == 2 * 65536

    def test_statement_replication_takes_one_placement_per_shard(self):
        """Catches writes counted once per placement under repmodel 'c'."""
        replicated = table(repmodel="c")
        by_shard = {
            1: [
                (1, citus_table_row("orders_1", 100, n_tup_ins=100, seq_scan=2)),
                (2, citus_table_row("orders_1", 100, n_tup_ins=100, seq_scan=3)),
            ],
            2: [
                (1, citus_table_row("orders_2", 50, n_tup_ins=50, seq_scan=1)),
                (2, citus_table_row("orders_2", 50, n_tup_ins=50, seq_scan=0)),
            ],
        }
        combined = citus.combine_table(replicated, by_shard)
        assert combined["estimated_rows"] == 150
        assert combined["counters"]["n_tup_ins"] == 150
        assert combined["counters"]["seq_scan"] == 6

    def test_a_placement_with_reset_counters_does_not_undercount_writes(self):
        """Catches pg_stat_reset() on one node lowering a shard's writes."""
        by_shard = {
            1: [
                (1, citus_table_row("orders_1", 100, n_tup_ins=0)),
                (2, citus_table_row("orders_1", 100, n_tup_ins=100)),
            ]
        }
        combined = citus.combine_table(table(shard_count=1), by_shard)
        assert combined["counters"]["n_tup_ins"] == 100

    def test_an_analyzed_empty_shard_is_not_never_analyzed(self):
        """Catches reltuples 0 being read as -1 (never analyzed)."""
        by_shard = {
            1: [(1, citus_table_row("orders_1", 0))],
            2: [(2, citus_table_row("orders_2", 10))],
        }
        combined = citus.combine_table(table(), by_shard)
        assert combined["shards_never_analyzed"] == 0
        assert combined["estimated_rows"] == 10

    def test_a_table_whose_shards_were_never_analyzed_reports_minus_one(self):
        """Catches a table with no analyzed shard reported with 0 rows, which reads as
        an empty table.
        """
        by_shard = {
            1: [(1, citus_table_row("orders_1", -1, last_analyze=None))],
            2: [(2, citus_table_row("orders_2", -1, last_analyze=None))],
        }
        combined = citus.combine_table(table(), by_shard)
        assert combined["estimated_rows"] == -1
        assert combined["shards_never_analyzed"] == 2
        assert combined["counters"]["last_analyze"] is None

    def test_the_oldest_shard_analyze_time_is_reported(self):
        """Catches the newest analyze time reported, which hides a shard with stale
        statistics.
        """
        by_shard = {
            1: [
                (
                    1,
                    citus_table_row(
                        "orders_1", 1, last_analyze="2026-10-01T00:00:00+00:00"
                    ),
                )
            ],
            2: [
                (
                    2,
                    citus_table_row(
                        "orders_2", 1, last_analyze="2026-10-05T00:00:00.5+00:00"
                    ),
                )
            ],
        }
        combined = citus.combine_table(table(), by_shard)
        assert combined["counters"]["last_analyze"] == datetime(
            2026, 10, 1, tzinfo=timezone.utc
        )


class TestColumnStatistics:
    def shards(self, *values):
        return [
            {"shardid": i, "reltuples": rows, "n_distinct": nd}
            for i, (rows, nd) in enumerate(values)
        ]

    def test_a_single_shard_table_uses_its_value(self):
        """Catches the n_distinct of a table with one shard changed by a combination
        method.
        """
        value, method = citus.combine_n_distinct(
            table(shard_count=1, table_type="reference"), "id", self.shards((100, -1.0))
        )
        assert (value, method) == (-1.0, "single_placement")

    def test_the_distribution_column_adds_up_across_shards(self):
        """Catches the distinct values of the distribution column not added up. Each
        value is stored in one shard, so the counts add up exactly.
        """
        value, method = citus.combine_n_distinct(
            table(), "tenant_id", self.shards((1000, 5.0), (1000, 7.0))
        )
        assert (value, method) == (12.0, "distribution_column_sum")

    def test_an_append_table_does_not_add_up(self):
        """Catches append shards, which can share values, summed as disjoint."""
        value, method = citus.combine_n_distinct(
            table(partmethod="a"), "tenant_id", self.shards((1000, 5.0), (1000, 7.0))
        )
        assert (value, method) == (7.0, "max_absolute")

    def test_fractions_are_weighted_by_rows(self):
        """Catches a plain mean of n_distinct fractions, which lets a small shard skew
        the table.
        """
        value, method = citus.combine_n_distinct(
            table(), "email", self.shards((300, -1.0), (100, -0.5))
        )
        assert method == "weighted_fraction"
        assert value == pytest.approx(-0.875)

    def test_mixed_signs_give_a_lower_bound(self):
        """Catches shards that disagree in sign combined into a count above the largest
        one.
        """
        value, method = citus.combine_n_distinct(
            table(), "city", self.shards((1000, 40.0), (100, -0.9))
        )
        assert (value, method) == (90.0, "mixed_max_absolute")

    def test_a_summed_count_above_ten_percent_becomes_a_fraction(self):
        """Catches a combined count left absolute where ANALYZE would store a
        fraction, as for a unique distribution column."""
        value, method = citus.combine_n_distinct(
            table(), "tenant_id", self.shards((100, -1.0), (300, -1.0))
        )
        assert (value, method) == (-1.0, "distribution_column_sum")

    def test_null_frac_and_correlation_are_weighted_by_rows(self):
        """Catches a plain mean, which lets a small shard skew the table."""
        chosen = {
            1: (1, citus_table_row("orders_1", 300)),
            2: (2, citus_table_row("orders_2", 100)),
        }
        column_rows = {
            (1, 1): [
                {
                    "attname": "note",
                    "inherited": False,
                    "null_frac": 0.0,
                    "n_distinct": 5,
                    "correlation": 1.0,
                }
            ],
            (2, 2): [
                {
                    "attname": "note",
                    "inherited": False,
                    "null_frac": 0.4,
                    "n_distinct": 5,
                    "correlation": -1.0,
                }
            ],
        }
        statistics, _ = citus.combine_columns(table(), chosen, column_rows)
        assert statistics[0]["null_frac"] == pytest.approx(0.1)
        assert statistics[0]["correlation"] == pytest.approx(0.5)

    def test_a_never_analyzed_shard_does_not_weigh_null_frac(self):
        """Catches a -1 reltuples entering a weighted mean."""
        ctable = table(shard_count=2)
        chosen = {
            1: (1, citus_table_row("orders_1", 100)),
            2: (2, citus_table_row("orders_2", -1)),
        }
        column_rows = {
            (1, 1): [
                {
                    "attname": "note",
                    "inherited": False,
                    "null_frac": 0.2,
                    "n_distinct": 5,
                    "correlation": 0.5,
                }
            ],
            (2, 2): [
                {
                    "attname": "note",
                    "inherited": False,
                    "null_frac": 0.9,
                    "n_distinct": 5,
                    "correlation": -0.5,
                }
            ],
        }
        statistics, detail = citus.combine_columns(ctable, chosen, column_rows)
        assert statistics[0]["null_frac"] == pytest.approx(0.2)
        assert statistics[0]["correlation"] == pytest.approx(0.5)
        assert len(detail[0]["shards"]) == 2

    @pytest.mark.parametrize("shard_count", [1, 2])
    def test_a_column_with_no_analyzed_shard_reports_unknown(self, shard_count):
        """Catches a 0 reported for n_distinct, which reads as no distinct values,
        when no shard counts as analyzed. TRUNCATE sets reltuples back to -1 and
        keeps the old pg_stats rows, so a column can have rows in this state."""
        ctable = table(shard_count=shard_count)
        chosen = {
            i: (i, citus_table_row(f"orders_{i}", -1))
            for i in range(1, shard_count + 1)
        }
        column_rows = {
            (i, i): [
                {
                    "attname": "note",
                    "inherited": False,
                    "null_frac": 0.2,
                    "n_distinct": 5,
                    "correlation": 0.5,
                }
            ]
            for i in range(1, shard_count + 1)
        }
        statistics, detail = citus.combine_columns(ctable, chosen, column_rows)
        assert statistics[0]["n_distinct"] is None
        assert statistics[0]["null_frac"] is None
        assert statistics[0]["correlation"] is None
        assert detail[0]["n_distinct_method"] is None


class TestShardRead:
    def test_a_relation_without_an_active_placement_is_ignored(self):
        """Catches an orphaned shard left after a move counted twice."""
        remote = {k: list(v) for k, v in CITUS_REMOTE_TABLE_ROWS.items()}
        remote[3] = remote[3] + [citus_table_row("pgbench_branches_102137", 9999)]
        connection, ctx = ready(remote_tables=remote)
        combined = ctx.shard_statistics(connection).tables[CITUS_BRANCHES_OID]
        assert combined["estimated_rows"] == 630

    def test_an_orphaned_shard_on_the_coordinator_is_left_out_of_its_size(self):
        """Catches a shard relation that a move left on the coordinator, before the
        deferred cleanup drops it, counted in the logical database size on top of
        the shard's active placement."""
        coordinator_size = 19327047359
        connection, ctx = ready()
        baseline = citus.logical_database_size(connection, ctx, coordinator_size)

        remote = {k: list(v) for k, v in CITUS_REMOTE_TABLE_ROWS.items()}
        remote[1] = remote[1] + [
            citus_table_row("pgbench_branches_102137", 317, total_size_bytes=7000000)
        ]
        connection, ctx = ready(remote_tables=remote)
        stats = ctx.shard_statistics(connection)
        assert stats.orphaned_size_bytes == {0: 7000000}
        assert stats.tables[CITUS_BRANCHES_OID]["estimated_rows"] == 630
        size = citus.logical_database_size(connection, ctx, coordinator_size)
        assert size == baseline - 7000000

    def test_both_reports_rewrite_the_current_database_the_same_way(self):
        """Catches the schema catalog and the performance report disagreeing on the
        current database's size, or another database's row being rewritten."""
        connection, ctx = ready()
        catalog = [
            {
                "database_name": "postgres",
                "size_bytes": 19327047359,
                "size_human": "18 GB",
            },
            {"database_name": "other", "size_bytes": 8192, "size_human": "8192 bytes"},
        ]
        sizes = [
            {"datname": "postgres", "size_bytes": 19327047359, "size_human": "18 GB"},
            {"datname": "other", "size_bytes": 8192, "size_human": "8192 bytes"},
        ]
        citus.apply_logical_database_size(connection, ctx, catalog, "database_name")
        citus.apply_logical_database_size(connection, ctx, sizes, "datname")

        def without_name(row):
            return {
                k: v for k, v in row.items() if k not in ("database_name", "datname")
            }

        assert without_name(catalog[0]) == without_name(sizes[0])
        assert catalog[0]["citus"]["coordinator"] == {
            "size_bytes": 19327047359,
            "size_human": "18 GB",
        }
        assert catalog[0]["size_bytes"] != 19327047359
        assert "citus" not in catalog[1] and "citus" not in sizes[1]

    def test_a_node_that_fails_during_the_read_stops(self):
        """Catches the other nodes' values written as the table's whole."""
        connection, ctx = ready()
        connection.handlers.insert(
            0,
            (
                "run_command_on_all_nodes(",
                lambda sql, params: [
                    {"nodeid": 1, "success": True, "result": "[]"},
                    {"nodeid": 2, "success": True, "result": "[]"},
                    {"nodeid": 3, "success": False, "result": "connection lost"},
                ],
            ),
        )
        with pytest.raises(CitusClusterError, match="citus-worker-2"):
            ctx.shard_statistics(connection)

    def test_a_node_missing_from_the_results_stops(self):
        """Catches a node that returned nothing treated as holding nothing."""
        connection, ctx = ready()
        connection.handlers.insert(
            0,
            (
                "run_command_on_all_nodes(",
                lambda sql, params: [
                    {"nodeid": 1, "success": True, "result": "[]"},
                    {"nodeid": 2, "success": True, "result": "[]"},
                ],
            ),
        )
        with pytest.raises(
            CitusClusterError, match="citus-worker-2:5432: returned no result"
        ):
            ctx.shard_statistics(connection)

    def test_a_citus_foreign_table_does_not_stop_the_run(self):
        """Catches a foreign table added with citus_add_local_table_to_metadata()
        entering the shard read: its shard has relkind 'f', which no remote
        command returns, so the healthy run stopped as a missing shard."""
        foreign = {
            "table_oid": 17600,
            "schema_name": "public",
            "table_name": "remote_events",
            "relkind": "f",
            "table_type": "local",
            "partmethod": "n",
            "repmodel": "s",
            "colocation_id": 0,
            "distribution_column": None,
            "autoconverted": False,
            "shard_count": 1,
            "access_method": None,
        }
        connection, ctx = ready(
            tables=CITUS_TABLES + [foreign],
            shards=CITUS_SHARDS
            + [
                {
                    "shardid": 102300,
                    "table_oid": 17600,
                    "shard_relname": "remote_events_102300",
                }
            ],
            placements=CITUS_PLACEMENTS
            + [{"shardid": 102300, "groupid": 0, "shardstate": 1}],
        )
        stats = ctx.shard_statistics(connection)
        assert 17600 not in ctx.tables
        assert set(stats.tables) == {CITUS_BRANCHES_OID, CITUS_REFERENCE_OID}

    def test_the_classification_and_the_shard_read_share_one_relkind_filter(self):
        """Catches the two drifting apart, which makes a healthy run stop on a
        Citus table whose shard the remote commands never return."""
        _, ctx = ready()
        expected = f"c.relkind IN {citus.TABLE_RELKINDS}"
        assert expected in citus._classification_sql(ctx)
        for command in (citus.TABLE_COMMAND, citus.COLUMN_COMMAND, citus.INDEX_COMMAND):
            assert expected in command

    def test_the_regular_count_shares_the_relkind_filter(self, monkeypatch):
        """Catches the count of regular tables and the Citus classification drifting
        apart, which counts a relation twice or not at all in table_counts.

        The filter is changed for the test, because a copy of today's value would
        match it.
        """
        from planetscale_discovery.database.analyzers import citus_analyzer

        connection, ctx = ready()
        monkeypatch.setattr(citus_analyzer, "TABLE_RELKINDS", "('r', 'p', 'f')")
        citus_analyzer.CitusAnalyzer(connection, config={"citus": ctx}).analyze()
        count = [s for s in connection.statements() if "AS regular_count" in s]
        assert count, "the regular tables were not counted"
        assert "c.relkind IN ('r', 'p', 'f')" in count[0]

    def misnamed_index(self):
        """pgbench_branches_pkey on shard 102138 lost its shard id when
        PostgreSQL named it automatically, so shard_name() does not match it."""
        remote = {k: list(v) for k, v in CITUS_REMOTE_INDEX_ROWS.items()}
        remote[3] = [
            (
                dict(
                    citus_index_row("pgbench_branches_pkey_idx", 313),
                    table_relname="pgbench_branches_102138",
                )
                if r["index_relname"] == "pgbench_branches_pkey_102138"
                else r
            )
            for r in remote[3]
        ]
        return remote

    def test_an_index_missing_on_a_shard_stops_with_the_remedy(self):
        """Catches an index reported with a partial size and, once matched on no
        shard, idx_scan None, which drops it from unused_indexes."""
        from tests.fixtures.database_responses import CITUS_INDEXES

        indexes = [dict(i, is_partition_index=True) for i in CITUS_INDEXES]
        connection, ctx = ready(remote_indexes=self.misnamed_index(), indexes=indexes)
        with pytest.raises(CitusClusterError) as error:
            ctx.shard_statistics(connection)
        message = str(error.value)
        assert (
            "index public.pgbench_branches_pkey: not found on 1 of 2 shards" in message
        )
        assert "fix_all_partition_shard_index_names()" in message
        assert "If the index is still not found" in message

    def test_an_index_dropped_during_the_read_is_named_as_dropped(self):
        """Catches an index dropped between the two index reads reported as one
        created with DDL propagation off, a remedy that does not apply."""
        from tests.fixtures.database_responses import (
            CITUS_BRANCHES_PKEY_OID,
            CITUS_INDEX_SHARD_NAMES,
            CITUS_INDEXES,
        )

        def gone(row):
            return row["index_oid"] == CITUS_BRANCHES_PKEY_OID

        remote = {
            nodeid: [
                r
                for r in rows
                if not r["index_relname"].startswith("pgbench_branches_pkey_")
            ]
            for nodeid, rows in CITUS_REMOTE_INDEX_ROWS.items()
        }
        connection, ctx = ready(
            remote_indexes=remote,
            index_shard_names=[r for r in CITUS_INDEX_SHARD_NAMES if not gone(r)],
        )
        reads = []

        def index_list(sql, params):
            reads.append(sql)
            return (
                CITUS_INDEXES
                if len(reads) == 1
                else [r for r in CITUS_INDEXES if not gone(r)]
            )

        connection.handlers.insert(0, ("i.relname AS index_name", index_list))
        with pytest.raises(CitusClusterError) as error:
            ctx.shard_statistics(connection)
        message = str(error.value)
        assert "indexes changed during discovery" in message
        assert "index public.pgbench_branches_pkey: dropped during discovery" in message
        assert "enable_ddl_propagation" not in message

    def test_an_index_created_during_the_read_is_left_out(self):
        """Catches an index created between the two index reads stopping the run
        with a bare KeyError, although leaving it out misreports nothing."""
        from tests.fixtures.database_responses import (
            CITUS_BRANCHES_PKEY_OID,
            CITUS_INDEX_SHARD_NAMES,
        )

        created = {
            "index_oid": 99999,
            "shardid": 102137,
            "shard_index_name": "pgbench_branches_created_idx_102137",
        }
        connection, ctx = ready(index_shard_names=CITUS_INDEX_SHARD_NAMES + [created])
        stats = ctx.shard_statistics(connection)
        assert 99999 not in stats.indexes
        assert CITUS_BRANCHES_PKEY_OID in stats.indexes

    def test_an_index_not_on_a_partition_is_not_sent_to_the_partition_fix(self):
        """Catches an index created with DDL propagation off, on no shard of a table
        that is not partitioned, sent to a function that renames nothing.
        """
        remote = {
            nodeid: [
                r
                for r in rows
                if not r["index_relname"].startswith("pgbench_branches_pkey_")
            ]
            for nodeid, rows in CITUS_REMOTE_INDEX_ROWS.items()
        }
        connection, ctx = ready(remote_indexes=remote)
        with pytest.raises(CitusClusterError) as error:
            ctx.shard_statistics(connection)
        message = str(error.value)
        assert "not found on 2 of 2 shards" in message
        assert "fix_all_partition_shard_index_names" not in message
        index_names = [
            sql for sql, _ in connection.executed if "x.indisvalid AS is_valid" in sql
        ]
        assert "i.relispartition AS is_partition_index" in index_names[0]
        assert "citus.enable_ddl_propagation off" in message

    @staticmethod
    def invalid_branches_pkey():
        from tests.fixtures.database_responses import CITUS_INDEXES

        return [
            dict(i, is_valid=i["index_name"] != "pgbench_branches_pkey")
            for i in CITUS_INDEXES
        ]

    def test_an_invalid_index_missing_on_a_shard_does_not_stop(self):
        """Catches a failed CREATE INDEX CONCURRENTLY, which builds shard indexes on
        only some shards, stopping a read-only run until the index is rebuilt.
        """
        from tests.fixtures.database_responses import CITUS_BRANCHES_PKEY_OID

        connection, ctx = ready(
            remote_indexes=self.misnamed_index(), indexes=self.invalid_branches_pkey()
        )
        stats = ctx.shard_statistics(connection)
        invalid = stats.indexes[CITUS_BRANCHES_PKEY_OID]
        assert (invalid["shards_read"], invalid["shard_count"]) == (1, 2)
        assert invalid["index_size_bytes"] == 16384

    def test_an_invalid_index_on_no_shard_records_no_scan(self):
        """Catches an invalid index that exists on no shard reported with idx_scan
        None, which drops it from unused_indexes.
        """
        from tests.fixtures.database_responses import CITUS_BRANCHES_PKEY_OID

        remote = {
            nodeid: [
                r
                for r in rows
                if not r["index_relname"].startswith("pgbench_branches_pkey_")
            ]
            for nodeid, rows in CITUS_REMOTE_INDEX_ROWS.items()
        }
        connection, ctx = ready(
            remote_indexes=remote, indexes=self.invalid_branches_pkey()
        )
        invalid = ctx.shard_statistics(connection).indexes[CITUS_BRANCHES_PKEY_OID]
        assert (invalid["shards_read"], invalid["idx_scan"]) == (0, 0)
        assert invalid["index_size_bytes"] == 0

    def test_a_shard_found_on_no_node_stops(self):
        """Catches a shard that no node returned summed as if it held no rows."""
        remote = {k: list(v) for k, v in CITUS_REMOTE_TABLE_ROWS.items()}
        remote[3] = [r for r in remote[3] if r["relname"] != "pgbench_branches_102138"]
        connection, ctx = ready(remote_tables=remote)
        with pytest.raises(CitusClusterError, match="shard 102138"):
            ctx.shard_statistics(connection)

    def test_shards_are_read_in_batches(self, monkeypatch):
        """Catches one call holding every shard of a large cluster in memory."""
        monkeypatch.setattr(citus, "SHARD_BATCH_SIZE", 2)
        connection, ctx = ready()
        ctx.shard_statistics(connection)
        batches = [
            params[1]
            for sql, params in connection.executed
            if "run_command_on_all_nodes(" in sql
            and params
            and params[0] == citus.TABLE_COMMAND
        ]
        assert sorted(len(b) for b in batches) == [1, 2]


class TestScale:
    """Combining stays linear in the number of tables, shards and indexes."""

    TABLES = 20000

    def cluster(self):
        tables, shards, placements, indexes, shard_names = [], [], [], [], []
        remote_tables = {2: [], 3: []}
        remote_columns = {2: [], 3: []}
        remote_indexes = {2: [], 3: []}
        for i in range(self.TABLES):
            schema, oid, shardid = f"tenant_{i}", 100000 + i, 200000 + i
            node, relname = 2 + i % 2, f"orders_{shardid}"
            tables.append(
                {
                    "table_oid": oid,
                    "schema_name": schema,
                    "table_name": "orders",
                    "table_type": "schema",
                    "partmethod": "n",
                    "repmodel": "s",
                    "colocation_id": 10 + i,
                    "distribution_column": None,
                    "autoconverted": False,
                    "shard_count": 1,
                    "access_method": "heap",
                }
            )
            shards.append(
                {"shardid": shardid, "table_oid": oid, "shard_relname": relname}
            )
            placements.append(
                {"shardid": shardid, "groupid": node - 1, "shardstate": 1}
            )
            remote_tables[node].append(
                citus_table_row(relname, 100, schema_name=schema)
            )
            remote_columns[node].append(
                {
                    "schema_name": schema,
                    "relname": relname,
                    "attname": "order_id",
                    "inherited": False,
                    "null_frac": 0.0,
                    "n_distinct": -1.0,
                    "correlation": 1.0,
                }
            )
            for offset, name in enumerate(("orders_pkey", "orders_status_idx")):
                index_oid = 300000 + 2 * i + offset
                indexes.append(
                    {
                        "index_oid": index_oid,
                        "table_oid": oid,
                        "schema_name": schema,
                        "index_name": name,
                    }
                )
                shard_names.append(
                    {
                        "index_oid": index_oid,
                        "shardid": shardid,
                        "shard_index_name": f"{name}_{shardid}",
                    }
                )
                remote_indexes[node].append(
                    dict(citus_index_row(f"{name}_{shardid}", 100), schema_name=schema)
                )
        return citus_handlers(
            tables=tables,
            shards=shards,
            placements=placements,
            remote_tables=remote_tables,
            remote_columns=remote_columns,
            remote_indexes=remote_indexes,
            indexes=indexes,
            index_shard_names=shard_names,
        )

    def test_shards_and_placements_are_scanned_a_fixed_number_of_times(self):
        """Catches a loop that rescans every shard or placement for each table:
        100k schema tables make that 1e10 iterations. Counting scans makes the
        test independent of the machine's speed."""

        class CountingDict(dict):
            scans = 0

            def items(self):
                CountingDict.scans += 1
                return super().items()

        class CountingList(list):
            scans = 0

            def __iter__(self):
                CountingList.scans += 1
                return super().__iter__()

        connection = FakeConnection(self.cluster())
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        ctx.shards = CountingDict(ctx.shards)
        ctx.placements = CountingList(ctx.placements)

        stats = citus.collect_shard_statistics(connection, ctx)

        assert len(stats.tables) == self.TABLES
        assert CountingDict.scans <= 5
        assert CountingList.scans <= 5

    def test_a_large_schema_sharded_cluster_is_combined_quickly(self):
        """Catches a loop that rescans every index's rows for each index, which
        takes minutes at this size and hours at 100k schemas."""
        import time

        connection = FakeConnection(self.cluster())
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        started = time.monotonic()
        stats = citus.collect_shard_statistics(connection, ctx)
        elapsed = time.monotonic() - started

        assert len(stats.indexes) == 2 * self.TABLES
        assert stats.tables[100000 + self.TABLES - 1]["estimated_rows"] == 100
        assert elapsed < 10, f"combining {self.TABLES} tables took {elapsed:.1f}s"


class TestRemoteCommands:
    FORBIDDEN = (
        "most_common_vals",
        "most_common_freqs",
        "histogram_bounds",
        "pg_dist_authinfo",
        "pg_dist_poolinfo",
        "shardminvalue",
        "shardmaxvalue",
    )

    @pytest.mark.parametrize("command", citus.REMOTE_COMMANDS)
    def test_commands_are_selects(self, command):
        """Catches a remote command that writes on a node, including through a
        data-modifying CTE."""
        assert command.startswith(("SELECT ", "WITH "))
        writes = re.findall(
            r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|COPY|CREATE|DROP|ALTER|GRANT"
            r"|ANALYZE|VACUUM)\b",
            command,
        )
        assert writes == []

    @pytest.mark.parametrize("command", citus.REMOTE_COMMANDS)
    def test_commands_name_no_value_or_credential(self, command):
        """Catches customer values or credentials read from a node."""
        for name in self.FORBIDDEN:
            assert name not in command

    @pytest.mark.parametrize("command", citus.REMOTE_COMMANDS)
    def test_commands_qualify_every_catalog_object(self, command):
        """Catches a planted relation or function shadowing a catalog on a node.

        A remote session does not inherit this tool's search_path pin.
        """
        unqualified = re.findall(
            r"(?<!pg_catalog\.)\b(pg_(?!catalog\b)[a-z_]+)\b", command
        )
        assert unqualified == []
        functions = re.findall(
            r"(?<!pg_catalog\.)\b(json_agg|unnest|relation_is_a_known_shard"
            r"|pg_relation_size|pg_total_relation_size|current_database)\(",
            command,
        )
        assert functions == []

    def test_every_citus_query_reads_only_catalogs(self):
        """Catches a Citus query that reads a customer table.

        Covers the remote commands and every query the preflight, the shard read
        and the Citus module run on the coordinator.
        """
        from planetscale_discovery.database.analyzers.citus_analyzer import (
            CitusAnalyzer,
        )

        connection, ctx = ready()
        ctx.shard_statistics(connection)
        CitusAnalyzer(connection, config={"citus": ctx}).analyze()
        statements = connection.statements() + [
            params[0]
            for sql, params in connection.executed
            if "run_command_on_all_nodes(" in sql
        ]
        for sql in statements:
            # A CTE the statement defines itself is not a relation it reads.
            ctes = set(re.findall(r"\b(\w+) AS MATERIALIZED \(", sql))
            for target in re.findall(
                r"\b(?:FROM|JOIN)\s+(?:ROWS FROM \(\s*)?([^\s(]+\(?)", sql
            ):
                if target.rstrip(")") in ctes:
                    continue
                assert target.startswith("pg_catalog."), (target, sql)
            for name in self.FORBIDDEN:
                assert name not in sql

    def test_shard_names_are_parameters(self):
        """Catches a command built with Python string formatting."""
        connection, ctx = ready()
        shard = ("public", "pgbench_branches_102137")
        citus.run_on_all_nodes(connection, ctx, citus.TABLE_COMMAND, [shard])
        sql, params = connection.executed[-1]
        assert (
            "pg_catalog.format(%s, %s::pg_catalog.name[]::text,"
            " %s::pg_catalog.name[]::text)"
        ) in sql
        assert "pgbench_branches_102137" not in sql
        assert params == [citus.TABLE_COMMAND, ["public"], ["pgbench_branches_102137"]]

    @pytest.mark.parametrize(
        "command", [citus.TABLE_COMMAND, citus.COLUMN_COMMAND, citus.INDEX_COMMAND]
    )
    def test_shard_commands_look_up_their_batch_by_name(self, command):
        """Catches a shard command that filters every relation on a node, which
        makes each batch's work grow with the node's relation count.

        The plans are checked against a live cluster in
        tests/integration/test_citus_discovery.py.
        """
        assert command.startswith(citus._SHARDS)
        assert "JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid" in command
        assert "AND c.relname = w.relname" in command
        assert "substring(" not in command

    @pytest.mark.parametrize("command", citus.REMOTE_COMMANDS)
    def test_no_command_casts_a_relation_name_to_a_number(self, command):
        """Catches a shard id parsed from every relname on a node, where an ordinary
        table such as log_20261029101112123456 overflows int8 and fails the run.
        SQL does not promise to evaluate relation_is_a_known_shard() first."""
        assert "int8" not in command
        assert "bigint" not in command


class TestOutputCompatibility:
    """Existing readers see the same keys and types for Citus and regular rows."""

    TABLES = [
        {
            "schema_name": "public",
            "table_name": name,
            "table_oid": oid,
            "owner": "postgres",
            "table_type": "r",
            "persistence": "p",
            "is_partition": False,
            "has_indexes": True,
            "has_rules": False,
            "has_triggers": False,
            "has_inheritance": False,
            "estimated_rows": rows,
            "table_size_bytes": size,
            "table_size_human": f"{size} bytes",
            "total_size_bytes": size,
            "total_size_human": f"{size} bytes",
            "description": None,
        }
        for name, oid, rows, size in (
            ("pgbench_branches", CITUS_BRANCHES_OID, 0, 0),
            ("reference_t1", CITUS_REFERENCE_OID, -1, 0),
            ("unsharded_t1", 17572, 25000016, 1914019840),
        )
    ]

    def schema_handlers(self):
        return [
            ("SHOW server_version_num", [{"server_version_num": "180006"}]),
            ("c.relhassubclass as has_inheritance", self.TABLES),
            (
                "FROM pg_stats",
                [
                    {
                        "schemaname": "public",
                        "tablename": "unsharded_t1",
                        "attname": "id",
                        "n_distinct": -1.0,
                        "null_frac": 0.0,
                        "correlation": 1.0,
                    }
                ],
            ),
            (
                "pg_database_size(d.datname) as size_bytes",
                [
                    {
                        "database_name": "postgres",
                        "size_bytes": 19327047359,
                        "size_human": "18 GB",
                    }
                ],
            ),
            (
                "pg_get_indexdef(i.oid) as index_definition",
                [
                    {
                        "schema_name": "public",
                        "index_name": name,
                        "table_name": table,
                        "estimated_rows": rows,
                        "index_size_bytes": size,
                        "index_size_human": f"{size} bytes",
                    }
                    for name, table, rows, size in (
                        ("pgbench_branches_pkey", "pgbench_branches", 0, 8192),
                        ("unsharded_t1_pkey", "unsharded_t1", 25000016, 788561920),
                    )
                ],
            ),
            # The fixture has no partitioned Citus table, so a Citus table is
            # listed as a partition to exercise the filling of the fields.
            (
                "child_class.relispartition = true",
                [
                    {
                        "parent_schema": "public",
                        "parent_table": "events",
                        "partition_schema": "public",
                        "partition_name": "pgbench_branches",
                        "partition_bound": "FOR VALUES FROM (1) TO (10)",
                        "partition_size_bytes": 0,
                        "partition_size_human": "0 bytes",
                        "estimated_rows": 0,
                    }
                ],
            ),
        ]

    def analyze(self, with_citus=True):
        handlers = citus_handlers() if with_citus else []
        connection = FakeConnection(handlers + self.schema_handlers())
        ctx = None
        if with_citus:
            ctx = citus.detect_citus(connection)
            citus.preflight(connection, ctx)
        return SchemaAnalyzer(
            connection, config={"citus": ctx} if ctx else {}
        ).analyze()

    def test_no_citus_key_without_citus(self):
        """Catches a citus key written into the schema report of a database without
        Citus, where the output format promises none anywhere."""
        assert not list(keys_named(self.analyze(with_citus=False), "citus"))

    def test_each_section_holds_the_documented_citus_keys(self):
        """Catches a key added to or dropped from a citus object without
        docs/output-format.md, which lists the keys for each section."""
        result = self.analyze()
        tables = {t["table_name"]: t for t in result["table_analysis"]}
        assert set(tables["pgbench_branches"]["citus"]) == {
            "table_type",
            "distribution_column",
            "colocation_id",
            "shard_count",
            "partmethod",
            "replication_model",
            "autoconverted",
            "access_method",
            "placements_per_shard",
            "source",
            "coordinator",
            "shard_statistics",
        }
        assert set(tables["pgbench_branches"]["citus"]["shard_statistics"]) == {
            "shards_read",
            "shards_never_analyzed",
            "inactive_placements",
            "placement_size_bytes",
            "columns",
        }
        indexes = {i["index_name"]: i for i in result["index_analysis"]}
        assert set(indexes["pgbench_branches_pkey"]["citus"]) == {
            "source",
            "shards_read",
            "coordinator",
        }
        assert set(result["partition_analysis"][0]["citus"]) == {
            "source",
            "coordinator",
        }
        assert set(result["database_catalog"][0]["citus"]) == {"source", "coordinator"}

    @staticmethod
    def shape(row):
        """Key order and value types, without the citus key."""
        return [(k, type(v)) for k, v in row.items() if k != "citus"]

    def test_citus_rows_keep_every_key_type_and_position(self):
        """Catches a reader of the existing fields breaking on a Citus table."""
        rows = {t["table_name"]: t for t in self.analyze()["table_analysis"]}
        regular = rows["unsharded_t1"]
        for name in ("pgbench_branches", "reference_t1"):
            assert self.shape(rows[name]) == self.shape(regular)
            assert self.shape(rows[name]["statistics"][0]) == self.shape(
                regular["statistics"][0]
            )

    def test_index_entries_hold_logical_values(self):
        """Catches a Citus index reported with its shell's size and counters, or a
        regular index given a citus key.
        """
        indexes = {i["index_name"]: i for i in self.analyze()["index_analysis"]}
        branches = indexes["pgbench_branches_pkey"]
        assert branches["index_size_bytes"] == 2 * 16384
        assert branches["estimated_rows"] == 630
        assert branches["usage_stats"]["idx_scan"] == 0
        assert branches["citus"]["coordinator"]["index_size_bytes"] == 8192
        assert "citus" not in indexes["unsharded_t1_pkey"]

    def test_a_partitioned_index_gets_no_usage_stats(self):
        """Catches usage_stats full of None on a Citus parent's index, where a
        regular partitioned index has no usage_stats at all."""
        remote_indexes = {
            nodeid: [
                dict(row, idx_scan=None, idx_tup_read=None, idx_tup_fetch=None)
                for row in rows
            ]
            for nodeid, rows in CITUS_REMOTE_INDEX_ROWS.items()
        }
        connection = FakeConnection(
            citus_handlers(remote_indexes=remote_indexes) + self.schema_handlers()
        )
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        indexes = {
            i["index_name"]: i
            for i in SchemaAnalyzer(connection, config={"citus": ctx}).analyze()[
                "index_analysis"
            ]
        }
        assert "usage_stats" not in indexes["pgbench_branches_pkey"]

    def test_an_invalid_index_holds_the_shards_values_and_is_flagged(self):
        """Catches an invalid index reported with its shell's size while its shard
        indexes take space on the nodes, or with no sign that it is invalid.
        """
        from tests.fixtures.database_responses import CITUS_INDEXES

        indexes = [
            dict(i, is_valid=i["index_name"] != "pgbench_branches_pkey")
            for i in CITUS_INDEXES
        ]
        connection = FakeConnection(
            citus_handlers(indexes=indexes) + self.schema_handlers()
        )
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        result = SchemaAnalyzer(connection, config={"citus": ctx}).analyze()
        branches = {i["index_name"]: i for i in result["index_analysis"]}[
            "pgbench_branches_pkey"
        ]
        assert branches["index_size_bytes"] == 2 * 16384
        assert branches["citus"]["source"] == "shards"
        assert branches["citus"]["reason"] == "invalid_index"
        assert branches["citus"]["shard_count"] == 2
        assert branches["citus"]["coordinator"]["index_size_bytes"] == 8192
        valid = {i["index_name"]: i for i in result["index_analysis"]}
        assert "reason" not in valid["unsharded_t1_pkey"].get("citus", {})

    def test_partition_entries_hold_logical_values(self):
        """Catches a Citus partition reported with its shell's size and rows."""
        partition = self.analyze()["partition_analysis"][0]
        assert partition["partition_size_bytes"] == 2 * 16384
        assert partition["estimated_rows"] == 630
        assert partition["citus"]["coordinator"]["partition_size_bytes"] == 0

    def test_a_table_renamed_since_the_classification_is_still_a_citus_table(self):
        """Catches a name lookup, which labels a Citus table renamed between the
        classification and the table listing as regular, with its shell's values."""
        renamed = [
            (
                dict(t, table_name="branches_v2")
                if t["table_name"] == "pgbench_branches"
                else t
            )
            for t in self.TABLES
        ]
        handlers = [("c.relhassubclass as has_inheritance", renamed)]
        connection = FakeConnection(
            handlers + citus_handlers() + self.schema_handlers()
        )
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        rows = {
            t["table_name"]: t
            for t in SchemaAnalyzer(connection, config={"citus": ctx}).analyze()[
                "table_analysis"
            ]
        }
        assert rows["branches_v2"]["citus"]["table_type"] == "distributed"
        assert rows["branches_v2"]["estimated_rows"] == 630

    def test_existing_fields_hold_logical_values(self):
        """Catches a Citus table reported with its shell's rows and size, or without the
        coordinator's values under citus.coordinator.
        """
        rows = {t["table_name"]: t for t in self.analyze()["table_analysis"]}
        branches = rows["pgbench_branches"]
        assert branches["estimated_rows"] == 630
        assert branches["total_size_bytes"] == 131072
        assert branches["total_size_human"] == "131072 bytes"
        assert branches["citus"]["coordinator"]["estimated_rows"] == 0
        assert branches["citus"]["source"] == "shards"
        assert rows["reference_t1"]["statistics"][0]["attname"] == "id"

    def test_regular_tables_are_unchanged(self):
        """Catches a Citus change altering what is reported for a regular table."""
        with_citus = {t["table_name"]: t for t in self.analyze()["table_analysis"]}
        plain = {
            t["table_name"]: t for t in self.analyze(with_citus=False)["table_analysis"]
        }
        regular = dict(with_citus["unsharded_t1"])
        assert regular.pop("citus") == {"table_type": "regular"}
        assert regular == plain["unsharded_t1"]

    def test_the_current_database_is_counted_logically(self):
        """Catches the current database's size counted with the coordinator's placements
        twice, or without the data on the workers.
        """
        catalog = self.analyze()["database_catalog"][0]
        placements_on_coordinator = 5801107456
        logical = 2 * 65536 + 5801107456
        assert catalog["size_bytes"] == (
            19327047359 - placements_on_coordinator - 24576 + logical
        )
        assert catalog["citus"]["coordinator"]["size_bytes"] == 19327047359

    def test_a_failed_shard_read_writes_no_shell_values(self):
        """Catches a table_analysis built from coordinator values after a failure."""
        connection = FakeConnection(citus_handlers() + self.schema_handlers())
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        connection.handlers.insert(
            0, ("run_command_on_all_nodes(", RuntimeError("node went away"))
        )
        with pytest.raises(CitusClusterError):
            SchemaAnalyzer(connection, config={"citus": ctx}).analyze()


class TestPerformanceAnalyzer:
    def analyzer(self, idx_scan=0, **citus_kwargs):
        from tests.fixtures.database_responses import (
            CITUS_REMOTE_INDEX_ROWS,
            citus_index_row,
        )

        remote_indexes = {k: list(v) for k, v in CITUS_REMOTE_INDEX_ROWS.items()}
        remote_indexes[2] = [
            citus_index_row("pgbench_branches_pkey_102137", 317, idx_scan=idx_scan)
        ] + remote_indexes[2][1:]
        handlers = citus_handlers(remote_indexes=remote_indexes, **citus_kwargs) + [
            (
                "FROM pg_stat_user_tables",
                [
                    {
                        "schemaname": "public",
                        "tablename": name,
                        **{key: 0 for key in citus.TABLE_COUNTER_KEYS},
                    }
                    for name in ("pgbench_branches", "unsharded_t1")
                ],
            ),
            (
                "pg_size_pretty(pg_database_size(datname)) as size_human",
                [
                    {
                        "datname": "postgres",
                        "size_bytes": 19327047359,
                        "size_human": "18 GB",
                    }
                ],
            ),
            (
                "FROM pg_stat_user_indexes",
                [
                    {
                        "schemaname": "public",
                        "tablename": name,
                        "indexrelname": f"{name}_pkey",
                        "idx_scan": 0,
                        "idx_tup_read": 0,
                        "idx_tup_fetch": 0,
                        "index_size_bytes": 8192,
                        "index_size_human": "8192 bytes",
                    }
                    for name in ("pgbench_branches", "unsharded_t1")
                ],
            ),
            (
                "FROM pg_tables",
                [
                    {
                        "schemaname": "public",
                        "tablename": "unsharded_t1",
                        "total_size_bytes": 2703138816,
                        "total_size_human": "2578 MB",
                        "table_size_bytes": 1914019840,
                        "table_size_human": "1825 MB",
                    },
                    {
                        "schemaname": "public",
                        "tablename": "pgbench_branches",
                        "total_size_bytes": 8192,
                        "total_size_human": "8192 bytes",
                        "table_size_bytes": 0,
                        "table_size_human": "0 bytes",
                    },
                ],
            ),
        ]
        connection = FakeConnection(handlers)
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        return PerformanceAnalyzer(connection, config={"citus": ctx})

    def test_no_citus_key_without_citus(self):
        """Catches a citus key written into the performance report of a database
        without Citus, where the output format promises none anywhere."""
        analyzer = self.analyzer()
        plain = PerformanceAnalyzer(analyzer.connection, config={})
        assert not list(keys_named(plain.analyze(), "citus"))

    def test_each_section_holds_the_documented_citus_keys(self):
        """Catches a key added to or dropped from a citus object without
        docs/output-format.md, which lists the keys for each section."""
        result = self.analyzer().analyze()
        rows = {r["tablename"]: r for r in result["table_statistics"]}
        assert set(rows["pgbench_branches"]["citus"]) == {
            "table_type",
            "source",
            "shards_read",
            "shards_without_event",
            "coordinator",
        }
        indexes = {i["indexrelname"]: i for i in result["index_usage"]["all_indexes"]}
        assert set(indexes["pgbench_branches_pkey"]["citus"]) == {
            "source",
            "shards_read",
            "coordinator",
        }
        largest = result["resource_utilization"]["largest_tables"]
        for row in largest:
            if "citus" in row:
                assert set(row["citus"]) == {"source", "coordinator"}
        added = [r for r in largest if r["tablename"] == "reference_t1"]
        assert added and added[0]["citus"]["coordinator"] is None

    def test_a_scanned_distributed_index_is_not_unused(self):
        """Catches every Citus index listed as unused because its shell is idle."""
        usage = self.analyzer(idx_scan=42).analyze()["index_usage"]
        unused = [i["indexrelname"] for i in usage["unused_indexes"]]
        assert "pgbench_branches_pkey" not in unused
        assert "unsharded_t1_pkey" in unused

    def test_table_counters_hold_the_shards_values(self):
        """Catches Citus table activity reported from the idle shell."""
        rows = {
            r["tablename"]: r for r in self.analyzer().analyze()["table_statistics"]
        }
        branches = rows["pgbench_branches"]
        assert branches["n_tup_ins"] == 630
        assert branches["seq_scan"] == 2
        assert branches["citus"]["coordinator"]["n_tup_ins"] == 0
        assert rows["unsharded_t1"]["n_tup_ins"] == 0
        assert "citus" not in rows["unsharded_t1"]

    def test_the_current_database_size_is_logical(self):
        """Catches the performance report's database size taken from the coordinator
        alone.
        """
        sizes = self.analyzer().analyze()["resource_utilization"]["database_sizes"]
        assert sizes[0]["size_bytes"] == 19327047359 - 5801107456 - 24576 + (
            2 * 65536 + 5801107456
        )
        assert sizes[0]["citus"]["coordinator"]["size_bytes"] == 19327047359

    def test_index_sizes_hold_the_shards_values(self):
        """Catches a Citus index in index_usage reported with its shell's 8 kB, while a
        regular index keeps its own size.
        """
        indexes = {
            i["indexrelname"]: i
            for i in self.analyzer().analyze()["index_usage"]["all_indexes"]
        }
        assert indexes["pgbench_branches_pkey"]["index_size_bytes"] == 2 * 16384
        assert indexes["unsharded_t1_pkey"]["index_size_bytes"] == 8192

    def test_a_scanned_invalid_index_is_not_unused(self):
        """Catches an invalid index whose shard indexes take scans on their nodes
        listed as unused with its shell's idle counters and 8 kB.
        """
        from tests.fixtures.database_responses import CITUS_INDEXES

        indexes = [
            dict(i, is_valid=i["index_name"] != "pgbench_branches_pkey")
            for i in CITUS_INDEXES
        ]
        usage = self.analyzer(idx_scan=42, indexes=indexes).analyze()["index_usage"]
        branches = {i["indexrelname"]: i for i in usage["all_indexes"]}[
            "pgbench_branches_pkey"
        ]
        assert branches["idx_scan"] == 42
        assert branches["index_size_bytes"] == 2 * 16384
        assert branches["citus"]["reason"] == "invalid_index"
        assert "pgbench_branches_pkey" not in [
            i["indexrelname"] for i in usage["unused_indexes"]
        ]

    def test_largest_tables_stays_at_its_limit(self):
        """Catches every Citus table appended to largest_tables, which turns the
        section into a list of 100k rows on a cluster with schema-based sharding."""
        from planetscale_discovery.database.analyzers.performance_analyzer import (
            LARGEST_TABLES_LIMIT,
        )

        count = LARGEST_TABLES_LIMIT + 5
        ctx = citus.CitusContext(extension_version="14.1-1")
        stats = citus.ShardStatistics()
        for i in range(count):
            oid = 50000 + i
            ctx.tables[oid] = table(oid=oid, schema_name=f"tenant_{i}", table_name="t")
            stats.tables[oid] = {"total_size_bytes": i, "table_size_bytes": i}
        regular = {
            "schemaname": "public",
            "tablename": "big",
            "total_size_bytes": 10 * count,
            "table_size_bytes": 10 * count,
        }
        largest = [regular]
        PerformanceAnalyzer(None, config={"citus": ctx})._apply_citus_largest_tables(
            largest, stats, {}, ctx
        )
        assert len(largest) == LARGEST_TABLES_LIMIT
        assert largest[0] is regular
        # The smallest Citus tables are the ones dropped.
        assert [r["total_size_bytes"] for r in largest[1:]] == list(
            range(count - 1, count - LARGEST_TABLES_LIMIT, -1)
        )

    def test_large_citus_tables_are_ranked_by_logical_size(self):
        """Catches a large distributed table missing from largest_tables."""
        largest = self.analyzer().analyze()["resource_utilization"]["largest_tables"]
        names = [t["tablename"] for t in largest]
        assert names[0] == "reference_t1"
        assert set(names) == {"reference_t1", "unsharded_t1", "pgbench_branches"}
