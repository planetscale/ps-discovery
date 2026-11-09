"""Citus detection and the preflight check that stops a discovery early."""

import re
from pathlib import Path

import pytest

from planetscale_discovery.database import citus
from planetscale_discovery.database.citus import CitusClusterError
from tests.fixtures.database_responses import (
    CITUS_NODES,
    CITUS_PLACEMENTS,
    FakeConnection,
    citus_handlers,
)


def prepared(**kwargs):
    connection = FakeConnection(citus_handlers(**kwargs))
    ctx = citus.detect_citus(connection)
    return connection, ctx


class TestDetection:
    def test_a_server_without_the_extension_runs_no_other_citus_query(self):
        """Catches Citus queries reaching a server that does not run Citus."""
        connection = FakeConnection([])
        assert citus.detect_citus(connection) is None
        assert len(connection.executed) == 1

    def test_the_extension_is_detected(self):
        """Catches a database with the citus extension not detected as one."""
        _, ctx = prepared()
        assert ctx.extension_version == "14.1-1"


class TestPreflight:
    def test_a_readable_cluster_passes_and_fills_the_context(self):
        """Catches a healthy cluster stopped by the preflight, or a context left without
        its nodes and tables.
        """
        connection, ctx = prepared()
        citus.preflight(connection, ctx)
        assert ctx.node_role == "coordinator"
        assert ctx.table_counts() == {"distributed": 1, "reference": 1}
        assert ctx.readable_groups() == {0, 1, 2}

    def test_connection_info_holds_no_count_the_summary_also_holds(self):
        """Catches a second copy of the table or node counts, which disagreed
        with analysis_results.citus.summary: Citus types only against
        regular included, and pg_dist_node rows against nodes read."""
        connection, ctx = prepared()
        citus.preflight(connection, ctx)
        assert ctx.connection_info() == {
            "extension_version": "14.1-1",
            "citus_version": "Citus 14.1.0 on x86_64-pc-linux-gnu",
            "node_role": "coordinator",
        }

    def test_a_connection_to_a_worker_stops(self):
        """Catches discovery reporting a worker's view as the cluster's."""
        connection, ctx = prepared(local_group_id=1)
        with pytest.raises(CitusClusterError, match="Connect to the coordinator"):
            citus.preflight(connection, ctx)

    def test_a_missing_function_stops_as_unsupported(self):
        """Catches a Citus version that lacks a function the read needs being run
        anyway.
        """
        connection, ctx = prepared(missing_functions=("relation_is_a_known_shard",))
        with pytest.raises(CitusClusterError) as error:
            citus.preflight(connection, ctx)
        assert "relation_is_a_known_shard" in str(error.value)
        assert "not supported" in str(error.value)

    def test_a_missing_metadata_table_stops_as_unsupported(self):
        """Catches a Citus version that lacks a metadata table being run anyway."""
        connection, ctx = prepared(missing_tables=("pg_dist_placement",))
        with pytest.raises(CitusClusterError) as error:
            citus.preflight(connection, ctx)
        assert "pg_catalog.pg_dist_placement: table not found" in str(error.value)
        assert "not supported" in str(error.value)

    def test_shards_are_hidden_for_the_session_first(self):
        """Catches shards listed as tables when a role default shows them."""
        connection, ctx = prepared()
        connection.executed.clear()
        citus.preflight(connection, ctx)
        first = connection.statements()[0]
        assert (
            "set_config('citus.show_shards_for_app_name_prefixes', '', false)" in first
        )

    def test_the_coordinator_is_read_when_the_metadata_omits_it(self):
        """Catches the coordinator's placements left unread when pg_dist_node does not
        list it. run_command_on_all_nodes() adds the current node in that case.
        """
        workers = [n for n in CITUS_NODES if n["groupid"] != 0]
        connection, ctx = prepared(nodes=workers)
        citus.preflight(connection, ctx)
        assert ctx.readable_groups() == {0, 1, 2}

    def test_a_missing_execute_privilege_names_the_grant(self):
        """Catches a permission error that is reported as a warning."""
        connection, ctx = prepared(no_execute=("run_command_on_all_nodes",))
        with pytest.raises(CitusClusterError) as error:
            citus.preflight(connection, ctx)
        assert "GRANT EXECUTE ON FUNCTION pg_catalog.run_command_on_all_nodes(" in str(
            error.value
        )
        assert "TO discovery" in str(error.value)

    def test_a_missing_select_privilege_names_the_grant(self):
        """Catches a missing SELECT on a pg_dist_* table reported without the GRANT that
        fixes it.
        """
        connection, ctx = prepared(no_select=("pg_dist_placement",))
        with pytest.raises(CitusClusterError) as error:
            citus.preflight(connection, ctx)
        assert "GRANT SELECT ON pg_catalog.pg_dist_placement TO discovery" in str(
            error.value
        )

    @pytest.mark.parametrize("role", ["app-reader", "AppUser"])
    def test_a_grant_names_a_role_that_needs_quoting_quoted(self, role):
        """Catches a GRANT remedy that is a syntax error for app-reader, or grants to
        appuser, the name folded to lowercase, instead of AppUser."""
        # A missing metadata privilege stops the preflight before the remote one
        # is checked, so each remedy is read from its own run.
        connection, ctx = prepared(role=role, no_select=("pg_dist_placement",))
        with pytest.raises(CitusClusterError) as error:
            citus.preflight(connection, ctx)
        assert f'GRANT SELECT ON pg_catalog.pg_dist_placement TO "{role}"' in str(
            error.value
        )
        assert any(
            "pg_catalog.quote_ident(current_user)" in sql
            for sql in connection.statements()
        )

        connection, ctx = prepared(role=role, no_execute=("run_command_on_all_nodes",))
        with pytest.raises(CitusClusterError) as error:
            citus.preflight(connection, ctx)
        assert f') TO "{role}"' in str(error.value)

    def test_reading_from_secondaries_stops(self):
        """Catches write counters read from a secondary, where they are 0."""
        connection, ctx = prepared(use_secondary_nodes="always")
        with pytest.raises(CitusClusterError, match="use_secondary_nodes"):
            citus.preflight(connection, ctx)

    def test_a_shard_only_on_an_inactive_node_stops(self):
        """Catches a shard that no node read, summed as if it held no rows."""
        nodes = [dict(n) for n in CITUS_NODES]
        nodes[2]["isactive"] = False
        connection, ctx = prepared(nodes=nodes)
        with pytest.raises(CitusClusterError) as error:
            citus.preflight(connection, ctx)
        assert "shard 102138 of public.pgbench_branches" in str(error.value)
        assert "citus-worker-2:5432 (primary, inactive)" in str(error.value)

    def test_a_shard_only_on_a_secondary_stops(self):
        """Catches a shard whose only placement is on a secondary, which
        run_command_on_all_nodes() does not read, summed as if it held no rows.
        """
        nodes = [dict(n) for n in CITUS_NODES] + [
            dict(CITUS_NODES[2], nodeid=4, groupid=3, noderole="secondary")
        ]
        placements = [dict(p) for p in CITUS_PLACEMENTS]
        placements[1]["groupid"] = 3
        connection, ctx = prepared(nodes=nodes, placements=placements)
        with pytest.raises(CitusClusterError, match="shard 102138"):
            citus.preflight(connection, ctx)

    def test_inactive_nodes_and_secondaries_are_not_expected_to_answer(self):
        """Catches a run that stops because an inactive node or a secondary, which
        run_command_on_all_nodes() does not contact, returned no result."""
        nodes = [dict(n) for n in CITUS_NODES] + [
            dict(CITUS_NODES[2], nodeid=4, groupid=3, isactive=False),
            dict(CITUS_NODES[2], nodeid=5, groupid=2, noderole="secondary"),
        ]
        connection, ctx = prepared(nodes=nodes)
        citus.preflight(connection, ctx)
        assert 4 not in ctx.readable_node_ids()
        assert 5 not in ctx.readable_node_ids()

    def test_a_worker_without_citus_metadata_stops_up_front(self):
        """Catches a worker whose metadata was never synced, as after an upgrade from
        Citus 10 that was not finalized, stopping the run later with a list of
        unreadable shards instead of naming the node and the remedy."""
        nodes = [dict(n) for n in CITUS_NODES]
        nodes[1]["hasmetadata"] = False
        connection, ctx = prepared(nodes=nodes)
        with pytest.raises(CitusClusterError) as error:
            citus.preflight(connection, ctx)
        message = str(error.value)
        assert (
            "citus-worker-1:5432: Citus metadata is not synced to the node" in message
        )
        assert "citus_finalize_upgrade_to_citus11()" in message
        assert not any(
            "run_command_on_all_nodes(" in sql for sql in connection.statements()
        ), "the check runs before any node is read"

    def test_a_worker_without_metadata_or_placements_passes(self):
        """Catches a worker without metadata that holds no placement stopping the
        run, although none of its shards could be misread."""
        nodes = [dict(n) for n in CITUS_NODES] + [
            dict(CITUS_NODES[2], nodeid=4, groupid=3, hasmetadata=False)
        ]
        connection, ctx = prepared(nodes=nodes)
        citus.preflight(connection, ctx)

    def test_the_coordinator_row_never_needs_hasmetadata(self):
        """Catches the coordinator stopped for hasmetadata false on its own
        pg_dist_node row, although it holds the metadata it would sync."""
        nodes = [dict(n) for n in CITUS_NODES]
        nodes[0]["hasmetadata"] = False
        connection, ctx = prepared(nodes=nodes)
        citus.preflight(connection, ctx)

    def test_a_node_added_during_the_run_stops_it(self):
        """Catches the rows of a node added to pg_dist_node after the preflight
        credited to the coordinator, where a reference table's copy is added to
        the coordinator's and other shards are taken for leftovers of a move."""
        message = a_node_added_during_the_run()
        assert "nodes changed during discovery" in message
        assert "node 9: not in pg_dist_node when discovery started" in message

    def test_node_id_zero_is_rejected_when_the_coordinator_is_listed(self):
        """Catches node id 0 accepted as the coordinator while pg_dist_node lists
        it under its own id, so a second copy of its rows would be read."""
        connection, ctx = prepared()
        citus.preflight(connection, ctx)
        with_node_answering(connection, citus.UNLISTED_NODE_ID)
        with pytest.raises(CitusClusterError, match="node 0: not in pg_dist_node"):
            ctx.shard_statistics(connection)

    def test_an_unlisted_coordinator_is_named_by_its_host(self):
        """Catches a failure on a coordinator that pg_dist_node does not list named
        "node 0", which tells the customer nothing."""
        workers = [n for n in CITUS_NODES if n["groupid"] != 0]
        message = stopped_preflight(
            nodes=workers,
            node_failures={citus.UNLISTED_NODE_ID: "could not connect: timeout"},
        )
        assert "citus-coordinator:5432: unreachable" in message

    def test_a_primary_without_placements_must_answer(self):
        """Catches an active primary that holds no placement left out of the check,
        although run_command_on_all_nodes() connects to it on every read."""
        nodes = [dict(n) for n in CITUS_NODES] + [
            dict(CITUS_NODES[2], nodeid=4, groupid=3, nodename="citus-worker-3")
        ]
        message = stopped_preflight(
            nodes=nodes,
            node_failures={4: "connection to the remote node failed: timeout"},
        )
        assert "citus-worker-3:5432: unreachable" in message

    def test_an_inactive_placement_beside_an_active_one_passes(self):
        """Catches an inactive placement stopping a run in which every shard has an
        active placement.
        """
        placements = CITUS_PLACEMENTS + [
            {"shardid": 102137, "groupid": 2, "shardstate": 3}
        ]
        connection, ctx = prepared(placements=placements)
        citus.preflight(connection, ctx)

    def test_an_unreachable_node_stops_and_is_named(self):
        """Catches an unreachable node that does not stop the run, or that the message
        does not name.
        """
        connection, ctx = prepared(
            node_failures={
                3: "connection to the remote node failed: Connection refused"
            }
        )
        with pytest.raises(CitusClusterError) as error:
            citus.preflight(connection, ctx)
        assert "citus-worker-2:5432: unreachable" in str(error.value)

    def test_a_rejected_login_is_named(self):
        """Catches a node that rejects the discovery role reported as unreachable."""
        connection, ctx = prepared(
            node_failures={2: 'FATAL: password authentication failed for user "d"'}
        )
        with pytest.raises(CitusClusterError, match="rejected the discovery role"):
            citus.preflight(connection, ctx)

    def test_a_failed_classification_stops(self):
        """Catches every Citus table reported with its shell's values."""
        handlers = [("END AS table_type", RuntimeError("permission denied"))]
        connection = FakeConnection(handlers + citus_handlers())
        ctx = citus.detect_citus(connection)
        with pytest.raises(CitusClusterError, match="metadata could not be read"):
            citus.preflight(connection, ctx)

    def test_it_is_a_connection_error(self):
        """Catches a Citus failure that bypasses the CLI's fatal path, which catches
        ConnectionError.
        """
        assert issubclass(CitusClusterError, ConnectionError)


class TestCitusErrors:
    def test_a_failure_is_raised_as_a_citus_cluster_error(self):
        """Catches a failure in a Citus read escaping as a plain exception, which
        run_analysis() records as a module failure and writes a report with shell
        values."""
        with pytest.raises(CitusClusterError) as error:
            with citus.citus_errors("Values could not be read.", "schema analysis"):
                raise KeyError("pgbench_branches")
        assert error.value.summary == "Values could not be read."
        assert error.value.problems == [
            {
                "subject": "schema analysis",
                "cause": "failed",
                "detail": "'pgbench_branches'",
            }
        ]
        assert isinstance(error.value.__cause__, KeyError)

    def test_a_citus_cluster_error_passes_through_unchanged(self):
        """Catches a node or privilege problem replaced by a generic summary that no
        longer names the node or the GRANT."""
        original = CitusClusterError(
            "A node is down.", [{"subject": "n", "cause": "x"}]
        )
        with pytest.raises(CitusClusterError) as error:
            with citus.citus_errors("Values could not be read.", "schema analysis"):
                raise original
        assert error.value is original


class TestUnusedExtension:
    """The extension is installed, and no table was ever distributed."""

    NO_TABLES = dict(tables=[], shards=[], placements=[])

    def test_no_node_is_contacted(self):
        """Catches a single node whose loopback login fails stopping a run that
        has no Citus table to misreport."""
        connection, ctx = prepared(
            node_failures={1: "connection to the remote node localhost:5432 failed"},
            **self.NO_TABLES,
        )
        citus.preflight(connection, ctx)
        assert not any(
            "run_command_on_all_nodes(" in sql for sql in connection.statements()
        )

    def test_checks_that_only_the_node_read_needs_are_skipped(self):
        """Catches the secondary node setting or the run_command_on_all_nodes privilege
        stopping a run that reads no node.
        """
        connection, ctx = prepared(
            use_secondary_nodes="always",
            no_execute=("run_command_on_all_nodes",),
            **self.NO_TABLES,
        )
        citus.preflight(connection, ctx)

    def test_unreadable_metadata_still_stops(self):
        """Catches a run that continues without SELECT on pg_dist_partition. Without it,
        no Citus table cannot be told apart from a Citus table that cannot be read.
        """
        connection, ctx = prepared(no_select=("pg_dist_partition",), **self.NO_TABLES)
        with pytest.raises(CitusClusterError, match="pg_dist_partition"):
            citus.preflight(connection, ctx)

    def test_a_worker_connection_still_stops(self):
        """Catches a worker without synced metadata, whose pg_dist_partition is
        empty while it holds shards, being read as a regular database."""
        connection, ctx = prepared(local_group_id=1, **self.NO_TABLES)
        with pytest.raises(CitusClusterError, match="Connect to the coordinator"):
            citus.preflight(connection, ctx)

    def test_the_node_read_checks_apply_once_a_citus_table_exists(self):
        """Catches the node read checks skipped on a database that holds Citus tables."""
        connection, ctx = prepared(no_execute=("run_command_on_all_nodes",))
        with pytest.raises(CitusClusterError, match="cannot read the Citus shards"):
            citus.preflight(connection, ctx)


class TestOptionalFeatures:
    def test_features_are_detected_rather_than_assumed(self):
        """Catches SQL that names a column or argument an older Citus lacks."""
        connection, ctx = prepared(
            missing_tables=("pg_dist_schema",), give_warning=False
        )
        citus.preflight(connection, ctx)
        assert not ctx.has_pg_dist_schema
        assert not ctx.has_connection_error_warning
        sql = " ".join(connection.statements())
        assert "pg_dist_schema)" not in citus._classification_sql(ctx)
        assert "give_warning_for_connection_errors :=" not in sql

    def test_the_classification_follows_citus_own_order(self):
        """Catches a reordered CASE, which mislabels tables that match two
        branches (a reference table also has colocationid != 0).

        Agreement with Citus's own citus_tables view is checked against a live
        cluster in tests/integration/test_citus_discovery.py.
        """
        _, ctx = prepared()
        ctx.has_pg_dist_schema = True
        sql = citus._classification_sql(ctx)
        order = [
            sql.index(fragment)
            for fragment in (
                "THEN 'schema'",
                "WHEN p.partkey IS NOT NULL THEN 'distributed'",
                "WHEN p.repmodel = 't' THEN 'reference'",
                "WHEN p.colocationid = 0 THEN 'local'",
                "ELSE 'distributed'",
            )
        ]
        assert order == sorted(order)


class TestNames:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("orders_102008", "orders_102008"),
            ("sales.orders_102008", "orders_102008"),
            ('"Sales"."Orders_102008"', "Orders_102008"),
            ('"a.b"."c""d_1"', 'c"d_1'),
        ],
    )
    def test_shard_names_lose_their_schema(self, name, expected):
        """Catches a shard in a quoted schema, or one other than public, that maps to
        nothing."""
        assert citus.unqualified(name) == expected


class TestSqlFragments:
    def test_fragments_are_empty_without_citus(self):
        """Catches a Citus predicate added to an analyzer's SQL on a server without
        Citus, where every analyzer must run exactly today's SQL.
        """
        assert citus.relation_filter(None, "c.oid", "n.oid") == ""
        assert citus.namespace_filter(None, "n.oid") == ""
        assert citus.function_filter(None, "p.oid", "n.oid") == ""
        assert citus.type_filter(None, "t.oid", "n.oid", "t.typrelid") == ""
        assert citus.event_trigger_filter(None, "pg_event_trigger.oid") == ""
        assert citus.namespace_name_filter(None, "schemaname") == ""

    def test_where_turns_conditions_into_a_clause_or_nothing(self):
        """Catches a WHERE clause added without Citus, which changes the SQL that
        every non-Citus run, workload capture's schema read included, executes."""
        _, ctx = prepared()
        assert citus.where(citus.relation_filter(None, "c.oid")) == ""
        clause = citus.where(citus.relation_filter(ctx, "c.oid"))
        assert clause.startswith(
            "WHERE NOT pg_catalog.relation_is_a_known_shard(c.oid)"
        )
        assert " AND NOT EXISTS" in clause

    def test_extension_membership_is_matched_by_identity(self):
        """Catches a customer schema named like a Citus schema being hidden."""
        _, ctx = prepared()
        fragment = citus.namespace_filter(ctx, "n.oid")
        assert "deptype = 'e'" in fragment
        assert not re.search(r"nspname\s+(NOT\s+)?IN", fragment)


TROUBLESHOOTING = Path(__file__).parents[2] / "docs" / "troubleshooting.md"


def documented_messages():
    """The messages quoted in the troubleshooting table for a stopped Citus run."""
    text = TROUBLESHOOTING.read_text()
    section = text.split("### Citus Discovery Stopped", 1)[1].split("\n### ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| `")]
    return {m for row in rows for m in re.findall(r"`([^`]+)`", row.split("|")[1])}


def stopped_preflight(**kwargs):
    connection, ctx = prepared(**kwargs)
    with pytest.raises(CitusClusterError) as error:
        citus.preflight(connection, ctx)
    return str(error.value)


def a_node_returns_nothing():
    connection, ctx = prepared()
    connection.handlers.insert(
        0,
        (
            "run_command_on_all_nodes(",
            lambda sql, params: [
                {"nodeid": 1, "success": True, "result": "1"},
                {"nodeid": 2, "success": True, "result": "1"},
            ],
        ),
    )
    with pytest.raises(CitusClusterError) as error:
        citus.preflight(connection, ctx)
    return str(error.value)


def an_index_on_no_shard():
    from tests.fixtures.database_responses import CITUS_REMOTE_INDEX_ROWS

    remote = {
        nodeid: [
            r
            for r in rows
            if not r["index_relname"].startswith("pgbench_branches_pkey_")
        ]
        for nodeid, rows in CITUS_REMOTE_INDEX_ROWS.items()
    }
    connection, ctx = prepared(remote_indexes=remote)
    citus.preflight(connection, ctx)
    with pytest.raises(CitusClusterError) as error:
        ctx.shard_statistics(connection)
    return str(error.value)


def with_node_answering(connection, nodeid):
    """Add a node that answers every run_command_on_all_nodes() call, as one
    added to pg_dist_node after the preflight read it would."""
    original = next(
        r for m, r in connection.handlers if m == "run_command_on_all_nodes("
    )

    def respond(sql, params):
        rows = original(sql, params)
        return rows + [dict(rows[-1], nodeid=nodeid)]

    connection.handlers.insert(0, ("run_command_on_all_nodes(", respond))


def a_node_added_during_the_run():
    connection, ctx = prepared()
    citus.preflight(connection, ctx)
    with_node_answering(connection, 9)
    with pytest.raises(CitusClusterError) as error:
        ctx.shard_statistics(connection)
    return str(error.value)


def an_index_dropped_during_the_read():
    from tests.fixtures.database_responses import (
        CITUS_INDEX_SHARD_NAMES,
        CITUS_INDEXES,
        CITUS_REMOTE_INDEX_ROWS,
    )

    kept = [r for r in CITUS_INDEXES if r["index_name"] != "pgbench_branches_pkey"]
    kept_oids = {r["index_oid"] for r in kept}
    remote = {
        nodeid: [
            r
            for r in rows
            if not r["index_relname"].startswith("pgbench_branches_pkey_")
        ]
        for nodeid, rows in CITUS_REMOTE_INDEX_ROWS.items()
    }
    connection, ctx = prepared(
        remote_indexes=remote,
        index_shard_names=[
            r for r in CITUS_INDEX_SHARD_NAMES if r["index_oid"] in kept_oids
        ],
    )
    citus.preflight(connection, ctx)
    reads = []

    def index_list(sql, params):
        reads.append(sql)
        return CITUS_INDEXES if len(reads) == 1 else kept

    connection.handlers.insert(0, ("i.relname AS index_name", index_list))
    with pytest.raises(CitusClusterError) as error:
        ctx.shard_statistics(connection)
    return str(error.value)


def a_shard_only_on_an_inactive_node():
    nodes = [dict(n) for n in CITUS_NODES]
    nodes[2]["isactive"] = False
    return stopped_preflight(nodes=nodes)


# Each message the troubleshooting table quotes, and the situation it names.
SITUATIONS = {
    "Discovery is connected to a Citus worker": lambda: stopped_preflight(
        local_group_id=1
    ),
    "<node>: unreachable": lambda: stopped_preflight(
        node_failures={3: "connection to the remote node failed: Connection refused"}
    ),
    "<node>: returned no result": a_node_returns_nothing,
    "<node>: the node rejected the discovery role": lambda: stopped_preflight(
        node_failures={2: 'FATAL: password authentication failed for user "d"'}
    ),
    "no active placement on a node that discovery can read": (
        a_shard_only_on_an_inactive_node
    ),
    "node <id>: not in pg_dist_node when discovery started": (
        a_node_added_during_the_run
    ),
    "<node>: Citus metadata is not synced to the node": lambda: stopped_preflight(
        nodes=[dict(n, hasmetadata=n["groupid"] == 0) for n in CITUS_NODES]
    ),
    "missing EXECUTE privilege": lambda: stopped_preflight(
        no_execute=("run_command_on_all_nodes",)
    ),
    "missing SELECT privilege": lambda: stopped_preflight(
        no_select=("pg_dist_placement",)
    ),
    "citus.use_secondary_nodes": lambda: stopped_preflight(
        use_secondary_nodes="always"
    ),
    "index <name>: not found on N of M shards": an_index_on_no_shard,
    "index <name>: dropped during discovery": an_index_dropped_during_the_read,
    "function not found": lambda: stopped_preflight(
        missing_functions=("relation_is_a_known_shard",)
    ),
    "table not found; this Citus version is not supported": lambda: stopped_preflight(
        missing_tables=("pg_dist_placement",)
    ),
}


def as_pattern(message):
    pattern = re.escape(message)
    pattern = pattern.replace(re.escape("<node>"), r"[\w.-]+:\d+")
    pattern = pattern.replace(re.escape("<name>"), r"\S+")
    pattern = pattern.replace(re.escape("<id>"), r"\d+")
    return pattern.replace(re.escape("N of M"), r"\d+ of \d+")


class TestTroubleshootingMessages:
    def test_every_documented_message_has_a_situation(self):
        """Catches a row added to the troubleshooting table, or a situation removed,
        without the other, so a message is documented that nothing checks."""
        assert documented_messages() == set(SITUATIONS)

    @pytest.mark.parametrize("message", sorted(SITUATIONS))
    def test_the_run_prints_the_documented_message(self, message):
        """Catches a stop message reworded in the code, so the customer cannot find
        its remedy in the troubleshooting table."""
        printed = SITUATIONS[message]()
        assert re.search(as_pattern(message), printed), (message, printed)
