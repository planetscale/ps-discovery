"""Difference consecutive snapshots into one measured window.

The planner takes a workload file, not two pg_stat_statements readings, so this
arithmetic belongs here. Resets, lifetime min/max and recomputed means are the
sharp edges; each is handled at its call site.
"""

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from planetscale_discovery.workload.collect import (
    INDEX_GAUGES,
    TABLE_SIZE_GAUGES,
    WRITE_COUNTERS,
)

BASIS_WINDOWED = "windowed"
BASIS_CUMULATIVE = "cumulative"


def _calls(statements: Dict[str, Dict[str, Any]]) -> float:
    return sum((s["counters"].get("calls") or 0) for s in statements.values())


def _pgss_window(usable: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The extension's capacity, and whether it discarded anything measured here.

    Eviction is judged from the change across the window rather than from the
    final value. A server that evicted statements last month and none during
    this capture lost nothing from this result. ``None`` where the server cannot
    report ``dealloc``, which is every version before PostgreSQL 14.
    """
    first = (usable[0].get("pgss") or {}).get("dealloc")
    last = (usable[-1].get("pgss") or {}).get("dealloc")
    evicted = None if first is None or last is None else last > first
    return {"max": (usable[-1].get("pgss") or {}).get("max"), "evicted": evicted}


def _column_stats(usable: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each column's distribution, as it stood first and last.

    These are gauges, not counters, so they are carried rather than differenced.
    Both readings are kept because the change is the point: a key that spreads
    evenly at the start of a capture and piles onto one value by the end is a
    different risk from one that was always lopsided, and a single reading
    cannot tell them apart.
    """
    first = {(c["table"], c["column"]): c for c in (usable[0].get("columns") or [])}
    out: List[Dict[str, Any]] = []
    for current in usable[-1].get("columns") or []:
        earlier = first.get((current["table"], current["column"])) or {}
        top = current.get("top_frequencies") or []
        top_first = (earlier.get("top_frequencies") or [None])[0]
        out.append(
            {
                "table": current["table"],
                "column": current["column"],
                "n_distinct": current.get("n_distinct"),
                "null_frac": current.get("null_frac"),
                "correlation": current.get("correlation"),
                "top_frequencies": top,
                "mcv_coverage": current.get("mcv_coverage"),
                "top_share": top[0] if top else None,
                "first_seen": {
                    "n_distinct": earlier.get("n_distinct"),
                    "null_frac": earlier.get("null_frac"),
                    "top_share": top_first,
                },
            }
        )
    return out


def _row_identity(entry: Dict[str, Any]) -> tuple:
    """What makes one ``pg_stat_statements`` row the same row as before.

    The server's own ``queryid`` identifies the statement, and the remaining
    three columns are the rest of that view's primary key. ``queryid`` is null
    when ``compute_query_id`` is off, so the text stands in for it there.
    """
    queryid = entry.get("queryid")
    return (
        entry.get("userid"),
        entry.get("dbid"),
        bool(entry.get("toplevel", True)),
        queryid if queryid is not None else statement_id(entry.get("query")),
    )


def statement_id(text: Optional[str]) -> str:
    """Stable identity for a statement, from its redacted text."""
    normalized = " ".join((text or "").split()).rstrip(";").strip()
    digest = hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()
    return digest[:16]


def _output_key(entry: Dict[str, Any]) -> str:
    query = entry.get("query")
    if query is not None:
        return statement_id(query)
    queryid = entry.get("queryid")
    return f"masked:{queryid}" if queryid is not None else statement_id(None)


def merge_snapshots(
    snapshots: Sequence[Dict[str, Any]], allow_partial: bool = False
) -> Dict[str, Any]:
    """Combine ordered snapshots into one window."""
    usable = [s for s in snapshots if s.get("status") in ("ok", "degraded")]

    mismatch = _server_mismatch(usable)
    if mismatch:
        return {"usable": False, "reason": mismatch, "basis": None}

    if len(usable) < 2:
        return _cumulative(usable, snapshots, allow_partial)

    statements: Dict[str, Dict[str, Any]] = {}
    tables: Dict[str, Dict[str, Any]] = {}
    indexes: Dict[str, Dict[str, Any]] = {}
    covered = 0.0
    measured: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    peaks: Dict[str, Dict[str, Any]] = {}
    resets = 0

    for previous, current in zip(usable, usable[1:]):
        seconds = interval_seconds(previous, current)
        if seconds is None:
            skipped.append(
                {
                    "start": previous.get("captured_at_server"),
                    "end": current.get("captured_at_server"),
                    "reason": "the pair is not ordered by the server clock",
                }
            )
            continue
        covered += seconds
        # Per interval, not only in total: an interval that collected nothing is
        # a gap in the traffic, and only the interval's own count shows it.
        before = _calls(statements)
        resets += _accumulate_statements(previous, current, statements)
        resets += _accumulate_relations(previous, current, tables, indexes)
        _track_peak_writes(previous, current, seconds, peaks)
        measured.append(
            {
                "start": previous.get("captured_at_server"),
                "end": current.get("captured_at_server"),
                "seconds": seconds,
                "calls": _calls(statements) - before,
            }
        )

    if not measured:
        return _cumulative(usable, snapshots, allow_partial)

    _carry_gauges(usable[-1], tables)
    _carry_index_gauges(usable[-1], indexes)
    _carry_first_seen(usable[0], tables, indexes)
    _carry_peaks(peaks, tables)
    _finish_statements(statements, covered)

    return {
        "usable": True,
        "basis": BASIS_WINDOWED,
        "covered_seconds": covered,
        "coverage": {
            "snapshots_total": len(snapshots),
            "snapshots_usable": len(usable),
            "intervals_used": len(measured),
            "intervals": measured,
            "intervals_skipped": skipped,
            "window_start": usable[0].get("captured_at_server"),
            "window_end": usable[-1].get("captured_at_server"),
        },
        "pgss": _pgss_window(usable),
        "row_cap_reached": any(
            w.get("code") == "row_cap_reached"
            for s in usable
            for w in (s.get("warnings") or [])
        ),
        "resets_observed": resets,
        "statements": statements,
        "tables": tables,
        "indexes": indexes,
        # Lifetime totals from the newest snapshot, alongside the window.
        "lifetime": _lifetime(usable[-1]),
        "totals": _totals(statements, covered),
        # Carried so a reader can tell which database a window came from.
        "server": dict(usable[-1].get("server") or {}),
        "columns": _column_stats(usable),
    }


def _server_mismatch(usable: Sequence[Dict[str, Any]]) -> Optional[str]:
    """Refuse to merge snapshots from different servers or across a major."""
    seen = set()
    for snapshot in usable:
        server = snapshot.get("server") or {}
        seen.add(
            (
                server.get("database_oid"),
                server.get("database"),
                server.get("version_num"),
            )
        )
    if len(seen) > 1:
        return (
            "these snapshots come from more than one server or major version, "
            "so their counters are not comparable. Capture each server into "
            "its own session directory."
        )
    return None


def _cumulative(usable, snapshots, allow_partial) -> Dict[str, Any]:
    """One snapshot only: lifetime totals, which is a different claim."""
    if not usable:
        return {
            "usable": False,
            "reason": "no usable snapshot in this session",
            "basis": None,
        }
    if not allow_partial:
        return {
            "usable": False,
            "reason": (
                "only one usable snapshot, so no interval can be measured. Run "
                "'workload collect' again, or pass --allow-partial to report "
                "cumulative totals instead of a window."
            ),
            "basis": None,
        }

    snapshot = usable[-1]
    statements: Dict[str, Dict[str, Any]] = {}
    for entry in snapshot.get("statements") or []:
        key = _output_key(entry)
        # Several rows can carry the same text, one per role that ran it, so
        # their counters are summed. Assigning would report only the last row.
        record = statements.setdefault(
            key,
            {
                "id": key,
                "query": entry.get("query"),
                "query_kind": entry.get("query_kind"),
                "text_truncated": entry.get("text_truncated"),
                "text_unavailable": entry.get("text_unavailable", False),
                "queryids": [],
                "counters": {},
                "max_exec_time": None,
                "min_exec_time": None,
            },
        )
        queryid = entry.get("queryid")
        if queryid is not None and queryid not in record["queryids"]:
            record["queryids"].append(queryid)
        for name, value in (entry.get("counters") or {}).items():
            if value is not None:
                record["counters"][name] = record["counters"].get(name, 0.0) + value
        record["max_exec_time"] = _max(
            record["max_exec_time"], _lifetime_value(entry, "max_exec_time")
        )
        record["min_exec_time"] = _min(
            record["min_exec_time"], _lifetime_value(entry, "min_exec_time")
        )
    _finish_statements(statements, None)

    return {
        "usable": True,
        "basis": BASIS_CUMULATIVE,
        "covered_seconds": None,
        "coverage": {
            "snapshots_total": len(snapshots),
            "snapshots_usable": len(usable),
            "intervals_used": 0,
            "intervals_skipped": [],
            "window_start": snapshot.get("captured_at_server"),
            "window_end": snapshot.get("captured_at_server"),
        },
        "resets_observed": 0,
        "statements": statements,
        "tables": {t["table"]: dict(t) for t in snapshot.get("tables") or []},
        "indexes": {_index_key(i): dict(i) for i in snapshot.get("indexes") or []},
        "lifetime": _lifetime(snapshot),
        "server": dict(snapshot.get("server") or {}),
        "columns": _column_stats(usable),
        "totals": _totals(statements, None),
    }


def interval_seconds(previous, current) -> Optional[float]:
    """Seconds between two snapshots, by the server clock."""
    start = _parse(previous.get("captured_at_server"))
    end = _parse(current.get("captured_at_server"))
    if start is None or end is None:
        return None
    try:
        seconds = (end - start).total_seconds()
    except TypeError:
        # One is timezone-aware and the other is not; not comparable.
        return None
    return seconds if seconds > 0 else None


def _parse(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _accumulate_statements(previous, current, out) -> int:
    # Two keys, and they are not the same key.
    #
    # `pg_stat_statements` holds one row per (user, database, top-level, query),
    # so the same text appears several times when more than one role runs it.
    # Measured on a live server: 609 rows for 562 distinct texts, with COMMIT
    # held three times over.
    #
    # Differencing is per row, because only a row can be compared with itself.
    # Keying that by text alone compared one role's row against another role's
    # earlier reading, which reports a reset whenever the second row is smaller
    # and computes the delta from the wrong baseline.
    #
    # Output stays keyed by text, so the query log holds each statement once.
    before = {_row_identity(e): e for e in previous.get("statements") or []}
    resets = 0
    for entry in current.get("statements") or []:
        key = _output_key(entry)
        record = out.setdefault(
            key,
            {
                "id": key,
                "query": entry.get("query"),
                "query_kind": entry.get("query_kind"),
                "text_truncated": entry.get("text_truncated"),
                "text_unavailable": entry.get("text_unavailable", False),
                "queryids": [],
                "counters": {},
                "max_exec_time": None,
                "min_exec_time": None,
            },
        )
        queryid = entry.get("queryid")
        if queryid is not None and queryid not in record["queryids"]:
            record["queryids"].append(queryid)

        prior = (before.get(_row_identity(entry)) or {}).get("counters") or {}
        if _add_counters(record["counters"], prior, entry.get("counters") or {}):
            resets += 1

        # Lifetime values: carried, never differenced. The collector keeps them
        # beside `counters` for that reason, so read the record itself first.
        record["max_exec_time"] = _max(
            record["max_exec_time"], _lifetime_value(entry, "max_exec_time")
        )
        record["min_exec_time"] = _min(
            record["min_exec_time"], _lifetime_value(entry, "min_exec_time")
        )
    return resets


def _accumulate_relations(previous, current, tables, indexes) -> int:
    resets = 0
    resets += _accumulate_group(
        {t["table"]: t for t in previous.get("tables") or []},
        {t["table"]: t for t in current.get("tables") or []},
        tables,
        key_fields=("table",),
    )
    resets += _accumulate_group(
        {_index_key(i): i for i in previous.get("indexes") or []},
        {_index_key(i): i for i in current.get("indexes") or []},
        indexes,
        key_fields=(
            "table",
            "index",
            "leading_column",
            "is_unique",
            "is_primary",
            "definition",
        ),
    )
    return resets


def _accumulate_group(before, after, out, key_fields) -> int:
    resets = 0
    for key, current in after.items():
        record = out.setdefault(key, {"counters": {}})
        # Descriptive fields come from the newest snapshot, so a rebuilt index keeps.
        for field in key_fields:
            record[field] = current.get(field)
        prior = (before.get(key) or {}).get("counters") or {}
        if _add_counters(record["counters"], prior, current.get("counters") or {}):
            resets += 1
    return resets


def _add_counters(target, prior, current) -> bool:
    """Add one interval's deltas. Returns True if a reset was detected."""
    was_reset = False
    for name, value in current.items():
        if value is None:
            continue
        previous = prior.get(name)
        if previous is None:
            delta = value
        elif value < previous:
            delta = value
            was_reset = True
        else:
            delta = value - previous
        target[name] = target.get(name, 0.0) + delta
    return was_reset


def _carry_gauges(snapshot, tables) -> None:
    """Gauges describe the present, so they are carried, not differenced."""
    for table in snapshot.get("tables") or []:
        record = tables.get(table["table"])
        if record is None:
            continue
        record["gauges"] = dict(table.get("gauges") or {})
        record["estimated_rows"] = table.get("estimated_rows")
        record["never_analyzed"] = table.get("never_analyzed")
        record["last_analyze"] = table.get("last_analyze")
        record["last_autoanalyze"] = table.get("last_autoanalyze")


def _carry_index_gauges(snapshot, indexes) -> None:
    """The same for indexes, which carried no gauge before sizes arrived."""
    for entry in snapshot.get("indexes") or []:
        record = indexes.get(_index_key(entry))
        if record is None:
            continue
        record["gauges"] = dict(entry.get("gauges") or {})


def _carry_first_seen(snapshot, tables, indexes) -> None:
    """The size gauges as they stood at the start of the window.

    Growth is the difference between two readings, so the earlier one has to
    survive the merge. A relation the first snapshot did not hold gets None
    rather than zero, because it was not measured as empty, it was not measured.
    """
    first_tables = {t["table"]: t for t in (snapshot.get("tables") or [])}
    for table_name, record in tables.items():
        earlier = (first_tables.get(table_name) or {}).get("gauges") or {}
        record["first_seen"] = {g: earlier.get(g) for g in TABLE_SIZE_GAUGES}

    first_indexes = {_index_key(i): i for i in (snapshot.get("indexes") or [])}
    for key, record in indexes.items():
        earlier = (first_indexes.get(key) or {}).get("gauges") or {}
        record["first_seen"] = {g: earlier.get(g) for g in INDEX_GAUGES}


def _track_peak_writes(previous, current, seconds, peaks) -> None:
    """The busiest interval's write rate, which the window total hides.

    A table averaging 200 writes a second across a day and one doing 12,000 for
    twenty minutes size the same shard and behave nothing alike. Only the
    interval's own rate shows the second case.
    """
    if not seconds:
        return
    before = {
        t["table"]: t.get("counters") or {} for t in (previous.get("tables") or [])
    }
    for table in current.get("tables") or []:
        prior = before.get(table["table"])
        if prior is None:
            continue
        counters = table.get("counters") or {}
        written = 0.0
        for name in WRITE_COUNTERS:
            value = counters.get(name)
            if value is None:
                continue
            earlier = prior.get(name) or 0.0
            # The reset rule from _add_counters: a counter below its previous
            # reading was reset, so the current value is the whole delta.
            written += value if value < earlier else value - earlier
        rate = written / seconds
        best = peaks.get(table["table"])
        if best is None or rate > best["peak_writes_per_second"]:
            peaks[table["table"]] = {
                "peak_writes_per_second": rate,
                "peak_interval_start": previous.get("captured_at_server"),
            }


def _carry_peaks(peaks, tables) -> None:
    """Attach each table's busiest interval, or None where none was measured."""
    for name, record in tables.items():
        record["peak"] = peaks.get(name)


def _finish_statements(statements, covered_seconds) -> None:
    """Recompute derived values that must not be summed or averaged."""
    for record in statements.values():
        counters = record["counters"]
        calls = counters.get("calls") or 0
        total = counters.get("total_exec_time")
        # A mean of means weights every snapshot equally regardless of traffic.
        record["mean_exec_time"] = (total / calls) if calls and total else None
        record["calls_per_second"] = (
            (calls / covered_seconds) if covered_seconds and calls else None
        )


def _totals(statements, covered_seconds) -> Dict[str, Any]:
    calls = sum((s["counters"].get("calls") or 0) for s in statements.values())
    total_time = sum(
        (s["counters"].get("total_exec_time") or 0) for s in statements.values()
    )
    return {
        "distinct_statements": len(statements),
        "calls": calls,
        "total_exec_time_ms": total_time,
        "calls_per_second": (calls / covered_seconds) if covered_seconds else None,
    }


def _lifetime(snapshot) -> Dict[str, Any]:
    """Absolute relation counters from the newest snapshot."""
    return {
        "as_of": snapshot.get("captured_at_server"),
        "tables": {t["table"]: dict(t) for t in snapshot.get("tables") or []},
        "indexes": {_index_key(i): dict(i) for i in snapshot.get("indexes") or []},
    }


def _index_key(entry) -> str:
    return f"{entry['table']}::{entry['index']}"


def _get(entry, name):
    return (entry.get("counters") or {}).get(name)


def _lifetime_value(entry, name):
    """A lifetime extreme, which sits beside ``counters`` rather than inside it.

    Falls back to ``counters`` so a session that spans a version upgrade still
    merges: snapshots written before this change carry neither, and one written
    by a future collector that moves the field back is still read.
    """
    value = entry.get(name)
    return _get(entry, name) if value is None else value


def _max(a, b):
    values = [v for v in (a, b) if v is not None]
    return max(values) if values else None


def _min(a, b):
    values = [v for v in (a, b) if v is not None]
    return min(values) if values else None
