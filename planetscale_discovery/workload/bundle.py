"""Write the sharding planning tools' input files.

Those tools read three things: a query workload, a schema, and a row count per
table. Those are what this writes, and nothing beyond them. A file nothing reads
is a file someone has to explain.

Every statement is emitted: there is no cap, floor or quota, because ranking a
workload needs a cost model, which belongs to the planning tools and not here.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from planetscale_discovery.workload.collect import MCF_KEPT
from planetscale_discovery.workload.schema_sql import (
    application_views,
    render_schema_sql,
    validate_round_trip,
)

# manifest.json's shape is set by the tool that reads it, so the version is a
# contract with that tool rather than a description of this one.
MANIFEST_SCHEMA_VERSION = 3

# column_stats.json is ours, read by the migration tooling rather than the
# planning tools, so its version moves with this repository.
COLUMN_STATS_SCHEMA_VERSION = 1

# table_activity.json is ours as well, read by the migration tooling.
# 2 added the size, growth and peak fields.
TABLE_ACTIVITY_SCHEMA_VERSION = 2

# What the query log is for: statements that route to data.
PLANNABLE_KINDS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"})

# The cardinality format, matching the consumer exactly.
CARDINALITY_EXCLUDED_SCHEMAS = frozenset(
    {"pg_catalog", "information_schema", "pg_toast", "__neki"}
)
CARDINALITY_RELKIND = "r"
CARDINALITY_BASENAME = "plantest_counts.json"


def write_bundle(
    out_dir: Union[str, Path],
    merged: Dict[str, Any],
    schema_analysis: Dict[str, Any],
    collector_version: str,
    target_schemas: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Write the bundle and return a summary of what it holds.

    The bundle is the planning tools' input and nothing else: a query workload,
    a schema and a row count per table. The summary is returned for the command
    line to print and for the README, and is not itself a file. A reader that
    needs a number takes it from the files themselves.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)

    # First, so it exists before any content does.
    gitignore = out_dir / ".gitignore"
    if not gitignore.exists():
        _write_text(gitignore, "*\n")

    statements = list((merged.get("statements") or {}).values())
    covered = merged.get("covered_seconds")
    known = known_table_names(schema_analysis, target_schemas=target_schemas)
    plannable, excluded = _split_by_plannability(statements, known)

    identity = capture_id(merged)
    _write_text(
        out_dir / "workload.sql", render_workload_sql(plannable, covered, identity)
    )

    schema = render_schema_sql(schema_analysis, target_schemas=target_schemas)
    parse = validate_round_trip(schema["statements"])
    # A statement the parser rejects is left out, so schema.sql always parses.
    # The count is reported rather than written to a file of its own.
    schema_sql = (
        parse["clean_sql"] if parse.get("clean_sql") is not None else schema["sql"]
    )
    _write_text(out_dir / "schema.sql", f"-- neki:capture {identity}\n{schema_sql}")

    cardinality = write_cardinality(out_dir, schema_analysis, target_schemas)
    _write_json(out_dir / "manifest.json", render_manifest(merged, collector_version))
    columns = render_column_stats(merged, identity)
    _write_json(out_dir / "column_stats.json", columns)
    _write_json(
        out_dir / "table_activity.json", render_table_activity(merged, identity)
    )

    summary = {
        "capture_id": identity,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "discovery_version": collector_version,
        "basis": merged.get("basis"),
        "covered_seconds": covered,
        "coverage": merged.get("coverage"),
        "totals": merged.get("totals"),
        "statements": {
            "collected": len(statements),
            "in_workload_sql": len(plannable),
            "excluded_from_workload_sql": len(excluded),
            "excluded_kinds": sorted({s.get("query_kind") or "" for s in excluded}),
            "excluded_calls": sum((s["counters"].get("calls") or 0) for s in excluded),
            "excluded_not_this_application": sum(
                1
                for s in excluded
                if s.get("excluded_reason") == "references no captured table"
            ),
            "excluded_truncated": sum(
                1
                for s in excluded
                if s.get("excluded_reason") == "statement text was truncated"
            ),
        },
        "schema": {
            "statements": len(schema["statements"]),
            "tables": len(schema["tables"]),
            "views": len(schema.get("views") or []),
            "parse_ok": parse.get("ok"),
            "parse_available": parse.get("available"),
            "parse_failures": len(parse.get("failures") or []),
        },
        "cardinality": cardinality,
        "resets_observed": merged.get("resets_observed"),
        "caveats": _caveats(merged, excluded, parse, cardinality),
    }
    _write_text(out_dir / "README.md", _render_readme(summary, out_dir))
    return summary


def capture_id(merged: Dict[str, Any]) -> str:
    """A short name for one capture, derived from what makes it that capture.

    Two captures of the same database over the same window produce the same id,
    and any other pair produces a different one. That is what makes it useful
    for correlating files: an id that was random would only say "these came from
    one run", and could not confirm which run.

    Eight hex characters. The inputs are a database and a pair of timestamps, so
    the space is nowhere near large enough for a collision to matter.
    """
    server = merged.get("server") or {}
    coverage = merged.get("coverage") or {}
    material = "|".join(
        str(part)
        for part in (
            server.get("database"),
            server.get("database_oid"),
            coverage.get("window_start"),
            coverage.get("window_end"),
        )
    )
    return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:8]


def bundle_dir_name(merged: Dict[str, Any]) -> str:
    """The directory one capture is written to, carrying its id."""
    return f"workload-{capture_id(merged)}"


def render_manifest(merged: Dict[str, Any], collector_version: str) -> Dict[str, Any]:
    """How the capture was taken, for a consumer that reads the bundle later.

    The shape is fixed by that consumer, and holds when the capture happened
    rather than what it found. Anything a reader can get from the files the
    planner reads is not repeated here.

    Timestamps come from the database server, not the host running cron, so a
    clock difference between the two cannot shift the window.
    """
    coverage = merged.get("coverage") or {}
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "capture_id": capture_id(merged),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "discovery_version": collector_version,
        "window": {
            "start": coverage.get("window_start"),
            "end": coverage.get("window_end"),
            "tz": "UTC",
        },
        "intervals": [
            {
                "start": interval.get("start"),
                "end": interval.get("end"),
                "calls": int(interval.get("calls") or 0),
            }
            for interval in (coverage.get("intervals") or [])
        ],
        "intervals_skipped": coverage.get("intervals_skipped") or [],
        "resets_observed": merged.get("resets_observed") or 0,
        "capped": bool(merged.get("row_cap_reached")),
        "pgss": merged.get("pgss") or {"max": None, "evicted": None},
    }


# A column whose top value moves by more than this between the first and last
# reading has changed shape during the capture, which is worth pointing at.
SKEW_DRIFT_THRESHOLD = 0.05


def render_column_stats(merged: Dict[str, Any], identity: str) -> Dict[str, Any]:
    """How each column's values are distributed, for judging a shard key.

    Row counts say how big a table is. This says whether a column can spread it:
    a key whose most common value holds 40% of the rows puts 40% of that table
    on one shard however many shards there are, and a row count cannot show it.

    Frequencies only. ``most_common_vals`` and ``histogram_bounds`` hold sampled
    rows from the customer's tables and are never read.
    """
    columns = merged.get("columns") or []
    drifted = [
        column
        for column in columns
        if column.get("top_share") is not None
        and (column.get("first_seen") or {}).get("top_share") is not None
        and abs(column["top_share"] - column["first_seen"]["top_share"])
        > SKEW_DRIFT_THRESHOLD
    ]
    return {
        "schema_version": COLUMN_STATS_SCHEMA_VERSION,
        "capture_id": identity,
        "source": "pg_stats",
        "most_common_frequencies_kept": MCF_KEPT,
        "columns_measured": len(columns),
        "columns_whose_skew_moved": len(drifted),
        "columns": columns,
    }


def render_table_activity(merged: Dict[str, Any], identity: str) -> Dict[str, Any]:
    """How much each table was written and read, and which indexes served it.

    Two things the query log cannot say. Statements are not parsed, so the log
    never attributes a write to a table, and write concentration is what decides
    whether a shard becomes a hotspot. And an index's scan count is evidence of
    the access path arrived at from real use rather than inferred from a
    statement: the column at the front of the busiest index is a shard-key
    candidate on its own footing.

    It also carries the bytes. A row count cannot say how big a shard will be,
    and shard count is decided in bytes, so each table and index reports its
    size, its size at the start of the window, and the difference. Sizes are
    gauges and are carried; the write counters are differenced.

    Counters are for the measured window, differenced from the snapshots, so
    they line up with the same window ``manifest.json`` describes. Index
    definitions are not repeated here, because ``schema.sql`` holds them.
    """
    coverage = merged.get("coverage") or {}

    def counter(entry, name):
        return int((entry.get("counters") or {}).get(name) or 0)

    # A gauge absent is not a gauge at zero, so this returns None where a
    # counter returns 0. The cumulative path carries raw snapshot entries,
    # which hold gauges under the same key, so one reader serves both.
    def gauge(entry, name):
        value = (entry.get("gauges") or {}).get(name)
        return None if value is None else int(value)

    def first_seen(entry, name):
        value = (entry.get("first_seen") or {}).get(name)
        return None if value is None else int(value)

    def growth(entry, name):
        last, earlier = gauge(entry, name), first_seen(entry, name)
        return None if last is None or earlier is None else last - earlier

    tables = [
        {
            "table": name,
            "rows_inserted": counter(entry, "n_tup_ins"),
            "rows_updated": counter(entry, "n_tup_upd"),
            "rows_deleted": counter(entry, "n_tup_del"),
            "index_scans": counter(entry, "idx_scan"),
            "sequential_scans": counter(entry, "seq_scan"),
            "rows_read_sequentially": counter(entry, "seq_tup_read"),
            # Bytes, because a shard is sized in bytes and a row count cannot
            # say how big one is. total includes TOAST and every index.
            "total_size_bytes": gauge(entry, "total_size_bytes"),
            "table_size_bytes": gauge(entry, "table_size_bytes"),
            "indexes_size_bytes": gauge(entry, "indexes_size_bytes"),
            "first_seen": {
                "total_size_bytes": first_seen(entry, "total_size_bytes"),
                "table_size_bytes": first_seen(entry, "table_size_bytes"),
                "indexes_size_bytes": first_seen(entry, "indexes_size_bytes"),
            },
            "total_size_growth_bytes": growth(entry, "total_size_bytes"),
            # The busiest interval, not the window average. A shard is sized
            # for its peak.
            "peak_writes_per_second": (entry.get("peak") or {}).get(
                "peak_writes_per_second"
            ),
            "peak_interval_start": (entry.get("peak") or {}).get("peak_interval_start"),
        }
        for name, entry in sorted((merged.get("tables") or {}).items())
    ]
    indexes = [
        {
            "table": entry.get("table"),
            "index": entry.get("index"),
            # The column a lookup goes through, which is what makes this a
            # shard-key signal rather than a usage statistic.
            "leading_column": entry.get("leading_column"),
            "is_unique": bool(entry.get("is_unique")),
            "is_primary": bool(entry.get("is_primary")),
            "scans": counter(entry, "idx_scan"),
            "rows_read": counter(entry, "idx_tup_read"),
            "rows_fetched": counter(entry, "idx_tup_fetch"),
            # An index moves with its shard and routinely exceeds its table.
            "size_bytes": gauge(entry, "size_bytes"),
            "first_seen": {"size_bytes": first_seen(entry, "size_bytes")},
            "size_growth_bytes": growth(entry, "size_bytes"),
        }
        for _, entry in sorted((merged.get("indexes") or {}).items())
    ]
    return {
        "schema_version": TABLE_ACTIVITY_SCHEMA_VERSION,
        "capture_id": identity,
        "source": "pg_stat_user_tables + pg_stat_user_indexes",
        # The same window manifest.json describes, so the two can be read together.
        "window": {
            "start": coverage.get("window_start"),
            "end": coverage.get("window_end"),
            "tz": "UTC",
            "seconds": merged.get("covered_seconds"),
            "intervals": coverage.get("intervals_used"),
        },
        "tables_measured": len(tables),
        "indexes_measured": len(indexes),
        # Named for the window, not for the index. An index unused across a
        # short capture is not an unused index, and a reader should not be
        # invited to delete it.
        "indexes_not_scanned_in_window": sum(1 for i in indexes if not i["scans"]),
        "tables": tables,
        "indexes": indexes,
    }


def known_table_names(
    schema_analysis: Dict[str, Any],
    target_schemas: Optional[Sequence[str]] = None,
) -> Set[str]:
    """Lowercased relation names from the captured schema, bare and qualified.

    Views count. The schema analyzer keeps them in their own section, because
    ``table_analysis`` selects ``relkind IN ('r','p')``. An application that
    reads through a view names only the view, so leaving views out drops that
    traffic from the query log. An extension's own views are not the
    application's, and ``application_views`` is what draws that line.

    target_schemas must match what render_schema_sql() was given, or a query
    against a table left out of schema.sql can still count as known here.
    """
    wanted = set(target_schemas) if target_schemas else None
    names: Set[str] = set()
    sections = (
        (schema_analysis.get("table_analysis") or [], "table_name"),
        (application_views(schema_analysis), "view_name"),
    )
    for relations, name_field in sections:
        for relation in relations:
            if wanted and relation.get("schema_name") not in wanted:
                continue
            schema = (relation.get("schema_name") or "").lower()
            name = (relation.get(name_field) or "").lower()
            if name:
                names.add(name)
                if schema:
                    names.add(f"{schema}.{name}")
    return names


def _touches_a_known_table(query: str, known: Set[str]) -> bool:
    """Whether the statement names any table from the captured schema."""
    words = set(re.findall(r"[a-z_][a-z0-9_$]*(?:\.[a-z_][a-z0-9_$]*)?", query.lower()))
    return bool(words & known)


def _split_by_plannability(
    statements: Sequence[Dict[str, Any]],
    known: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    plannable, excluded = [], []
    for statement in statements:
        query = statement.get("query") or ""
        kind = (statement.get("query_kind") or "").upper()
        if not query or kind not in PLANNABLE_KINDS:
            excluded.append(statement)
            continue
        # A statement naming no captured table is not this application's
        # workload -- it is monitoring, psql, or this tool's own catalog reads.
        # The planner cannot plan a relation absent from schema.sql either.
        if known and not _touches_a_known_table(query, known):
            statement["excluded_reason"] = "references no captured table"
            excluded.append(statement)
            continue
        if statement.get("text_truncated"):
            statement["excluded_reason"] = "statement text was truncated"
            excluded.append(statement)
            continue
        plannable.append(statement)
    return plannable, excluded


def render_workload_sql(
    statements: Sequence[Dict[str, Any]],
    covered_seconds: Optional[float],
    identity: str = "",
) -> str:
    """The query log: one statement per record, with its counters as headers."""
    lines = [
        "-- Workload captured by ps-discovery.",
    ]
    if identity:
        lines.append(f"-- neki:capture {identity}")
    lines += [
        "-- Every observed statement is included; nothing is ranked or dropped.",
    ]
    if covered_seconds:
        lines.append(f"-- Observed over {covered_seconds:.0f} seconds of traffic.")
    else:
        lines.append("-- One snapshot only: lifetime totals, not a measured window.")
    lines.append("")

    for statement in sorted(statements, key=lambda s: s["id"]):
        counters = statement.get("counters") or {}
        lines.append(f"-- neki:stmt id={statement['id']}")
        lines.append(
            "-- neki:metrics"
            f" calls={_num(counters.get('calls'))}"
            f" total_exec_time_ms={_ms(counters.get('total_exec_time'))}"
            f" rows={_num(counters.get('rows'))}"
        )
        text = (statement.get("query") or "").rstrip().rstrip(";")
        lines.append(f"{text};")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_cardinality(
    schema_analysis: Dict[str, Any],
    target_schemas: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Any]]:
    """Build the cardinality mapping: schema -> table -> row count."""
    stats: Dict[str, Dict[str, float]] = {}
    never_analyzed: List[str] = []
    excluded_relkinds = 0
    table_count = 0
    wanted = set(target_schemas) if target_schemas else None

    for table in schema_analysis.get("table_analysis") or []:
        schema = table.get("schema_name")
        name = table.get("table_name")
        if not schema or not name or schema in CARDINALITY_EXCLUDED_SCHEMAS:
            continue
        if wanted is not None and schema not in wanted:
            continue
        # Default to 'r': the analyzer selects only ordinary and partitioned tables,.
        if (table.get("table_type") or CARDINALITY_RELKIND) != CARDINALITY_RELKIND:
            excluded_relkinds += 1
            continue

        raw = table.get("estimated_rows")
        try:
            if raw is not None and float(raw) < 0:
                never_analyzed.append(f"{schema}.{name}")
        except (TypeError, ValueError):
            pass

        stats.setdefault(schema, {})[name] = _reltuples(raw)
        table_count += 1

    meta = {
        "table_count": table_count,
        "schema_count": len(stats),
        "never_analyzed": sorted(never_analyzed),
        "excluded_non_ordinary_tables": excluded_relkinds,
        "format": "schema_table_reltuples",
    }
    return stats, meta


def write_cardinality(
    out_dir: Union[str, Path],
    schema_analysis: Dict[str, Any],
    target_schemas: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Write both cardinality filenames with identical content."""
    stats, meta = render_cardinality(schema_analysis, target_schemas)
    out_dir = Path(out_dir)
    stem = Path(CARDINALITY_BASENAME).stem
    base_path = out_dir / f"{stem}.json"
    card_path = out_dir / f"{stem}-card.json"

    # Two-space indent and a trailing newline, matching the consumer's own output byte.
    payload = json.dumps(stats, indent=2, sort_keys=True) + "\n"
    for path in (base_path, card_path):
        _write_text(path, payload)

    meta["paths"] = {"base": str(base_path), "cardinality": str(card_path)}
    return meta


def _reltuples(value: Any) -> float:
    """Any negative value becomes 0, matching the consumer's normalization."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _caveats(merged, excluded, parse, cardinality) -> List[str]:
    """Notes that change how the counts in this bundle should be read."""
    caveats: List[str] = []

    if merged.get("basis") != "windowed":
        # Names the cause first. Leading with "since the statistics were last
        # reset" reads as though something cleared them, when the only fact is
        # that one snapshot cannot be differenced against anything.
        caveats.append(
            "Only one snapshot was collected, so this is not a measured window. "
            "The counters are lifetime totals for each statement, and no rate "
            "can be derived from them. Run 'collect' at least twice, some hours "
            "apart, for a measured window."
        )

    tables = (merged.get("tables") or {}).values()
    if tables and not any(
        (t.get("gauges") or {}).get("total_size_bytes") is not None for t in tables
    ):
        caveats.append(
            "No table size was reported, so table_activity.json holds null "
            "rather than a byte count. Read null as not measured, not as empty."
        )
    elif merged.get("basis") == "windowed" and not any(
        (t.get("first_seen") or {}).get("total_size_bytes") is not None for t in tables
    ):
        caveats.append(
            "Sizes were measured once, so no growth figure could be derived. "
            "A growth rate needs a size reading at each end of the window."
        )

    skipped = (merged.get("coverage") or {}).get("intervals_skipped") or []
    if skipped:
        caveats.append(
            f"{len(skipped)} interval(s) were skipped and are excluded from the "
            "window, so rates are over observed time only."
        )

    if merged.get("resets_observed"):
        caveats.append(
            f"Statistics were reset {merged['resets_observed']} time(s) during "
            "collection. Affected intervals were recovered from absolute "
            "counters rather than discarded."
        )

    # An empty capture and a busy one look the same in a file listing, so the
    # bundle has to say which it is. The extension records nothing when
    # pg_stat_statements.track is 'none', and init stops for that, so a capture
    # that reaches here with no calls saw a database that was genuinely idle.
    calls = (merged.get("totals") or {}).get("calls") or 0
    if not calls:
        caveats.append(
            "No queries ran on this database during the capture window, so "
            "there is no workload here to plan a sharding scheme from. Capture "
            "again over a period when the application is in use."
        )

    foreign = [
        s
        for s in excluded
        if s.get("excluded_reason") == "references no captured table"
    ]
    truncated = [
        s
        for s in excluded
        if s.get("excluded_reason") == "statement text was truncated"
    ]
    other = [s for s in excluded if not s.get("excluded_reason")]

    if truncated:
        caveats.append(
            f"{len(truncated)} statement(s) were longer than the text limit and "
            "are left out, because a statement cut mid-way cannot be read back. "
            "Raise workload.statement_text_max_chars to keep them."
        )

    if other:
        calls = sum((s["counters"].get("calls") or 0) for s in other)
        caveats.append(
            f"{len(other)} statement(s) are left out because they read and "
            f"write no data, so there is nothing to route: "
            f"{', '.join(sorted({s.get('query_kind') or '' for s in other}))}. "
            f"They ran {calls:,.0f} time(s)."
        )

    if foreign:
        calls = sum((s["counters"].get("calls") or 0) for s in foreign)
        caveats.append(
            f"{len(foreign)} statement(s) are left out because they name no "
            f"table or view from your schema. They ran {calls:,.0f} time(s), "
            "and they are monitoring tools, psql, and this tool's own reads of "
            "the system catalogs rather than your application's traffic."
        )

    if parse.get("available") is False:
        caveats.append(
            "The schema file was not re-parsed before being written, because the "
            "optional SQL parser (pglast) is not installed. Run ./setup.sh to "
            "install it, then run finalize again to check the file."
        )
    elif parse.get("failures"):
        caveats.append(
            f"{len(parse['failures'])} schema statement(s) could not be read "
            "back and were left out, so schema.sql is incomplete."
        )

    if cardinality.get("never_analyzed"):
        caveats.append(
            f"{len(cardinality['never_analyzed'])} table(s) have never been "
            "analyzed by PostgreSQL, so their row count is reported as 0 even "
            "if they hold data. Run ANALYZE on them and capture again for an "
            "accurate size."
        )

    caveats.append(
        "PostgreSQL records statements, not transactions, so this capture "
        "cannot show which tables your application writes together in one "
        "transaction. Two tables written together usually belong on the same "
        "shard, so tell your migration engineer about the write paths that "
        "matter most to you."
    )
    return caveats


def _render_readme(summary: Dict[str, Any], out_dir: Path) -> str:
    covered = summary.get("covered_seconds")
    window = (
        f"{covered / 60:,.0f} minutes of traffic" if covered else "an unmeasured period"
    )
    caveats = summary.get("caveats") or []
    statements = summary["statements"]
    schema = summary["schema"]
    not_data = (
        statements["excluded_from_workload_sql"]
        - statements["excluded_not_this_application"]
    )
    return f"""# Workload bundle {summary['capture_id']}

Captured by ps-discovery {summary['discovery_version']} on
{summary['generated_at']}, covering {window}.

`{summary['capture_id']}` is this capture's id. It names this directory and
appears inside each file, so a file that gets separated from the rest can still
be traced back to the capture it came from.

## What was collected

| | |
| --- | --- |
| Queries kept, to plan the sharding scheme from | {statements['in_workload_sql']} |
| Statements seen in total | {statements['collected']} |
| Left out, because they read and write no data | {not_data} |
| Left out, because they are not your application's | {statements['excluded_not_this_application']} |
| Tables described | {schema['tables']} |
| Views described | {schema['views']} |
| Tables with a row count | {summary['cardinality']['table_count']} |

Every query that touches one of your tables is here, however rarely it ran.
Nothing is ranked, dropped for being small, or sampled.

## Worth knowing about these numbers

{chr(10).join(f"- {c}" for c in caveats) if caveats else "Nothing to note."}

## Files

| File | What it is |
| --- | --- |
| `workload.sql` | the queries, each with how often it ran and how long it took |
| `schema.sql` | your tables, indexes and views, as `CREATE` statements |
| `plantest_counts.json` | how many rows each table holds |
| `plantest_counts-card.json` | the same content, under a second name the planning tools look for |
| `manifest.json` | when this capture ran, and over which intervals |
| `column_stats.json` | how evenly the values in each column are spread |
| `table_activity.json` | how much each table was written and read, and which indexes were used |

## Sending it

Compress this directory into one archive and send the archive to your
PlanetScale migration engineer.

    tar -czf {out_dir.name}.tar.gz {out_dir.name}

Send the whole archive rather than individual files.

## What is in here, and what is not

The queries in `workload.sql` keep their structure and lose their values. A
query your application ran as:

    SELECT * FROM orders WHERE customer_email = 'ada@example.com'

is recorded as:

    SELECT * FROM orders WHERE customer_email = $1

The structure is what a sharding scheme is designed from. The values are not
needed for it, so they were removed before anything was written here. No table
of yours was read to produce this bundle, and it contains no rows from your
data.

Every file is readable only by you (mode `0600`), and this directory carries a
`.gitignore` so it cannot be committed to a repository by accident. Delete it
once the sharding scheme is planned.
"""


def _write_text(path: Path, text: str) -> None:
    """Write via temp file and rename, owner-only from the moment it exists."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, indent=2, default=str) + "\n")


def _num(value) -> str:
    """A count, in full. ``:g`` gave 6 significant digits and switched to
    scientific notation at 1e6, so every hot statement in a multi-day capture
    was written as ``calls=3e+06``."""
    return "0" if value is None else f"{value:.0f}"


def _ms(value) -> str:
    """A duration in milliseconds, which is a float and keeps its decimals."""
    return "0" if value is None else f"{value:.3f}"
