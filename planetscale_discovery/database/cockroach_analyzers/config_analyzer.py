"""CockroachDB configuration analysis.

Reuses PostgreSQL settings collection, then overwrites version_info so
the report records CockroachDB's real version instead of PG 13.0.
"""

from typing import Any, Dict, List, Optional

from planetscale_discovery.database.analyzers.config_analyzer import ConfigAnalyzer
from planetscale_discovery.database.cockroach import (
    is_user_zone_target,
    parse_cockroach_version,
)
from planetscale_discovery.database.cockroach_compat import _parse_num_replicas
from planetscale_discovery.database.cockroach_privileges import (
    probe_discovery_privileges,
)


class CockroachConfigAnalyzer(ConfigAnalyzer):
    """PostgreSQL config analyzer with CockroachDB version and cluster facts."""

    def analyze(self) -> Dict[str, Any]:
        results = super().analyze()
        results["version_info"] = self._get_cockroach_version_info(
            results.get("version_info") or {}
        )
        results["cockroach_cluster"] = self._get_cluster_info()
        results["discovery_privileges"] = probe_discovery_privileges(
            self.connection, self.add_error, self.add_warning
        )
        return results

    def _get_cockroach_version_info(self, pg_info: Dict[str, Any]) -> Dict[str, Any]:
        version_string = pg_info.get("version_string")
        if not version_string:
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute("SELECT version()")
                    version_string = cursor.fetchone()["version"]
            except Exception as exc:
                self.add_error("Could not read version()", exc)
                return pg_info

        parsed = parse_cockroach_version(version_string) or {}
        info = dict(pg_info)
        info.update(
            {
                "version_string": version_string,
                "distribution": "CockroachDB",
                "engine": "cockroachdb",
                "major_version": parsed.get("major", 0),
                "minor_version": parsed.get("minor", 0),
                "patch_version": parsed.get("patch", 0),
                "product_version": parsed.get("version", "unknown"),
                "pg_compat_version_num": pg_info.get("version_num", 130000),
                "pg_compat_note": (
                    "server_version_num is a PostgreSQL compatibility value "
                    "(130000). product_version is the CockroachDB release."
                ),
            }
        )
        return info

    def _get_cluster_info(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "node_count": None,
            "replication_factor_configured": None,
            "replication_factor_actual_min": None,
            "replication_factor_actual_max": None,
            "rangefeed_enabled": None,
            "zone_configurations": [],
            "size_semantics": {
                "logical_bytes": (
                    "total_stats.live_bytes from crdb_internal.tenant_span_stats: "
                    "one copy, uncompressed, includes indexes. Quote this as "
                    "database size."
                ),
                "do_not_quote_as_size": [
                    "approximate_total_stats.live_bytes (replicated)",
                    "approximate_disk_bytes (compressed, all replicas, MVCC garbage)",
                    "sum(range_size) from SHOW RANGES (overcounts shared ranges)",
                ],
            },
        }

        info["node_count"] = self._scalar_int(
            "SELECT count(*) AS n FROM crdb_internal.gossip_nodes",
            "node_count",
        )
        info["rangefeed_enabled"] = self._show_cluster_setting("kv.rangefeed.enabled")
        current = self._current_database()
        info["zone_configurations"] = [
            zone
            for zone in self._zone_configurations()
            if is_user_zone_target(zone.get("target") or zone.get("zone_name"), current)
        ]
        info["replication_factor_configured"] = self._configured_replicas(
            info["zone_configurations"]
        )
        actual = self._actual_replicas()
        info["replication_factor_actual_min"] = actual.get("min")
        info["replication_factor_actual_max"] = actual.get("max")
        info["over_replicated_zones"] = self._over_replicated_zones(
            info["zone_configurations"], info["node_count"]
        )
        info["cluster_regions"] = self._cluster_regions()
        info["license"] = {
            "organization": self._show_cluster_setting("cluster.organization")
        }
        # Over-replicated zones are reported once, as an over_replicated_zone
        # analysis gap (see DatabaseDiscovery._add_cockroach_gaps), not as an
        # analyzer error as well.
        return info

    def _scalar_int(self, sql: str, label: str):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                row = cursor.fetchone()
            if not row:
                return None
            return int(next(iter(row.values())))
        except Exception as exc:
            self.add_warning(f"Could not read {label}: {exc}")
            return None

    def _show_cluster_setting(self, name: str):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"SHOW CLUSTER SETTING {name}")
                row = cursor.fetchone()
            if not row:
                return None
            value = next(iter(row.values()))
            if isinstance(value, str):
                lowered = value.lower()
                if lowered in ("t", "true", "on"):
                    return True
                if lowered in ("f", "false", "off"):
                    return False
            return value
        except Exception as exc:
            self.add_warning(f"Could not read cluster setting {name}: {exc}")
            return None

    def _cluster_regions(self) -> List[str]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SHOW REGIONS FROM CLUSTER")
                rows = [dict(row) for row in cursor.fetchall()]
            names = []
            for row in rows:
                name = row.get("region") or row.get("region_name") or row.get("name")
                if name:
                    names.append(str(name))
            return names
        except Exception as exc:
            self.add_warning(f"Could not read cluster regions: {exc}")
            return []

    def _current_database(self) -> Optional[str]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT current_database() AS current_database")
                row = cursor.fetchone() or {}
            return row.get("current_database")
        except Exception:
            return None

    def _zone_configurations(self) -> List[Dict[str, Any]]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SHOW ZONE CONFIGURATIONS")
                return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            self.add_warning(f"Could not read zone configurations: {exc}")
            return []

    def _configured_replicas(self, zones: List[Dict[str, Any]]):
        replicas = []
        for zone in zones:
            raw = zone.get("raw_config_sql") or zone.get("config_sql") or ""
            parsed = _parse_num_replicas(raw)
            if parsed is not None:
                replicas.append(parsed)
        return max(replicas) if replicas else None

    def _over_replicated_zones(
        self, zones: List[Dict[str, Any]], node_count
    ) -> List[Dict[str, Any]]:
        if node_count is None:
            return []
        over = []
        for zone in zones:
            raw = zone.get("raw_config_sql") or zone.get("config_sql") or ""
            replicas = _parse_num_replicas(raw)
            if replicas is None:
                continue
            if replicas > int(node_count):
                over.append(
                    {
                        "target": zone.get("target") or zone.get("zone_name") or "zone",
                        "num_replicas": replicas,
                        "node_count": int(node_count),
                    }
                )
        return over

    def _actual_replicas(self) -> Dict[str, Any]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        max(array_length(replicas, 1)) AS max_replicas,
                        min(array_length(replicas, 1)) AS min_replicas
                    FROM crdb_internal.ranges_no_leases
                    """)
                row = cursor.fetchone() or {}
            return {
                "max": (
                    int(row["max_replicas"])
                    if row.get("max_replicas") is not None
                    else None
                ),
                "min": (
                    int(row["min_replicas"])
                    if row.get("min_replicas") is not None
                    else None
                ),
            }
        except Exception as exc:
            self.add_warning(f"Could not read actual replica counts: {exc}")
            return {"max": None, "min": None}
