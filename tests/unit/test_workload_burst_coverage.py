"""Tests for coverage() and representativeness()."""

from planetscale_discovery.workload.burst.coverage import (
    STATUS_NO_AGGREGATE,
    STATUS_NO_JOIN_KEY,
    STATUS_OK,
    coverage,
    representativeness,
)


class TestCoverage:
    def test_no_aggregate_is_reported(self):
        result = coverage([{"query_id": 1}], {"statements": {}})
        assert result["status"] == STATUS_NO_AGGREGATE

    def test_burst_with_no_query_id_is_reported(self):
        merged = {"statements": {"a": {"queryids": [1], "counters": {}}}}
        result = coverage([{"sql": "select 1"}], merged)
        assert result["status"] == STATUS_NO_JOIN_KEY

    def test_aggregate_with_no_queryids_is_reported(self):
        merged = {"statements": {"a": {"queryids": [], "counters": {}}}}
        result = coverage([{"query_id": 1}], merged)
        assert result["status"] == STATUS_NO_JOIN_KEY

    def test_normal_join_computes_shares_and_missed_list(self):
        merged = {
            "statements": {
                "a": {
                    "id": "a",
                    "query": "select a",
                    "queryids": [1],
                    "counters": {"calls": 10, "total_exec_time": 100},
                },
                "b": {
                    "id": "b",
                    "query": "select b",
                    "queryids": [2],
                    "counters": {"calls": 5, "total_exec_time": 50},
                },
                "c": {
                    "id": "c",
                    "query": "select c",
                    "queryids": [3],
                    "counters": {"calls": 20, "total_exec_time": 200},
                },
            }
        }
        burst = [{"query_id": 1}]
        result = coverage(burst, merged)

        assert result["status"] == STATUS_OK
        assert result["statements"]["in_aggregate"] == 3
        assert result["statements"]["observed_in_burst"] == 1
        assert result["statements"]["not_observed"] == 2
        assert result["calls"]["total"] == 35.0
        assert result["calls"]["observed"] == 10.0
        assert result["calls"]["share"] == 10.0 / 35.0
        assert result["exec_time_ms"]["share"] == 100.0 / 350.0

        heaviest = result["heaviest_not_observed"]
        assert [item["id"] for item in heaviest] == ["c", "b"]
        assert result["not_observed_calls"] == 25.0


class TestRepresentativeness:
    def test_missing_burst_seconds_is_unknown(self):
        result = representativeness(
            [{}], {"covered_seconds": 10, "totals": {"calls": 5}}, None
        )
        assert result["known"] is False

    def test_missing_covered_seconds_is_unknown(self):
        result = representativeness([{}], {"totals": {"calls": 5}}, 10)
        assert result["known"] is False

    def test_quieter_than_window(self):
        merged = {"covered_seconds": 100, "totals": {"calls": 1000}}
        result = representativeness([{}] * 10, merged, 100)
        assert result["known"] is True
        assert result["ratio"] < 0.5
        assert "quieter period" in result["detail"]

    def test_within_band(self):
        merged = {"covered_seconds": 100, "totals": {"calls": 100}}
        result = representativeness([{}] * 100, merged, 100)
        assert result["ratio"] == 1.0
        assert "quieter" not in result["detail"]
        assert "peak" not in result["detail"]

    def test_caught_a_peak(self):
        merged = {"covered_seconds": 100, "totals": {"calls": 100}}
        result = representativeness([{}] * 1000, merged, 100)
        assert result["ratio"] > 2.0
        assert "peak" in result["detail"]


def _record(sid, queryid, calls, exec_time, nested_calls=0.0, nested_time=0.0):
    """A merged statement, shaped as merge.py leaves it."""
    return {
        "id": sid,
        "query": f"select {sid}",
        "queryids": [queryid],
        "counters": {"calls": calls, "total_exec_time": exec_time},
        "nested_counters": (
            {"calls": nested_calls, "total_exec_time": nested_time}
            if nested_calls
            else {}
        ),
        "nested_calls": nested_calls,
    }


class TestNestedCallsInCoverage:
    """The log holds only the statements that a client sends. A call that a
    function, procedure or trigger runs never reaches it."""

    def test_a_statement_only_a_trigger_runs_is_left_out(self):
        """It was reported as the heaviest statement the burst missed."""
        merged = {
            "statements": {
                "app": _record("app", 1, 10, 100),
                "trigger": _record("trigger", 2, 50, 500, 50, 500),
            }
        }
        result = coverage([{"query_id": 1}], merged)

        assert result["calls"]["total"] == 10.0
        assert result["calls"]["share"] == 1.0
        assert result["exec_time_ms"]["share"] == 1.0
        assert result["heaviest_not_observed"] == []
        assert result["statements"]["only_nested"] == 1
        assert result["statements"]["observed_in_burst"] == 1
        assert result["nested_calls_left_out"] == 50.0
        assert result["nesting_unknown"] is False

    def test_a_statement_also_run_nested_counts_its_top_level_part(self):
        """Its nested calls and nested time lowered the observed share."""
        merged = {
            "statements": {
                "app": _record("app", 1, 10, 100),
                "both": _record("both", 2, 30, 300, 20, 250),
            }
        }
        result = coverage([{"query_id": 2}], merged)

        assert result["calls"]["total"] == 20.0
        assert result["calls"]["observed"] == 10.0
        assert result["exec_time_ms"]["total"] == 150.0
        assert result["exec_time_ms"]["observed"] == 50.0
        missed = result["heaviest_not_observed"]
        assert [(m["id"], m["calls"]) for m in missed] == [("app", 10.0)]

    def test_a_missed_statement_reports_its_top_level_calls(self):
        merged = {
            "statements": {
                "app": _record("app", 1, 10, 100),
                "both": _record("both", 2, 30, 300, 20, 250),
            }
        }
        missed = coverage([{"query_id": 1}], merged)["heaviest_not_observed"]
        assert missed == [
            {
                "id": "both",
                "query": "select both",
                "calls": 10.0,
                "total_exec_time_ms": 50.0,
            }
        ]

    def test_an_unknown_split_keeps_every_call_and_says_so(self):
        """PostgreSQL 13 and earlier have no toplevel column."""
        unknown = dict(_record("old", 2, 30, 300), nested_calls=None)
        merged = {"statements": {"app": _record("app", 1, 10, 100), "old": unknown}}
        result = coverage([{"query_id": 1}], merged)

        assert result["calls"]["total"] == 40.0
        assert result["statements"]["only_nested"] == 0
        assert result["nesting_unknown"] is True


class TestNestedCallsInRepresentativeness:
    def _merged(self, *records):
        statements = {r["id"]: r for r in records}
        calls = sum(r["counters"]["calls"] for r in records)
        return {
            "covered_seconds": 100,
            "statements": statements,
            "totals": {"calls": calls},
        }

    def test_the_window_rate_counts_top_level_calls(self):
        """With track = all, the trigger's calls made an ordinary burst look
        like a quiet one."""
        merged = self._merged(
            _record("app", 1, 100, 100), _record("trigger", 2, 900, 900, 900, 900)
        )
        result = representativeness([{}] * 100, merged, 100)

        assert result["window_calls_per_second"] == 1.0
        assert result["ratio"] == 1.0
        assert "quieter" not in result["detail"]
        assert result["nested_calls_left_out"] == 900.0
        assert result["nesting_unknown"] is False

    def test_an_unknown_split_keeps_every_call_and_says_so(self):
        unknown = dict(_record("old", 1, 1000, 1000), nested_calls=None)
        result = representativeness([{}] * 100, self._merged(unknown), 100)

        assert result["window_calls_per_second"] == 10.0
        assert result["nesting_unknown"] is True
        assert "cannot separate the calls" in result["detail"]

    def test_a_window_of_nested_calls_only_is_unknown(self):
        """The ratio would divide by zero top-level calls."""
        merged = self._merged(_record("trigger", 1, 50, 50, 50, 50))
        result = representativeness([{}] * 10, merged, 100)

        assert result["known"] is False
        assert "every call in the aggregate window ran inside" in result["detail"]
