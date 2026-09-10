"""Bytes per table and per index, their growth, and the peak write rate.

A row count cannot say how big a shard will be, and shard count is decided in
bytes. Sizes are gauges, so they are carried and not differenced; growth is the
difference between the first and last reading; the peak is the busiest interval
rather than the window average.
"""

from unittest.mock import MagicMock

from planetscale_discovery.workload.collect import (
    INDEX_GAUGES,
    INDEX_SQL,
    TABLE_SIZE_GAUGES,
    TABLE_SQL,
    WorkloadCollector,
)
from planetscale_discovery.workload.bundle import render_table_activity
from planetscale_discovery.workload.merge import merge_snapshots

from .test_workload_table_activity import index, snap, table, window


def sized(name, total=None, ins=0, **kwargs):
    """A table entry carrying size gauges, which table() leaves empty."""
    entry = table(name, ins=ins, **kwargs)
    if total is not None:
        entry["gauges"] = {
            "total_size_bytes": float(total),
            "table_size_bytes": float(total) * 0.6,
            "indexes_size_bytes": float(total) * 0.4,
        }
    return entry


def sized_index(table_name, name, size=None, **kwargs):
    entry = index(table_name, name, **kwargs)
    if size is not None:
        entry["gauges"] = {"size_bytes": float(size)}
    return entry


class TestTheQueriesAskForTheSizes:
    def test_the_table_query_selects_all_three_table_sizes(self):
        """Catches a size column dropped from one query but not the other."""
        for name in TABLE_SIZE_GAUGES:
            assert f"AS {name}" in TABLE_SQL

    def test_the_index_query_selects_the_index_size(self):
        for name in INDEX_GAUGES:
            assert f"AS {name}" in INDEX_SQL

    def test_sizes_are_keyed_by_oid_rather_than_by_name(self):
        """A name needing quotes breaks a concatenated identifier."""
        assert "pg_total_relation_size(c.oid)" in TABLE_SQL
        assert "pg_relation_size(s.indexrelid)" in INDEX_SQL


class TestSizesAreCollectedAsGauges:
    def _collector(self):
        collector = WorkloadCollector(MagicMock())
        return collector

    def test_a_table_size_lands_in_gauges_not_counters(self):
        """A size in counters would be differenced, which makes it nonsense."""
        row = {
            "schemaname": "public",
            "relname": "orders",
            "total_size_bytes": 4096,
            "table_size_bytes": 2048,
            "indexes_size_bytes": 2048,
            "reltuples": 10.0,
        }
        entry = self._collector()._table_row(row)
        assert entry["gauges"]["total_size_bytes"] == 4096.0
        for name in TABLE_SIZE_GAUGES:
            assert name not in entry["counters"]

    def test_an_index_size_lands_in_gauges(self):
        row = {
            "schemaname": "public",
            "relname": "orders",
            "indexrelname": "orders_pkey",
            "size_bytes": 8192,
        }
        entry = self._collector()._index_row(row)
        assert entry["gauges"]["size_bytes"] == 8192.0
        assert "size_bytes" not in entry["counters"]


class TestTheLastAndFirstReadingBothSurvive:
    def test_a_table_reports_the_last_reading_and_the_first(self):
        merged = window(
            [sized("public.orders", total=1000)],
            [sized("public.orders", total=2500)],
        )
        entry = render_table_activity(merged, "id")["tables"][0]
        assert entry["total_size_bytes"] == 2500
        assert entry["first_seen"]["total_size_bytes"] == 1000
        assert entry["total_size_growth_bytes"] == 1500

    def test_an_index_reports_the_same_three_figures(self):
        """Indexes carried no gauge before sizes, so this is a new carry path."""
        merged = window(
            [sized("public.orders", total=1000)],
            [sized("public.orders", total=1000)],
            [sized_index("public.orders", "orders_pkey", size=400)],
            [sized_index("public.orders", "orders_pkey", size=700)],
        )
        entry = render_table_activity(merged, "id")["indexes"][0]
        assert entry["size_bytes"] == 700
        assert entry["first_seen"]["size_bytes"] == 400
        assert entry["size_growth_bytes"] == 300

    def test_a_shrinking_table_reports_negative_growth(self):
        """Vacuum truncates. A clamp to zero would hide a real reading."""
        merged = window(
            [sized("public.orders", total=9000)],
            [sized("public.orders", total=4000)],
        )
        entry = render_table_activity(merged, "id")["tables"][0]
        assert entry["total_size_growth_bytes"] == -5000


class TestAbsentIsNotZero:
    def test_a_table_absent_from_the_first_snapshot_has_no_first_reading(self):
        """A table created mid-window was not measured as empty."""
        merged = window(
            [sized("public.orders", total=1000)],
            [sized("public.orders", total=1000), sized("public.new", total=500)],
        )
        entries = {t["table"]: t for t in render_table_activity(merged, "id")["tables"]}
        assert entries["public.new"]["first_seen"]["total_size_bytes"] is None
        assert entries["public.new"]["total_size_growth_bytes"] is None

    def test_a_server_that_reported_no_size_reports_null_not_zero(self):
        """Zero bytes is a claim. Not measured is a different claim."""
        merged = window([table("public.orders")], [table("public.orders")])
        entry = render_table_activity(merged, "id")["tables"][0]
        assert entry["total_size_bytes"] is None
        assert entry["total_size_growth_bytes"] is None

    def test_one_snapshot_gives_a_size_but_no_growth(self):
        """The cumulative path has no earlier reading to difference against."""
        merged = merge_snapshots(
            [
                snap(
                    "2026-08-25 10:00:00+00:00",
                    [sized("public.orders", total=700)],
                    [],
                )
            ],
            allow_partial=True,
        )
        entry = render_table_activity(merged, "id")["tables"][0]
        assert entry["total_size_bytes"] == 700
        assert entry["total_size_growth_bytes"] is None


class TestThePeakWriteRate:
    def _three_intervals(self):
        # 100 writes over 600s, then 6000 over 600s, then 100 over 600s.
        return merge_snapshots(
            [
                snap("2026-08-25 10:00:00+00:00", [sized("public.o", ins=0)], []),
                snap("2026-08-25 10:10:00+00:00", [sized("public.o", ins=100)], []),
                snap("2026-08-25 10:20:00+00:00", [sized("public.o", ins=6100)], []),
                snap("2026-08-25 10:30:00+00:00", [sized("public.o", ins=6200)], []),
            ]
        )

    def test_the_peak_is_the_busiest_interval_not_the_mean(self):
        """The window average hides a burst, which is what sizes a shard."""
        entry = render_table_activity(self._three_intervals(), "id")["tables"][0]
        assert entry["peak_writes_per_second"] == 10.0
        assert entry["rows_inserted"] / 1800 < 4.0

    def test_the_peak_names_the_interval_it_happened_in(self):
        entry = render_table_activity(self._three_intervals(), "id")["tables"][0]
        assert entry["peak_interval_start"] == "2026-08-25 10:10:00+00:00"

    def test_a_counter_reset_does_not_produce_a_negative_peak(self):
        """A reset makes the current value the whole delta, never a negative."""
        merged = merge_snapshots(
            [
                snap("2026-08-25 10:00:00+00:00", [sized("public.o", ins=5000)], []),
                snap("2026-08-25 10:10:00+00:00", [sized("public.o", ins=60)], []),
            ]
        )
        entry = render_table_activity(merged, "id")["tables"][0]
        assert entry["peak_writes_per_second"] == 0.1

    def test_one_snapshot_reports_no_peak(self):
        merged = merge_snapshots(
            [snap("2026-08-25 10:00:00+00:00", [sized("public.o", ins=1)], [])],
            allow_partial=True,
        )
        entry = render_table_activity(merged, "id")["tables"][0]
        assert entry["peak_writes_per_second"] is None
        assert entry["peak_interval_start"] is None


class TestTheBundleSaysWhenSizesAreMissing:
    def test_a_capture_with_no_sizes_is_named_in_the_caveats(self):
        """Silence would let a reader take a null for an empty table."""
        from planetscale_discovery.workload.bundle import _caveats

        merged = window([table("public.orders")], [table("public.orders")])
        caveats = " ".join(_caveats(merged, [], {}, {}))
        assert "No table size was reported" in caveats

    def test_a_capture_with_sizes_is_not(self):
        from planetscale_discovery.workload.bundle import _caveats

        merged = window(
            [sized("public.orders", total=10)], [sized("public.orders", total=20)]
        )
        caveats = " ".join(_caveats(merged, [], {}, {}))
        assert "No table size was reported" not in caveats


class TestTheSnapshotRecordsItsOwnShape:
    def test_the_snapshot_version_moved_with_the_new_gauges(self):
        """An older snapshot lacks sizes, so a mixed session must be readable."""
        collector = WorkloadCollector(MagicMock())
        collector._rows = MagicMock(return_value=[])
        assert collector.collect()["schema_version"] == 2
