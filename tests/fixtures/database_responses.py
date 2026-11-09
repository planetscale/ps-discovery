"""
Database query response fixtures for testing
"""

import json
import re

POSTGRES_VERSION_RESPONSE = [
    {
        "version": "PostgreSQL 17.4 on aarch64-unknown-linux-gnu, compiled by gcc (GCC) 12.4.0, 64-bit"
    }
]

POSTGRES_VERSION_NUM_RESPONSE = [{"server_version_num": "170004"}]

POSTGRES_DATABASES_RESPONSE = [
    {
        "datname": "postgres",
        "datdba": "10",
        "encoding": "6",
        "datcollate": "C",
        "datctype": "C",
        "pg_database_size": 7715840,
    },
    {
        "datname": "template0",
        "datdba": "10",
        "encoding": "6",
        "datcollate": "C",
        "datctype": "C",
        "pg_database_size": 7715840,
    },
    {
        "datname": "template1",
        "datdba": "10",
        "encoding": "6",
        "datcollate": "C",
        "datctype": "C",
        "pg_database_size": 7715840,
    },
    {
        "datname": "testdb",
        "datdba": "16384",
        "encoding": "6",
        "datcollate": "C",
        "datctype": "C",
        "pg_database_size": 73000000,
    },
]

POSTGRES_TABLES_RESPONSE = [
    {
        "schemaname": "public",
        "tablename": "users",
        "tableowner": "postgres",
        "pg_total_relation_size": 16384000,
        "n_tup_ins": 1000,
        "n_tup_upd": 100,
        "n_tup_del": 10,
    },
    {
        "schemaname": "public",
        "tablename": "orders",
        "tableowner": "postgres",
        "pg_total_relation_size": 32768000,
        "n_tup_ins": 5000,
        "n_tup_upd": 500,
        "n_tup_del": 50,
    },
]

POSTGRES_INDEXES_RESPONSE = [
    {
        "schemaname": "public",
        "tablename": "users",
        "indexname": "users_pkey",
        "indexdef": "CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id)",
        "pg_total_relation_size": 8192,
    },
    {
        "schemaname": "public",
        "tablename": "users",
        "indexname": "idx_users_email",
        "indexdef": "CREATE INDEX idx_users_email ON public.users USING btree (email)",
        "pg_total_relation_size": 16384,
    },
]

POSTGRES_EXTENSIONS_RESPONSE = [
    {
        "extname": "plpgsql",
        "extversion": "1.0",
        "extrelocatable": False,
        "extschema": "pg_catalog",
    }
]

POSTGRES_USERS_RESPONSE = [
    {
        "rolname": "postgres",
        "rolsuper": False,
        "rolinherit": True,
        "rolcreaterole": True,
        "rolcreatedb": True,
        "rolcanlogin": True,
        "rolreplication": False,
        "rolconnlimit": -1,
        "rolvaliduntil": None,
    },
    {
        "rolname": "rdsadmin",
        "rolsuper": True,
        "rolinherit": True,
        "rolcreaterole": True,
        "rolcreatedb": True,
        "rolcanlogin": True,
        "rolreplication": True,
        "rolconnlimit": -1,
        "rolvaliduntil": None,
    },
]

POSTGRES_SETTINGS_RESPONSE = [
    {
        "name": "max_connections",
        "setting": "100",
        "unit": None,
        "category": "Connections and Authentication / Connection Settings",
        "short_desc": "Sets the maximum number of concurrent connections.",
        "context": "postmaster",
        "vartype": "integer",
        "source": "configuration file",
        "min_val": "1",
        "max_val": "1000000",
        "boot_val": "100",
        "reset_val": "100",
    },
    {
        "name": "shared_buffers",
        "setting": "32768",
        "unit": "8kB",
        "category": "Resource Usage / Memory",
        "short_desc": "Sets the number of shared memory buffers.",
        "context": "postmaster",
        "vartype": "integer",
        "source": "configuration file",
        "min_val": "16",
        "max_val": "1073741823",
        "boot_val": "1024",
        "reset_val": "32768",
    },
]

#
# Citus. The shapes and values are copied from a test cluster (Citus 14.1 on
# PostgreSQL 18: one coordinator and two workers, pgbench schema), reduced to
# two shards of one distributed table and one reference table.
#

CITUS_BRANCHES_OID = 17507
CITUS_REFERENCE_OID = 17549
CITUS_REGULAR_OID = 17572
CITUS_BRANCHES_PKEY_OID = 17508
CITUS_REFERENCE_PKEY_OID = 17552

CITUS_NODES = [
    {
        "nodeid": 1,
        "groupid": 0,
        "nodename": "citus-coordinator",
        "nodeport": 5432,
        "noderole": "primary",
        "nodecluster": "default",
        "isactive": True,
        "shouldhaveshards": False,
        "hasmetadata": True,
    },
    {
        "nodeid": 2,
        "groupid": 1,
        "nodename": "citus-worker-1",
        "nodeport": 5432,
        "noderole": "primary",
        "nodecluster": "default",
        "isactive": True,
        "shouldhaveshards": True,
        "hasmetadata": True,
    },
    {
        "nodeid": 3,
        "groupid": 2,
        "nodename": "citus-worker-2",
        "nodeport": 5432,
        "noderole": "primary",
        "nodecluster": "default",
        "isactive": True,
        "shouldhaveshards": True,
        "hasmetadata": True,
    },
]

CITUS_TABLES = [
    {
        "table_oid": CITUS_BRANCHES_OID,
        "schema_name": "public",
        "table_name": "pgbench_branches",
        "table_type": "distributed",
        "partmethod": "h",
        "repmodel": "s",
        "colocation_id": 3,
        "distribution_column": "bid",
        "autoconverted": False,
        "shard_count": 2,
        "access_method": "heap",
    },
    {
        "table_oid": CITUS_REFERENCE_OID,
        "schema_name": "public",
        "table_name": "reference_t1",
        "table_type": "reference",
        "partmethod": "n",
        "repmodel": "t",
        "colocation_id": 4,
        "distribution_column": None,
        "autoconverted": False,
        "shard_count": 1,
        "access_method": "heap",
    },
]

CITUS_SHARDS = [
    {
        "shardid": 102137,
        "table_oid": CITUS_BRANCHES_OID,
        "shard_relname": "pgbench_branches_102137",
    },
    {
        "shardid": 102138,
        "table_oid": CITUS_BRANCHES_OID,
        "shard_relname": "pgbench_branches_102138",
    },
    {
        "shardid": 102265,
        "table_oid": CITUS_REFERENCE_OID,
        "shard_relname": "reference_t1_102265",
    },
]

CITUS_PLACEMENTS = [
    {"shardid": 102137, "groupid": 1, "shardstate": 1},
    {"shardid": 102138, "groupid": 2, "shardstate": 1},
    {"shardid": 102265, "groupid": 0, "shardstate": 1},
    {"shardid": 102265, "groupid": 1, "shardstate": 1},
    {"shardid": 102265, "groupid": 2, "shardstate": 1},
]


def citus_table_row(relname, reltuples, **overrides):
    """One row of the remote table command, as json_agg() writes it."""
    row = {
        "schema_name": "public",
        "relname": relname,
        "relkind": "r",
        "reltuples": reltuples,
        "table_size_bytes": 16384,
        "total_size_bytes": 65536,
        "seq_scan": 1,
        "seq_tup_read": 0,
        "idx_scan": 0,
        "idx_tup_fetch": 0,
        "n_tup_ins": int(reltuples) if reltuples > 0 else 0,
        "n_tup_upd": 0,
        "n_tup_del": 0,
        "n_tup_hot_upd": 0,
        "n_live_tup": int(reltuples) if reltuples > 0 else 0,
        "n_dead_tup": 0,
        "n_mod_since_analyze": 0,
        "last_vacuum": None,
        "last_autovacuum": None,
        "last_analyze": "2026-10-27T22:17:09.167203+00:00",
        "last_autoanalyze": None,
        "vacuum_count": 0,
        "autovacuum_count": 0,
        "analyze_count": 1,
        "autoanalyze_count": 0,
    }
    row.update(overrides)
    return row


def _reference_row(reltuples, analyzed_at, **overrides):
    return citus_table_row(
        "reference_t1_102265",
        reltuples,
        table_size_bytes=4222812160,
        total_size_bytes=5801107456,
        n_tup_ins=50000000,
        last_analyze=analyzed_at,
        **overrides,
    )


CITUS_REMOTE_TABLE_ROWS = {
    1: [_reference_row(4.999656e07, "2026-10-27T23:27:52.237928+00:00")],
    2: [
        citus_table_row("pgbench_branches_102137", 317),
        _reference_row(4.9999892e07, "2026-10-27T23:27:40.1+00:00"),
    ],
    3: [
        citus_table_row("pgbench_branches_102138", 313),
        _reference_row(5.000156e07, "2026-10-27T23:27:45+00:00"),
    ],
}


def citus_column_row(relname, attname, n_distinct, null_frac=0.0, correlation=1.0):
    return {
        "schema_name": "public",
        "relname": relname,
        "attname": attname,
        "inherited": False,
        "null_frac": null_frac,
        "n_distinct": n_distinct,
        "correlation": correlation,
    }


CITUS_REMOTE_COLUMN_ROWS = {
    1: [citus_column_row("reference_t1_102265", "id", -1.0)],
    2: [
        citus_column_row("pgbench_branches_102137", "bid", -1.0),
        citus_column_row("reference_t1_102265", "id", -1.0),
    ],
    3: [
        citus_column_row("pgbench_branches_102138", "bid", -1.0),
        citus_column_row("reference_t1_102265", "id", -1.0),
    ],
}


def citus_index_row(index_relname, reltuples, idx_scan=0, size=16384):
    return {
        "schema_name": "public",
        "index_relname": index_relname,
        "reltuples": reltuples,
        "idx_scan": idx_scan,
        "idx_tup_read": idx_scan,
        "idx_tup_fetch": idx_scan,
        "index_size_bytes": size,
    }


CITUS_REMOTE_INDEX_ROWS = {
    1: [citus_index_row("reference_t1_pkey_102265", 4.999656e07, size=1577099264)],
    2: [
        citus_index_row("pgbench_branches_pkey_102137", 317),
        citus_index_row("reference_t1_pkey_102265", 4.9999892e07, size=1577099264),
    ],
    3: [
        citus_index_row("pgbench_branches_pkey_102138", 313),
        citus_index_row("reference_t1_pkey_102265", 5.000156e07, size=1577099264),
    ],
}

CITUS_INDEXES = [
    {
        "index_oid": CITUS_BRANCHES_PKEY_OID,
        "table_oid": CITUS_BRANCHES_OID,
        "schema_name": "public",
        "index_name": "pgbench_branches_pkey",
    },
    {
        "index_oid": CITUS_REFERENCE_PKEY_OID,
        "table_oid": CITUS_REFERENCE_OID,
        "schema_name": "public",
        "index_name": "reference_t1_pkey",
    },
]

CITUS_INDEX_SHARD_NAMES = [
    {
        "index_oid": CITUS_BRANCHES_PKEY_OID,
        "shardid": 102137,
        "shard_index_name": "pgbench_branches_pkey_102137",
    },
    {
        "index_oid": CITUS_BRANCHES_PKEY_OID,
        "shardid": 102138,
        "shard_index_name": "pgbench_branches_pkey_102138",
    },
    {
        "index_oid": CITUS_REFERENCE_PKEY_OID,
        "shardid": 102265,
        "shard_index_name": "reference_t1_pkey_102265",
    },
]

CITUS_FUNCTION_ARGS = {
    "citus_version": "",
    "column_to_column_name": "table_name regclass, column_var_text text",
    "relation_is_a_known_shard": "regclass",
    "run_command_on_all_nodes": (
        "command text, parallel boolean, give_warning_for_connection_errors boolean,"
        " OUT nodeid integer, OUT success boolean, OUT result text"
    ),
    "shard_name": "object_name regclass, shard_id bigint",
}

CITUS_DIST_TABLES = (
    "pg_dist_colocation",
    "pg_dist_local_group",
    "pg_dist_node",
    "pg_dist_object",
    "pg_dist_partition",
    "pg_dist_placement",
    "pg_dist_shard",
    "pg_dist_schema",
)

CITUS_DATABASE_SIZES = {1: "19327047359", 2: "88322569919", 3: "89151944383"}


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.connection.executed.append((sql, params))
        self.rows = self.connection.respond(sql, params)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class FakeConnection:
    """Answers each query with the rows of the first handler whose marker it holds.

    A handler's response is a list of rows, a callable taking (sql, params), or
    an exception to raise. Queries that match no handler return no rows.
    """

    def __init__(self, handlers):
        self.handlers = list(handlers)
        self.executed = []
        self.closed = 0

    def cursor(self, *args, **kwargs):
        return FakeCursor(self)

    def respond(self, sql, params):
        for marker, response in self.handlers:
            if marker in sql:
                if isinstance(response, Exception):
                    raise response
                if callable(response):
                    return response(sql, params)
                return [dict(row) for row in response]
        return []

    def statements(self):
        return [sql for sql, _ in self.executed]


def citus_handlers(
    role="discovery",
    local_group_id=0,
    use_secondary_nodes="never",
    missing_functions=(),
    no_execute=(),
    missing_tables=(),
    no_select=(),
    give_warning=True,
    nodes=None,
    tables=None,
    shards=None,
    placements=None,
    node_failures=None,
    remote_tables=None,
    remote_columns=None,
    remote_indexes=None,
    indexes=None,
    index_shard_names=None,
):
    """Handlers that make a FakeConnection behave like the test cluster."""
    from planetscale_discovery.database import citus

    indexes = CITUS_INDEXES if indexes is None else indexes
    index_shard_names = (
        CITUS_INDEX_SHARD_NAMES if index_shard_names is None else index_shard_names
    )

    nodes = CITUS_NODES if nodes is None else nodes
    tables = CITUS_TABLES if tables is None else tables
    shards = CITUS_SHARDS if shards is None else shards
    placements = CITUS_PLACEMENTS if placements is None else placements
    node_failures = node_failures or {}
    remote = {
        citus.TABLE_COMMAND: (
            CITUS_REMOTE_TABLE_ROWS if remote_tables is None else remote_tables
        ),
        citus.COLUMN_COMMAND: (
            CITUS_REMOTE_COLUMN_ROWS if remote_columns is None else remote_columns
        ),
        citus.INDEX_COMMAND: (
            CITUS_REMOTE_INDEX_ROWS if remote_indexes is None else remote_indexes
        ),
    }

    def functions(sql, params):
        args = dict(CITUS_FUNCTION_ARGS)
        if not give_warning:
            args["run_command_on_all_nodes"] = "command text, parallel boolean"
        return [
            {"proname": name, "args": a, "can_execute": name not in no_execute}
            for name, a in args.items()
            if name not in missing_functions
        ]

    def dist_tables(sql, params):
        return [
            {
                "name": name,
                "present": name not in missing_tables,
                "can_select": None if name in missing_tables else name not in no_select,
            }
            for name in CITUS_DIST_TABLES
        ]

    def run_command(sql, params):
        command = params[0]
        # params[1] and params[2] hold the batch's shard schemas and names.
        batch = (
            {int(name.rsplit("_", 1)[1]) for name in params[2]}
            if len(params) > 2
            else None
        )
        # (node id reported, node id whose fixture data it returns)
        readable = [
            (n["nodeid"], n["nodeid"])
            for n in nodes
            if n["isactive"] and n["noderole"] == "primary"
        ]
        # Like the real function, the current node is added as node id 0 when
        # pg_dist_node does not list it. It returns the coordinator's data.
        if not any(n["groupid"] == local_group_id for n in nodes):
            readable.append((citus.UNLISTED_NODE_ID, 1))
        rows = []
        for nodeid, data in readable:
            if nodeid in node_failures:
                rows.append(
                    {
                        "nodeid": nodeid,
                        "success": False,
                        "result": node_failures[nodeid],
                    }
                )
                continue
            if command in remote:
                node_rows = [
                    r
                    for r in remote[command].get(data, [])
                    if batch is None
                    # The server selects by the table shard's name; an index row
                    # may name its table shard when its own name lacks a shard id.
                    or int(
                        (
                            r.get("relname")
                            or r.get("table_relname")
                            or r.get("index_relname")
                        ).rsplit("_", 1)[1]
                    )
                    in batch
                ]
                result = json.dumps(node_rows)
            elif command == citus.DATABASE_SIZE_COMMAND:
                result = CITUS_DATABASE_SIZES.get(data, "0")
            else:
                result = "1"
            rows.append({"nodeid": nodeid, "success": True, "result": result})
        return rows

    shard_names_by_shard = {}
    for row in index_shard_names:
        shard_names_by_shard.setdefault(row["shardid"], []).append(row)

    def index_shard_names_in(sql, params):
        return [
            row
            for shardid in params[0]
            for row in shard_names_by_shard.get(shardid, [])
        ]

    def size_pretty(sql, params):
        return [{"v": v, "pretty": f"{v} bytes"} for v in params[0]]

    def classified(sql, params):
        # Applies the relkind filter as the server would, so a fixture table
        # may carry a relkind; one without it is an ordinary table.
        filtered = f"c.relkind IN {citus.TABLE_RELKINDS}" in sql
        return [
            {k: v for k, v in row.items() if k != "relkind"}
            for row in tables
            if not filtered or row.get("relkind", "r") in ("r", "p")
        ]

    return [
        ("WHERE extname = 'citus'", [{"extversion": "14.1-1"}]),
        # Quoted as quote_ident() does for a name that is not lowercase and plain.
        (
            "pg_catalog.quote_ident(current_user) AS role",
            [
                {
                    "role": (
                        role
                        if re.fullmatch(r"[a-z_][a-z0-9_$]*", role)
                        else '"' + role.replace('"', '""') + '"'
                    )
                }
            ],
        ),
        ("pg_get_function_identity_arguments(p.oid) AS args", functions),
        ("pg_catalog.to_regclass('pg_catalog.' || t.name)", dist_tables),
        (
            "AS local_group_id",
            [
                {
                    "local_group_id": local_group_id,
                    "citus_version": "Citus 14.1.0 on x86_64-pc-linux-gnu",
                    "current_database": "postgres",
                    "use_secondary_nodes": use_secondary_nodes,
                    "cluster_name": "default",
                    "local_hostname": "citus-coordinator",
                    "port": "5432",
                    "has_autoconverted": True,
                }
            ],
        ),
        ("nodecluster::text AS nodecluster", nodes),
        ("END AS table_type", classified),
        ("AS shard_relname", shards),
        ("SELECT shardid, groupid, shardstate", placements),
        ("run_command_on_all_nodes(", run_command),
        ("AS shard_index_name", index_shard_names_in),
        ("i.relname AS index_name", indexes),
        ("pg_size_pretty(v) AS pretty", size_pretty),
        ("AS shells", [{"shells": 24576}]),
    ]


POSTGRES_CONNECTIONS_RESPONSE = [
    {
        "pid": 12345,
        "usename": "postgres",
        "datname": "testdb",
        "client_addr": "127.0.0.1",
        "state": "active",
        "query": "SELECT * FROM users;",
        "backend_start": "2024-01-01 12:00:00",
    },
    {
        "pid": 12346,
        "usename": "appuser",
        "datname": "testdb",
        "client_addr": "10.0.1.100",
        "state": "idle",
        "query": "",
        "backend_start": "2024-01-01 11:00:00",
    },
]
