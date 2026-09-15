from typing import Any, Dict, Iterable, List, Optional

MISSED_REPORTED = 20

STATUS_OK = "ok"
STATUS_NO_JOIN_KEY = "no_join_key"
STATUS_NO_AGGREGATE = "no_aggregate"


def coverage(
    burst_statements: Iterable[Dict[str, Any]], merged: Dict[str, Any]
) -> Dict[str, Any]:
    aggregate = list((merged.get("statements") or {}).values())
    observed_ids = {
        statement["query_id"]
        for statement in burst_statements
        if statement.get("query_id")
    }

    if not aggregate:
        return _unavailable(
            STATUS_NO_AGGREGATE,
            "no aggregate window to compare against; run collect at least twice",
        )

    if not observed_ids:
        return _unavailable(
            STATUS_NO_JOIN_KEY,
            "the log window carries no query_id, so it cannot be joined to the "
            "aggregate window. csvlog carries the column only on PostgreSQL 14 "
            "and later, and only with compute_query_id on. An imported stderr "
            "log carries it only in a log_line_prefix this tool could name: "
            "the prefix is inferred from the file, and an unnamed field is "
            "read but not captured",
        )

    if not any(record.get("queryids") for record in aggregate):
        return _unavailable(
            STATUS_NO_JOIN_KEY,
            "the aggregate window carries no queryid, so a burst cannot be "
            "joined to it. pg_stat_statements supplies one with "
            "compute_query_id on; a PlanetScale Insights window does not, "
            "because its fingerprint is a different identifier",
        )

    total_calls = 0.0
    total_time = 0.0
    seen_calls = 0.0
    seen_time = 0.0
    missed: List[Dict[str, Any]] = []

    for record in aggregate:
        counters = record.get("counters") or {}
        calls = float(counters.get("calls") or 0)
        exec_time = float(counters.get("total_exec_time") or 0)
        total_calls += calls
        total_time += exec_time

        if _observed(record, observed_ids):
            seen_calls += calls
            seen_time += exec_time
        else:
            missed.append(
                {
                    "id": record.get("id"),
                    "query": record.get("query"),
                    "calls": calls,
                    "total_exec_time_ms": exec_time,
                }
            )

    missed.sort(key=lambda item: item["calls"], reverse=True)
    matched = len(aggregate) - len(missed)

    return {
        "status": STATUS_OK,
        "joined_on": "query_id",
        "statements": {
            "in_aggregate": len(aggregate),
            "observed_in_burst": matched,
            "not_observed": len(missed),
        },
        "calls": {
            "total": total_calls,
            "observed": seen_calls,
            "share": _share(seen_calls, total_calls),
        },
        "exec_time_ms": {
            "total": total_time,
            "observed": seen_time,
            "share": _share(seen_time, total_time),
        },
        "heaviest_not_observed": missed[:MISSED_REPORTED],
        "not_observed_calls": sum(item["calls"] for item in missed),
    }


def representativeness(
    burst_statements: Iterable[Dict[str, Any]],
    merged: Dict[str, Any],
    burst_seconds: Optional[float],
) -> Dict[str, Any]:
    statements = list(burst_statements)
    window_seconds = merged.get("covered_seconds")
    totals = merged.get("totals") or {}
    window_calls = float(totals.get("calls") or 0)

    if not (burst_seconds and window_seconds and window_calls):
        return {
            "known": False,
            "detail": (
                "needs a measured aggregate window and a burst duration; "
                "one of them is missing"
            ),
        }

    burst_rate = len(statements) / float(burst_seconds)
    window_rate = window_calls / float(window_seconds)
    ratio = (burst_rate / window_rate) if window_rate else None

    return {
        "known": True,
        "burst_statements_per_second": burst_rate,
        "window_calls_per_second": window_rate,
        "ratio": ratio,
        "detail": _representativeness_detail(ratio),
    }


def _representativeness_detail(ratio: Optional[float]) -> str:
    if ratio is None:
        return "the aggregate window recorded no calls per second"
    if ratio < 0.5:
        return (
            f"the burst ran at {ratio:.2f}x the window's average rate, so it "
            "saw a quieter period than the workload as a whole. Take another "
            "during a busy period before designing from it"
        )
    if ratio > 2.0:
        return (
            f"the burst ran at {ratio:.2f}x the window's average rate, so it "
            "caught a peak rather than ordinary traffic"
        )
    return f"the burst ran at {ratio:.2f}x the window's average rate"


def _observed(record: Dict[str, Any], observed_ids: set) -> bool:
    for queryid in record.get("queryids") or []:
        try:
            if int(queryid) and int(queryid) in observed_ids:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _share(part: float, whole: float) -> Optional[float]:
    return (part / whole) if whole else None


def _unavailable(status: str, detail: str) -> Dict[str, Any]:
    return {
        "status": status,
        "joined_on": None,
        "detail": detail,
        "statements": None,
        "calls": None,
        "exec_time_ms": None,
        "heaviest_not_observed": [],
    }
