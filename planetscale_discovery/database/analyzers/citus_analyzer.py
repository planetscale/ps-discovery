"""
Citus Cluster Analysis Module
Reports the nodes, colocation groups, tables and shard placements of a Citus
cluster. It runs only when the citus extension is detected.
"""

from typing import Any, Dict, List

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer
from planetscale_discovery.database.citus import (
    DATABASE_SIZE_COMMAND,
    TABLE_RELKINDS,
    CitusContext,
    citus_errors,
    fetchall,
    relation_filter,
    run_on_all_nodes,
)


class CitusAnalyzer(DatabaseAnalyzer):
    """Analyzes the topology and data placement of a Citus cluster."""

    def __init__(self, connection, config=None, logger=None):
        super().__init__(connection, config, logger)
        ctx = self.config.get("citus")
        if ctx is None:
            raise ValueError("CitusAnalyzer needs the Citus context from detection")
        self.citus: CitusContext = ctx

    def analyze(self) -> Dict[str, Any]:
        """Run the Citus analysis. Any failure stops the discovery."""
        with citus_errors("The Citus cluster analysis failed.", "citus analysis"):
            return self._analyze()

    def _analyze(self) -> Dict[str, Any]:
        ctx = self.citus
        stats = ctx.shard_statistics(self.connection)
        nodes = self._get_nodes()
        tables = self._get_tables(stats)
        regular_count = self._count_regular_tables()

        type_counts = dict(ctx.table_counts())
        type_counts["regular"] = regular_count
        logical = sum(t["logical_size_bytes"] for t in tables)
        # None when no node was read, rather than a total that omits every node.
        storage = (
            sum(n["database_size_bytes"] or 0 for n in nodes) if ctx.tables else None
        )

        return {
            "version": {
                "extension_version": ctx.extension_version,
                "citus_version": ctx.citus_version,
                "node_role": ctx.node_role,
            },
            "nodes": nodes,
            "colocation_groups": self._get_colocation_groups(),
            "tables": tables,
            "shard_placement": self._get_shard_placement(stats),
            "distributed_schemas": self._get_distributed_schemas(),
            "distributed_functions": self._get_distributed_functions(),
            "summary": {
                "node_count": len(nodes),
                "table_counts": type_counts,
                "citus_table_logical_size_bytes": logical,
                "cluster_storage_bytes": storage,
            },
        }

    def _get_nodes(self) -> List[Dict[str, Any]]:
        """pg_dist_node, with each node's database size.

        The sum of the sizes is the cluster's storage, every placement included.
        Without a Citus table, no node is contacted, and the sizes are None.
        """
        ctx = self.citus
        sizes = (
            run_on_all_nodes(self.connection, ctx, DATABASE_SIZE_COMMAND)
            if ctx.tables
            else {}
        )
        nodes = []
        for node in ctx.nodes:
            size = sizes.get(node["nodeid"])
            nodes.append(dict(node, database_size_bytes=int(size) if size else None))
        known = {node["nodeid"] for node in ctx.nodes}
        for nodeid, size in sizes.items():
            # The coordinator, when it is not recorded in the metadata.
            if nodeid not in known:
                nodes.append(
                    {
                        "nodeid": nodeid,
                        "groupid": ctx.local_group_id,
                        "nodename": ctx.local_hostname,
                        "nodeport": ctx.port,
                        "noderole": "primary",
                        "nodecluster": ctx.cluster_name,
                        "isactive": True,
                        "shouldhaveshards": False,
                        "hasmetadata": True,
                        "database_size_bytes": int(size) if size else None,
                    }
                )
        return nodes

    def _get_colocation_groups(self) -> List[Dict[str, Any]]:
        groups = fetchall(
            self.connection,
            """
            SELECT colocationid AS colocation_id,
                   shardcount AS shard_count,
                   replicationfactor AS replication_factor,
                   distributioncolumntype::pg_catalog.regtype::text
                       AS distribution_column_type
            FROM pg_catalog.pg_dist_colocation
            ORDER BY colocationid
            """,
        )
        # Grouped once: schema-based sharding gives every schema its own group.
        tables_by_group: Dict[int, List[str]] = {}
        for t in self.citus.tables.values():
            tables_by_group.setdefault(t.colocation_id, []).append(
                f"{t.schema_name}.{t.table_name}"
            )
        for group in groups:
            group["tables"] = tables_by_group.get(group["colocation_id"], [])
        return groups

    def _get_tables(self, stats) -> List[Dict[str, Any]]:
        tables = []
        for table in self.citus.tables.values():
            combined = stats.tables[table.oid]
            tables.append(
                {
                    "schema_name": table.schema_name,
                    "table_name": table.table_name,
                    **table.describe(),
                    "placements_per_shard": combined["placements_per_shard"],
                    "estimated_rows": combined["estimated_rows"],
                    "logical_size_bytes": combined["total_size_bytes"],
                    "placement_size_bytes": combined["placement_size_bytes"],
                    "shards_read": combined["shards_read"],
                    "shards_never_analyzed": combined["shards_never_analyzed"],
                    "inactive_placements": combined["inactive_placements"],
                }
            )
        return tables

    def _get_shard_placement(self, stats) -> Dict[str, Any]:
        """Placement counts and bytes per node and per table, not per shard."""
        ctx = self.citus
        labels: Dict[int, str] = {}
        for node in ctx.nodes:
            if node.get("isactive") and node.get("noderole") == "primary":
                labels[node["groupid"]] = f"{node['nodename']}:{node['nodeport']}"
        if ctx.local_group_id is not None and not ctx.coordinator_in_metadata():
            labels[ctx.local_group_id] = ctx.coordinator_label()

        by_node: Dict[str, Dict[str, int]] = {}
        by_table: Dict[str, Dict[str, Dict[str, int]]] = {}
        for placement in stats.placements:
            node = labels.get(placement["groupid"], f"group {placement['groupid']}")
            table = ctx.tables[placement["table_oid"]]
            name = f"{table.schema_name}.{table.table_name}"
            totals = by_node.setdefault(node, {"placements": 0, "bytes": 0})
            totals["placements"] += 1
            totals["bytes"] += placement["total_size_bytes"]
            per_node = by_table.setdefault(name, {}).setdefault(
                node, {"placements": 0, "bytes": 0}
            )
            per_node["placements"] += 1
            per_node["bytes"] += placement["total_size_bytes"]
        return {"by_node": by_node, "by_table": by_table}

    def _get_distributed_schemas(self) -> List[Dict[str, Any]]:
        if not self.citus.has_pg_dist_schema:
            return []
        return fetchall(
            self.connection,
            """
            SELECT schemaid::pg_catalog.regnamespace::text AS schema_name,
                   colocationid AS colocation_id
            FROM pg_catalog.pg_dist_schema
            ORDER BY 1
            """,
        )

    def _get_distributed_functions(self) -> List[Dict[str, Any]]:
        return fetchall(
            self.connection,
            """
            SELECT objid::pg_catalog.regprocedure::text AS function,
                   distribution_argument_index,
                   colocationid AS colocation_id
            FROM pg_catalog.pg_dist_object
            WHERE classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
              AND (distribution_argument_index IS NOT NULL OR colocationid IS NOT NULL)
            ORDER BY 1
            """,
        )

    def _count_regular_tables(self) -> int:
        rows = fetchall(
            self.connection,
            f"""
            SELECT pg_catalog.count(*) AS regular_count
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN {TABLE_RELKINDS}
            AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
            {relation_filter(self.citus, "c.oid", "n.oid")}
            AND NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_dist_partition p
                WHERE p.logicalrelid::pg_catalog.oid = c.oid
            )
            """,
        )
        return int(rows[0]["regular_count"]) if rows else 0
