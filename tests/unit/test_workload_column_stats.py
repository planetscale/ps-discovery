"""Column distribution, which is what says whether a shard key spreads.

A row count says how big a table is. It cannot say that one value of the
candidate key holds 40% of the rows, which puts 40% of that table on one shard
however many shards there are. `pg_stats.most_common_freqs` says exactly that.

Frequencies only. `most_common_vals` and `histogram_bounds` hold sampled rows
from the customer's tables and never appear here, which is the line these tests
hold: this file may carry how often values occur, never which values they are.
"""

import json

from planetscale_discovery.workload.bundle import (
    COLUMN_STATS_SCHEMA_VERSION,
    render_column_stats,
    write_bundle,
)
from planetscale_discovery.workload.collect import MCF_KEPT
from planetscale_discovery.workload.merge import merge_snapshots

from .test_workload_bundle import SCHEMA, merged, stmt

SERVER = {"database": "app", "database_oid": 1, "version_num": 170000}


def column(name, top=0.02, n_distinct=50.0, null_frac=0.0, entries=5):
    """One pg_stats row as the collector records it."""
    return {
        "table": "public.orders",
        "column": name,
        "n_distinct": n_distinct,
        "null_frac": null_frac,
        "correlation": 0.1,
        "top_frequencies": [top] + [0.001] * (entries - 1),
        "mcv_coverage": top + 0.001 * (entries - 1),
    }


def snap(at, columns):
    return {
        "status": "ok",
        "captured_at_server": at,
        "server": SERVER,
        "statements": [],
        "tables": [],
        "indexes": [],
        "warnings": [],
        "columns": columns,
    }


def window(first_columns, last_columns):
    return merge_snapshots(
        [
            snap("2026-08-25 10:00:00+00:00", first_columns),
            snap("2026-08-25 11:00:00+00:00", last_columns),
        ]
    )


class TestItCarriesFrequenciesAndNeverValues:
    def test_the_top_share_is_reported(self):
        stats = render_column_stats(
            window([column("tenant_id")], [column("tenant_id")]), "id"
        )
        assert stats["columns"][0]["top_share"] == 0.02

    def test_a_lopsided_column_is_visible(self):
        """One value holding 80% of the rows disqualifies a shard key."""
        stats = render_column_stats(
            window([column("status", top=0.8)], [column("status", top=0.8)]), "id"
        )
        assert stats["columns"][0]["top_share"] == 0.8

    def test_no_value_from_the_customer_s_data_is_present(self):
        stats = render_column_stats(
            window([column("tenant_id")], [column("tenant_id")]), "id"
        )
        text = json.dumps(stats)
        for forbidden in ("most_common_vals", "histogram_bounds"):
            assert forbidden not in text

    def test_the_frequency_list_is_capped(self):
        """The tail is dropped, not the signal: skew lives in the head."""
        long_list = column("c", entries=100)
        assert len(long_list["top_frequencies"]) == 100
        # The collector truncates; the cap is what the bundle declares.
        stats = render_column_stats(window([long_list], [long_list]), "id")
        assert stats["most_common_frequencies_kept"] == MCF_KEPT

    def test_a_column_with_no_common_values_reports_none(self):
        """A unique column has no MCV list, which is itself a good sign."""
        unique = dict(column("id"), top_frequencies=[], mcv_coverage=None)
        stats = render_column_stats(window([unique], [unique]), "id")
        assert stats["columns"][0]["top_share"] is None


class TestItTracksChangeAcrossTheCapture:
    def test_both_readings_are_kept(self):
        stats = render_column_stats(
            window([column("tenant_id", top=0.02)], [column("tenant_id", top=0.30)]),
            "id",
        )
        entry = stats["columns"][0]
        assert entry["first_seen"]["top_share"] == 0.02
        assert entry["top_share"] == 0.30

    def test_a_column_whose_skew_moved_is_counted(self):
        stats = render_column_stats(
            window([column("tenant_id", top=0.02)], [column("tenant_id", top=0.30)]),
            "id",
        )
        assert stats["columns_whose_skew_moved"] == 1

    def test_a_steady_column_is_not_counted(self):
        stats = render_column_stats(
            window([column("tenant_id", top=0.02)], [column("tenant_id", top=0.021)]),
            "id",
        )
        assert stats["columns_whose_skew_moved"] == 0

    def test_a_column_that_appeared_mid_capture_has_no_earlier_reading(self):
        stats = render_column_stats(window([], [column("added_later")]), "id")
        assert stats["columns"][0]["first_seen"]["top_share"] is None
        assert stats["columns_whose_skew_moved"] == 0


class TestItIsWrittenToTheBundle:
    def _write(self, tmp_path):
        payload = merged([stmt("a")])
        payload["server"] = SERVER
        payload["columns"] = [
            dict(column("tenant_id"), first_seen={"top_share": 0.02}, top_share=0.02)
        ]
        write_bundle(tmp_path, payload, SCHEMA, "t")
        return json.loads((tmp_path / "column_stats.json").read_text())

    def test_the_file_declares_its_version_and_source(self, tmp_path):
        payload = self._write(tmp_path)
        assert payload["schema_version"] == COLUMN_STATS_SCHEMA_VERSION
        assert payload["source"] == "pg_stats"

    def test_it_carries_the_capture_id(self, tmp_path):
        assert len(self._write(tmp_path)["capture_id"]) == 8

    def test_it_counts_what_it_measured(self, tmp_path):
        assert self._write(tmp_path)["columns_measured"] == 1


class TestAnEmptyReadingIsNamed:
    """An empty column_stats.json must say why it is empty.

    The capture role from the guide holds pg_monitor only, and pg_stats shows a
    column only to a role that may SELECT it. A database that ANALYZE never
    reached looks the same in the file, so the caveat names the cause the
    capture can see.
    """

    def _caveats(self, tmp_path, **extra):
        payload = merged([stmt("a")], **extra)
        payload["server"] = SERVER
        return write_bundle(tmp_path, payload, SCHEMA, "t")["caveats"]

    def _tables(self, last_autoanalyze=None, estimated_rows=1000.0):
        return {
            "public.orders": {
                "table": "public.orders",
                "last_analyze": None,
                "last_autoanalyze": last_autoanalyze,
                "estimated_rows": estimated_rows,
            }
        }

    def test_analyzed_tables_with_no_visible_statistics_name_the_grant(self, tmp_path):
        caveats = self._caveats(
            tmp_path,
            tables=self._tables("2026-10-01 00:00:00+00"),
            columns=[],
            pg_stats_read=True,
        )
        caveat = next(c for c in caveats if "column_stats.json is empty" in c)
        assert "may not SELECT" in caveat
        assert "Run ANALYZE" not in caveat

    def test_tables_that_were_never_analyzed_ask_for_analyze(self, tmp_path):
        caveats = self._caveats(
            tmp_path, tables=self._tables(None), columns=[], pg_stats_read=True
        )
        caveat = next(c for c in caveats if "column_stats.json is empty" in c)
        assert "1 table(s) record no ANALYZE" in caveat
        assert "Run ANALYZE" in caveat

    def test_a_mix_of_empty_and_unanalyzed_tables_names_both(self, tmp_path):
        tables = {
            **self._tables("2026-10-01 00:00:00+00", estimated_rows=0.0),
            "public.items": {
                "table": "public.items",
                "last_analyze": None,
                "last_autoanalyze": None,
                "estimated_rows": None,
            },
        }
        caveats = self._caveats(tmp_path, tables=tables, columns=[], pg_stats_read=True)
        caveat = next(c for c in caveats if "column_stats.json is empty" in c)
        assert "1 table(s) record no ANALYZE" in caveat
        assert "1 table(s) record an ANALYZE but hold no rows" in caveat

    def test_an_analyzed_empty_table_is_not_blamed_on_the_grant(self, tmp_path):
        caveats = self._caveats(
            tmp_path,
            tables=self._tables("2026-10-01 00:00:00+00", estimated_rows=0.0),
            columns=[],
            pg_stats_read=True,
        )
        caveat = next(c for c in caveats if "column_stats.json is empty" in c)
        assert "hold no rows" in caveat
        assert "SELECT" not in caveat

    def test_a_failed_read_is_not_reported_as_an_empty_one(self, tmp_path):
        caveats = self._caveats(
            tmp_path, tables=self._tables(None), columns=[], pg_stats_read=False
        )
        caveat = next(c for c in caveats if "column_stats.json is empty" in c)
        assert "W214" in caveat
        assert "ANALYZE" not in caveat

    def test_a_capture_with_column_statistics_has_no_such_caveat(self, tmp_path):
        caveats = self._caveats(
            tmp_path,
            tables=self._tables(None),
            columns=[dict(column("tenant_id"), first_seen={}, top_share=0.02)],
            pg_stats_read=True,
        )
        assert not any("column_stats.json is empty" in c for c in caveats)

    def test_a_failed_last_read_keeps_the_earlier_reading(self):
        failed = snap("2026-08-25 11:00:00+00:00", [])
        failed["warnings"] = [{"code": "column_stats_read_failed", "detail": "x"}]
        result = merge_snapshots(
            [snap("2026-08-25 10:00:00+00:00", [column("tenant_id")]), failed]
        )
        assert [c["column"] for c in result["columns"]] == ["tenant_id"]
        assert result["pg_stats_read"] is True

    def test_a_failed_read_after_a_good_one_is_named(self, tmp_path):
        """A lost tick showed only on the collect console."""
        caveats = self._caveats(
            tmp_path,
            tables=self._tables("2026-10-01 00:00:00+00"),
            columns=[dict(column("tenant_id"), first_seen={}, top_share=0.02)],
            pg_stats_read=True,
            pg_stats_failed_reads=2,
            pg_stats_read_at="2026-08-25 11:00:00+00:00",
        )
        caveat = next(c for c in caveats if "could not read pg_stats" in c)
        assert caveat.startswith("2 snapshot(s)")
        assert "taken at 2026-08-25 11:00:00+00:00" in caveat

    def test_the_merge_counts_the_failed_reads(self):
        failed = snap("2026-08-25 12:00:00+00:00", [])
        failed["warnings"] = [{"code": "column_stats_read_failed", "detail": "x"}]
        result = merge_snapshots(
            [
                snap("2026-08-25 10:00:00+00:00", [column("tenant_id")]),
                snap("2026-08-25 11:00:00+00:00", [column("tenant_id")]),
                failed,
            ]
        )
        assert result["pg_stats_failed_reads"] == 1
        assert result["pg_stats_read_at"] == "2026-08-25 11:00:00+00:00"

    def test_no_reading_at_all_is_recorded_as_unread(self):
        failed = [
            dict(snap(at, []), warnings=[{"code": "column_stats_read_failed"}])
            for at in ("2026-08-25 10:00:00+00:00", "2026-08-25 11:00:00+00:00")
        ]
        result = merge_snapshots(failed)
        assert result["columns"] == []
        assert result["pg_stats_read"] is False
