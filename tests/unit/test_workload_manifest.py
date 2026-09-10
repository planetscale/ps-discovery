"""manifest.json records how a capture was taken.

Its shape is fixed by the tool that reads it, so these tests assert the contract
rather than this repository's preferences: the exact key set, the types, and the
per-interval timing that a reader cannot reconstruct from the other files.

It deliberately holds no findings. Counts of what the capture contains are
readable from the files the planner reads, and duplicating them here would give
a consumer two sources that can disagree.
"""

import json

from planetscale_discovery.workload.bundle import (
    MANIFEST_SCHEMA_VERSION,
    render_manifest,
    write_bundle,
)
from planetscale_discovery.workload.merge import merge_snapshots

from .test_workload_bundle import SCHEMA, merged, stmt

SERVER = {"database": "app", "database_oid": 1, "version_num": 170000}
TOP_LEVEL_KEYS = {
    "schema_version",
    "capture_id",
    "generated_at",
    "discovery_version",
    "window",
    "intervals",
    "intervals_skipped",
    "resets_observed",
    "capped",
    "pgss",
}


def row(calls, queryid="q1"):
    return {
        "userid": 10,
        "dbid": 1,
        "queryid": queryid,
        "toplevel": True,
        "query": "SELECT $1 FROM orders",
        "query_kind": "SELECT",
        "counters": {"calls": calls, "total_exec_time": calls * 2.0, "rows": calls},
    }


def snap(at, calls, dealloc=0.0, pgss_max=5000, warnings=()):
    return {
        "status": "ok",
        "captured_at_server": at,
        "server": SERVER,
        "statements": [row(calls)],
        "tables": [],
        "indexes": [],
        "warnings": list(warnings),
        "pgss": {"max": pgss_max, "dealloc": dealloc},
    }


def three_hours():
    return merge_snapshots(
        [
            snap("2026-08-25 10:00:00+00:00", 1000),
            snap("2026-08-25 11:00:00+00:00", 1500),
            snap("2026-08-25 12:00:00+00:00", 1800),
        ]
    )


class TestTheContract:
    def test_the_key_set_is_exactly_what_the_consumer_reads(self):
        manifest = render_manifest(three_hours(), "1.3.1")
        assert set(manifest) == TOP_LEVEL_KEYS

    def test_the_schema_version_is_declared(self):
        assert render_manifest(three_hours(), "1.3.1")["schema_version"] == (
            MANIFEST_SCHEMA_VERSION
        )

    def test_it_carries_no_findings(self):
        """Caveats, cardinality and prose belong to the files, not here."""
        manifest = render_manifest(three_hours(), "1.3.1")
        for absent in ("caveats", "not_produced", "cardinality", "statements"):
            assert absent not in manifest

    def test_the_collector_version_is_recorded(self):
        assert render_manifest(three_hours(), "1.3.1")["discovery_version"] == "1.3.1"


class TestTheWindow:
    def test_it_spans_the_first_and_last_snapshot(self):
        window = render_manifest(three_hours(), "t")["window"]
        assert window["start"] == "2026-08-25 10:00:00+00:00"
        assert window["end"] == "2026-08-25 12:00:00+00:00"

    def test_the_zone_is_named(self):
        assert render_manifest(three_hours(), "t")["window"]["tz"] == "UTC"


class TestTheIntervals:
    def test_one_entry_per_measured_interval(self):
        assert len(render_manifest(three_hours(), "t")["intervals"]) == 2

    def test_each_carries_its_own_call_count(self):
        """500 then 300, which no other file in the bundle records."""
        intervals = render_manifest(three_hours(), "t")["intervals"]
        assert [i["calls"] for i in intervals] == [500, 300]

    def test_each_carries_its_own_bounds(self):
        first = render_manifest(three_hours(), "t")["intervals"][0]
        assert first["start"] == "2026-08-25 10:00:00+00:00"
        assert first["end"] == "2026-08-25 11:00:00+00:00"

    def test_a_quiet_interval_is_reported_as_zero(self):
        """A gap in the traffic, which the total alone would hide."""
        merged_result = merge_snapshots(
            [
                snap("2026-08-25 10:00:00+00:00", 1000),
                snap("2026-08-25 11:00:00+00:00", 1000),
                snap("2026-08-25 12:00:00+00:00", 1400),
            ]
        )
        assert [
            i["calls"] for i in render_manifest(merged_result, "t")["intervals"]
        ] == [
            0,
            400,
        ]

    def test_an_interval_the_clock_disordered_is_listed_separately(self):
        merged_result = merge_snapshots(
            [
                snap("2026-08-25 10:00:00+00:00", 1000),
                snap("2026-08-25 09:00:00+00:00", 1500),
                snap("2026-08-25 11:00:00+00:00", 1800),
            ]
        )
        manifest = render_manifest(merged_result, "t")
        assert len(manifest["intervals_skipped"]) == 1
        assert "start" in manifest["intervals_skipped"][0]
        assert manifest["intervals_skipped"][0]["reason"]


class TestPgssState:
    def test_the_capacity_is_reported(self):
        assert render_manifest(three_hours(), "t")["pgss"]["max"] == 5000

    def test_eviction_during_the_window_is_reported(self):
        merged_result = merge_snapshots(
            [
                snap("2026-08-25 10:00:00+00:00", 1000, dealloc=0.0),
                snap("2026-08-25 11:00:00+00:00", 1500, dealloc=12.0),
            ]
        )
        assert render_manifest(merged_result, "t")["pgss"]["evicted"] is True

    def test_eviction_before_the_window_is_not_this_capture_s_problem(self):
        """A server that evicted last month lost nothing from this result."""
        merged_result = merge_snapshots(
            [
                snap("2026-08-25 10:00:00+00:00", 1000, dealloc=99.0),
                snap("2026-08-25 11:00:00+00:00", 1500, dealloc=99.0),
            ]
        )
        assert render_manifest(merged_result, "t")["pgss"]["evicted"] is False

    def test_a_server_that_cannot_report_it_says_unknown(self):
        """pg_stat_statements_info arrived in PostgreSQL 14."""
        merged_result = merge_snapshots(
            [
                snap("2026-08-25 10:00:00+00:00", 1000, dealloc=None),
                snap("2026-08-25 11:00:00+00:00", 1500, dealloc=None),
            ]
        )
        assert render_manifest(merged_result, "t")["pgss"]["evicted"] is None


class TestCapped:
    def test_it_is_false_when_no_snapshot_hit_the_cap(self):
        assert render_manifest(three_hours(), "t")["capped"] is False

    def test_it_is_true_when_a_snapshot_hit_the_cap(self):
        merged_result = merge_snapshots(
            [
                snap("2026-08-25 10:00:00+00:00", 1000),
                snap(
                    "2026-08-25 11:00:00+00:00",
                    1500,
                    warnings=[{"code": "row_cap_reached", "detail": "20000 rows"}],
                ),
            ]
        )
        assert render_manifest(merged_result, "t")["capped"] is True


class TestItIsWrittenToTheBundle:
    def test_the_file_is_valid_json_with_the_contract_keys(self, tmp_path):
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "1.3.1")
        payload = json.loads((tmp_path / "manifest.json").read_text())
        assert set(payload) == TOP_LEVEL_KEYS

    def test_it_is_owner_only(self, tmp_path):
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        assert oct((tmp_path / "manifest.json").stat().st_mode)[-3:] == "600"
