"""The distribution tier: pg_stats readings for declared columns, a catalog
read and never a table scan. Values stay raw until the bundle writer hashes them."""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from psycopg2.extras import RealDictCursor

from planetscale_discovery.workload.valuehash import refusal_reason

# The consumer keys its contract arms off the source discriminator:
# this tier is the HEAD (the MCV sample), never the complete GROUP BY.
DISTRIBUTIONS_SCHEMA_VERSION = 1
DISTRIBUTIONS_SOURCE = "pg_stats_mcv"

Hit = Tuple[str, str, str, str]  # schema, table, column, type


def resolve_targets(
    schema_analysis: Dict[str, Any],
    by_column: Sequence[str],
    explicit: Sequence[Any],
    target_schemas: Optional[Sequence[str]],
) -> Tuple[List[Dict[str, str]], List[str]]:
    """Resolve the declaration against the schema inventory. Names fold case,
    an exact match wins, and a declaration matching nothing is a named note."""
    inventory: List[Hit] = [
        (t["schema_name"], t["table_name"], c["column_name"], c.get("data_type") or "")
        for t in schema_analysis.get("table_analysis") or []
        if not t["schema_name"].startswith("pg_")
        and (not target_schemas or t["schema_name"] in target_schemas)
        for c in t.get("columns") or []
    ]
    targets: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    notes: List[str] = []

    def add(hit: Hit, declared: str) -> None:
        # The first declaration wins: a blanket-and-explicit overlap reads once.
        target = dict(zip(("schema", "table", "column", "type"), hit))
        targets.setdefault(hit[:3], {**target, "declared": declared})

    for name in by_column:
        hits = [h for h in inventory if h[2].lower() == str(name).lower()]
        if not hits:
            notes.append(
                f"distributions: by_column {name!r} matched no column in the "
                "captured schemas — skipped, named"
            )
        for hit in hits:
            add(hit, "by_column")
    for entry in explicit:
        # Two spellings: {table, column[, schema]} or "[schema.]table.column".
        spec = entry if isinstance(entry, dict) else {}
        if isinstance(entry, str) and entry.count(".") in (1, 2):
            parts = entry.split(".")
            spec = dict(zip(("column", "table", "schema"), reversed(parts)))
        schema, table, column = (
            str(spec.get(k) or "") for k in ("schema", "table", "column")
        )
        if not (table and column):
            notes.append(
                f"distributions: explicit entry {entry!r} is neither "
                "'[schema.]table.column' nor a mapping with table and column — skipped, named"
            )
            continue
        found = [
            h
            for h in inventory
            if (h[1].lower(), h[2].lower()) == (table.lower(), column.lower())
            and schema in ("", h[0])
        ]
        found = [h for h in found if h[1:3] == (table, column)] or found
        if len(found) != 1:
            notes.append(
                f"distributions: explicit entry {entry!r} resolved to {len(found)} "
                "in-scope columns, not one — skipped, named"
            )
            continue
        add(found[0], "explicit")
    return [targets[k] for k in sorted(targets)], notes


# One catalog query for the tier. A parent's whole-tree row wins, since that
# is what a query against the parent reads.
_PG_STATS_SQL = """
SELECT DISTINCT ON (s.schemaname, s.tablename, s.attname)
       s.schemaname, s.tablename, s.attname,
       s.n_distinct, s.null_frac,
       s.most_common_freqs, s.most_common_vals::text::text[],
       st.last_analyze, st.last_autoanalyze
FROM pg_stats s
LEFT JOIN pg_stat_all_tables st
  ON st.schemaname = s.schemaname AND st.relname = s.tablename
WHERE (s.schemaname, s.tablename, s.attname) IN %s
ORDER BY s.schemaname, s.tablename, s.attname, s.inherited DESC
"""


_READABLE_SQL = """
SELECT has_column_privilege(r.oid, %s, 'SELECT') AS granted,
       row_security_active(r.oid) AS row_security
FROM (SELECT format('%%I.%%I', %s, %s)::regclass::oid AS oid) r
"""


def unreadable_targets(
    connection, targets: Sequence[Dict[str, str]]
) -> List[Tuple[Dict[str, str], str]]:
    """The targets pg_stats hides from this role, each with the reason."""
    unreadable = []
    connection.rollback()
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        for target in targets:
            cur.execute(
                _READABLE_SQL, (target["column"], target["schema"], target["table"])
            )
            row = cur.fetchone()
            if not row["granted"]:
                unreadable.append((target, "no SELECT grant on the column"))
            elif row["row_security"]:
                unreadable.append((target, "row-level security applies to this role"))
    return unreadable


def read_column(
    stats_rows: Dict[Tuple[str, str, str], Dict[str, Any]],
    target: Dict[str, str],
) -> Dict[str, Any]:
    """One column's pg_stats row as a distribution entry, values RAW for
    finalize to hash. A missing row raises: unknown, never zero."""
    if reason := refusal_reason(target["type"]):
        raise ValueError(reason)
    row = stats_rows.get((target["schema"], target["table"], target["column"]))
    if row is None:
        # pg_stats also hides columns this role cannot SELECT.
        raise LookupError("no pg_stats row: never ANALYZEd, or not readable")
    freqs = [float(f) for f in (row.get("most_common_freqs") or [])]
    vals = row.get("most_common_vals") or []
    if len(vals) != len(freqs):
        # pg_stats keeps the pair in lockstep; a mismatch fails the column by name.
        raise ValueError(
            f"pg_stats row's most_common_vals ({len(vals)}) and "
            f"most_common_freqs ({len(freqs)}) disagree in length"
        )
    # The ::text::text[] cast delivers every value as text, which the hashing reads.
    mcv = [{"value": str(v), "frequency": f} for v, f in zip(vals, freqs)]
    # Deterministic order: frequency desc, then the value asc.
    mcv.sort(key=lambda v: (-v["frequency"], v["value"]))
    return {
        # The type rides along: the finalize-time hashing is type-directed.
        "type": target["type"],
        "table": f"{target['schema']}.{target['table']}",
        "column": target["column"],
        "declared": target["declared"],
        "n_distinct": row.get("n_distinct"),
        "null_frac": row.get("null_frac"),
        "mcv": mcv,
        # float4 frequencies can sum past 1; coverage is a fraction of rows.
        "mcv_coverage": min(1.0, sum(freqs)) if freqs else None,
        # Timestamps; the store serializes them as text.
        "last_analyze": row.get("last_analyze"),
        "last_autoanalyze": row.get("last_autoanalyze"),
    }


def collect_distributions(
    connection,
    schema_analysis: Dict[str, Any],
    by_column: Sequence[str],
    explicit: Sequence[Any],
    target_schemas: Optional[Sequence[str]],
    logger,
) -> Dict[str, Any]:
    """The declared columns' pg_stats readings: one catalog query, and
    every column not read is a named note."""
    targets, notes = resolve_targets(
        schema_analysis, by_column, explicit, target_schemas
    )
    stats_rows: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    if targets:
        keys = tuple((t["schema"], t["table"], t["column"]) for t in targets)
        # The snapshot's queries share this connection; a failed one aborted
        # its transaction.
        connection.rollback()
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_PG_STATS_SQL, (keys,))
            stats_rows = {
                (r["schemaname"], r["tablename"], r["attname"]): r
                for r in cur.fetchall()
            }

    columns: List[Dict[str, Any]] = []
    for target in targets:
        try:
            columns.append(read_column(stats_rows, target))
        except Exception as exc:  # omitted AND named — never zeroed
            scope = f"{target['schema']}.{target['table']}.{target['column']}"
            notes.append(f"distributions: {scope} not measured: {exc}")
            logger.warning(notes[-1])
    logger.info(
        f"distributions: {len(columns)} column(s) read from pg_stats, "
        f"{len(notes)} named omission(s)"
    )
    return {
        "columns_declared": {"by_column": list(by_column), "explicit": list(explicit)},
        "columns": columns,
        "notes": notes,
    }
