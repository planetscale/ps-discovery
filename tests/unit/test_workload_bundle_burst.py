"""Tests for rendering and scoping burst data into a bundle."""

import csv
import io

from planetscale_discovery.workload.bundle_burst import (
    _bursts_in_window,
    _scope_burst,
    _write_bursts,
    render_burst_csv,
    write_bursts,
)

HEADER = [
    "timestamp",
    "session_id",
    "user",
    "schema",
    "query",
    "query_time",
    "query_id",
    "parameters",
    "command_tag",
]


def statement(**extra):
    return {
        "log_time": "2026-08-25 10:00:00.000 UTC",
        "session_id": "sess-1",
        "user_name": "app",
        "database_name": "public",
        "sql": "select 1",
        "duration_ms": 12.5,
        "query_id": 42,
        "parameters": "1,2",
        "session_line_num": 1,
        **extra,
    }


def burst(window_start, window_end, statements=None, status="ok", **extra):
    return {
        "window_start": window_start,
        "window_end": window_end,
        "status": status,
        "statements": statements if statements is not None else [],
        **extra,
    }


class TestRenderBurstCsv:
    def test_header_present_by_default(self):
        text = render_burst_csv([])
        assert text.splitlines()[0].split(",") == HEADER

    def test_header_can_be_omitted(self):
        text = render_burst_csv([statement()], header=False)
        assert text.splitlines()[0].split(",")[0] != "timestamp"

    def test_a_formula_looking_value_gets_the_guard_prefix(self):
        for prefix in ("=", "+", "-", "@"):
            text = render_burst_csv([statement(sql=f"{prefix}cmd|calc")], header=False)
            row = text.splitlines()[0].split(",")
            query_cell = row[4]
            assert query_cell.startswith("'" + prefix)

    def test_fields_land_in_the_right_columns(self):
        text = render_burst_csv([statement(command_tag="SELECT")], header=False)
        row = next(csv.reader(io.StringIO(text)))
        assert row[1] == "sess-1"
        assert row[2] == "app"
        assert row[3] == "public"
        assert row[4] == "select 1"
        assert row[5] == "0.012500"
        assert row[6] == "42"
        assert row[7] == "1,2"
        assert row[8] == "SELECT"

    def test_a_non_string_cell_does_not_kill_finalize(self):
        """The bug: an exported log with an integer user name raised TypeError."""
        text = render_burst_csv(
            [statement(user_name=123, database_name=456, parameters=789)], header=False
        )
        row = next(csv.reader(io.StringIO(text)))
        assert row[2] == "123"
        assert row[3] == "456"
        assert row[7] == "789"

    def test_a_framing_row_carries_its_command_tag(self):
        text = render_burst_csv(
            [statement(sql="BEGIN", command_tag="BEGIN")], header=False
        )
        row = next(csv.reader(io.StringIO(text)))
        assert row[8] == "BEGIN"


class TestBurstsInWindow:
    def merged(
        self, start="2026-08-25 10:00:00+00:00", end="2026-08-25 12:00:00+00:00"
    ):
        return {"coverage": {"window_start": start, "window_end": end}}

    def test_a_burst_inside_the_window_is_included(self):
        b = burst("2026-08-25 10:30:00+00:00", "2026-08-25 10:45:00+00:00")
        included, excluded, unreadable, undated = _bursts_in_window([b], self.merged())
        assert included == [b]
        assert excluded == []
        assert unreadable == []

    def test_a_burst_entirely_before_the_window_is_excluded(self):
        b = burst("2026-08-25 08:00:00+00:00", "2026-08-25 09:00:00+00:00")
        included, excluded, unreadable, undated = _bursts_in_window([b], self.merged())
        assert included == []
        assert excluded == [b]

    def test_a_burst_entirely_after_the_window_is_excluded(self):
        b = burst("2026-08-25 13:00:00+00:00", "2026-08-25 14:00:00+00:00")
        included, excluded, unreadable, undated = _bursts_in_window([b], self.merged())
        assert included == []
        assert excluded == [b]

    def test_a_live_read_after_the_window_is_kept(self):
        """The bug: log_fdw watches the log after its own snapshot, so the
        last window of every capture fell outside and was dropped."""
        b = burst(
            "2026-08-25 12:00:05+00:00", "2026-08-25 12:10:00+00:00", source="log_fdw"
        )
        included, excluded, unreadable, undated = _bursts_in_window([b], self.merged())
        assert included == [b]
        assert excluded == []

    def test_a_live_read_before_the_window_is_still_excluded(self):
        b = burst(
            "2026-08-20 08:00:00+00:00", "2026-08-20 09:00:00+00:00", source="log_fdw"
        )
        included, excluded, unreadable, undated = _bursts_in_window([b], self.merged())
        assert included == []
        assert excluded == [b]

    def test_an_unreadable_burst_is_routed_to_its_own_list(self):
        b = {"status": "unreadable", "path": "burst-unknown.json.gz"}
        included, excluded, unreadable, undated = _bursts_in_window([b], self.merged())
        assert included == []
        assert excluded == []
        assert unreadable == [b]

    def test_an_unparseable_burst_bound_is_undated_not_excluded(self):
        b = burst("2026-08-25 10:30:00 EDT", "2026-08-25 10:45:00 EDT")
        included, excluded, unreadable, undated = _bursts_in_window([b], self.merged())
        assert excluded == []
        assert included == [b]
        assert undated == [b]


class TestScopeBurst:
    def test_statements_not_touching_a_known_table_are_filtered_out(self):
        b = burst(
            "2026-08-25 10:00:00+00:00",
            "2026-08-25 10:15:00+00:00",
            statements=[
                statement(sql="select 1 from orders"),
                statement(sql="select 1"),
            ],
        )
        scoped = _scope_burst(b, {"orders"})
        assert len(scoped["statements"]) == 1
        assert "orders" in scoped["statements"][0]["sql"]

    def test_an_empty_known_set_returns_the_burst_unchanged(self):
        b = burst(
            "2026-08-25 10:00:00+00:00",
            "2026-08-25 10:15:00+00:00",
            statements=[statement()],
        )
        assert _scope_burst(b, set()) is b

    def test_a_kept_transaction_keeps_its_begin_and_commit(self):
        b = burst(
            "2026-08-25 10:00:00+00:00",
            "2026-08-25 10:15:00+00:00",
            statements=[
                statement(sql="BEGIN", command_tag="BEGIN", session_line_num=1),
                statement(
                    sql="update orders set x=1",
                    command_tag="UPDATE",
                    session_line_num=2,
                ),
                statement(sql="COMMIT", command_tag="COMMIT", session_line_num=3),
            ],
        )
        scoped = _scope_burst(b, {"orders"})
        assert [s["command_tag"] for s in scoped["statements"]] == [
            "BEGIN",
            "UPDATE",
            "COMMIT",
        ]

    def test_a_transaction_touching_nothing_known_is_dropped_whole(self):
        b = burst(
            "2026-08-25 10:00:00+00:00",
            "2026-08-25 10:15:00+00:00",
            statements=[
                statement(sql="BEGIN", command_tag="BEGIN", session_line_num=1),
                statement(
                    sql="select 1 from pg_stat_activity",
                    command_tag="SELECT",
                    session_line_num=2,
                ),
                statement(sql="COMMIT", command_tag="COMMIT", session_line_num=3),
            ],
        )
        assert _scope_burst(b, {"orders"})["statements"] == []


class TestWriteBursts:
    def merged(self):
        return {
            "coverage": {
                "window_start": "2026-08-25 10:00:00+00:00",
                "window_end": "2026-08-25 12:00:00+00:00",
            },
            "statements": {},
            "covered_seconds": None,
            "totals": {"calls": 0},
        }

    def test_no_statements_returns_taken_false(self, tmp_path):
        result = _write_bursts(
            tmp_path, [burst("x", "y", statements=[])], self.merged()
        )
        assert result == {"taken": False}

    def test_statements_write_the_csv_and_report_degraded_bursts(self, tmp_path):
        healthy = burst(
            "2026-08-25 10:00:00+00:00",
            "2026-08-25 10:15:00+00:00",
            statements=[statement()],
            status="ok",
        )
        degraded = burst(
            "2026-08-25 10:15:00+00:00",
            "2026-08-25 10:30:00+00:00",
            statements=[statement()],
            status="degraded",
        )
        result = _write_bursts(tmp_path, [healthy, degraded], self.merged())
        assert result["taken"] is True
        assert result["degraded_bursts"] == 1
        assert (tmp_path / "burst.csv").is_file()
        assert (tmp_path / "coverage.json").is_file()


class TestWriteBurstsPublic:
    def test_returns_a_three_tuple_with_matching_counts(self, tmp_path):
        merged = {
            "coverage": {
                "window_start": "2026-08-25 10:00:00+00:00",
                "window_end": "2026-08-25 12:00:00+00:00",
            },
            "statements": {},
            "covered_seconds": None,
            "totals": {"calls": 0},
        }
        inside = burst(
            "2026-08-25 10:30:00+00:00",
            "2026-08-25 10:45:00+00:00",
            statements=[statement()],
        )
        outside = burst("2026-08-25 08:00:00+00:00", "2026-08-25 09:00:00+00:00")
        broken = {"status": "unreadable", "path": "burst-broken.json.gz"}

        burst_summary, outside_window, unreadable = write_bursts(
            tmp_path, [inside, outside, broken], merged, set()
        )
        assert burst_summary["used"] == 1
        assert burst_summary["outside_window"] == 1
        assert burst_summary["unreadable"] == 1
        assert outside_window == [outside]
        assert unreadable == [broken]
