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
