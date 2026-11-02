"""
Citus support for PostgreSQL discovery.

A database with the citus extension holds regular tables next to tables that
Citus manages. For a Citus table, the coordinator stores an empty shell; the
rows, column statistics and activity counters are stored on the shard
placements across the nodes. This module detects the extension, checks that
every placement can be read, reads the shards' catalogs through
run_command_on_all_nodes(), and combines the values per logical table.

A failure that would leave a Citus table described by its shell raises
CitusClusterError, which stops the discovery. A partial report is never written.
"""

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

CITUS_EXTENSIONS = ("citus", "citus_columnar")

REQUIRED_FUNCTIONS = (
    "citus_version",
    "column_to_column_name",
    "relation_is_a_known_shard",
    "run_command_on_all_nodes",
    "shard_name",
)

REQUIRED_TABLES = (
    "pg_dist_colocation",
    "pg_dist_local_group",
    "pg_dist_node",
    "pg_dist_object",
    "pg_dist_partition",
    "pg_dist_placement",
    "pg_dist_shard",
)

OPTIONAL_TABLES = ("pg_dist_schema",)

# Needed only to read the nodes, which happens only when a Citus table exists.
REMOTE_FUNCTIONS = ("run_command_on_all_nodes",)

# run_command_on_all_nodes() holds every node's result in memory on the
# coordinator, so the shards are read in batches.
SHARD_BATCH_SIZE = 1000

ACTIVE_SHARD_STATE = 1

# The node id run_command_on_all_nodes() reports for the current node when
# pg_dist_node does not list it.
UNLISTED_NODE_ID = 0

# Problems listed in one error message before the rest are only counted.
MAX_LISTED_PROBLEMS = 20

READ_COUNTERS = ("seq_scan", "seq_tup_read", "idx_scan", "idx_tup_fetch")
WRITE_COUNTERS = ("n_tup_ins", "n_tup_upd", "n_tup_del", "n_tup_hot_upd")
PLACEMENT_COUNTERS = ("n_live_tup", "n_dead_tup", "n_mod_since_analyze")
RUN_COUNTERS = (
    "vacuum_count",
    "autovacuum_count",
    "analyze_count",
    "autoanalyze_count",
)
EVENT_TIMES = ("last_vacuum", "last_autovacuum", "last_analyze", "last_autoanalyze")
INDEX_READ_COUNTERS = ("idx_scan", "idx_tup_read", "idx_tup_fetch")

TABLE_COUNTER_KEYS = (
    READ_COUNTERS + WRITE_COUNTERS + PLACEMENT_COUNTERS + EVENT_TIMES + RUN_COUNTERS
)

# The relations whose shards store data: tables and partitioned tables. The
# classification and every remote command share this filter, so a Citus table
# is never expected whose shard the remote commands do not read. A foreign
# table added to the metadata stores its rows on its own server, so it has no
# shard values to read, and it is reported as a foreign table, as on regular
# PostgreSQL.
TABLE_RELKINDS = "('r', 'p')"

# The remote commands run in a session whose search_path this tool does not
# control, so every relation and function in them is qualified.
#
# Each command looks up its batch's shards by schema and name through pg_class's
# (relname, relnamespace) index, so a node's work follows the batch size rather
# than the number of relations on the node. A view is filtered once in its own
# CTE: pg_stat_user_tables groups every relation, so a join condition is not
# pushed into it, and a filter repeated per shard would grow with the square of
# the batch.
_SHARDS = (
    "WITH shard AS MATERIALIZED ("
    " SELECT c.oid, n.nspname, c.relname, c.relkind, c.reltuples"
    " FROM ROWS FROM (pg_catalog.unnest(%L::pg_catalog.name[]),"
    " pg_catalog.unnest(%L::pg_catalog.name[])) AS w(nspname, relname)"
    " JOIN pg_catalog.pg_namespace n ON n.nspname = w.nspname"
    " JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = w.relname"
    f" WHERE c.relkind IN {TABLE_RELKINDS}"
    " AND pg_catalog.relation_is_a_known_shard(c.oid))"
)

TABLE_COMMAND = (
    _SHARDS + ", stat AS MATERIALIZED ("
    " SELECT * FROM pg_catalog.pg_stat_user_tables"
    " WHERE relid = ANY (ARRAY(SELECT oid FROM shard)))"
    " SELECT COALESCE(pg_catalog.json_agg(t), '[]')::text FROM ("
    " SELECT c.nspname AS schema_name, c.relname, c.relkind::text AS relkind,"
    " c.reltuples,"
    " pg_catalog.pg_relation_size(c.oid) AS table_size_bytes,"
    " pg_catalog.pg_total_relation_size(c.oid) AS total_size_bytes,"
    " s.seq_scan, s.seq_tup_read, s.idx_scan, s.idx_tup_fetch,"
    " s.n_tup_ins, s.n_tup_upd, s.n_tup_del, s.n_tup_hot_upd,"
    " s.n_live_tup, s.n_dead_tup, s.n_mod_since_analyze,"
    " s.last_vacuum, s.last_autovacuum, s.last_analyze, s.last_autoanalyze,"
    " s.vacuum_count, s.autovacuum_count, s.analyze_count, s.autoanalyze_count"
    " FROM shard c LEFT JOIN stat s ON s.relid = c.oid"
    ") t"
)

# most_common_vals, most_common_freqs and histogram_bounds are never selected:
# they hold values from the customer's data.
COLUMN_COMMAND = (
    _SHARDS + ", stat AS MATERIALIZED ("
    " SELECT schemaname, tablename, attname, inherited, null_frac, n_distinct,"
    " correlation FROM pg_catalog.pg_stats"
    " WHERE tablename = ANY (ARRAY(SELECT relname FROM shard))"
    " AND schemaname = ANY (ARRAY(SELECT nspname FROM shard)))"
    " SELECT COALESCE(pg_catalog.json_agg(t), '[]')::text FROM ("
    " SELECT s.schemaname AS schema_name, s.tablename AS relname, s.attname,"
    " s.inherited, s.null_frac, s.n_distinct, s.correlation"
    " FROM shard c JOIN stat s"
    " ON s.schemaname = c.nspname AND s.tablename = c.relname"
    ") t"
)

INDEX_COMMAND = (
    _SHARDS + " SELECT COALESCE(pg_catalog.json_agg(t), '[]')::text FROM ("
    " SELECT n.nspname AS schema_name, i.relname AS index_relname,"
    " i.reltuples, s.idx_scan, s.idx_tup_read, s.idx_tup_fetch,"
    " pg_catalog.pg_relation_size(i.oid) AS index_size_bytes"
    " FROM shard c"
    " JOIN pg_catalog.pg_index x ON x.indrelid = c.oid"
    " JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid"
    " JOIN pg_catalog.pg_namespace n ON n.oid = i.relnamespace"
    " LEFT JOIN pg_catalog.pg_stat_user_indexes s ON s.indexrelid = i.oid"
    ") t"
)

DATABASE_SIZE_COMMAND = (
    "SELECT pg_catalog.pg_database_size(pg_catalog.current_database())::text"
)

CONNECTIVITY_COMMAND = "SELECT 1"

REMOTE_COMMANDS = (
    TABLE_COMMAND,
    COLUMN_COMMAND,
    INDEX_COMMAND,
    DATABASE_SIZE_COMMAND,
    CONNECTIVITY_COMMAND,
)


class CitusClusterError(ConnectionError):
    """A Citus node, shard or privilege that discovery needs is unavailable.

    It subclasses ConnectionError so that the CLI treats it like a coordinator
    that cannot be reached: the discovery stops, and no database result is
    written.
    """

    def __init__(self, summary: str, problems: Optional[List[Dict[str, str]]] = None):
        self.summary = summary
        self.problems = problems or []
        super().__init__(self._render())

    def _render(self) -> str:
        lines = [self.summary]
        for problem in self.problems[:MAX_LISTED_PROBLEMS]:
            line = f"  - {problem['subject']}: {problem['cause']}"
            if problem.get("detail"):
                line += f" ({problem['detail']})"
            lines.append(line)
        hidden = len(self.problems) - MAX_LISTED_PROBLEMS
        if hidden > 0:
            lines.append(f"  - and {hidden} more")
        return "\n".join(lines)


@contextmanager
def citus_errors(summary: str, subject: str, cause: str = "failed") -> Iterator[None]:
    """Raise any failure inside the block as a CitusClusterError, which stops the
    discovery. A CitusClusterError raised inside passes through unchanged."""
    try:
        yield
    except CitusClusterError:
        raise
    except Exception as e:
        raise CitusClusterError(
            summary, [{"subject": subject, "cause": cause, "detail": str(e)}]
        ) from e


# --- SQL fragments used by the analyzers ------------------------------------


def _extension_member(catalog: str, oid_expr: str) -> str:
    # oid_expr must be qualified: inside this subquery a bare "oid" resolves
    # to pg_extension.oid.
    return (
        "EXISTS (SELECT 1 FROM pg_catalog.pg_depend citus_dep"
        " JOIN pg_catalog.pg_extension citus_ext"
        " ON citus_ext.oid = citus_dep.refobjid"
        f" WHERE citus_dep.classid = '{catalog}'::pg_catalog.regclass"
        f" AND citus_dep.objid = {oid_expr}"
        " AND citus_dep.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass"
        " AND citus_dep.deptype = 'e'"
        " AND citus_ext.extname IN ('citus', 'citus_columnar'))"
    )


def _not_known_shard(oid_expr: str) -> str:
    return f"NOT pg_catalog.relation_is_a_known_shard({oid_expr})"


def _conditions(parts: Iterable[str]) -> str:
    return "".join(f" AND {part}" for part in parts)


def where(conditions: str) -> str:
    """Turn a filter's " AND ..." conditions into a WHERE clause.

    For a query that has no WHERE clause of its own. Returns '' without Citus,
    so the query's text stays exactly as it is on a server without Citus.
    """
    return "WHERE " + conditions.removeprefix(" AND ") if conditions else ""


def relation_filter(
    ctx: Optional["CitusContext"], rel_oid: str, ns_oid: Optional[str] = None
) -> str:
    """Exclude shard relations and relations that belong to the extension."""
    if ctx is None:
        return ""
    parts = [
        _not_known_shard(rel_oid),
        "NOT " + _extension_member("pg_catalog.pg_class", rel_oid),
    ]
    if ns_oid:
        parts.append("NOT " + _extension_member("pg_catalog.pg_namespace", ns_oid))
    return _conditions(parts)


def namespace_filter(ctx: Optional["CitusContext"], ns_oid: str) -> str:
    """Exclude the schemas that belong to the extension (citus, citus_internal)."""
    if ctx is None:
        return ""
    return _conditions(["NOT " + _extension_member("pg_catalog.pg_namespace", ns_oid)])


def namespace_name_filter(ctx: Optional["CitusContext"], nspname_expr: str) -> str:
    """namespace_filter() for a view that exposes the schema only by name."""
    if ctx is None:
        return ""
    return _conditions(
        [
            "NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace citus_ns"
            f" WHERE citus_ns.nspname = {nspname_expr} AND "
            + _extension_member("pg_catalog.pg_namespace", "citus_ns.oid")
            + ")"
        ]
    )


def function_filter(ctx: Optional["CitusContext"], proc_oid: str, ns_oid: str) -> str:
    """Exclude the functions that belong to the extension."""
    if ctx is None:
        return ""
    return _conditions(
        [
            "NOT " + _extension_member("pg_catalog.pg_proc", proc_oid),
            "NOT " + _extension_member("pg_catalog.pg_namespace", ns_oid),
        ]
    )


def type_filter(
    ctx: Optional["CitusContext"], type_oid: str, ns_oid: str, typrelid: str
) -> str:
    """Exclude the extension's types and the row types of shards.

    pg_type is not hidden the way pg_class is, so the row type of a shard
    placement on this node is visible.
    """
    if ctx is None:
        return ""
    return _conditions(
        [
            "NOT " + _extension_member("pg_catalog.pg_type", type_oid),
            "NOT " + _extension_member("pg_catalog.pg_namespace", ns_oid),
            f"({typrelid} = 0 OR ({_not_known_shard(typrelid)} AND NOT "
            + _extension_member("pg_catalog.pg_class", typrelid)
            + "))",
        ]
    )


def event_trigger_filter(ctx: Optional["CitusContext"], evt_oid: str) -> str:
    """Exclude the event triggers that belong to the extension."""
    if ctx is None:
        return ""
    return _conditions(
        ["NOT " + _extension_member("pg_catalog.pg_event_trigger", evt_oid)]
    )


# --- Detection and metadata --------------------------------------------------


@dataclass
class CitusTable:
    """A table recorded in pg_dist_partition."""

    oid: int
    schema_name: str
    table_name: str
    table_type: str
    partmethod: str
    repmodel: str
    colocation_id: int
    distribution_column: Optional[str]
    autoconverted: Optional[bool]
    shard_count: int
    access_method: Optional[str]

    def describe(self) -> Dict[str, Any]:
        return {
            "table_type": self.table_type,
            "distribution_column": self.distribution_column,
            "colocation_id": self.colocation_id,
            "shard_count": self.shard_count,
            "partmethod": self.partmethod,
            "replication_model": self.repmodel,
            "autoconverted": self.autoconverted,
            "access_method": self.access_method,
        }


@dataclass
class ShardStatistics:
    """Values combined per logical table and index from every shard placement."""

    tables: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    indexes: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    placements: List[Dict[str, Any]] = field(default_factory=list)
    # By node group: shard relations a move left behind, before cleanup drops them.
    orphaned_size_bytes: Dict[int, int] = field(default_factory=dict)


@dataclass
class CitusContext:
    """What discovery knows about the Citus cluster it is connected to."""

    extension_version: str
    citus_version: Optional[str] = None
    local_group_id: Optional[int] = None
    current_database: Optional[str] = None
    has_pg_dist_schema: bool = False
    has_autoconverted: bool = False
    has_connection_error_warning: bool = False
    use_secondary_nodes: Optional[str] = None
    cluster_name: Optional[str] = None
    # How run_command_on_all_nodes() reaches the current node when pg_dist_node
    # does not list it: citus.local_hostname and port, reported as node id 0.
    local_hostname: Optional[str] = None
    port: Optional[int] = None
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    tables: Dict[int, CitusTable] = field(default_factory=dict)
    shards: Dict[int, Tuple[int, str]] = field(default_factory=dict)
    placements: List[Dict[str, Any]] = field(default_factory=list)
    _shard_statistics: Optional[ShardStatistics] = None
    _tables_by_name: Optional[Dict[Tuple[str, str], CitusTable]] = None
    _shell_size_bytes: Optional[int] = None

    @property
    def node_role(self) -> str:
        return "coordinator" if self.local_group_id == 0 else "worker"

    def table_by_name(self, schema_name: str, table_name: str) -> Optional[CitusTable]:
        """For rows that carry no oid. Look a row up in self.tables by its oid
        when it has one: a name can change between two queries."""
        if self._tables_by_name is None:
            self._tables_by_name = {
                (t.schema_name, t.table_name): t for t in self.tables.values()
            }
        return self._tables_by_name.get((schema_name, table_name))

    def readable_node_ids(self) -> List[int]:
        """The nodes run_command_on_all_nodes() reads, from its definition."""
        return [
            n["nodeid"]
            for n in self.nodes
            if n.get("isactive")
            and n.get("noderole") == "primary"
            and (self.cluster_name is None or n.get("nodecluster") == self.cluster_name)
        ]

    def expected_node_ids(self) -> List[int]:
        """Every node id run_command_on_all_nodes() can report: the nodes read in
        the preflight, and the current node when pg_dist_node does not list it."""
        expected = self.readable_node_ids()
        if not self.coordinator_in_metadata():
            expected.append(UNLISTED_NODE_ID)
        return expected

    def readable_groups(self) -> set:
        readable = set(self.readable_node_ids())
        groups = {n["groupid"] for n in self.nodes if n["nodeid"] in readable}
        # The function adds the current node when the metadata does not hold it.
        if self.local_group_id is not None and not self.coordinator_in_metadata():
            groups.add(self.local_group_id)
        return groups

    def coordinator_in_metadata(self) -> bool:
        return any(n["groupid"] == self.local_group_id for n in self.nodes)

    def coordinator_label(self) -> str:
        """The current node, named as run_command_on_all_nodes() reaches it when
        pg_dist_node does not list it."""
        return f"{self.local_hostname}:{self.port}"

    def node_label(self, nodeid: Optional[int]) -> str:
        for node in self.nodes:
            if node["nodeid"] == nodeid:
                return f"{node['nodename']}:{node['nodeport']}"
        if nodeid == UNLISTED_NODE_ID and not self.coordinator_in_metadata():
            return self.coordinator_label()
        return f"node {nodeid}"

    def table_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for table in self.tables.values():
            counts[table.table_type] = counts.get(table.table_type, 0) + 1
        return counts

    def connection_info(self) -> Dict[str, Any]:
        """What describes the connection itself, for connection_info.citus.

        Counts of the cluster's tables and nodes are reported once, in the
        citus module's summary, so that two copies cannot disagree.
        """
        return {
            "extension_version": self.extension_version,
            "citus_version": self.citus_version,
            "node_role": self.node_role,
        }

    def shard_statistics(self, connection) -> ShardStatistics:
        """Read the shards on the first call, and return the cached result."""
        if self._shard_statistics is None:
            self._shard_statistics = collect_shard_statistics(connection, self)
        return self._shard_statistics


def fetchall(connection, sql: str, params: Optional[Sequence[Any]] = None):
    """Every row of a query, as dicts."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def _fetchone(connection, sql: str, params: Optional[Sequence[Any]] = None):
    rows = fetchall(connection, sql, params)
    return rows[0] if rows else None


def detect_citus(connection) -> Optional[CitusContext]:
    """Return a context when the citus extension is installed, else None."""
    row = _fetchone(
        connection,
        "SELECT extversion FROM pg_catalog.pg_extension WHERE extname = 'citus'",
    )
    if not row:
        return None
    return CitusContext(extension_version=row["extversion"])


def preflight(connection, ctx: CitusContext) -> None:
    """Confirm that every Citus node, shard and privilege is available.

    Raises CitusClusterError naming each problem. On success, the context holds
    the cluster's nodes, the Citus tables and their placements.

    The checks that only reading the nodes needs run only when the database
    holds a Citus table. Without one, no node is contacted, so an unused
    extension cannot stop the run.
    """
    with citus_errors(
        "Citus metadata could not be read on the coordinator.",
        "coordinator",
        cause="query failed",
    ):
        _hide_shards(connection)
        remote_problems = _check_privileges(connection, ctx)
        _read_settings(connection, ctx)
        _read_metadata(connection, ctx)
    if not ctx.tables:
        return
    if remote_problems:
        raise CitusClusterError(
            "The discovery role cannot read the Citus shards.", remote_problems
        )
    _check_secondary_nodes(ctx)
    _check_metadata_sync(ctx)
    _check_placements(ctx)
    run_on_all_nodes(connection, ctx, CONNECTIVITY_COMMAND)


def _hide_shards(connection) -> None:
    """Hide shards from pg_class for the rest of this session.

    A role or database default for citus.show_shards_for_app_name_prefixes
    would otherwise list every shard placement on the coordinator as a table
    in each analyzer that relies on Citus hiding them. The setting applies to
    this connection only.
    """
    fetchall(
        connection,
        "SELECT pg_catalog.set_config("
        "'citus.show_shards_for_app_name_prefixes', '', false) AS hidden",
    )


def _check_privileges(connection, ctx: CitusContext) -> List[Dict[str, str]]:
    """Raise for what reading the coordinator's metadata needs.

    Returns the problems with run_command_on_all_nodes(), which only reading
    the nodes needs, so that the caller raises them only when there is a Citus
    table to read.
    """
    problems = []
    remote_problems = []
    # Quoted, so that a GRANT for a role such as app-reader or AppUser can be run.
    role = _fetchone(connection, "SELECT pg_catalog.quote_ident(current_user) AS role")[
        "role"
    ]

    functions = fetchall(
        connection,
        """
        SELECT p.proname,
               pg_catalog.pg_get_function_identity_arguments(p.oid) AS args,
               pg_catalog.has_function_privilege(p.oid, 'EXECUTE') AS can_execute
        FROM pg_catalog.pg_proc p
        WHERE p.pronamespace = 'pg_catalog'::pg_catalog.regnamespace
          AND p.proname = ANY(%s)
        """,
        (list(REQUIRED_FUNCTIONS),),
    )

    def bucket(name: str) -> List[Dict[str, str]]:
        return remote_problems if name in REMOTE_FUNCTIONS else problems

    found = {f["proname"] for f in functions}
    for name in REQUIRED_FUNCTIONS:
        if name not in found:
            bucket(name).append(
                {
                    "subject": f"pg_catalog.{name}()",
                    "cause": "function not found; this Citus version is not supported",
                }
            )
    for function in functions:
        if not function["can_execute"]:
            bucket(function["proname"]).append(
                {
                    "subject": f"pg_catalog.{function['proname']}()",
                    "cause": "missing EXECUTE privilege",
                    "detail": f"GRANT EXECUTE ON FUNCTION pg_catalog.{function['proname']}"
                    f"({function['args']}) TO {role}",
                }
            )
        if function["proname"] == "run_command_on_all_nodes":
            if "give_warning_for_connection_errors" in (function["args"] or ""):
                ctx.has_connection_error_warning = True

    tables = fetchall(
        connection,
        """
        SELECT t.name,
               pg_catalog.to_regclass('pg_catalog.' || t.name) IS NOT NULL AS present,
               CASE WHEN pg_catalog.to_regclass('pg_catalog.' || t.name) IS NULL THEN NULL
                    ELSE pg_catalog.has_table_privilege(
                        pg_catalog.to_regclass('pg_catalog.' || t.name), 'SELECT')
               END AS can_select
        FROM pg_catalog.unnest(%s::text[]) AS t(name)
        """,
        (list(REQUIRED_TABLES + OPTIONAL_TABLES),),
    )
    for table in tables:
        if not table["present"]:
            if table["name"] in REQUIRED_TABLES:
                problems.append(
                    {
                        "subject": f"pg_catalog.{table['name']}",
                        "cause": "table not found; this Citus version is not supported",
                    }
                )
            continue
        if table["name"] == "pg_dist_schema":
            ctx.has_pg_dist_schema = True
        if not table["can_select"]:
            problems.append(
                {
                    "subject": f"pg_catalog.{table['name']}",
                    "cause": "missing SELECT privilege",
                    "detail": f"GRANT SELECT ON pg_catalog.{table['name']} TO {role}",
                }
            )

    if problems:
        raise CitusClusterError(
            "The discovery role cannot read the Citus metadata it needs.", problems
        )
    return remote_problems


def _read_settings(connection, ctx: CitusContext) -> None:
    row = _fetchone(
        connection,
        """
        SELECT (SELECT groupid FROM pg_catalog.pg_dist_local_group) AS local_group_id,
               pg_catalog.citus_version() AS citus_version,
               pg_catalog.current_database() AS current_database,
               pg_catalog.current_setting('citus.use_secondary_nodes', true)
                   AS use_secondary_nodes,
               pg_catalog.current_setting('citus.cluster_name', true) AS cluster_name,
               pg_catalog.current_setting('citus.local_hostname', true)
                   AS local_hostname,
               pg_catalog.current_setting('port') AS port,
               EXISTS (
                   SELECT 1 FROM pg_catalog.pg_attribute
                   WHERE attrelid = 'pg_catalog.pg_dist_partition'::pg_catalog.regclass
                     AND attname = 'autoconverted' AND NOT attisdropped
               ) AS has_autoconverted
        """,
    )
    ctx.local_group_id = row["local_group_id"]
    ctx.citus_version = row["citus_version"]
    ctx.current_database = row["current_database"]
    ctx.use_secondary_nodes = row["use_secondary_nodes"]
    ctx.cluster_name = row["cluster_name"]
    ctx.local_hostname = row.get("local_hostname")
    ctx.port = int(row["port"]) if row.get("port") else None
    ctx.has_autoconverted = bool(row["has_autoconverted"])

    # Stops the run even when pg_dist_partition is empty: a worker whose
    # metadata is not synced has an empty pg_dist_partition while it holds
    # shards, which relation_is_a_known_shard() cannot then recognize.
    if ctx.local_group_id != 0:
        raise CitusClusterError(
            "Discovery is connected to a Citus worker. Connect to the coordinator.",
            [
                {
                    "subject": "connection",
                    "cause": f"pg_dist_local_group.groupid is {ctx.local_group_id}, "
                    "and only the coordinator has group 0",
                }
            ],
        )


def _check_secondary_nodes(ctx: CitusContext) -> None:
    if ctx.use_secondary_nodes not in (None, "never"):
        raise CitusClusterError(
            "Discovery reads only primary nodes.",
            [
                {
                    "subject": "citus.use_secondary_nodes",
                    "cause": f"set to '{ctx.use_secondary_nodes}' for this session; "
                    "set it to 'never' for the discovery role",
                }
            ],
        )


def _classification_sql(ctx: CitusContext) -> str:
    # The CASE expression is the one Citus uses for citus_table_type in its
    # citus_tables and citus_shards views.
    schema_case = (
        "WHEN p.colocationid IN (SELECT colocationid FROM pg_catalog.pg_dist_schema)"
        " THEN 'schema' "
        if ctx.has_pg_dist_schema
        else ""
    )
    autoconverted = "p.autoconverted" if ctx.has_autoconverted else "NULL::boolean"
    return f"""
        SELECT p.logicalrelid::pg_catalog.oid AS table_oid,
               n.nspname AS schema_name,
               c.relname AS table_name,
               CASE {schema_case}
                    WHEN p.partkey IS NOT NULL THEN 'distributed'
                    WHEN p.repmodel = 't' THEN 'reference'
                    WHEN p.colocationid = 0 THEN 'local'
                    ELSE 'distributed'
               END AS table_type,
               p.partmethod::text AS partmethod,
               p.repmodel::text AS repmodel,
               p.colocationid AS colocation_id,
               pg_catalog.column_to_column_name(p.logicalrelid, p.partkey)
                   AS distribution_column,
               {autoconverted} AS autoconverted,
               (SELECT pg_catalog.count(*) FROM pg_catalog.pg_dist_shard s
                WHERE s.logicalrelid = p.logicalrelid) AS shard_count,
               a.amname AS access_method
        FROM pg_catalog.pg_dist_partition p
        JOIN pg_catalog.pg_class c ON c.oid = p.logicalrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_catalog.pg_am a ON a.oid = c.relam
        WHERE c.relkind IN {TABLE_RELKINDS}
          AND NOT {_extension_member('pg_catalog.pg_class', 'p.logicalrelid')}
        ORDER BY n.nspname, c.relname
    """


def _read_metadata(connection, ctx: CitusContext) -> None:
    ctx.nodes = fetchall(
        connection,
        """
        SELECT nodeid, groupid, nodename, nodeport, noderole::text AS noderole,
               nodecluster::text AS nodecluster, isactive, shouldhaveshards, hasmetadata
        FROM pg_catalog.pg_dist_node
        ORDER BY nodeid
        """,
    )
    ctx.tables = {
        row["table_oid"]: CitusTable(
            oid=row["table_oid"],
            schema_name=row["schema_name"],
            table_name=row["table_name"],
            table_type=row["table_type"],
            partmethod=row["partmethod"],
            repmodel=row["repmodel"],
            colocation_id=row["colocation_id"],
            distribution_column=row["distribution_column"],
            autoconverted=row["autoconverted"],
            shard_count=int(row["shard_count"]),
            access_method=row["access_method"],
        )
        for row in fetchall(connection, _classification_sql(ctx))
    }
    ctx._tables_by_name = None
    ctx.shards = {
        row["shardid"]: (row["table_oid"], unqualified(row["shard_relname"]))
        for row in fetchall(
            connection,
            """
            SELECT s.shardid, s.logicalrelid::pg_catalog.oid AS table_oid,
                   pg_catalog.shard_name(s.logicalrelid, s.shardid) AS shard_relname
            FROM pg_catalog.pg_dist_shard s
            """,
        )
        if row["table_oid"] in ctx.tables
    }
    ctx.placements = [
        row
        for row in fetchall(
            connection,
            """
            SELECT shardid, groupid, shardstate
            FROM pg_catalog.pg_dist_placement
            ORDER BY shardid, groupid
            """,
        )
        if row["shardid"] in ctx.shards
    ]


def _check_metadata_sync(ctx: CitusContext) -> None:
    """Stop when a worker that holds placements has no Citus metadata.

    relation_is_a_known_shard() reads the node's own metadata, so such a node
    returns none of its shards, and the shard read would stop later naming
    shards instead of the node. This follows an upgrade from Citus 10 that was
    not finalized.
    """
    holding = {
        p["groupid"] for p in ctx.placements if p["shardstate"] == ACTIVE_SHARD_STATE
    }
    readable = set(ctx.readable_node_ids())
    problems = []
    for node in ctx.nodes:
        if (
            node["nodeid"] in readable
            and node["groupid"] != ctx.local_group_id
            and node["groupid"] in holding
            and node.get("hasmetadata") is False
        ):
            problems.append(
                {
                    "subject": f"{node['nodename']}:{node['nodeport']}",
                    "cause": "Citus metadata is not synced to the node",
                    "detail": (
                        "on the coordinator, run SELECT "
                        "citus_finalize_upgrade_to_citus11(); after an upgrade from "
                        "Citus 10, or SELECT start_metadata_sync_to_node("
                        f"'{node['nodename']}', {node['nodeport']});"
                    ),
                }
            )
    if problems:
        raise CitusClusterError("A Citus node cannot recognize its shards.", problems)


def _check_placements(ctx: CitusContext) -> None:
    readable = ctx.readable_groups()
    active: Dict[int, List[int]] = {}
    for placement in ctx.placements:
        if placement["shardstate"] == ACTIVE_SHARD_STATE:
            active.setdefault(placement["shardid"], []).append(placement["groupid"])

    groups_to_nodes: Dict[int, List[str]] = {}
    for node in ctx.nodes:
        state = "active" if node.get("isactive") else "inactive"
        groups_to_nodes.setdefault(node["groupid"], []).append(
            f"{node['nodename']}:{node['nodeport']} ({node['noderole']}, {state})"
        )

    problems = []
    for shardid, (table_oid, _) in sorted(ctx.shards.items()):
        groups = active.get(shardid, [])
        if any(group in readable for group in groups):
            continue
        table = ctx.tables[table_oid]
        holders = [label for g in groups for label in groups_to_nodes.get(g, [])]
        problems.append(
            {
                "subject": f"shard {shardid} of {table.schema_name}.{table.table_name}",
                "cause": "no active placement on a node that discovery can read",
                "detail": "placements: " + (", ".join(holders) or "none"),
            }
        )
    if problems:
        raise CitusClusterError(
            "Some shards cannot be read. Every Citus node that holds a "
            "placement must be active and primary.",
            problems,
        )


def unqualified(name: str) -> str:
    """The last identifier of a possibly qualified, possibly quoted name.

    shard_name() qualifies the name with its schema outside the search path,
    and quotes either part when needed.
    """
    parts = []
    current = []
    quoted = False
    i = 0
    while i < len(name):
        ch = name[i]
        if quoted:
            if ch == '"':
                if i + 1 < len(name) and name[i + 1] == '"':
                    current.append('"')
                    i += 1
                else:
                    quoted = False
            else:
                current.append(ch)
        elif ch == '"':
            quoted = True
        elif ch == ".":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts[-1]


# --- Running commands on every node ------------------------------------------


def run_on_all_nodes(
    connection,
    ctx: CitusContext,
    command: str,
    shards: Optional[Sequence[Tuple[str, str]]] = None,
) -> Dict[int, str]:
    """Run a constant command on every readable node; return each node's result.

    A batch's shard schemas and names are passed as query parameters and placed
    into the command on the server with format(), so no command is built in
    Python. Any node that fails raises CitusClusterError.
    """
    if shards is None:
        command_sql = "%s"
        params: List[Any] = [command]
    else:
        command_sql = (
            "pg_catalog.format(%s, %s::pg_catalog.name[]::text,"
            " %s::pg_catalog.name[]::text)"
        )
        params = [command, [s for s, _ in shards], [r for _, r in shards]]
    warning_arg = (
        ", give_warning_for_connection_errors := true"
        if ctx.has_connection_error_warning
        else ""
    )
    sql = (
        "SELECT nodeid, success, result FROM pg_catalog.run_command_on_all_nodes("
        f"{command_sql}{warning_arg})"
    )
    try:
        rows = fetchall(connection, sql, params)
    except Exception as e:
        raise CitusClusterError(
            "A Citus node could not be read.",
            [
                {
                    "subject": "run_command_on_all_nodes",
                    "cause": "failed",
                    "detail": str(e),
                }
            ],
        ) from e

    returned = {row["nodeid"] for row in rows}
    # The function reads pg_dist_node on every call, and the preflight read it
    # once. A node it did not see would be read as the coordinator's group.
    unexpected = sorted(returned - set(ctx.expected_node_ids()))
    if unexpected:
        raise CitusClusterError(
            "The Citus cluster's nodes changed during discovery. Run discovery "
            "again.",
            [
                {
                    "subject": f"node {nodeid}",
                    "cause": "not in pg_dist_node when discovery started",
                }
                for nodeid in unexpected
            ],
        )

    problems = []
    results: Dict[int, str] = {}
    for row in rows:
        if not row["success"]:
            problems.append(
                {
                    "subject": ctx.node_label(row["nodeid"]),
                    "cause": _failure_cause(row["result"]),
                    "detail": (row["result"] or "").strip()[:300],
                }
            )
        else:
            results[row["nodeid"]] = row["result"]
    for nodeid in ctx.readable_node_ids():
        if nodeid not in returned:
            problems.append(
                {"subject": ctx.node_label(nodeid), "cause": "returned no result"}
            )
    if problems:
        raise CitusClusterError(
            "A Citus node could not be read. Every node must be up and must "
            "accept the discovery role.",
            problems,
        )
    return results


def _failure_cause(message: Optional[str]) -> str:
    text = (message or "").lower()
    if "password authentication failed" in text or "no pg_hba.conf entry" in text:
        return "the node rejected the discovery role"
    if "permission denied" in text:
        return "permission denied on the node"
    if (
        "connection" in text
        or "could not connect" in text
        or "timeout" in text
        or "timed out" in text
    ):
        return "unreachable"
    return "command failed"


# --- Reading and combining shard statistics ----------------------------------


def _batches(ctx: CitusContext) -> List[List[int]]:
    batches: List[List[int]] = []
    current: List[int] = []
    by_table: Dict[int, List[int]] = {}
    for shardid, (table_oid, _) in sorted(ctx.shards.items()):
        by_table.setdefault(table_oid, []).append(shardid)
    for table_oid in sorted(by_table):
        for shardid in by_table[table_oid]:
            current.append(shardid)
            if len(current) >= SHARD_BATCH_SIZE:
                batches.append(current)
                current = []
    if current:
        batches.append(current)
    return batches


def _rows(results: Dict[int, str]) -> Iterable[Tuple[int, Dict[str, Any]]]:
    for nodeid, text in results.items():
        for row in json.loads(text or "[]"):
            yield nodeid, row


def collect_shard_statistics(connection, ctx: CitusContext) -> ShardStatistics:
    """Read every shard placement's catalogs and combine them per table."""
    with citus_errors("Citus shard statistics could not be read.", "shard read"):
        return _collect_shard_statistics(connection, ctx)


def _collect_shard_statistics(connection, ctx: CitusContext) -> ShardStatistics:
    active_groups: Dict[int, set] = {}
    for placement in ctx.placements:
        if placement["shardstate"] == ACTIVE_SHARD_STATE:
            active_groups.setdefault(placement["shardid"], set()).add(
                placement["groupid"]
            )

    # (schema, shard relname) -> shardid, so a remote row maps to its shard.
    shard_by_name = {
        (ctx.tables[table_oid].schema_name, relname): shardid
        for shardid, (table_oid, relname) in ctx.shards.items()
    }

    indexes = _read_index_names(connection, ctx)
    index_shard_by_name: Dict[Tuple[str, str], Tuple[int, int]] = {}
    group_by_node = {node["nodeid"]: node["groupid"] for node in ctx.nodes}

    def group_of(nodeid: int) -> Optional[int]:
        if nodeid in group_by_node:
            return group_by_node[nodeid]
        # run_command_on_all_nodes() adds the current node when the metadata
        # does not hold it. Any other node is rejected in run_on_all_nodes().
        return ctx.local_group_id if nodeid == UNLISTED_NODE_ID else None

    table_rows: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}
    column_rows: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    index_rows: Dict[Tuple[int, int], List[Tuple[int, Dict[str, Any]]]] = {}
    orphaned: Dict[int, int] = {}

    def placement_of(nodeid: int, row: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        shardid = shard_by_name.get((row["schema_name"], row["relname"]))
        if shardid is None:
            return None
        group = group_of(nodeid)
        # A relation left on a node after a shard move has no active placement.
        if group not in active_groups.get(shardid, set()):
            return None
        return shardid, group

    for batch in _batches(ctx):
        for shard_index in _read_index_shard_names(connection, batch):
            # An index created since the index list was read is not reported,
            # as if it had been created after the run.
            if shard_index["index_oid"] not in indexes:
                continue
            index_shard_by_name[
                (
                    indexes[shard_index["index_oid"]]["schema_name"],
                    unqualified(shard_index["shard_index_name"]),
                )
            ] = (shard_index["index_oid"], shard_index["shardid"])

        names = [
            (ctx.tables[ctx.shards[shardid][0]].schema_name, ctx.shards[shardid][1])
            for shardid in batch
        ]
        for nodeid, row in _rows(
            run_on_all_nodes(connection, ctx, TABLE_COMMAND, names)
        ):
            where = placement_of(nodeid, row)
            if where:
                table_rows.setdefault(where[0], []).append((where[1], row))
            elif (row["schema_name"], row["relname"]) in shard_by_name:
                # A shard relation that a move left on the node, until Citus's
                # deferred cleanup drops it. pg_database_size() still counts it.
                group = group_of(nodeid)
                if group is not None:
                    orphaned[group] = orphaned.get(group, 0) + (
                        row.get("total_size_bytes") or 0
                    )

        for nodeid, row in _rows(
            run_on_all_nodes(connection, ctx, COLUMN_COMMAND, names)
        ):
            where = placement_of(nodeid, row)
            if where:
                column_rows.setdefault(where, []).append(row)

        for nodeid, row in _rows(
            run_on_all_nodes(connection, ctx, INDEX_COMMAND, names)
        ):
            key = index_shard_by_name.get((row["schema_name"], row["index_relname"]))
            if key is None:
                continue
            index_oid, shardid = key
            group = group_of(nodeid)
            if group in active_groups.get(shardid, set()):
                index_rows.setdefault((index_oid, shardid), []).append((group, row))

    # Grouped once, so that combining stays linear in the number of shards. A
    # cluster with schema-based sharding can hold 100k tables, each with its own
    # shard.
    shards_by_table: Dict[int, List[int]] = {}
    for shardid, (table_oid, _) in ctx.shards.items():
        shards_by_table.setdefault(table_oid, []).append(shardid)
    inactive_by_shard: Dict[int, int] = {}
    for placement in ctx.placements:
        if placement["shardstate"] != ACTIVE_SHARD_STATE:
            shardid = placement["shardid"]
            inactive_by_shard[shardid] = inactive_by_shard.get(shardid, 0) + 1
    rows_by_index: Dict[int, Dict[int, List[Tuple[int, Dict[str, Any]]]]] = {}
    for (index_oid, shardid), rows in index_rows.items():
        rows_by_index.setdefault(index_oid, {})[shardid] = rows

    stats = ShardStatistics(orphaned_size_bytes=orphaned)
    missing = []
    for table_oid, table in ctx.tables.items():
        by_shard: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}
        for shardid in shards_by_table.get(table_oid, []):
            rows = table_rows.get(shardid)
            if not rows:
                missing.append(
                    {
                        "subject": f"shard {shardid} of {table.schema_name}.{table.table_name}",
                        "cause": "not found on any node that holds an active placement",
                    }
                )
                continue
            by_shard[shardid] = rows
        if missing:
            continue
        inactive = sum(inactive_by_shard.get(shardid, 0) for shardid in by_shard)
        combined = combine_table(table, by_shard)
        combined["inactive_placements"] = inactive
        statistics, detail = combine_columns(
            table, combined.pop("_chosen"), column_rows
        )
        combined["statistics"] = statistics
        combined["columns"] = detail
        stats.tables[table_oid] = combined
        for shardid, rows in by_shard.items():
            for group, row in rows:
                stats.placements.append(
                    {
                        "table_oid": table_oid,
                        "shardid": shardid,
                        "groupid": group,
                        "total_size_bytes": row.get("total_size_bytes") or 0,
                    }
                )
    if missing:
        raise CitusClusterError("Some shards could not be read.", missing)

    unmatched = []
    for index_oid, info in indexes.items():
        table_stats = stats.tables.get(info["table_oid"])
        if table_stats is None:
            continue
        found = rows_by_index.get(index_oid, {})
        table_shards = shards_by_table.get(info["table_oid"], [])
        invalid = info.get("is_valid") is False
        missing_shards = set(table_shards) - set(found)
        # A failed CREATE INDEX CONCURRENTLY leaves the coordinator's index
        # invalid, with shard indexes on only some shards. Those can be valid on
        # their nodes and take scans, so the shards found are reported, flagged,
        # instead of stopping a read-only run until a DDL.
        if missing_shards and not invalid:
            unmatched.append(
                (index_oid, _unmatched_index(info, len(missing_shards), len(found)))
            )
            continue
        combined = combine_index(found, table_stats["chosen_groups"])
        if invalid and not found:
            # No shard index exists, so none was scanned.
            combined.update({key: 0 for key in INDEX_READ_COUNTERS})
        stats.indexes[index_oid] = dict(info, **combined, shard_count=len(table_shards))
    # An index missing on some shards would report a partial size, and its scan
    # counts would drop it from unused_indexes, so the run stops instead.
    if unmatched:
        # An index dropped since the index list was read has no shard index
        # left, which the remedies below would misdiagnose.
        current = _read_index_names(connection, ctx)
        dropped = [
            {
                "subject": problem["subject"],
                "cause": "dropped during discovery",
            }
            for index_oid, problem in unmatched
            if index_oid not in current
        ]
        if dropped:
            raise CitusClusterError(
                "The Citus indexes changed during discovery. Run discovery again.",
                dropped,
            )
        raise CitusClusterError(
            "Some shard indexes could not be matched to their index.",
            [problem for _, problem in unmatched],
        )
    return stats


NOT_PROPAGATED_REMEDY = (
    "the index was not created on those shards, typically because it was "
    "created with citus.enable_ddl_propagation off; recreate it with DDL "
    "propagation on, or drop it"
)


def _unmatched_index(info: Dict[str, Any], missing: int, found: int) -> Dict[str, str]:
    # Only an index that PostgreSQL created on a partition from its parent's
    # index is named automatically, which is what the Citus function repairs.
    if info.get("is_partition_index"):
        remedy = (
            "a partition's shard index can lack its shard id after automatic "
            "naming; run SELECT fix_all_partition_shard_index_names(); on the "
            "coordinator. If the index is still not found, " + NOT_PROPAGATED_REMEDY
        )
    else:
        remedy = NOT_PROPAGATED_REMEDY
    return {
        "subject": f"index {info['schema_name']}.{info['index_name']}",
        "cause": f"not found on {missing} of {missing + found} shards",
        "detail": remedy,
    }


def invalid_index_flag(info: Dict[str, Any]) -> Dict[str, Any]:
    """The citus keys that flag an invalid index, whose values can cover only the
    shards its build reached."""
    if info.get("is_valid") is not False:
        return {}
    return {"reason": "invalid_index", "shard_count": info["shard_count"]}


def _read_index_names(connection, ctx: CitusContext) -> Dict[int, Dict[str, Any]]:
    if not ctx.tables:
        return {}
    rows = fetchall(
        connection,
        """
        SELECT x.indexrelid::pg_catalog.oid AS index_oid,
               x.indrelid::pg_catalog.oid AS table_oid,
               n.nspname AS schema_name,
               i.relname AS index_name,
               x.indisvalid AS is_valid,
               i.relispartition AS is_partition_index
        FROM pg_catalog.pg_index x
        JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = i.relnamespace
        WHERE x.indrelid = ANY(%s::pg_catalog.oid[])
        """,
        (list(ctx.tables),),
    )
    return {row["index_oid"]: row for row in rows}


def _read_index_shard_names(connection, shard_ids: List[int]) -> List[Dict[str, Any]]:
    return fetchall(
        connection,
        """
        SELECT x.indexrelid::pg_catalog.oid AS index_oid, s.shardid,
               pg_catalog.shard_name(x.indexrelid, s.shardid) AS shard_index_name
        FROM pg_catalog.pg_index x
        JOIN pg_catalog.pg_dist_shard s ON s.logicalrelid::pg_catalog.oid = x.indrelid
        WHERE s.shardid = ANY(%s::pg_catalog.int8[])
        """,
        ([int(s) for s in shard_ids],),
    )


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse a timestamptz as json_agg() writes it."""
    if value is None or isinstance(value, datetime):
        return value
    text = str(value)
    # Python 3.10 accepts only 3 or 6 fractional digits, and PostgreSQL drops
    # trailing zeros.
    match = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$", text)
    if match:
        head, fraction, tail = match.groups()
        text = head + ("." + (fraction + "000000")[:6] if fraction else "") + tail
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _sum(values: Iterable[Any]) -> Optional[int]:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _analyzed_at(row: Dict[str, Any]) -> Optional[datetime]:
    times = [parse_timestamp(row.get(k)) for k in ("last_analyze", "last_autoanalyze")]
    times = [t for t in times if t is not None]
    return max(times) if times else None


def _chosen(rows: List[Tuple[int, Dict[str, Any]]]) -> Tuple[int, Dict[str, Any]]:
    """The placement analyzed most recently, then the lowest group id."""

    def key(item):
        group, row = item
        at = _analyzed_at(row)
        return (0 if at else 1, -(at.timestamp()) if at else 0, group)

    return sorted(rows, key=key)[0]


def _reltuples(row: Dict[str, Any]) -> Optional[float]:
    value = row.get("reltuples")
    return None if value is None else float(value)


def _analyzed(reltuples: Optional[float]) -> bool:
    # reltuples is -1 until the first VACUUM or ANALYZE, and 0 for an analyzed
    # empty shard.
    return reltuples is not None and reltuples >= 0


def combine_table(
    table: CitusTable, by_shard: Dict[int, List[Tuple[int, Dict[str, Any]]]]
) -> Dict[str, Any]:
    """Combine one table's placements into logical values.

    A read runs on one placement and a write is applied to every placement, so
    read counters are summed over every placement, write counters take each
    shard's largest placement, and row estimates take one placement per shard.
    """
    chosen = {shardid: _chosen(rows) for shardid, rows in by_shard.items()}
    all_rows = [row for rows in by_shard.values() for _, row in rows]
    chosen_rows = [row for _, row in chosen.values()]

    analyzed = [r for r in chosen_rows if _analyzed(_reltuples(r))]
    never_analyzed = len(chosen_rows) - len(analyzed)
    estimated_rows = (
        int(round(sum(_reltuples(r) or 0 for r in analyzed))) if analyzed else -1
    )

    counters: Dict[str, Any] = {}
    for key in READ_COUNTERS + RUN_COUNTERS:
        counters[key] = _sum(r.get(key) for r in all_rows)
    for key in WRITE_COUNTERS:
        counters[key] = _sum(
            _max(row.get(key) for _, row in rows) for rows in by_shard.values()
        )
    for key in PLACEMENT_COUNTERS:
        counters[key] = _sum(r.get(key) for r in chosen_rows)

    shards_without_event: Dict[str, int] = {}
    for key in EVENT_TIMES:
        per_shard = []
        for rows in by_shard.values():
            times = [parse_timestamp(row.get(key)) for _, row in rows]
            times = [t for t in times if t is not None]
            per_shard.append(max(times) if times else None)
        recorded = [t for t in per_shard if t is not None]
        counters[key] = min(recorded) if recorded else None
        shards_without_event[key] = len(per_shard) - len(recorded)

    return {
        "estimated_rows": estimated_rows,
        "table_size_bytes": sum(r.get("table_size_bytes") or 0 for r in chosen_rows),
        "total_size_bytes": sum(r.get("total_size_bytes") or 0 for r in chosen_rows),
        "placement_size_bytes": sum(r.get("total_size_bytes") or 0 for r in all_rows),
        "counters": counters,
        "shards_read": len(by_shard),
        "shards_never_analyzed": never_analyzed,
        "shards_without_event": shards_without_event,
        "placements_per_shard": {
            "min": min(len(rows) for rows in by_shard.values()) if by_shard else 0,
            "max": max(len(rows) for rows in by_shard.values()) if by_shard else 0,
        },
        "chosen_groups": {shardid: group for shardid, (group, _) in chosen.items()},
        "_chosen": {shardid: (group, row) for shardid, (group, row) in chosen.items()},
    }


def _max(values: Iterable[Any]) -> Optional[int]:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _absolute_distinct(n_distinct: float, reltuples: float) -> float:
    return n_distinct if n_distinct >= 0 else -n_distinct * reltuples


def _postgres_convention(distinct: float, rows: float) -> float:
    # ANALYZE stores a negative fraction when the distinct count exceeds 10% of
    # the rows, so the count is assumed to grow with the table.
    if rows > 0 and distinct > 0.1 * rows:
        return -min(distinct / rows, 1.0)
    return distinct


def combine_n_distinct(
    table: CitusTable, attname: str, shards: List[Dict[str, Any]]
) -> Tuple[Optional[float], Optional[str]]:
    """Combine the shards' n_distinct values, and name the method used."""
    values = [
        (float(s["n_distinct"]), float(s["reltuples"]))
        for s in shards
        if s.get("n_distinct") is not None and _analyzed(s.get("reltuples"))
    ]
    if not values:
        return None, None
    total_rows = sum(rows for _, rows in values)

    if table.shard_count == 1:
        return values[0][0], "single_placement"

    if attname == table.distribution_column and table.partmethod in ("h", "r"):
        distinct = sum(_absolute_distinct(nd, rows) for nd, rows in values)
        return _postgres_convention(distinct, total_rows), "distribution_column_sum"

    if all(nd < 0 for nd, _ in values):
        if total_rows > 0:
            return (
                sum(nd * rows for nd, rows in values) / total_rows,
                "weighted_fraction",
            )
        return sum(nd for nd, _ in values) / len(values), "weighted_fraction"

    if all(nd >= 0 for nd, _ in values):
        return max(nd for nd, _ in values), "max_absolute"

    distinct = max(_absolute_distinct(nd, rows) for nd, rows in values)
    return _postgres_convention(distinct, total_rows), "mixed_max_absolute"


def _weighted_mean(values: List[Tuple[float, float]]) -> Optional[float]:
    """Mean of (value, weight) pairs; a plain mean when no weight is positive."""
    if not values:
        return None
    weight = sum(w for _, w in values if w > 0)
    if weight > 0:
        return sum(v * w for v, w in values if w > 0) / weight
    return sum(v for v, _ in values) / len(values)


def combine_columns(
    table: CitusTable,
    chosen: Dict[int, Tuple[int, Dict[str, Any]]],
    column_rows: Dict[Tuple[int, int], List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Combine pg_stats rows into one row per column, as pg_stats reports them.

    Each shard contributes the rows of its chosen placement. Returns the rows
    for the existing statistics field and the detail for each shard.
    """
    grouped: Dict[Tuple[str, bool], List[Dict[str, Any]]] = {}
    for shardid, (group, table_row) in sorted(chosen.items()):
        reltuples = _reltuples(table_row)
        for row in column_rows.get((shardid, group), []):
            grouped.setdefault((row["attname"], bool(row.get("inherited"))), []).append(
                {
                    "shardid": shardid,
                    "reltuples": reltuples,
                    "n_distinct": row.get("n_distinct"),
                    "null_frac": row.get("null_frac"),
                    "correlation": row.get("correlation"),
                }
            )

    statistics = []
    detail = []
    for (attname, inherited), shards in sorted(grouped.items()):
        analyzed = [s for s in shards if _analyzed(s["reltuples"])]
        null_frac = _weighted_mean(
            [
                (float(s["null_frac"]), s["reltuples"])
                for s in analyzed
                if s["null_frac"] is not None
            ]
        )
        correlation = _weighted_mean(
            [
                (float(s["correlation"]), s["reltuples"])
                for s in analyzed
                if s["correlation"] is not None
            ]
        )
        n_distinct, method = combine_n_distinct(table, attname, shards)
        statistics.append(
            {
                "schemaname": table.schema_name,
                "tablename": table.table_name,
                "attname": attname,
                "n_distinct": n_distinct,
                "null_frac": null_frac,
                "correlation": correlation,
            }
        )
        detail.append(
            {
                "attname": attname,
                "inherited": inherited,
                "n_distinct_method": method,
                "shards": shards,
            }
        )
    return statistics, detail


def combine_index(
    by_shard: Dict[int, List[Tuple[int, Dict[str, Any]]]],
    chosen_groups: Dict[int, int],
) -> Dict[str, Any]:
    """Combine one index's placements: reads over every placement, size once."""
    all_rows = [row for rows in by_shard.values() for _, row in rows]
    one_per_shard = []
    for shardid, rows in by_shard.items():
        group = chosen_groups.get(shardid)
        match = [row for g, row in rows if g == group] or [rows[0][1]]
        one_per_shard.append(match[0])
    analyzed = [r for r in one_per_shard if _analyzed(_reltuples(r))]
    combined: Dict[str, Any] = {
        key: _sum(r.get(key) for r in all_rows) for key in INDEX_READ_COUNTERS
    }
    combined["index_size_bytes"] = sum(
        r.get("index_size_bytes") or 0 for r in one_per_shard
    )
    combined["estimated_rows"] = (
        int(round(sum(_reltuples(r) or 0 for r in analyzed))) if analyzed else -1
    )
    combined["shards_read"] = len(by_shard)
    return combined


def logical_database_size(connection, ctx: CitusContext, coordinator_size: int) -> int:
    """The current database's size with each Citus table counted once.

    pg_database_size() on the coordinator counts the shells, the placements
    stored on the coordinator and the shard relations that a move left there,
    and nothing stored on the workers.
    """
    stats = ctx.shard_statistics(connection)
    if ctx._shell_size_bytes is None:
        row = _fetchone(
            connection,
            "SELECT COALESCE(pg_catalog.sum(pg_catalog.pg_total_relation_size(t)), 0)"
            "::pg_catalog.int8 AS shells"
            " FROM pg_catalog.unnest(%s::pg_catalog.oid[]) AS t",
            (list(ctx.tables),),
        )
        ctx._shell_size_bytes = int(row["shells"]) if row else 0
    on_coordinator = sum(
        p["total_size_bytes"]
        for p in stats.placements
        if p["groupid"] == ctx.local_group_id
    )
    if ctx.local_group_id is not None:
        on_coordinator += stats.orphaned_size_bytes.get(ctx.local_group_id, 0)
    logical = sum(t["total_size_bytes"] for t in stats.tables.values())
    return max(coordinator_size - on_coordinator - ctx._shell_size_bytes, 0) + logical


def apply_logical_database_size(
    connection, ctx: CitusContext, rows: List[Dict[str, Any]], name_key: str
) -> None:
    """Rewrite the current database's row with its logical size.

    The coordinator's values are kept under citus.coordinator. The schema and
    performance reports both use this, so that they report the same size.
    """
    current = [
        (row, logical_database_size(connection, ctx, row.get("size_bytes") or 0))
        for row in rows
        if row.get(name_key) == ctx.current_database
    ]
    if not current:
        return
    pretty = size_pretty(connection, [size for _, size in current])
    for row, size in current:
        row["citus"] = {
            "source": "shards",
            "coordinator": {
                "size_bytes": row.get("size_bytes"),
                "size_human": row.get("size_human"),
            },
        }
        row["size_bytes"] = size
        row["size_human"] = pretty.get(size)


def size_pretty(connection, values: Iterable[Optional[int]]) -> Dict[int, str]:
    """pg_size_pretty() for many values in one round trip, so the text matches."""
    distinct = sorted({int(v) for v in values if v is not None})
    if not distinct:
        return {}
    rows = fetchall(
        connection,
        "SELECT v, pg_catalog.pg_size_pretty(v) AS pretty"
        " FROM pg_catalog.unnest(%s::pg_catalog.int8[]) AS v",
        (distinct,),
    )
    return {int(row["v"]): row["pretty"] for row in rows}
