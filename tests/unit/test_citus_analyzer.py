"""The Citus module: nodes, colocation groups, tables and placements."""

import pytest

from planetscale_discovery.database import citus
from planetscale_discovery.database.analyzers.citus_analyzer import CitusAnalyzer
from planetscale_discovery.database.citus import CitusClusterError
from tests.fixtures.database_responses import FakeConnection, citus_handlers

COLOCATION = (
    "FROM pg_catalog.pg_dist_colocation",
    [
        {
            "colocation_id": 3,
            "shard_count": 2,
            "replication_factor": 1,
            "distribution_column_type": "integer",
        },
        {
            "colocation_id": 4,
            "shard_count": 1,
            "replication_factor": -1,
            "distribution_column_type": "-",
        },
    ],
)
REGULAR = ("AS regular_count", [{"regular_count": 2}])


def analyzer(*extra):
    connection = FakeConnection(list(extra) + citus_handlers() + [COLOCATION, REGULAR])
    ctx = citus.detect_citus(connection)
    citus.preflight(connection, ctx)
    return CitusAnalyzer(connection, config={"citus": ctx})


class TestCitusAnalyzer:
    def test_the_summary_counts_regular_tables_beside_citus_tables(self):
        """Catches regular tables left out of the Citus summary's table counts."""
        summary = analyzer().analyze()["summary"]
        assert summary["table_counts"] == {
            "distributed": 1,
            "reference": 1,
            "regular": 2,
        }
        assert summary["node_count"] == 3
        assert (
            summary["cluster_storage_bytes"] == 19327047359 + 88322569919 + 89151944383
        )

    def test_a_coordinator_missing_from_the_metadata_is_still_reported(self):
        """Catches the coordinator dropped from nodes, node_count and
        cluster_storage_bytes when pg_dist_node lists the workers only, or its
        placements labeled without its host and port."""
        from tests.fixtures.database_responses import CITUS_NODES

        workers = [n for n in CITUS_NODES if n["groupid"] != 0]
        connection = FakeConnection(
            citus_handlers(nodes=workers) + [COLOCATION, REGULAR]
        )
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        result = CitusAnalyzer(connection, config={"citus": ctx}).analyze()

        coordinator = [n for n in result["nodes"] if n["groupid"] == 0]
        assert len(coordinator) == 1
        assert coordinator[0]["nodeid"] == citus.UNLISTED_NODE_ID
        assert (coordinator[0]["nodename"], coordinator[0]["nodeport"]) == (
            "citus-coordinator",
            5432,
        )
        assert coordinator[0]["database_size_bytes"] == 19327047359
        assert result["summary"]["node_count"] == 3
        assert (
            result["summary"]["cluster_storage_bytes"]
            == 19327047359 + 88322569919 + 89151944383
        )
        by_node = result["shard_placement"]["by_node"]
        assert "citus-coordinator:5432" in by_node
        assert not [label for label in by_node if label.startswith(("group ", "None"))]

    def test_tables_report_logical_and_placement_sizes(self):
        """Catches a reference table's logical size counted once for every placement."""
        tables = {t["table_name"]: t for t in analyzer().analyze()["tables"]}
        reference = tables["reference_t1"]
        assert reference["logical_size_bytes"] == 5801107456
        assert reference["placement_size_bytes"] == 3 * 5801107456
        assert reference["placements_per_shard"] == {"min": 3, "max": 3}
        assert tables["pgbench_branches"]["distribution_column"] == "bid"

    def test_placements_are_summed_per_node(self):
        """Catches a placement on the coordinator left out of the totals for each node."""
        by_node = analyzer().analyze()["shard_placement"]["by_node"]
        assert by_node["citus-coordinator:5432"] == {
            "placements": 1,
            "bytes": 5801107456,
        }
        assert by_node["citus-worker-1:5432"]["placements"] == 2

    def test_colocation_groups_list_their_tables(self):
        """Catches a colocation group listed without its tables."""
        groups = analyzer().analyze()["colocation_groups"]
        assert groups[0]["tables"] == ["public.pgbench_branches"]

    def test_tables_are_grouped_by_colocation_once(self):
        """Catches a scan of every table for each colocation group: schema-based
        sharding gives every schema its own group, so 10k schemas of 100k
        tables make that 1e9 iterations."""
        instance = analyzer()

        class CountingDict(dict):
            scans = 0

            def values(self):
                CountingDict.scans += 1
                return super().values()

        instance.citus.tables = CountingDict(instance.citus.tables)
        instance._get_colocation_groups()
        assert CountingDict.scans == 1

    def test_without_a_citus_table_no_node_is_read(self):
        """Catches the node size read contacting nodes for an unused extension,
        or a cluster storage total that silently omits every node."""
        connection = FakeConnection(
            citus_handlers(tables=[], shards=[], placements=[], node_failures={1: "x"})
            + [COLOCATION, REGULAR]
        )
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        result = CitusAnalyzer(connection, config={"citus": ctx}).analyze()
        assert not any(
            "run_command_on_all_nodes(" in sql for sql in connection.statements()
        )
        assert all(n["database_size_bytes"] is None for n in result["nodes"])
        assert result["summary"]["cluster_storage_bytes"] is None
        assert result["summary"]["table_counts"] == {"regular": 2}

    def test_distributed_schemas_are_reported(self):
        """Catches the distributed schemas of a cluster with schema-based sharding
        reported as an empty list."""
        schemas = (
            "FROM pg_catalog.pg_dist_schema\n",
            [
                {"schema_name": "tenant_acme", "colocation_id": 7},
                {"schema_name": "tenant_globex", "colocation_id": 8},
            ],
        )
        assert analyzer(schemas).analyze()["distributed_schemas"] == schemas[1]

    def test_a_citus_version_without_distributed_schemas_reads_none(self):
        """Catches a query on pg_dist_schema on a Citus version that lacks it, which
        would stop the run as a failed section."""
        connection = FakeConnection(
            citus_handlers(missing_tables=("pg_dist_schema",)) + [COLOCATION, REGULAR]
        )
        ctx = citus.detect_citus(connection)
        citus.preflight(connection, ctx)
        result = CitusAnalyzer(connection, config={"citus": ctx}).analyze()
        assert result["distributed_schemas"] == []
        assert not any(
            "FROM pg_catalog.pg_dist_schema\n" in sql for sql in connection.statements()
        )

    def test_it_requires_the_context_from_detection(self):
        """Catches a CitusAnalyzer created without a context, where every section would
        fail on None. Discovery creates the module only after detection.
        """
        with pytest.raises(ValueError, match="Citus context"):
            CitusAnalyzer(FakeConnection([]), config={})

    def test_a_failing_section_stops_instead_of_going_missing(self):
        """Catches a Citus report written with a section silently absent."""
        failing = ("FROM pg_catalog.pg_dist_object", RuntimeError("permission denied"))
        with pytest.raises(CitusClusterError, match="cluster analysis failed"):
            analyzer(failing).analyze()
