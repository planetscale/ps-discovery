"""The same statement text can occupy several pg_stat_statements rows.

That view holds one row per (userid, dbid, toplevel, queryid), so a statement
run by three roles is three rows carrying identical text. Measured on a live
server: 609 rows for 562 distinct texts, with COMMIT held three times.

Differencing has to compare a row with itself. Keying it by text alone compared
one role's reading against another role's, which reported a reset whenever the
second row was smaller and took the delta from the wrong baseline. On an
untouched server that produced "Statistics were reset 44 time(s)" for 43
duplicated texts.
"""

import pytest

from planetscale_discovery.workload.merge import merge_snapshots, statement_id

SERVER = {"database": "app", "database_oid": 1, "version_num": 170000}
T0 = "2026-08-25 10:00:00+00:00"
T1 = "2026-08-25 10:10:00+00:00"

COMMIT = "COMMIT"


def row(query, calls, userid=10, toplevel=True, queryid=None):
    return {
        "userid": userid,
        "dbid": 1,
        "queryid": queryid if queryid is not None else f"q{abs(hash(query)) % 997}",
        "toplevel": toplevel,
        "query": query,
        "query_kind": "SELECT" if query != COMMIT else "COMMIT",
        "counters": {"calls": calls, "total_exec_time": calls * 2.0, "rows": calls},
    }


def snap(rows, at):
    return {
        "status": "ok",
        "captured_at_server": at,
        "server": SERVER,
        "statements": rows,
        "tables": [],
        "indexes": [],
    }


class TestDuplicateTextIsNotAReset:
    """Three roles run COMMIT. Every row grows. Nothing was reset."""

    def _merged(self):
        first = snap(
            [
                row(COMMIT, 1000, userid=10, queryid="c1"),
                row(COMMIT, 50, userid=20, queryid="c1"),
                row(COMMIT, 5, userid=30, queryid="c1"),
            ],
            T0,
        )
        second = snap(
            [
                row(COMMIT, 1200, userid=10, queryid="c1"),
                row(COMMIT, 70, userid=20, queryid="c1"),
                row(COMMIT, 9, userid=30, queryid="c1"),
            ],
            T1,
        )
        return merge_snapshots([first, second])

    def test_no_reset_is_reported(self):
        assert self._merged()["resets_observed"] == 0

    def test_the_deltas_are_summed_across_the_rows(self):
        """200 + 20 + 4, each row against its own earlier reading."""
        record = self._merged()["statements"][statement_id(COMMIT)]
        assert record["counters"]["calls"] == 224

    def test_the_text_appears_once(self):
        assert len(self._merged()["statements"]) == 1

    def test_every_queryid_is_recorded(self):
        record = self._merged()["statements"][statement_id(COMMIT)]
        assert record["queryids"] == ["c1"]


class TestRowsAreToldApartByEveryKeyColumn:
    def _calls(self, first_rows, second_rows):
        merged = merge_snapshots([snap(first_rows, T0), snap(second_rows, T1)])
        record = merged["statements"][statement_id(COMMIT)]
        return merged["resets_observed"], record["counters"]["calls"]

    def test_toplevel_separates_two_rows(self):
        resets, calls = self._calls(
            [row(COMMIT, 900, toplevel=True), row(COMMIT, 4, toplevel=False)],
            [row(COMMIT, 950, toplevel=True), row(COMMIT, 6, toplevel=False)],
        )
        assert (resets, calls) == (0, 52)

    def test_queryid_separates_two_rows(self):
        resets, calls = self._calls(
            [row(COMMIT, 900, queryid="a"), row(COMMIT, 4, queryid="b")],
            [row(COMMIT, 950, queryid="a"), row(COMMIT, 6, queryid="b")],
        )
        assert (resets, calls) == (0, 52)

    def test_a_new_row_contributes_its_whole_value(self):
        """A role that first appears in the second snapshot has no baseline."""
        resets, calls = self._calls(
            [row(COMMIT, 900, userid=10)],
            [row(COMMIT, 950, userid=10), row(COMMIT, 7, userid=20)],
        )
        assert (resets, calls) == (0, 57)


class TestARealResetIsStillCaught:
    def test_a_counter_going_backwards_is_reported(self):
        merged = merge_snapshots(
            [
                snap([row(COMMIT, 1000, userid=10, queryid="c1")], T0),
                snap([row(COMMIT, 5, userid=10, queryid="c1")], T1),
            ]
        )
        assert merged["resets_observed"] == 1
        assert merged["statements"][statement_id(COMMIT)]["counters"]["calls"] == 5


class TestCumulativeSumsDuplicatesToo:
    def test_one_snapshot_sums_the_rows_rather_than_keeping_the_last(self):
        merged = merge_snapshots(
            [
                snap(
                    [
                        row(COMMIT, 1000, userid=10),
                        row(COMMIT, 50, userid=20),
                        row(COMMIT, 5, userid=30),
                    ],
                    T0,
                )
            ],
            allow_partial=True,
        )
        record = merged["statements"][statement_id(COMMIT)]
        assert merged["basis"] == "cumulative"
        assert record["counters"]["calls"] == 1055


TRIGGER = "UPDATE counts SET n = n + $1 WHERE org_id = NEW.org_id"


class TestNestedCallsSurviveTheMerge:
    """A top-level row and a nested row with the same text become one record.

    With pg_stat_statements.track = all, a statement that a trigger runs is
    recorded beside the statements the application sends. The record keeps
    the nested part, so a reader can tell the two apart.
    """

    def _record(self, first_rows, second_rows, missing=()):
        first, second = snap(first_rows, T0), snap(second_rows, T1)
        for snapshot in (first, second):
            snapshot["statement_columns_missing"] = list(missing)
        merged = merge_snapshots([first, second])
        return merged, merged["statements"][statement_id(TRIGGER)]

    def test_the_nested_calls_are_counted_beside_the_total(self):
        _, record = self._record(
            [row(TRIGGER, 900, toplevel=True), row(TRIGGER, 40, toplevel=False)],
            [row(TRIGGER, 950, toplevel=True), row(TRIGGER, 70, toplevel=False)],
        )
        assert record["counters"]["calls"] == 80
        assert record["nested_calls"] == 30

    def test_a_statement_only_a_trigger_runs_is_all_nested(self):
        _, record = self._record(
            [row(TRIGGER, 40, toplevel=False)], [row(TRIGGER, 70, toplevel=False)]
        )
        assert record["nested_calls"] == record["counters"]["calls"] == 30

    def test_a_top_level_statement_has_no_nested_calls(self):
        merged, record = self._record([row(TRIGGER, 900)], [row(TRIGGER, 950)])
        assert record["nested_calls"] == 0
        assert merged["toplevel_known"] is True

    def test_a_server_with_no_toplevel_column_cannot_say(self):
        """Snapshots from older versions of this tool stored False there."""
        merged, record = self._record(
            [row(TRIGGER, 40, toplevel=False)],
            [row(TRIGGER, 70, toplevel=False)],
            missing=("toplevel",),
        )
        assert record["nested_calls"] is None
        assert merged["toplevel_known"] is False

    @pytest.mark.parametrize("unknown", [1, 2])
    def test_one_unknown_interval_makes_the_window_unknown(self, unknown):
        """A nested count from the known interval alone would understate the
        window, in either order. A flag reset per interval passed the suite."""
        snapshots = [
            snap([row(TRIGGER, calls, toplevel=False)], at)
            for calls, at in ((40, T0), (70, T1), (90, "2026-08-25 10:20:00+00:00"))
        ]
        snapshots[unknown]["statement_columns_missing"] = ["toplevel"]
        merged = merge_snapshots(snapshots)
        record = merged["statements"][statement_id(TRIGGER)]
        assert record["nested_calls"] is None
        assert merged["toplevel_known"] is False

    def test_one_snapshot_counts_the_nested_calls_as_well(self):
        merged = merge_snapshots(
            [
                snap(
                    [row(TRIGGER, 900, toplevel=True), row(TRIGGER, 4, toplevel=False)],
                    T0,
                )
            ],
            allow_partial=True,
        )
        assert merged["statements"][statement_id(TRIGGER)]["nested_calls"] == 4
