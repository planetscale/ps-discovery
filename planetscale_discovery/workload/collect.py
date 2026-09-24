"""Read one point-in-time snapshot of workload counters.

Two sources: pg_stat_statements for statement text and counters, and
pg_stat_user_tables / pg_stat_user_indexes for relation counters, which need no
extension. The statement column list is built from information_schema.columns
because the columns move across majors, so one code path covers 12 through 18.
"""

import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer
from planetscale_discovery.common.sanitize import redact_sql, statement_kind
from planetscale_discovery.common.utils import generate_timestamp

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"  # usable, but something is worth reporting
STATUS_FAILED = "failed"  # no usable data

# Canonical counter name -> the column names that have carried it.
COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "userid": ("userid",),
    "dbid": ("dbid",),
    "queryid": ("queryid",),
    "toplevel": ("toplevel",),
    "query": ("query",),
    "calls": ("calls",),
    "rows": ("rows",),
    "total_exec_time": ("total_exec_time", "total_time"),
    "min_exec_time": ("min_exec_time", "min_time"),
    "max_exec_time": ("max_exec_time", "max_time"),
    "mean_exec_time": ("mean_exec_time", "mean_time"),
    "stddev_exec_time": ("stddev_exec_time", "stddev_time"),
    "plans": ("plans",),
    "total_plan_time": ("total_plan_time",),
    "shared_blks_hit": ("shared_blks_hit",),
    "shared_blks_read": ("shared_blks_read",),
    "shared_blks_dirtied": ("shared_blks_dirtied",),
    "shared_blks_written": ("shared_blks_written",),
    "local_blks_hit": ("local_blks_hit",),
    "local_blks_read": ("local_blks_read",),
    "temp_blks_read": ("temp_blks_read",),
    "temp_blks_written": ("temp_blks_written",),
    "shared_blk_read_time": ("shared_blk_read_time", "blk_read_time"),
    "shared_blk_write_time": ("shared_blk_write_time", "blk_write_time"),
    "wal_records": ("wal_records",),
    "wal_fpi": ("wal_fpi",),
    "wal_bytes": ("wal_bytes",),
    "stats_since": ("stats_since",),
}

# Counters that accumulate, so they are differenced at merge time.
TABLE_COUNTERS = (
    "seq_scan",
    "seq_tup_read",
    "idx_scan",
    "idx_tup_fetch",
    "n_tup_ins",
    "n_tup_upd",
    "n_tup_del",
)

# The subset that is a write, for the peak write rate.
WRITE_COUNTERS = ("n_tup_ins", "n_tup_upd", "n_tup_del")

# Sizes are gauges, not counters: they describe the present, so they are carried
# rather than differenced. Rows are not bytes, and a shard is sized in bytes.
TABLE_SIZE_GAUGES = ("total_size_bytes", "table_size_bytes", "indexes_size_bytes")

TABLE_GAUGES = (
    "n_live_tup",
    "n_dead_tup",
    "n_mod_since_analyze",
) + TABLE_SIZE_GAUGES

INDEX_COUNTERS = ("idx_scan", "idx_tup_read", "idx_tup_fetch")

# An index routinely exceeds its table and moves with the shard, so its size is
# a figure of its own rather than a share of the table's.
INDEX_GAUGES = ("size_bytes",)

# Excluded rather than allowlisted, matching what the schema analyzer does.
EXCLUDED_SCHEMAS = ("information_schema", "pg_catalog", "pg_toast", "__neki")

SERVER_SQL = (
    "SELECT current_database() AS database,"
    " current_setting('server_version_num') AS version_num,"
    " pg_is_in_recovery() AS is_replica,"
    " (SELECT oid FROM pg_database WHERE datname = current_database())"
    "   AS database_oid"
)

CLOCK_SQL = "SELECT clock_timestamp() AS captured_at_server"

PGSS_COLUMNS_SQL = (
    "SELECT column_name FROM information_schema.columns"
    " WHERE table_name = 'pg_stat_statements'"
)

PGSS_MAX_SQL = "SELECT current_setting('pg_stat_statements.max', true) AS m"

TABLE_SQL = """
SELECT s.schemaname, s.relname,
       s.seq_scan, s.seq_tup_read, s.idx_scan, s.idx_tup_fetch,
       s.n_tup_ins, s.n_tup_upd, s.n_tup_del,
       s.n_live_tup, s.n_dead_tup, s.n_mod_since_analyze,
       s.last_analyze, s.last_autoanalyze,
       c.reltuples,
       pg_total_relation_size(c.oid) AS total_size_bytes,
       pg_relation_size(c.oid) AS table_size_bytes,
       pg_indexes_size(c.oid) AS indexes_size_bytes,
       clock_timestamp() AS captured_at_server
FROM pg_stat_user_tables s
JOIN pg_class c ON c.oid = s.relid
WHERE NOT (s.schemaname = ANY(%(excluded)s))
  AND (%(schemas)s::text[] IS NULL OR s.schemaname = ANY(%(schemas)s))
ORDER BY s.schemaname, s.relname
"""

# indkey[0] is the leading column, which is what decides whether a lookup through this.
INDEX_SQL = """
SELECT s.schemaname, s.relname, s.indexrelname,
       s.idx_scan, s.idx_tup_read, s.idx_tup_fetch,
       i.indisunique, i.indisprimary,
       a.attname AS leading_column,
       pg_relation_size(s.indexrelid) AS size_bytes,
       pg_get_indexdef(s.indexrelid) AS definition
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
LEFT JOIN pg_attribute a
       ON a.attrelid = s.relid AND a.attnum = i.indkey[0] AND i.indkey[0] <> 0
WHERE NOT (s.schemaname = ANY(%(excluded)s))
  AND (%(schemas)s::text[] IS NULL OR s.schemaname = ANY(%(schemas)s))
ORDER BY s.schemaname, s.relname, s.indexrelname
"""


# How many of the most common frequencies to keep. Skew lives in the head of the
# distribution: the top value's share is what decides whether a key spreads, and
# the full list runs to default_statistics_target (100) entries per column.
MCF_KEPT = 10

# Frequencies, never values. most_common_vals and histogram_bounds hold sampled
# rows from the customer's tables and are never read. most_common_freqs is an
# array of floats describing how often the values in that list occur, which is
# what says whether a candidate shard key spreads evenly or piles onto one shard.
# reltuples and the analyze timestamps ride along: n_distinct decodes against
# a real row count, and a stale reading shows. in_index_or_constraint marks index keys and PK/FK/unique columns.
COLUMN_SQL = """
SELECT s.schemaname, s.tablename, s.attname,
       s.n_distinct, s.null_frac, s.correlation,
       s.most_common_freqs,
       GREATEST(c.reltuples, 0) AS row_count,
       st.last_analyze, st.last_autoanalyze,
       (icol.attname IS NOT NULL) AS in_index_or_constraint
FROM pg_stats s
JOIN pg_namespace n ON n.nspname = s.schemaname
JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = s.tablename
LEFT JOIN pg_stat_all_tables st ON st.relid = c.oid
LEFT JOIN (
    SELECT n.nspname AS schemaname, c.relname AS tablename, a.attname
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
    WHERE c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
    UNION
    SELECT n.nspname, c.relname, a.attname
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY(con.conkey)
    WHERE con.contype IN ('p', 'f', 'u') AND a.attnum > 0 AND NOT a.attisdropped
) icol ON icol.schemaname = s.schemaname
      AND icol.tablename = s.tablename
      AND icol.attname = s.attname
WHERE NOT (s.schemaname = ANY(%(excluded)s))
  AND (%(schemas)s::text[] IS NULL OR s.schemaname = ANY(%(schemas)s))
  -- An inheritance parent emits a second, tree-wide row per column; both of
  -- the consumer's collectors filter it, and an arbitrary winner is worse.
  AND NOT s.inherited
ORDER BY s.schemaname, s.tablename, s.attname
"""


def _decode_ndv(
    n_distinct: Optional[float], row_count: Optional[float]
) -> Optional[float]:
    """PostgreSQL's signed n_distinct as a distinct count, floored at 1;
    unknown (0) or a fraction on zero rows decodes to absent instead."""
    if n_distinct is None or row_count is None or n_distinct == 0:
        return None
    if n_distinct > 0:
        return max(float(n_distinct), 1.0)
    if row_count <= 0:
        return None
    return max(-float(n_distinct) * row_count, 1.0)


class WorkloadCollector(DatabaseAnalyzer):
    """One snapshot of statement and relation counters. Never raises."""

    def __init__(
        self,
        connection,
        config: Optional[Dict[str, Any]] = None,
        logger=None,
        schemas: Optional[Sequence[str]] = None,
        row_limit: int = 20000,
        statement_text_max_chars: int = 8192,
    ):
        super().__init__(connection, config, logger)
        self._lost_during: Optional[str] = None
        # None means every non-system schema.
        self.schemas = list(schemas) if schemas else None
        self.row_limit = row_limit
        self.statement_text_max_chars = statement_text_max_chars

    def analyze(self) -> Dict[str, Any]:
        """Alias for collect(), satisfying the DatabaseAnalyzer contract."""
        return self.collect()

    def collect(self) -> Dict[str, Any]:
        started = time.monotonic()
        snapshot: Dict[str, Any] = {
            # 2 added relation and index size gauges.
            "schema_version": 2,
            "status": STATUS_FAILED,
            "captured_at_client": generate_timestamp(),
            "captured_at_server": None,
            "schemas": self.schemas,
            "server": {},
            "statements": [],
            "tables": [],
            "indexes": [],
            "warnings": [],
            "notes": [],
            "pgss": {},
            "columns": [],
        }

        self._lost_during = None
        reads = self._read_all(snapshot)
        if self._lost_during:
            snapshot["warnings"].append(
                {
                    "code": "connection_lost",
                    "detail": (
                        f"the connection closed during the {self._lost_during} "
                        "read, so the reads after it were skipped. The snapshot "
                        "keeps everything read before it"
                    ),
                }
            )
        self._process_all(snapshot, reads)

        snapshot["duration_ms"] = int((time.monotonic() - started) * 1000)
        if snapshot["captured_at_server"] is None:
            snapshot["status"] = STATUS_FAILED
        elif snapshot["warnings"]:
            snapshot["status"] = STATUS_DEGRADED
        else:
            snapshot["status"] = STATUS_OK
        return snapshot

    def _read(
        self,
        what: str,
        sql: str,
        params: Optional[Dict[str, Any]] = None,
        optional: bool = False,
    ) -> Tuple[Optional[List[Any]], Optional[str]]:
        if self._lost_during:
            return None, (
                f"not read, because the connection closed during the "
                f"{self._lost_during} read"
            )
        self.logger.info(f"reading {what}")
        started = time.monotonic()
        try:
            rows = self._rows(sql, params)
        except Exception as e:
            if self.connection.closed:
                self._lost_during = what
            quiet = optional and not self._lost_during
            (self.logger.info if quiet else self.logger.warning)(
                f"reading {what}: failed after "
                f"{time.monotonic() - started:.1f}s: {e}"
            )
            return None, str(e)
        self.logger.info(
            f"reading {what}: {len(rows)} rows in {time.monotonic() - started:.1f}s"
        )
        return rows, None

    def _read_all(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        scope = {"excluded": list(EXCLUDED_SCHEMAS), "schemas": self.schemas}
        reads: Dict[str, Any] = {"clock": None}

        rows, _ = self._read("server identity", SERVER_SQL)
        reads["server"] = dict(rows[0]) if rows else None

        reads["tables"], error = self._read("table statistics", TABLE_SQL, scope)
        reads["indexes"] = None
        if error is None:
            reads["indexes"], error = self._read("index statistics", INDEX_SQL, scope)
        if error is not None:
            snapshot["warnings"].append(
                {"code": "relation_read_failed", "detail": error}
            )
            reads["tables"] = reads["indexes"] = None
        elif not reads["tables"]:
            rows, _ = self._read("server clock", CLOCK_SQL)
            if rows:
                reads["clock"] = _text(dict(rows[0])["captured_at_server"])

        reads["statements"] = self._read_statements(snapshot)

        reads["columns"], error = self._read("column statistics", COLUMN_SQL, scope)
        if error is not None:
            snapshot["warnings"].append(
                {"code": "column_stats_read_failed", "detail": error}
            )

        reads["pgss"] = self._read_pgss_info(reads["server"])
        return reads

    def _process_all(self, snapshot: Dict[str, Any], reads: Dict[str, Any]) -> None:
        snapshot["server"] = self._server_row(reads["server"])
        self._process_relations(snapshot, reads)
        self._process_statements(snapshot, reads["statements"])
        self._process_columns(snapshot, reads["columns"])
        snapshot["pgss"] = reads["pgss"]

    def _server_row(self, row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Enough to refuse to merge snapshots from two different servers."""
        if not row:
            return {}
        return {
            "database": row.get("database"),
            "database_oid": row.get("database_oid"),
            "version_num": _int(row.get("version_num")),
            "is_replica": bool(row.get("is_replica")),
        }

    def _process_relations(
        self, snapshot: Dict[str, Any], reads: Dict[str, Any]
    ) -> None:
        tables, indexes = reads["tables"], reads["indexes"]
        if tables is None or indexes is None:
            return

        if tables:
            snapshot["captured_at_server"] = _text(
                dict(tables[0]).get("captured_at_server")
            )
        else:
            snapshot["captured_at_server"] = reads["clock"]
            snapshot["notes"].append(
                {
                    "code": "no_tables_in_scope",
                    "detail": "no user tables were found in the configured schemas",
                }
            )

        with logged_step(
            self.logger, f"processing {len(tables)} tables and {len(indexes)} indexes"
        ):
            snapshot["tables"] = [self._table_row(dict(r)) for r in tables]
            snapshot["indexes"] = [self._index_row(dict(r)) for r in indexes]

        # A NULL scan count means the role cannot read another backend's statistics.
        blind = [t for t in snapshot["tables"] if t["counters"]["idx_scan"] is None]
        if blind:
            snapshot["warnings"].append(
                {
                    "code": "statistics_not_readable",
                    "detail": (
                        f"{len(blind)} table(s) returned no scan counts; the role "
                        "likely lacks pg_monitor. Absent counts are not zero."
                    ),
                }
            )

    def _read_statements(self, snapshot: Dict[str, Any]) -> Optional[List[Any]]:
        rows, error = self._read("pg_stat_statements columns", PGSS_COLUMNS_SQL)
        present = [str(dict(r)["column_name"]) for r in rows or []]
        if not present:
            if error is not None and self._lost_during:
                snapshot["warnings"].append(
                    {"code": "statement_read_failed", "detail": error}
                )
                return None
            snapshot["notes"].append(
                {
                    "code": "pg_stat_statements_unavailable",
                    "detail": (
                        "pg_stat_statements is not readable, so no statement "
                        "text or call counts were collected"
                    ),
                }
            )
            return None

        select, canonical, missing = build_statement_query(
            present, self.row_limit, self._pgss_relation()
        )
        snapshot["statement_columns"] = canonical
        snapshot["statement_columns_missing"] = missing
        if missing:
            snapshot["notes"].append(
                {
                    "code": "columns_unavailable",
                    "detail": (
                        f"this server lacks {len(missing)} of the counters we "
                        "collect; recorded so they are not read as zero"
                    ),
                }
            )

        rows, error = self._read("pg_stat_statements", select)
        if error is not None:
            snapshot["warnings"].append(
                {"code": "statement_read_failed", "detail": error}
            )
        return rows

    def _process_statements(
        self, snapshot: Dict[str, Any], rows: Optional[List[Any]]
    ) -> None:
        if rows is None:
            return
        with logged_step(self.logger, f"redacting {len(rows)} statements"):
            snapshot["statements"] = [self._statement_row(dict(r)) for r in rows]
        if len(rows) >= self.row_limit:
            snapshot["warnings"].append(
                {
                    "code": "row_cap_reached",
                    "detail": (
                        f"{len(rows)} rows hit the {self.row_limit} cap, so the "
                        "tail of the workload is missing from this snapshot"
                    ),
                }
            )
        masked = sum(1 for s in snapshot["statements"] if s.get("text_unavailable"))
        if masked:
            snapshot["warnings"].append(
                {
                    "code": "statement_text_masked",
                    "detail": (
                        f"{masked} of {len(rows)} entries have masked text, so "
                        "this role cannot see them. The sample is biased toward "
                        "statements run by this role."
                    ),
                }
            )

    def _process_columns(
        self, snapshot: Dict[str, Any], rows: Optional[List[Any]]
    ) -> None:
        """Per-column distribution, which is what says whether a key spreads.

        Taken every snapshot rather than once, so a key that is even today and
        lopsided next week is visible as a change rather than a single reading.
        """
        if rows is None:
            return
        with logged_step(self.logger, f"processing {len(rows)} column statistics"):
            snapshot["columns"] = [self._column_row(dict(r)) for r in rows]

    def _column_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """One column's distribution. Frequencies only, never the values."""
        freqs = list(row.get("most_common_freqs") or [])
        n_distinct = _number(row.get("n_distinct"))
        return {
            "table": f"{row['schemaname']}.{row['tablename']}",
            "column": row["attname"],
            "n_distinct": n_distinct,
            "null_frac": _number(row.get("null_frac")),
            "correlation": _number(row.get("correlation")),
            # Decoded here, where n_distinct meets a real row count; the
            # cardinality writer consumes ndv without decoding again.
            "ndv": _decode_ndv(n_distinct, _number(row.get("row_count"))),
            "in_index_or_constraint": bool(row.get("in_index_or_constraint")),
            "last_analyze": _text(row.get("last_analyze")),
            "last_autoanalyze": _text(row.get("last_autoanalyze")),
            # The head of the distribution, and how much of the table the whole
            # list accounts for. The tail is what is dropped, not the signal.
            "top_frequencies": [_number(f) for f in freqs[:MCF_KEPT]],
            "mcv_coverage": _number(sum(freqs)) if freqs else None,
        }

    def _read_pgss_info(self, server: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """The extension's capacity and how much it has discarded.

        ``dealloc`` counts statements evicted because the table filled, and it
        is the only evidence that the capture is missing entries. The view that
        holds it arrived in PostgreSQL 14, so an older server reports ``None``:
        unknown rather than zero, because zero would claim nothing was lost.
        """
        info: Dict[str, Any] = {"max": None, "dealloc": None}
        rows, _ = self._read("pg_stat_statements.max", PGSS_MAX_SQL)
        if rows:
            info["max"] = _int(dict(rows[0]).get("m"))
        version = _int((server or {}).get("version_num"))
        if version is not None and version < 140000:
            return info
        relation = self._pgss_relation(f"{PGSS_VIEW}_info")
        rows, _ = self._read(
            f"{PGSS_VIEW}_info", f"SELECT dealloc FROM {relation}", optional=True
        )
        if rows:
            info["dealloc"] = _number(dict(rows[0]).get("dealloc"))
        return info

    def _pgss_schema(self) -> Optional[str]:
        try:
            rows = self._rows(pgss_relation_query())
        except Exception:
            return None
        return dict(rows[0]).get("nspname") if rows else None

    def _pgss_relation(self, name: Optional[str] = None) -> str:
        """A pg_stat_statements relation, schema-qualified.

        CREATE EXTENSION puts pg_stat_statements wherever it is told, and that
        is public by default. This tool pins search_path to pg_catalog so a
        planted table cannot shadow a catalog read, which also means an
        unqualified name never resolves to a view in public. So the name is
        read from the catalog rather than resolved, falling back to the bare
        name when the catalog cannot answer.
        """
        name = name or PGSS_VIEW
        schema = self._pgss_schema()
        return quote_qualified(str(schema), name) if schema else name

    def _statement_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        raw = row.pop("query", None)
        # pg_stat_statements masks text the role may not see.
        masked = isinstance(raw, str) and "insufficient privilege" in raw.lower()
        redacted = None if masked else redact_sql(raw)
        truncated = bool(redacted and len(redacted) > self.statement_text_max_chars)
        if truncated and redacted:
            redacted = redacted[: self.statement_text_max_chars]

        counters = {name: _number(row.get(name)) for name in COUNTER_COLUMNS}
        return {
            "userid": _int(row.get("userid")),
            "dbid": _int(row.get("dbid")),
            "queryid": _text(row.get("queryid")),
            "toplevel": bool(row.get("toplevel", True)),
            "query": redacted,
            "query_kind": None if masked else statement_kind(raw),
            "text_unavailable": masked,
            "text_truncated": truncated,
            "stats_since": _text(row.get("stats_since")),
            # Outside `counters`, because these are lifetime extremes. Merging
            # differences every counter, and a subtracted maximum is nonsense.
            "min_exec_time": _number(row.get("min_exec_time")),
            "max_exec_time": _number(row.get("max_exec_time")),
            "counters": counters,
        }

    def _table_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        reltuples = row.get("reltuples")
        return {
            "table": f"{row['schemaname']}.{row['relname']}",
            "counters": {name: _number(row.get(name)) for name in TABLE_COUNTERS},
            "gauges": {name: _number(row.get(name)) for name in TABLE_GAUGES},
            # -1 means never analyzed, which is a different claim from 0 rows.
            "estimated_rows": (
                None if reltuples is None or reltuples < 0 else _number(reltuples)
            ),
            "never_analyzed": reltuples is not None and reltuples < 0,
            "last_analyze": _text(row.get("last_analyze")),
            "last_autoanalyze": _text(row.get("last_autoanalyze")),
        }

    def _index_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "table": f"{row['schemaname']}.{row['relname']}",
            "index": row["indexrelname"],
            "counters": {name: _number(row.get(name)) for name in INDEX_COUNTERS},
            "gauges": {name: _number(row.get(name)) for name in INDEX_GAUGES},
            "leading_column": row.get("leading_column"),
            "is_unique": bool(row.get("indisunique")),
            "is_primary": bool(row.get("indisprimary")),
            "definition": row.get("definition"),
        }

    def _rows(self, sql: str, params: Optional[Dict[str, Any]] = None):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, params) if params else cursor.execute(sql)
                return cursor.fetchall()
        finally:
            end_transaction(self.connection)


def end_transaction(connection) -> None:
    try:
        connection.rollback()
    except Exception:
        pass


@contextmanager
def logged_step(logger, what: str) -> Iterator[None]:
    logger.info(what)
    started = time.monotonic()
    yield
    logger.info(f"{what}: done in {time.monotonic() - started:.1f}s")


# Counters differenced at merge time.
COUNTER_COLUMNS = tuple(
    name
    for name in COLUMN_ALIASES
    if name
    not in {
        "userid",
        "dbid",
        "queryid",
        "toplevel",
        "query",
        "stats_since",
        "min_exec_time",
        "max_exec_time",
        "mean_exec_time",
        "stddev_exec_time",
    }
)


PGSS_VIEW = "pg_stat_statements"


def pgss_relation_query() -> str:
    """Where the extension actually lives. Its schema is not fixed."""
    return (
        "SELECT n.nspname FROM pg_extension e"
        " JOIN pg_namespace n ON n.oid = e.extnamespace"
        f" WHERE e.extname = '{PGSS_VIEW}'"
    )


def quote_qualified(schema: str, relation: str) -> str:
    """Quote both parts, doubling any embedded quote."""
    schema = schema.replace(chr(34), chr(34) * 2)
    relation = relation.replace(chr(34), chr(34) * 2)
    return f'"{schema}"."{relation}"'


def build_statement_query(
    present: Sequence[str],
    row_limit: int = 20000,
    relation: str = PGSS_VIEW,
) -> Tuple[str, List[str], List[str]]:
    """Build the pg_stat_statements SELECT for the columns this server has."""
    available = set(present)
    selected: List[str] = []
    canonical: List[str] = []
    missing: List[str] = []

    # Names come from COLUMN_ALIASES, never from the catalog, so nothing the
    # server reports can reach the SQL text.
    for name, candidates in COLUMN_ALIASES.items():
        for candidate in candidates:
            if candidate in available:
                if candidate == name:
                    selected.append(candidate)
                else:
                    selected.append(f"{candidate} AS {name}")
                canonical.append(name)
                break
        else:
            selected.append(f"NULL AS {name}")
            missing.append(name)

    sql = (
        "SELECT " + ", ".join(selected) + f" FROM {relation}"
        " WHERE dbid = (SELECT oid FROM pg_database"
        " WHERE datname = current_database())"
        f" ORDER BY calls DESC LIMIT {int(row_limit)}"
    )
    return sql, canonical, missing


def _number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value):
    return None if value is None else str(value)
