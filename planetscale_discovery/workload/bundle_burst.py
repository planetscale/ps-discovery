import csv
import io as _io
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from planetscale_discovery.workload.burst.coverage import coverage, representativeness
from planetscale_discovery.workload.logs.timestamps import (
    parse_log_stamp,
    parse_window_bound,
    to_rfc3339,
)
from planetscale_discovery.workload.logs.transactions import (
    group_by_session,
    transactions,
)


def render_burst_csv(
    statements: Sequence[Dict[str, Any]],
    log_timezone: Optional[str] = None,
    header: bool = True,
) -> str:
    buffer = _io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if header:
        writer.writerow(
            [
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
        )
    for statement in statements:
        duration = statement.get("duration_ms")
        writer.writerow(
            [
                _safe_cell(to_rfc3339(statement.get("log_time"), log_timezone)),
                _safe_cell(str(statement.get("session_id") or "")),
                _safe_cell(str(statement.get("user_name") or "")),
                _safe_cell(str(statement.get("database_name") or "")),
                _safe_cell(str(statement.get("sql") or "").strip()),
                f"{float(duration) / 1000.0:.6f}" if duration is not None else "",
                _safe_cell(
                    str(statement.get("query_id"))
                    if statement.get("query_id") is not None
                    else ""
                ),
                _safe_cell(str(statement.get("parameters") or "")),
                _safe_cell(str(statement.get("command_tag") or "")),
            ]
        )
    return buffer.getvalue()


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: str) -> str:
    if value[:1] in _FORMULA_START:
        return "'" + value
    return value


def _bursts_in_window(bursts, merged):
    readable = [b for b in bursts if b.get("status") != "unreadable"]
    unreadable = [b for b in bursts if b.get("status") == "unreadable"]

    window = merged.get("coverage") or {}
    window_start = parse_window_bound(window.get("window_start"))
    window_end = parse_window_bound(window.get("window_end"))
    # One snapshot collapses the window to an instant nothing can be inside.
    if window_start is None or window_end is None or window_end <= window_start:
        return readable, [], unreadable, []

    included, excluded, undated = [], [], []
    for burst in readable:
        start = parse_window_bound(burst.get("window_start"))
        end = parse_window_bound(burst.get("window_end"))
        # An unreadable bound means "window unknown", never "outside the window".
        if start is None or end is None:
            undated.append(burst)
            included.append(burst)
        elif end < window_start:
            excluded.append(burst)
        elif start > window_end and not _read_during_the_capture(burst):
            excluded.append(burst)
        else:
            included.append(burst)
    return included, excluded, unreadable, undated


def _read_during_the_capture(burst: Dict[str, Any]) -> bool:
    """A live read watches the log after its own snapshot, so the last window
    of a capture always starts after the last snapshot it is measured against.
    """
    from planetscale_discovery.workload.burst.collector import SOURCE_LIVE

    return burst.get("source") == SOURCE_LIVE


def _scope_burst(burst: Dict[str, Any], known: Set[str]) -> Dict[str, Any]:
    if not known:
        return burst
    from planetscale_discovery.workload.bundle import _touches_a_known_table

    statements = burst.get("statements") or []
    # Scope whole transactions: row-by-row would drop their BEGIN and COMMIT.
    keep = set()
    for records in group_by_session(statements).values():
        for shape in transactions(records):
            rows = shape["rows"]
            if any(_touches_a_known_table(r.get("sql") or "", known) for r in rows):
                keep.update(id(row) for row in rows)
    return dict(burst, statements=[s for s in statements if id(s) in keep])


def _write_bursts(
    out_dir: Path, bursts: Sequence[Dict[str, Any]], merged: Dict[str, Any]
) -> Dict[str, Any]:
    from planetscale_discovery.workload.bundle import _write_json, _write_text

    statements = [
        statement for burst in bursts for statement in (burst.get("statements") or [])
    ]
    if not statements:
        return {"taken": False}

    parts = []
    header_pending = True
    for burst in bursts:
        rows = burst.get("statements") or []
        if not rows:
            continue
        parts.append(
            render_burst_csv(
                rows, log_timezone=burst.get("log_timezone"), header=header_pending
            )
        )
        header_pending = False
    _write_text(out_dir / "burst.csv", "".join(parts))

    seconds = _burst_seconds(bursts)
    report = {
        "coverage": coverage(statements, merged),
        "representativeness": representativeness(statements, merged, seconds),
    }
    losses = [b["loss_summary"] for b in bursts if b.get("loss_summary")]
    if losses:
        report["loss"] = _sum_losses(losses)
    _write_json(out_dir / "coverage.json", report)

    sessions = group_by_session(statements)
    shapes = [t for records in sessions.values() for t in transactions(records)]
    explicit = [t for t in shapes if t["explicit"]]
    return {
        "taken": True,
        "bursts": len(bursts),
        "degraded_bursts": sum(1 for b in bursts if b.get("status") != "ok"),
        "statements": len(statements),
        "sessions": len(sessions),
        "closed_transactions": sum(1 for t in explicit if t["closed"]),
        "open_transactions": sum(1 for t in explicit if not t["closed"]),
        "with_values": sum(1 for s in statements if s.get("parameters")),
        "coverage": report["coverage"],
    }


def _sum_losses(losses: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total: Dict[str, Any] = {}
    for loss in losses:
        for key, value in loss.items():
            if isinstance(value, dict):
                merged_dict = total.setdefault(key, {})
                for sub, count in value.items():
                    merged_dict[sub] = merged_dict.get(sub, 0) + count
            else:
                total[key] = total.get(key, 0) + value
    return total


def _burst_seconds(bursts: Sequence[Dict[str, Any]]) -> Optional[float]:
    total = 0.0
    for burst in bursts:
        stamps = [
            str(statement.get("log_time") or "").strip()
            for statement in (burst.get("statements") or [])
        ]
        stamps = [stamp for stamp in stamps if stamp]
        if len(stamps) < 2:
            continue
        start, end = parse_log_stamp(min(stamps)), parse_log_stamp(max(stamps))
        if start is None or end is None:
            continue
        span = (end - start).total_seconds()
        if span > 0:
            total += span
    return total if total > 0 else None


def write_bursts(
    out_dir: Path,
    bursts: Sequence[Dict[str, Any]],
    merged: Dict[str, Any],
    known: Set[str],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    in_window, outside_window, unreadable, undated = _bursts_in_window(
        bursts or [], merged
    )
    in_window = [_scope_burst(b, known) for b in in_window]
    burst_summary = _write_bursts(out_dir, in_window, merged)
    burst_summary["used"] = len(in_window)
    burst_summary["outside_window"] = len(outside_window)
    burst_summary["unreadable"] = len(unreadable)
    burst_summary["undated"] = len(undated)
    return burst_summary, outside_window, unreadable
