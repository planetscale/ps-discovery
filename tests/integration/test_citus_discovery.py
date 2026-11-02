"""
Discovery against a live Citus cluster.

Every check compares the report with what the cluster itself says, so the test
holds for any Citus coordinator rather than one seeded schema. Set the
coordinator's connection string to run it; without it, every test skips.

    CITUS_E2E_DSN="host=... port=5432 dbname=postgres user=... password=..."

The run is read-only: it reads catalogs and statistics views on the coordinator
and, through run_command_on_shards(), run_command_on_placements() and
run_command_on_all_nodes(), on each shard placement. A test whose case the
cluster does not hold skips; test-data/citus-coverage.sql creates every case.
"""

import json
import os

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from psycopg2.extensions import parse_dsn  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

from planetscale_discovery.database import citus  # noqa: E402
from planetscale_discovery.database.citus import CitusClusterError  # noqa: E402
from planetscale_discovery.database.discovery import PostgreSQLDiscovery  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.db]

DSN = os.environ.get("CITUS_E2E_DSN")


@pytest.fixture(scope="module")
def dsn():
    if not DSN:
        pytest.skip("CITUS_E2E_DSN is not set")
    return DSN


@pytest.fixture(scope="module")
def connection(dsn):
    conn = psycopg2.connect(
        dsn,
        cursor_factory=RealDictCursor,
        options="-c default_transaction_read_only=on",
    )
    conn.autocommit = True
    yield conn
    conn.close()


# Shards are shown to the discovery connection, as a role default would show
# them, so the tests below exercise discovery's own hiding and filters rather
# than Citus's default.
SHOW_SHARDS = "-c citus.show_shards_for_app_name_prefixes=*"


@pytest.fixture(scope="module")
def report(dsn):
    params = parse_dsn(dsn)
    params["options"] = f"-c default_transaction_read_only=on {SHOW_SHARDS}"
    discovery = PostgreSQLDiscovery(params, citus_detection=True)
    assert discovery.connect()
    try:
        results = discovery.run_analysis()
    finally:
        discovery.close()
    if "citus" not in results["analysis_results"]:
        pytest.skip("the citus extension is not installed on CITUS_E2E_DSN")
    return results


def fetch(connection, sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return [dict(row) for row in cursor.fetchall()]


def tables_by_name(report):
    return {
        (t["schema_name"], t["table_name"]): t
        for t in report["analysis_results"]["schema"]["table_analysis"]
    }


def test_every_user_table_is_discovered(report, connection):
    """Catches a regular or Citus table dropped from table_analysis."""
    expected = {
        (row["nspname"], row["relname"])
        for row in fetch(
            connection,
            """
            SELECT n.nspname, c.relname
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
              AND NOT pg_catalog.relation_is_a_known_shard(c.oid)
              AND NOT EXISTS (
                  SELECT 1 FROM pg_catalog.pg_depend d
                  WHERE d.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
                    AND d.objid = c.oid AND d.deptype = 'e')
            """,
        )
    }
    assert set(tables_by_name(report)) == expected


def test_labels_match_citus_own_view(report, connection):
    """Catches a table type that disagrees with Citus's citus_tables view."""
    view = fetch(
        connection,
        """
        SELECT c.oid::pg_catalog.regclass::text AS name
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_depend d ON d.objid = c.oid AND d.deptype = 'e'
        JOIN pg_catalog.pg_extension e ON e.oid = d.refobjid AND e.extname = 'citus'
        WHERE c.relname = 'citus_tables'
        """,
    )[0]["name"]
    citus_types = {
        (row["nspname"], row["relname"]): row["citus_table_type"]
        for row in fetch(
            connection,
            f"""
            SELECT n.nspname, c.relname, t.citus_table_type
            FROM {view} t
            JOIN pg_catalog.pg_class c ON c.oid = t.table_name::pg_catalog.oid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            """,
        )
    }
    for key, table in tables_by_name(report).items():
        assert table["citus"]["table_type"] == citus_types.get(key, "regular"), key


def test_regular_tables_keep_their_coordinator_values(report, connection):
    """Catches a regular table on a Citus database whose row estimate is replaced, or
    that receives coordinator values meant for Citus tables.
    """
    reltuples = {
        (row["nspname"], row["relname"]): row["reltuples"]
        for row in fetch(
            connection,
            """
            SELECT n.nspname, c.relname, c.reltuples::bigint AS reltuples
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            """,
        )
    }
    for key, table in tables_by_name(report).items():
        if table["citus"]["table_type"] == "regular":
            assert "coordinator" not in table["citus"]
            assert table["estimated_rows"] == reltuples[key], key


def single_placement_tables(report):
    """Citus tables with one placement per shard and every shard analyzed.

    Covers distributed (including single-shard), schema and local tables, whose
    values each shard placement holds once. Reference tables, with a placement
    on every node, are covered by the unit tests.
    """
    for key, table in tables_by_name(report).items():
        citus_info = table["citus"]
        if citus_info["table_type"] == "regular":
            continue
        if citus_info["placements_per_shard"]["max"] != 1:
            continue
        if citus_info["shard_statistics"]["shards_never_analyzed"]:
            continue
        yield key, table


def on_shards(connection, schema, name, command):
    """run_command_on_shards() substitutes the shard name for one %s."""
    return fetch(
        connection,
        f"""
        SELECT result FROM pg_catalog.run_command_on_shards(
            '"{schema}"."{name}"'::pg_catalog.regclass, $cmd${command}$cmd$)
        """,
    )


def test_rows_and_sizes_match_the_shards(report, connection):
    """Catches a row estimate or size that disagrees with the shards' own values.

    The row estimate follows the Citus docs' example of summing each shard's
    reltuples through run_command_on_shards().
    """
    checked = set()
    for (schema, name), table in single_placement_tables(report):
        shards = on_shards(
            connection,
            schema,
            name,
            "SELECT c.reltuples::bigint || ',' || pg_catalog.pg_relation_size(c.oid)"
            " || ',' || pg_catalog.pg_total_relation_size(c.oid)"
            " FROM pg_catalog.pg_class c WHERE c.oid = '%s'::pg_catalog.regclass",
        )
        values = [tuple(map(int, row["result"].split(","))) for row in shards]
        expected = tuple(sum(column) for column in zip(*values))
        reported = (
            table["estimated_rows"],
            table["table_size_bytes"],
            table["total_size_bytes"],
        )
        assert reported == expected, (schema, name)
        checked.add(table["citus"]["table_type"])
    assert checked, "no Citus table with one placement per shard was found"


def test_single_shard_statistics_match_the_shard(report, connection):
    """Catches column statistics of a single-shard table (schema, local, or
    distributed without a distribution column) that differ from its shard's."""
    for (schema, name), table in single_placement_tables(report):
        if table["citus"]["shard_count"] != 1:
            continue
        shard = on_shards(
            connection,
            schema,
            name,
            "SELECT COALESCE(pg_catalog.json_agg(pg_catalog.json_build_array("
            "s.attname, s.inherited, s.null_frac, s.n_distinct, s.correlation)), '[]')"
            " FROM pg_catalog.pg_stats s"
            " WHERE pg_catalog.to_regclass(pg_catalog.format("
            "'%%I.%%I', s.schemaname, s.tablename)) = '%s'::pg_catalog.regclass",
        )[0]["result"]
        expected = {
            (attname, inherited): (null_frac, n_distinct, correlation)
            for attname, inherited, null_frac, n_distinct, correlation in json.loads(
                shard
            )
        }
        detail = table["citus"]["shard_statistics"]["columns"]
        reported = {
            (column["attname"], column["inherited"]): (
                row["null_frac"],
                row["n_distinct"],
                row["correlation"],
            )
            for column, row in zip(detail, table["statistics"])
        }
        assert reported.keys() == expected.keys(), (schema, name)
        for key, values in reported.items():
            assert values == pytest.approx(expected[key], abs=1e-6), (schema, name, key)


def strings(value):
    """Every string in the report, at any depth."""
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


def test_no_shard_object_is_reported_anywhere(report, connection, dsn):
    """Catches a shard placement, its index, constraint or row type reported as
    a customer object, with shards shown to the discovery connection."""
    shard_names = {
        row["name"]
        for row in fetch(
            connection,
            """
            SELECT pg_catalog.shard_name(s.logicalrelid, s.shardid) AS name
            FROM pg_catalog.pg_dist_shard s
            UNION
            SELECT pg_catalog.shard_name(x.indexrelid, s.shardid)
            FROM pg_catalog.pg_dist_shard s
            JOIN pg_catalog.pg_index x ON x.indrelid = s.logicalrelid
            UNION
            SELECT c.conname || '_' || s.shardid
            FROM pg_catalog.pg_dist_shard s
            JOIN pg_catalog.pg_constraint c ON c.conrelid = s.logicalrelid
            """,
        )
    }
    with psycopg2.connect(dsn, options=SHOW_SHARDS) as shown:
        with shown.cursor() as cursor:
            cursor.execute(
                "SELECT pg_catalog.count(*) FROM pg_catalog.pg_class"
                " WHERE pg_catalog.relation_is_a_known_shard(oid)"
            )
            visible = cursor.fetchone()[0]
    if not visible:
        pytest.skip("the coordinator holds no shard placement, so none can leak")
    leaked = {s for s in strings(report["analysis_results"]) if s in shard_names}
    assert not leaked


def test_no_extension_object_is_reported(report, connection):
    """Catches the extension's schemas, views, functions, types or event
    triggers, or grants on them, reported as customer objects."""
    members = fetch(
        connection,
        """
        SELECT d.classid::pg_catalog.regclass::text AS catalog,
               COALESCE(cn.nspname, pn.nspname, tn.nspname) AS schema_name,
               COALESCE(n.nspname, c.relname, p.proname, t.typname, e.evtname) AS name
        FROM pg_catalog.pg_depend d
        JOIN pg_catalog.pg_extension x ON x.oid = d.refobjid AND x.extname = 'citus'
        LEFT JOIN pg_catalog.pg_namespace n
            ON d.classid = 'pg_catalog.pg_namespace'::pg_catalog.regclass
           AND n.oid = d.objid
        LEFT JOIN pg_catalog.pg_class c
            ON d.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
           AND c.oid = d.objid
        LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid = c.relnamespace
        LEFT JOIN pg_catalog.pg_proc p
            ON d.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
           AND p.oid = d.objid
        LEFT JOIN pg_catalog.pg_namespace pn ON pn.oid = p.pronamespace
        LEFT JOIN pg_catalog.pg_type t
            ON d.classid = 'pg_catalog.pg_type'::pg_catalog.regclass
           AND t.oid = d.objid
        LEFT JOIN pg_catalog.pg_namespace tn ON tn.oid = t.typnamespace
        LEFT JOIN pg_catalog.pg_event_trigger e
            ON d.classid = 'pg_catalog.pg_event_trigger'::pg_catalog.regclass
           AND e.oid = d.objid
        WHERE d.deptype = 'e'
        """,
    )

    def named(catalog):
        return {
            (m["schema_name"], m["name"]) for m in members if m["catalog"] == catalog
        }

    schemas = {m["name"] for m in members if m["catalog"] == "pg_namespace"}
    relations = named("pg_class")
    functions = named("pg_proc")
    types = named("pg_type")
    events = {m["name"] for m in members if m["catalog"] == "pg_event_trigger"}
    assert schemas and relations and functions, "no extension member was found"

    results = report["analysis_results"]
    schema = results["schema"]
    features = results["features"]
    privileges = results["security"]["permission_analysis"]

    assert not {s["schema_name"] for s in schema["schema_inventory"]} & schemas
    assert (
        not {(v["schema_name"], v["view_name"]) for v in schema["view_analysis"]}
        & relations
    )
    assert (
        not {
            (f["schema_name"], f["function_name"]) for f in schema["function_analysis"]
        }
        & functions
    )
    reported_types = {
        (t["schema_name"], t["type_name"])
        for kind in ("composite_types", "enum_types", "domain_types", "range_types")
        for t in features["custom_data_types"][kind]
    }
    # A view's row type is not an extension member itself; it shares the view's name.
    assert not reported_types & (types | relations)
    assert not {t["trigger_name"] for t in features["event_triggers"]} & events
    assert not {p["schema_name"] for p in privileges["schema_privileges"]} & schemas
    assert (
        not {
            (p["schema_name"], p["table_name"]) for p in privileges["table_privileges"]
        }
        & relations
    )
    assert (
        not {
            (p["schema_name"], p["function_name"])
            for p in privileges["function_privileges"]
        }
        & functions
    )


def test_nodes_and_cluster_storage_match_the_cluster(report, connection):
    """Catches a node, the coordinator included, missing from the report, or a
    cluster storage that is not the sum of every node's database size."""
    sizes = {
        row["nodeid"]: int(row["result"])
        for row in fetch(
            connection,
            """
            SELECT nodeid, result FROM pg_catalog.run_command_on_all_nodes(
                'SELECT pg_catalog.pg_database_size(pg_catalog.current_database())')
            """,
        )
    }
    citus_report = report["analysis_results"]["citus"]
    if citus_report["summary"]["cluster_storage_bytes"] is None:
        pytest.skip("the database holds no Citus table, so no node is read")
    reported = {n["nodeid"]: n for n in citus_report["nodes"]}
    assert set(sizes) <= set(reported)
    for nodeid, size in sizes.items():
        assert reported[nodeid]["database_size_bytes"] == pytest.approx(size, rel=0.01)
        assert reported[nodeid]["nodename"] and reported[nodeid]["nodeport"]
    assert citus_report["summary"]["node_count"] == len(reported)
    assert citus_report["summary"]["cluster_storage_bytes"] == pytest.approx(
        sum(sizes.values()), rel=0.01
    )


def citus_tables(report):
    for key, table in tables_by_name(report).items():
        if table["citus"]["table_type"] != "regular":
            yield key, table


def on_placements(connection, schema, name, command):
    """Each placement's result, with the shard and the node group it is on."""
    rows = fetch(
        connection,
        f"""
        SELECT p.shardid, n.groupid, p.success, p.result
        FROM pg_catalog.run_command_on_placements(
            '"{schema}"."{name}"'::pg_catalog.regclass, $cmd${command}$cmd$) p
        JOIN pg_catalog.pg_dist_node n
          ON n.nodename = p.nodename AND n.nodeport = p.nodeport
        """,
    )
    failed = [row["result"] for row in rows if not row["success"]]
    assert not failed, (schema, name, failed[:1])
    return rows


PLACEMENT_VALUES = (
    "SELECT pg_catalog.json_build_object("
    "'reltuples', c.reltuples,"
    " 'table_size', pg_catalog.pg_relation_size(c.oid),"
    " 'total_size', pg_catalog.pg_total_relation_size(c.oid),"
    " 'analyzed', pg_catalog.date_part('epoch',"
    " GREATEST(s.last_analyze, s.last_autoanalyze)),"
    " 'n_tup_ins', s.n_tup_ins, 'n_tup_upd', s.n_tup_upd, 'n_tup_del', s.n_tup_del,"
    " 'seq_scan', s.seq_scan, 'idx_scan', s.idx_scan)::text"
    " FROM pg_catalog.pg_class c"
    " LEFT JOIN pg_catalog.pg_stat_user_tables s ON s.relid = c.oid"
    " WHERE c.oid = '%s'::pg_catalog.regclass"
)


def summed(values):
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def largest(values):
    present = [v for v in values if v is not None]
    return max(present) if present else None


def test_every_citus_table_follows_the_placement_rules(report, connection):
    """Catches a replicated shard counted once per placement for rows, sizes or
    writes, a read on one placement left out, or rows taken from a placement
    other than the one analyzed most recently.

    Covers every Citus table, so reference tables and tables with
    citus.shard_replication_factor above 1 are checked against the live
    placements rather than only against fakes.
    """
    statistics = {
        (row["schemaname"], row["tablename"]): row
        for row in report["analysis_results"]["performance"]["table_statistics"]
    }
    replicated = 0
    for (schema, name), table in citus_tables(report):
        by_shard = {}
        for row in on_placements(connection, schema, name, PLACEMENT_VALUES):
            by_shard.setdefault(row["shardid"], []).append(
                (row["groupid"], json.loads(row["result"]))
            )
        if max(len(rows) for rows in by_shard.values()) > 1:
            replicated += 1

        chosen = [
            sorted(
                rows,
                key=lambda item: (
                    item[1]["analyzed"] is None,
                    -(item[1]["analyzed"] or 0),
                    item[0],
                ),
            )[0][1]
            for rows in by_shard.values()
        ]
        analyzed = [r["reltuples"] for r in chosen if r["reltuples"] >= 0]
        assert table["estimated_rows"] == (
            int(round(sum(analyzed))) if analyzed else -1
        ), (schema, name)
        assert table["table_size_bytes"] == sum(r["table_size"] for r in chosen)
        assert table["total_size_bytes"] == sum(r["total_size"] for r in chosen)

        counters = statistics.get((schema, name))
        if counters is None:
            continue
        for key in ("n_tup_ins", "n_tup_upd", "n_tup_del"):
            expected = summed(
                largest(r[key] for _, r in rows) for rows in by_shard.values()
            )
            assert counters[key] == expected, (schema, name, key)
        for key in ("seq_scan", "idx_scan"):
            expected = summed(r[key] for rows in by_shard.values() for _, r in rows)
            assert counters[key] == expected, (schema, name, key)
    assert replicated, "no Citus table with more than one placement per shard"


def test_unanalyzed_shards_are_counted_and_left_out(report, connection):
    """Catches a shard that was never analyzed (reltuples -1) summed into the rows, or an
    analyzed empty shard (reltuples 0) counted as never analyzed."""
    checked = 0
    for (schema, name), table in citus_tables(report):
        never = table["citus"]["shard_statistics"]["shards_never_analyzed"]
        if not never:
            continue
        reltuples = [
            float(row["result"])
            for row in on_shards(
                connection,
                schema,
                name,
                "SELECT c.reltuples FROM pg_catalog.pg_class c"
                " WHERE c.oid = '%s'::pg_catalog.regclass",
            )
        ]
        assert never == sum(1 for r in reltuples if r < 0), (schema, name)
        analyzed = [r for r in reltuples if r >= 0]
        assert table["estimated_rows"] == (
            int(round(sum(analyzed))) if analyzed else -1
        ), (schema, name)
        checked += 1
    if not checked:
        pytest.skip("no Citus table has a shard that was never analyzed")


def shard_index_names(connection):
    """(table schema, table, index schema, index) -> {shardid: shard index name}.

    shard_name() applies Citus's own quoting and shortening; parse_ident()
    removes the quoting and the schema.
    """
    names = {}
    for row in fetch(
        connection,
        """
        SELECT tn.nspname AS table_schema, t.relname AS table_name,
               n.nspname AS index_schema, i.relname AS index_name, s.shardid,
               (pg_catalog.parse_ident(
                    pg_catalog.shard_name(x.indexrelid, s.shardid)))[
                 pg_catalog.array_length(pg_catalog.parse_ident(
                    pg_catalog.shard_name(x.indexrelid, s.shardid)), 1)]
                 AS shard_index
        FROM pg_catalog.pg_dist_shard s
        JOIN pg_catalog.pg_index x ON x.indrelid = s.logicalrelid
        JOIN pg_catalog.pg_class t ON t.oid = x.indrelid
        JOIN pg_catalog.pg_namespace tn ON tn.oid = t.relnamespace
        JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = i.relnamespace
        """,
    ):
        key = (
            row["table_schema"],
            row["table_name"],
            row["index_schema"],
            row["index_name"],
        )
        names.setdefault(key, {})[row["shardid"]] = row["shard_index"]
    return names


SHARD_INDEX_SIZES = (
    "SELECT COALESCE(pg_catalog.json_object_agg("
    "i.relname, pg_catalog.pg_relation_size(i.oid)), '{}')::text"
    " FROM pg_catalog.pg_index x"
    " JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid"
    " WHERE x.indrelid = '%s'::pg_catalog.regclass"
)


def shard_index_sizes(connection, schema, name):
    """{shardid: {shard index name: size}} for a table with one placement per shard."""
    return {
        row["shardid"]: json.loads(row["result"])
        for row in fetch(
            connection,
            f"""
            SELECT shardid, result FROM pg_catalog.run_command_on_shards(
                '"{schema}"."{name}"'::pg_catalog.regclass, $cmd${SHARD_INDEX_SIZES}$cmd$)
            """,
        )
    }


def test_citus_indexes_hold_their_shard_indexes_values(report, connection):
    """Catches a shard index missed because Citus quoted or shortened its name, a
    size other than the sum of its shard indexes, or an invalid index that is not
    flagged or is reported with shards where it was never built."""
    names = shard_index_names(connection)
    indexes = {
        (i["schema_name"], i["index_name"]): i
        for i in report["analysis_results"]["schema"]["index_analysis"]
    }
    checked = 0
    for (schema, name), table in citus_tables(report):
        if table["citus"]["placements_per_shard"]["max"] != 1:
            continue
        sizes = shard_index_sizes(connection, schema, name)
        for (t_schema, t_name, i_schema, i_name), by_shard in names.items():
            if (t_schema, t_name) != (schema, name):
                continue
            entry = indexes[(i_schema, i_name)]
            found = {
                shardid: sizes[shardid][shard_index]
                for shardid, shard_index in by_shard.items()
                if shard_index in sizes.get(shardid, {})
            }
            assert entry["citus"]["shards_read"] == len(found), (i_schema, i_name)
            assert entry["index_size_bytes"] == sum(found.values()), (i_schema, i_name)
            if entry["is_valid"]:
                assert len(found) == len(by_shard), (i_schema, i_name)
                assert "reason" not in entry["citus"]
            else:
                assert entry["citus"]["reason"] == "invalid_index"
                assert entry["citus"]["shard_count"] == len(by_shard)
            checked += 1
    assert checked, "no index on a Citus table with one placement per shard"


def test_regular_partitions_keep_their_coordinator_values(report, connection):
    """Catches a regular partition on a Citus database given a citus key or
    values other than its own."""
    own = {
        (row["nspname"], row["relname"]): row
        for row in fetch(
            connection,
            """
            SELECT n.nspname, c.relname, c.reltuples::bigint AS reltuples,
                   pg_catalog.pg_relation_size(c.oid) AS size_bytes
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relispartition AND c.relkind IN ('r', 'p')
              AND NOT EXISTS (
                  SELECT 1 FROM pg_catalog.pg_dist_partition p
                  WHERE p.logicalrelid::pg_catalog.oid = c.oid)
            """,
        )
    }
    checked = 0
    for partition in report["analysis_results"]["schema"]["partition_analysis"]:
        key = (partition["partition_schema"], partition["partition_name"])
        if key not in own:
            continue
        assert "citus" not in partition, key
        assert partition["partition_size_bytes"] == own[key]["size_bytes"], key
        assert partition["estimated_rows"] == own[key]["reltuples"], key
        checked += 1
    if not checked:
        pytest.skip("the cluster holds no regular partitioned table")


def test_regular_inheritance_is_reported(report, connection):
    """Catches a regular inheritance child dropped from inheritance_analysis on a
    Citus database."""
    expected = {
        (row["parent_schema"], row["parent_table"], row["child_schema"], row["child"])
        for row in fetch(
            connection,
            """
            SELECT pn.nspname AS parent_schema, p.relname AS parent_table,
                   cn.nspname AS child_schema, c.relname AS child
            FROM pg_catalog.pg_inherits i
            JOIN pg_catalog.pg_class p ON p.oid = i.inhparent
            JOIN pg_catalog.pg_namespace pn ON pn.oid = p.relnamespace
            JOIN pg_catalog.pg_class c ON c.oid = i.inhrelid
            JOIN pg_catalog.pg_namespace cn ON cn.oid = c.relnamespace
            WHERE NOT c.relispartition
              AND NOT pg_catalog.relation_is_a_known_shard(c.oid)
            """,
        )
    }
    if not expected:
        pytest.skip("the cluster holds no table inheritance")
    reported = {
        (r["parent_schema"], r["parent_table"], r["child_schema"], r["child_table"])
        for r in report["analysis_results"]["schema"]["inheritance_analysis"]
    }
    assert expected <= reported


def test_foreign_tables_in_citus_metadata_are_reported_as_foreign_tables(
    report, connection
):
    """Catches a foreign table added to the Citus metadata stopping the run, being
    labeled a Citus table, or missing from the foreign tables."""
    managed = {
        (row["nspname"], row["relname"])
        for row in fetch(
            connection,
            """
            SELECT n.nspname, c.relname
            FROM pg_catalog.pg_dist_partition p
            JOIN pg_catalog.pg_class c ON c.oid = p.logicalrelid::pg_catalog.oid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'f'
            """,
        )
    }
    if not managed:
        pytest.skip("no foreign table is in the Citus metadata")
    features = report["analysis_results"]["features"]
    foreign = {
        (t["schema_name"], t["table_name"])
        for t in features["foreign_data_wrappers"]["foreign_tables"]
    }
    assert managed <= foreign
    assert not managed & set(tables_by_name(report))


def test_distributed_functions_match_the_metadata(report, connection):
    """Catches a distributed function missing from the citus section, or reported
    with another distribution argument or colocation group."""
    expected = {
        (row["function"], row["distribution_argument_index"], row["colocation_id"])
        for row in fetch(
            connection,
            """
            SELECT objid::pg_catalog.regprocedure::text AS function,
                   distribution_argument_index, colocationid AS colocation_id
            FROM pg_catalog.pg_dist_object
            WHERE classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
              AND (distribution_argument_index IS NOT NULL
                   OR colocationid IS NOT NULL)
            """,
        )
    }
    if not expected:
        pytest.skip("the cluster holds no distributed function")
    reported = {
        (f["function"], f["distribution_argument_index"], f["colocation_id"])
        for f in report["analysis_results"]["citus"]["distributed_functions"]
    }
    assert reported == expected


def rows_read(plan, relations):
    """Rows each catalog in relations yields or discards, over every loop of an
    EXPLAIN ANALYZE plan, whether it is read by a scan of the table or its index.
    """
    total = 0
    if plan.get("Relation Name") in relations:
        per_loop = (
            plan.get("Actual Rows", 0)
            + plan.get("Rows Removed by Filter", 0)
            + plan.get("Rows Removed by Index Recheck", 0)
        )
        total += per_loop * plan.get("Actual Loops", 1)
    for child in plan.get("Plans", []):
        total += rows_read(child, relations)
    return total


@pytest.mark.parametrize(
    "command", [citus.TABLE_COMMAND, citus.COLUMN_COMMAND, citus.INDEX_COMMAND]
)
def test_shard_commands_read_only_their_batch(connection, command):
    """Catches a shard command that reads every relation, index or column on a
    node for each batch, which grows with the square of the shard count.

    A one-shard batch reads a few catalog rows for each of the table's columns and
    indexes; reading a node's whole pg_class, or one schema's, takes hundreds
    even on a small cluster. pg_statistic and pg_namespace are not counted: on a
    small node a scan of them is cheaper than their index, and the planner
    switches as the catalog grows.
    """
    shard = fetch(
        connection,
        """
        SELECT n.nspname, (pg_catalog.parse_ident(
                   pg_catalog.shard_name(s.logicalrelid, s.shardid)))[
                 pg_catalog.array_length(pg_catalog.parse_ident(
                   pg_catalog.shard_name(s.logicalrelid, s.shardid)), 1)] AS relname,
               (SELECT pg_catalog.count(*) FROM pg_catalog.pg_attribute a
                WHERE a.attrelid = c.oid AND a.attnum > 0)
               + (SELECT pg_catalog.count(*) FROM pg_catalog.pg_index x
                  WHERE x.indrelid = c.oid) AS width
        FROM pg_catalog.pg_dist_shard s
        JOIN pg_catalog.pg_class c ON c.oid = s.logicalrelid::pg_catalog.oid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
        ORDER BY s.shardid
        LIMIT 1
        """,
    )
    if not shard:
        pytest.skip("the cluster holds no shard")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT nodeid, success, result FROM pg_catalog.run_command_on_all_nodes("
            "'EXPLAIN (ANALYZE, FORMAT JSON) ' || pg_catalog.format(%s,"
            " %s::pg_catalog.name[]::text, %s::pg_catalog.name[]::text))",
            (command, [shard[0]["nspname"]], [shard[0]["relname"]]),
        )
        plans = cursor.fetchall()
    for row in plans:
        assert row["success"], row["result"]
        plan = json.loads(row["result"])[0]["Plan"]
        read = rows_read(plan, {"pg_class", "pg_index", "pg_attribute"})
        assert read <= 10 + 3 * shard[0]["width"], (row["nodeid"], read)


def test_distributed_schemas_match_the_metadata(report, connection):
    """Catches the distributed schemas of a cluster with schema-based sharding
    missing from the citus section, or reported with another colocation group."""
    expected = {
        (row["schema_name"], row["colocation_id"])
        for row in fetch(
            connection,
            """
            SELECT schemaid::pg_catalog.regnamespace::text AS schema_name,
                   colocationid AS colocation_id
            FROM pg_catalog.pg_dist_schema
            """,
        )
    }
    if not expected:
        pytest.skip("the cluster holds no distributed schema")
    reported = {
        (s["schema_name"], s["colocation_id"])
        for s in report["analysis_results"]["citus"]["distributed_schemas"]
    }
    assert reported == expected


def test_an_unpropagated_index_stops_with_its_remedy(connection, dsn):
    """Catches a valid index that exists on no shard, as after CREATE INDEX with
    citus.enable_ddl_propagation off, reported with a partial size instead of
    stopping, or stopping with the partition naming remedy.

    Runs only while such an index exists; the optional section of
    test-data/citus-coverage.sql creates one.
    """
    tables = {
        (row["nspname"], row["relname"])
        for row in fetch(
            connection,
            """
            SELECT n.nspname, c.relname
            FROM pg_catalog.pg_dist_partition p
            JOIN pg_catalog.pg_class c ON c.oid = p.logicalrelid::pg_catalog.oid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p') AND p.repmodel <> 't'
            """,
        )
    }
    valid = {
        (row["nspname"], row["relname"])
        for row in fetch(
            connection,
            """
            SELECT n.nspname, i.relname
            FROM pg_catalog.pg_index x
            JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = i.relnamespace
            WHERE x.indisvalid AND NOT i.relispartition
            """,
        )
    }
    unpropagated = set()
    sizes = {}
    for (t_schema, t_name, i_schema, i_name), by_shard in shard_index_names(
        connection
    ).items():
        if (t_schema, t_name) not in tables or (i_schema, i_name) not in valid:
            continue
        if (t_schema, t_name) not in sizes:
            sizes[(t_schema, t_name)] = shard_index_sizes(connection, t_schema, t_name)
        found = sizes[(t_schema, t_name)]
        if not any(name in found.get(s, {}) for s, name in by_shard.items()):
            unpropagated.add(f"index {i_schema}.{i_name}")
    if not unpropagated:
        pytest.skip("no valid index on a Citus table is missing from all its shards")

    params = parse_dsn(dsn)
    params["options"] = "-c default_transaction_read_only=on"
    discovery = PostgreSQLDiscovery(params, citus_detection=True)
    assert discovery.connect()
    try:
        with pytest.raises(CitusClusterError) as error:
            discovery.run_analysis()
    finally:
        discovery.close()
    message = str(error.value)
    for subject in unpropagated:
        assert subject in message
    assert "citus.enable_ddl_propagation off" in message
    assert "fix_all_partition_shard_index_names" not in message
