"""Tests for coverage() and representativeness()."""

from pytest import approx

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


class TestSampledRepresentativeness:
    """A log of 0.001 of the transactions ran at 0.001x the window's rate, and
    was read as a quiet period."""

    def test_the_rate_is_scaled_by_the_sample_rate(self):
        merged = {"covered_seconds": 100, "totals": {"calls": 100000}}
        result = representativeness(
            [{}] * 1000,
            merged,
            100,
            estimated_statements=1000 / 0.001,
            sample_rates=[0.001],
        )

        assert result["burst_statements_per_second"] == 10.0
        assert result["estimated_statements_per_second"] == 10000.0
        assert result["ratio"] == 10.0
        assert "quieter" not in result["detail"]
        assert "sample of the server's transactions" in result["detail"]
        assert result["sample_rates"] == [0.001]

    def test_an_unsampled_log_is_compared_as_before(self):
        merged = {"covered_seconds": 100, "totals": {"calls": 100}}
        result = representativeness(
            [{}] * 100, merged, 100, estimated_statements=100.0, sample_rates=[1.0]
        )

        assert result["ratio"] == 1.0
        assert "sample_rates" not in result
        assert "sample of" not in result["detail"]


class TestChanceAbsent:
    """A sampled log misses rare statements by chance. The expected count is
    the statement's calls times the share of the window's calls the log holds."""

    def _merged(self, seen=4980):
        return {
            "covered_seconds": 1000,
            "statements": {
                "seen": {"id": "seen", "queryids": [1], "counters": {"calls": seen}},
                "heavy": {"id": "heavy", "queryids": [2], "counters": {"calls": 5000}},
                "rare": {"id": "rare", "queryids": [3], "counters": {"calls": 20}},
            },
        }

    def test_each_missed_statement_carries_its_expected_count(self):
        """100 logged statements of 10000 calls is a share of 0.01."""
        result = coverage([{"query_id": 1}] * 100, self._merged())

        missed = {m["id"]: m for m in result["heaviest_not_observed"]}
        assert missed["heavy"]["expected_in_log"] == 50.0
        assert missed["heavy"]["chance_absent"] < 1e-20
        assert missed["rare"]["expected_in_log"] == 0.2
        assert round(missed["rare"]["chance_absent"], 3) == 0.819
        assert result["not_observed_likely_by_chance"] == 1

    def test_the_threshold_is_a_chance_of_0_05(self):
        """exp(-2.9) is 0.055 and counts; exp(-3.0) is 0.0498 and does not."""
        merged = {
            "statements": {
                "seen": {"id": "seen", "queryids": [1], "counters": {"calls": 9971}},
                "under": {"id": "under", "queryids": [2], "counters": {"calls": 29}},
            },
        }
        result = coverage([{"query_id": 1}] * 1000, merged)
        assert result["heaviest_not_observed"][0]["expected_in_log"] == approx(2.9)
        assert result["not_observed_likely_by_chance"] == 1

        merged["statements"]["under"]["counters"]["calls"] = 30
        merged["statements"]["seen"]["counters"]["calls"] = 9970
        result = coverage([{"query_id": 1}] * 1000, merged)
        assert result["heaviest_not_observed"][0]["expected_in_log"] == approx(3.0)
        assert result["not_observed_likely_by_chance"] == 0

    def test_no_calls_leaves_the_fields_out(self):
        merged = self._merged()
        for record in merged["statements"].values():
            record["counters"]["calls"] = 0
        result = coverage([{"query_id": 1}], merged)

        assert "expected_in_log" not in result["heaviest_not_observed"][0]
        assert result["not_observed_likely_by_chance"] is None

    def test_the_share_never_passes_one(self):
        result = coverage([{"query_id": 1}] * 20000, self._merged())

        missed = {m["id"]: m for m in result["heaviest_not_observed"]}
        assert missed["rare"]["expected_in_log"] == 20.0


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
        assert len(missed) == 1
        assert {k: missed[0][k] for k in ("id", "query", "calls")} == {
            "id": "both",
            "query": "select both",
            "calls": 10.0,
        }
        assert missed[0]["total_exec_time_ms"] == 50.0

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
