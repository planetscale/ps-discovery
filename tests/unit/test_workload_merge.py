"""Tests for differencing snapshots into a window."""

import pytest

from planetscale_discovery.workload.merge import (
    interval_seconds,
    merge_snapshots,
    statement_id,
)

SERVER = {"database": "app", "database_oid": 1, "version_num": 170000}
T0 = "2026-08-25 10:00:00+00:00"
T1 = "2026-08-25 10:10:00+00:00"


def stmt(query, calls, total=None, **counters):
    base = {"calls": calls, "total_exec_time": total if total is not None else calls}
    base.update(counters)
    return {"queryid": "1", "query": query, "query_kind": "SELECT", "counters": base}


def snap(statements=(), tables=(), indexes=(), at=T0, status="ok", server=None):
    return {
        "status": status,
        "captured_at_server": at,
        "server": server or SERVER,
        "statements": list(statements),
        "tables": list(tables),
        "indexes": list(indexes),
    }


def table(name, idx_scan=0, rows=100, **gauges):
    return {
        "table": name,
        "counters": {"idx_scan": idx_scan, "seq_scan": 0, "n_tup_ins": 0},
        "gauges": {"n_live_tup": rows, **gauges},
        "estimated_rows": rows,
        "never_analyzed": False,
    }


class TestWindow:
    def test_counters_are_differenced(self):
        merged = merge_snapshots(
            [snap([stmt("SELECT 1", 100)], at=T0), snap([stmt("SELECT 1", 250)], at=T1)]
        )
        assert merged["basis"] == "windowed"
        assert merged["covered_seconds"] == 600
        entry = merged["statements"][statement_id("SELECT 1")]
        assert entry["counters"]["calls"] == 150

    def test_a_fallen_counter_is_a_reset_not_a_negative(self):
        merged = merge_snapshots(
            [snap([stmt("SELECT 1", 900)], at=T0), snap([stmt("SELECT 1", 40)], at=T1)]
        )
        assert merged["statements"][statement_id("SELECT 1")]["counters"]["calls"] == 40
        assert merged["resets_observed"] == 1

    def test_a_reset_is_counted_once_per_entity(self):
        """One reset moves every counter, so per-counter counting overstates."""
        merged = merge_snapshots(
            [
                snap([stmt("SELECT 1", 900, total=900, rows=900)], at=T0),
                snap([stmt("SELECT 1", 5, total=5, rows=5)], at=T1),
            ]
        )
        assert merged["resets_observed"] == 1

    def test_mean_is_recomputed_not_averaged(self):
        merged = merge_snapshots(
            [
                snap([stmt("SELECT 1", 0, total=0)], at=T0),
                snap([stmt("SELECT 1", 4, total=10)], at=T1),
            ]
        )
        entry = merged["statements"][statement_id("SELECT 1")]
        assert entry["mean_exec_time"] == pytest.approx(2.5)

    def test_rates_use_covered_seconds(self):
        merged = merge_snapshots(
            [snap([stmt("SELECT 1", 0)], at=T0), snap([stmt("SELECT 1", 600)], at=T1)]
        )
        entry = merged["statements"][statement_id("SELECT 1")]
        assert entry["calls_per_second"] == pytest.approx(1.0)

    def test_gauges_are_carried_not_differenced(self):
        merged = merge_snapshots(
            [
                snap(tables=[table("public.t", rows=100)], at=T0),
                snap(tables=[table("public.t", rows=90)], at=T1),
            ]
        )
        assert merged["tables"]["public.t"]["gauges"]["n_live_tup"] == 90

    def test_lifetime_totals_are_kept_alongside_the_window(self):
        """A quiet window is all zeros; the lifetime figures are not."""
        merged = merge_snapshots(
            [
                snap(tables=[table("public.t", idx_scan=9_000_000)], at=T0),
                snap(tables=[table("public.t", idx_scan=9_000_000)], at=T1),
            ]
        )
        assert merged["tables"]["public.t"]["counters"]["idx_scan"] == 0
        lifetime = merged["lifetime"]["tables"]["public.t"]
        assert lifetime["counters"]["idx_scan"] == 9_000_000

    def test_indexes_are_differenced_and_keep_their_definition(self):
        index = {
            "table": "public.t",
            "index": "i",
            "counters": {"idx_scan": 10},
            "leading_column": "a",
            "definition": "CREATE INDEX i ON t (a)",
        }
        later = {**index, "counters": {"idx_scan": 30}}
        merged = merge_snapshots(
            [snap(indexes=[index], at=T0), snap(indexes=[later], at=T1)]
        )
        entry = merged["indexes"]["public.t::i"]
        assert entry["counters"]["idx_scan"] == 20
        assert entry["leading_column"] == "a"

    def test_totals_are_summed(self):
        merged = merge_snapshots(
            [
                snap([stmt("SELECT 1", 0), stmt("SELECT 2", 0)], at=T0),
                snap([stmt("SELECT 1", 10), stmt("SELECT 2", 5)], at=T1),
            ]
        )
        assert merged["totals"]["calls"] == 15
        assert merged["totals"]["distinct_statements"] == 2


class TestRefusals:
    def test_one_snapshot_is_refused_without_allow_partial(self):
        result = merge_snapshots([snap([stmt("SELECT 1", 5)])])
        assert result["usable"] is False
        assert "only one usable snapshot" in result["reason"]

    def test_one_snapshot_with_allow_partial_is_cumulative(self):
        result = merge_snapshots([snap([stmt("SELECT 1", 5)])], allow_partial=True)
        assert result["basis"] == "cumulative"
        assert result["covered_seconds"] is None

    def test_snapshots_from_two_servers_are_refused(self):
        """Counters from two servers are not comparable."""
        other = {**SERVER, "database_oid": 2}
        result = merge_snapshots(
            [
                snap([stmt("SELECT 1", 1)], at=T0),
                snap([stmt("SELECT 1", 2)], at=T1, server=other),
            ]
        )
        assert result["usable"] is False
        assert "more than one server" in result["reason"]

    def test_a_major_version_change_is_refused(self):
        other = {**SERVER, "version_num": 180000}
        result = merge_snapshots([snap(at=T0), snap(at=T1, server=other)])
        assert result["usable"] is False

    def test_a_failed_snapshot_is_excluded(self):
        result = merge_snapshots(
            [snap([stmt("SELECT 1", 1)], at=T0), snap(at=T1, status="failed")]
        )
        assert result["usable"] is False

    def test_no_usable_snapshot(self):
        assert merge_snapshots([])["usable"] is False


class TestIntervalSeconds:
    def test_ordered_pair(self):
        assert (
            interval_seconds({"captured_at_server": T0}, {"captured_at_server": T1})
            == 600
        )

    @pytest.mark.parametrize(
        "a,b",
        [
            (T1, T0),  # reversed
            (T0, T0),  # zero length
            (None, T1),
            (T0, None),
            ("not a date", T1),
        ],
    )
    def test_unusable_pairs_return_none(self, a, b):
        assert (
            interval_seconds({"captured_at_server": a}, {"captured_at_server": b})
            is None
        )

    def test_an_unmeasurable_interval_is_skipped_not_invented(self):
        merged = merge_snapshots(
            [
                snap([stmt("SELECT 1", 1)], at=T0),
                snap([stmt("SELECT 1", 9)], at=T1),
                snap([stmt("SELECT 1", 20)], at="garbage"),
            ]
        )
        assert merged["usable"]
        assert merged["covered_seconds"] == 600
        assert len(merged["coverage"]["intervals_skipped"]) == 1

    def test_every_interval_unmeasurable_means_no_window(self):
        result = merge_snapshots([snap(at="garbage"), snap(at="also garbage")])
        assert result["usable"] is False


class TestStatementId:
    def test_whitespace_and_semicolons_do_not_change_identity(self):
        assert statement_id("SELECT  1 ;") == statement_id("SELECT 1")

    def test_different_text_differs(self):
        assert statement_id("SELECT 1") != statement_id("SELECT 2")

    def test_none_is_stable(self):
        assert statement_id(None) == statement_id("")
