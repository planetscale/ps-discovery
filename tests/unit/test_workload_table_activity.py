"""What each table absorbed, and which indexes served it.

Two things the query log cannot say. Statements are never parsed, so the log
attributes no write to a table, and write concentration is what decides whether
a shard becomes a hotspot. And an index's scan count is evidence of the real
access path, arrived at from use rather than inferred from statement text.

The counters are for the measured window, so they must line up with the window
`manifest.json` describes. A reader that cannot trust the two to agree cannot
put a write rate on the traffic it sees.
"""

import json

from planetscale_discovery.workload.bundle import (
    TABLE_ACTIVITY_SCHEMA_VERSION,
    render_manifest,
    render_table_activity,
    write_bundle,
)
from planetscale_discovery.workload.merge import merge_snapshots

from .test_workload_bundle import SCHEMA, merged, stmt

SERVER = {"database": "app", "database_oid": 1, "version_num": 170000}


def table(name, ins=0, upd=0, dele=0, idx=0, seq=0):
    return {
        "table": name,
        "counters": {
            "n_tup_ins": ins,
            "n_tup_upd": upd,
            "n_tup_del": dele,
            "idx_scan": idx,
            "seq_scan": seq,
            "seq_tup_read": seq * 100,
        },
        "gauges": {},
        "estimated_rows": 1000.0,
        "never_analyzed": False,
    }


def index(table_name, name, scans=0, leading="tenant_id", primary=True):
    return {
        "table": table_name,
        "index": name,
        "leading_column": leading,
        "is_unique": True,
        "is_primary": primary,
        "definition": f"CREATE INDEX {name} ON {table_name} (tenant_id)",
        "counters": {
            "idx_scan": scans,
            "idx_tup_read": scans * 2,
            "idx_tup_fetch": scans,
        },
    }


def snap(at, tables, indexes):
    return {
        "status": "ok",
        "captured_at_server": at,
        "server": SERVER,
        "statements": [],
        "tables": tables,
        "indexes": indexes,
        "warnings": [],
    }


def window(first_t, last_t, first_i=(), last_i=()):
    return merge_snapshots(
        [
            snap("2026-08-25 10:00:00+00:00", first_t, list(first_i)),
            snap("2026-08-25 10:10:00+00:00", last_t, list(last_i)),
        ]
    )


class TestWriteVolumePerTable:
    def test_writes_are_the_change_across_the_window(self):
        """Absolute counters, so the window is the difference of two readings."""
        merged_result = window(
            [table("public.orders", ins=1000, upd=500)],
            [table("public.orders", ins=1300, upd=700)],
        )
        entry = render_table_activity(merged_result, "id")["tables"][0]
        assert entry["rows_inserted"] == 300
        assert entry["rows_updated"] == 200

    def test_a_table_nothing_wrote_to_reports_zero(self):
        merged_result = window(
            [table("public.lookup", ins=42)], [table("public.lookup", ins=42)]
        )
        entry = render_table_activity(merged_result, "id")["tables"][0]
        assert entry["rows_inserted"] == 0

    def test_sequential_scans_are_reported(self):
        """A table read sequentially fans out to every shard."""
        merged_result = window(
            [table("public.orders", seq=10)], [table("public.orders", seq=25)]
        )
        entry = render_table_activity(merged_result, "id")["tables"][0]
        assert entry["sequential_scans"] == 15
        assert entry["rows_read_sequentially"] == 1500


class TestIndexUsage:
    def _indexes(self):
        return render_table_activity(
            window(
                [table("public.orders")],
                [table("public.orders")],
                [index("public.orders", "orders_pkey", scans=100)],
                [index("public.orders", "orders_pkey", scans=1600)],
            ),
            "id",
        )["indexes"]

    def test_scans_are_the_change_across_the_window(self):
        assert self._indexes()[0]["scans"] == 1500

    def test_the_leading_column_travels_with_the_count(self):
        """The column a lookup goes through is what makes this a shard-key signal."""
        assert self._indexes()[0]["leading_column"] == "tenant_id"

    def test_an_index_definition_is_not_repeated(self):
        """schema.sql already holds it, and it was most of the old file's size."""
        assert "definition" not in self._indexes()[0]

    def test_an_index_not_scanned_in_the_window_is_counted(self):
        """Named for the window: a short capture is no evidence an index is dead."""
        activity = render_table_activity(
            window(
                [table("public.orders")],
                [table("public.orders")],
                [index("public.orders", "idle_idx", scans=7)],
                [index("public.orders", "idle_idx", scans=7)],
            ),
            "id",
        )
        assert activity["indexes_not_scanned_in_window"] == 1


class TestItAlignsWithTheManifest:
    def _both(self):
        merged_result = window(
            [table("public.orders", ins=1)], [table("public.orders", ins=2)]
        )
        return (
            render_table_activity(merged_result, "id"),
            render_manifest(merged_result, "1.0"),
        )

    def test_the_window_matches_the_manifest(self):
        activity, manifest = self._both()
        assert activity["window"]["start"] == manifest["window"]["start"]
        assert activity["window"]["end"] == manifest["window"]["end"]

    def test_the_interval_count_matches(self):
        activity, manifest = self._both()
        assert activity["window"]["intervals"] == len(manifest["intervals"])

    def test_it_carries_the_capture_id(self):
        activity, _ = self._both()
        assert activity["capture_id"] == "id"

    def test_it_names_its_source_and_version(self):
        activity, _ = self._both()
        assert activity["schema_version"] == TABLE_ACTIVITY_SCHEMA_VERSION
        assert "pg_stat_user_tables" in activity["source"]


class TestItIsWrittenToTheBundle:
    def test_the_file_is_valid_json(self, tmp_path):
        payload = merged([stmt("a")])
        payload["tables"] = {"public.orders": table("public.orders", ins=5)}
        payload["indexes"] = {
            "public.orders::orders_pkey": index("public.orders", "orders_pkey", scans=9)
        }
        write_bundle(tmp_path, payload, SCHEMA, "t")
        written = json.loads((tmp_path / "table_activity.json").read_text())
        assert written["tables_measured"] == 1
        assert written["indexes_measured"] == 1
