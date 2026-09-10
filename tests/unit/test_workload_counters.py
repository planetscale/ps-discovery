"""Counter fidelity: how a count is written, and which values survive merging.

Two defects sat here. ``:g`` formatting turned every hot statement's count into
``calls=3e+06``, and the lifetime minimum and maximum execution times were
selected from the server and then dropped before anything could read them.
"""

from unittest.mock import MagicMock

from planetscale_discovery.workload.bundle import render_workload_sql
from planetscale_discovery.workload.collect import WorkloadCollector
from planetscale_discovery.workload.merge import merge_snapshots

SERVER = {"database": "app", "database_oid": 1, "version_num": 170000}
T0 = "2026-08-25 10:00:00+00:00"
T1 = "2026-08-25 11:00:00+00:00"


def stmt(sid, calls=10, total=5.0, rows=None):
    return {
        "id": sid,
        "query": "SELECT $1 FROM orders WHERE id = $2",
        "query_kind": "SELECT",
        "queryids": ["1"],
        "counters": {
            "calls": calls,
            "total_exec_time": total,
            "rows": calls if rows is None else rows,
        },
    }


class TestMetricHeaderFormatting:
    """A multi-day capture puts every hot statement above 1e6."""

    def test_a_large_count_is_written_in_full(self):
        sql = render_workload_sql([stmt("a", calls=3_000_000)], 60.0)
        assert "calls=3000000" in sql
        assert "e+" not in sql

    def test_no_count_is_rounded_to_six_digits(self):
        sql = render_workload_sql([stmt("a", calls=1_234_567)], 60.0)
        assert "calls=1234567" in sql

    def test_a_row_count_is_written_in_full(self):
        sql = render_workload_sql([stmt("a", calls=10, rows=98_765_432)], 60.0)
        assert "rows=98765432" in sql

    def test_a_fractional_count_becomes_a_whole_number(self):
        """A differenced counter is a float; a call count is not fractional."""
        sql = render_workload_sql([stmt("a", calls=4200.4)], 60.0)
        assert "calls=4200 " in sql

    def test_the_duration_keeps_its_decimals(self):
        sql = render_workload_sql([stmt("a", total=88213.4567)], 60.0)
        assert "total_exec_time_ms=88213.457" in sql

    def test_a_large_duration_is_not_scientific(self):
        sql = render_workload_sql([stmt("a", total=2.5e9)], 60.0)
        assert "total_exec_time_ms=2500000000.000" in sql

    def test_a_missing_counter_is_zero(self):
        record = stmt("a")
        record["counters"] = {}
        sql = render_workload_sql([record], 60.0)
        assert "calls=0 total_exec_time_ms=0 rows=0" in sql


class TestLifetimeExecTimesAreCollected:
    def _row(self, **overrides):
        row = {
            "query": "SELECT * FROM orders WHERE id = 1",
            "calls": 3,
            "total_exec_time": 9.0,
            "min_exec_time": 0.25,
            "max_exec_time": 812.5,
        }
        row.update(overrides)
        return WorkloadCollector(MagicMock())._statement_row(row)

    def test_the_values_are_kept(self):
        row = self._row()
        assert row["min_exec_time"] == 0.25
        assert row["max_exec_time"] == 812.5

    def test_they_stay_out_of_the_counters(self):
        """Everything in `counters` is differenced, and an extreme must not be."""
        counters = self._row()["counters"]
        assert "min_exec_time" not in counters
        assert "max_exec_time" not in counters

    def test_a_server_that_cannot_say_reports_none(self):
        row = self._row(min_exec_time=None, max_exec_time=None)
        assert row["max_exec_time"] is None


class TestLifetimeExecTimesSurviveMerging:
    def _snapshot(self, at, calls, min_ms, max_ms):
        return {
            "status": "ok",
            "captured_at_server": at,
            "server": SERVER,
            "statements": [
                {
                    "queryid": "1",
                    "query": "SELECT $1 FROM orders",
                    "query_kind": "SELECT",
                    "min_exec_time": min_ms,
                    "max_exec_time": max_ms,
                    "counters": {"calls": calls, "total_exec_time": calls},
                }
            ],
            "tables": [],
            "indexes": [],
        }

    def test_the_window_carries_the_extremes(self):
        merged = merge_snapshots(
            [self._snapshot(T0, 100, 0.4, 90.0), self._snapshot(T1, 400, 0.2, 812.5)]
        )
        record = list(merged["statements"].values())[0]
        assert record["max_exec_time"] == 812.5
        assert record["min_exec_time"] == 0.2

    def test_an_extreme_is_never_differenced(self):
        """Subtracting a maximum would report 722.5 ms, which nothing observed."""
        merged = merge_snapshots(
            [self._snapshot(T0, 100, 0.4, 90.0), self._snapshot(T1, 400, 0.4, 812.5)]
        )
        record = list(merged["statements"].values())[0]
        assert record["max_exec_time"] == 812.5
        assert record["counters"]["calls"] == 300

    def test_a_single_snapshot_carries_them_too(self):
        merged = merge_snapshots(
            [self._snapshot(T0, 100, 0.4, 90.0)], allow_partial=True
        )
        record = list(merged["statements"].values())[0]
        assert merged["basis"] == "cumulative"
        assert record["max_exec_time"] == 90.0

    def test_a_snapshot_from_an_older_collector_still_merges(self):
        """Sessions span days, so a session can outlive an upgrade."""
        old = self._snapshot(T0, 100, None, None)
        old["statements"][0]["counters"]["max_exec_time"] = 90.0
        merged = merge_snapshots([old, self._snapshot(T1, 400, 0.2, 812.5)])
        record = list(merged["statements"].values())[0]
        assert record["max_exec_time"] == 812.5
