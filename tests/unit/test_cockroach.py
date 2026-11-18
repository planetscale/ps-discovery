"""Unit tests for CockroachDB detection, flavor, and discovery routing."""

import sqlite3
from unittest.mock import MagicMock, patch

from planetscale_discovery.database.cockroach import (
    allow_unsafe_internals,
    detect_cockroach_flavor,
    is_cockroach_version_string,
    is_system_database,
    is_user_zone_target,
    parse_cockroach_version,
    quote_ident,
    restricts_internals_by_default,
    user_database_sql,
)
from planetscale_discovery.database.discovery import PostgreSQLDiscovery

CRDB_VERSION = (
    "CockroachDB CCL v25.2.4 (x86_64-pc-linux-gnu, built 2025/07/31 21:20:50, "
    "go1.23.7 X:nocoverageredesign)"
)
CRDB_26_VERSION = (
    "CockroachDB CCL v26.2.10 (aarch64-apple-darwin21.2, built 2026/10/27 22:16:16, "
    "go1.25.13)"
)
UNKNOWN_VARIABLE = 'unrecognized configuration parameter "allow_unsafe_internals"'
PG_VERSION = "PostgreSQL 16.4 (Ubuntu 16.4-1.pgdg22.04+1) on x86_64-pc-linux-gnu"


class TestCockroachDetection:
    def test_detects_cockroach_version_string(self):
        assert is_cockroach_version_string(CRDB_VERSION)
        assert not is_cockroach_version_string(PG_VERSION)
        assert not is_cockroach_version_string(None)
        assert not is_cockroach_version_string("")

    def test_parses_real_product_version(self):
        parsed = parse_cockroach_version(CRDB_VERSION)
        assert parsed["engine"] == "cockroachdb"
        assert parsed["version"] == "25.2.4"
        assert parsed["major"] == 25
        assert parsed["minor"] == 2
        assert parsed["patch"] == 4

    def test_postgres_version_is_not_cockroach(self):
        assert parse_cockroach_version(PG_VERSION) is None
        assert detect_cockroach_flavor(PG_VERSION) is None

    def test_flavor_records_pg_compat_warning(self):
        flavor = detect_cockroach_flavor(CRDB_VERSION)
        assert flavor["engine"] == "cockroachdb"
        assert flavor["version"] == "25.2.4"
        assert flavor["pg_compat_version_num"] == 130000
        assert "130000" in flavor["pg_compat_note"]

    def test_user_zone_targets_drop_system_and_other_databases(self):
        assert is_user_zone_target("DATABASE tpcc", "tpcc")
        assert is_user_zone_target("TABLE tpcc.public.warehouse", "tpcc")
        assert is_user_zone_target("TABLE public.warehouse", "tpcc")
        assert not is_user_zone_target("RANGE meta", "tpcc")
        assert not is_user_zone_target("RANGE liveness", "tpcc")
        assert not is_user_zone_target("RANGE system", "tpcc")
        assert not is_user_zone_target("DATABASE system", "tpcc")
        assert not is_user_zone_target("DATABASE phantom_app", "tpcc")
        assert not is_user_zone_target(".default", "tpcc")
        assert not is_user_zone_target(
            "PARTITION p1 OF INDEX crdb_features.public.t_partitioned@t_partitioned_pkey",
            "tpcc",
        )
        assert is_user_zone_target(
            "PARTITION us_east OF INDEX mr_app.public.users_rbr@users_rbr_pkey",
            "mr_app",
        )
        assert is_user_zone_target("TABLE public.users_rbr", "mr_app")
        assert not is_user_zone_target(
            "PARTITION p1 OF TABLE crdb_features.public.t_partitioned",
            "mr_app",
        )

    def test_defaultdb_and_postgres_count_only_when_connected(self):
        assert is_system_database("defaultdb", "tpcc")
        assert is_system_database("postgres", "tpcc")
        assert not is_system_database("defaultdb", "defaultdb")
        assert not is_system_database("postgres", "postgres")
        assert is_system_database("system", "system")
        assert not is_system_database("tpcc", "tpcc")

    def test_quote_ident_escapes_embedded_double_quotes(self):
        assert quote_ident('weird"name') == '"weird""name"'
        assert quote_ident("public") == '"public"'

    def test_user_database_sql_matches_is_system_database(self):
        conn = sqlite3.connect(":memory:")
        names = ("system", "postgres", "defaultdb", "template1", "tpcc")
        for connected in names:
            conn.create_function("current_database", 0, lambda c=connected: c)
            for name in names:
                (kept,) = conn.execute(
                    f"SELECT {user_database_sql('name')} FROM (SELECT ? AS name)",
                    (name,),
                ).fetchone()
                assert bool(kept) == (not is_system_database(name, connected)), (
                    name,
                    connected,
                )
        conn.close()

    def test_defaultdb_zone_targets_count_when_connected(self):
        assert is_user_zone_target("TABLE defaultdb.public.orders", "defaultdb")
        assert is_user_zone_target(
            "PARTITION us_east OF INDEX defaultdb.public.orders@orders_pkey",
            "defaultdb",
        )
        assert not is_user_zone_target("TABLE defaultdb.public.orders", "tpcc")


class TestDiscoveryRouting:
    def _connect_with_version(self, version):
        discovery = PostgreSQLDiscovery(
            {"host": "127.0.0.1", "port": 26257, "database": "tpcc", "user": "root"}
        )
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = {
            "version": version,
            "current_database": "tpcc",
            "current_user": "root",
        }
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = None
        with patch(
            "planetscale_discovery.database.discovery.psycopg2.connect",
            return_value=conn,
        ):
            assert discovery.connect() is True
        return discovery

    def test_postgres_path_unchanged(self):
        discovery = self._connect_with_version(PG_VERSION)
        assert discovery.results["engine"] == "postgres"
        assert "flavor" not in discovery.results
        classes = discovery._analyzer_classes()
        from planetscale_discovery.database.analyzers.schema_analyzer import (
            SchemaAnalyzer,
        )

        assert classes["schema"] is SchemaAnalyzer

    def test_cockroach_swaps_analyzers_and_engine(self):
        discovery = self._connect_with_version(CRDB_VERSION)
        assert discovery.results["engine"] == "cockroachdb"
        assert discovery.results["flavor"]["version"] == "25.2.4"
        classes = discovery._analyzer_classes()
        from planetscale_discovery.database.cockroach_analyzers import (
            CockroachSchemaAnalyzer,
            CockroachFeatureAnalyzer,
            CockroachPerformanceAnalyzer,
            CockroachConfigAnalyzer,
        )

        assert classes["schema"] is CockroachSchemaAnalyzer
        assert classes["features"] is CockroachFeatureAnalyzer
        assert classes["performance"] is CockroachPerformanceAnalyzer
        assert classes["config"] is CockroachConfigAnalyzer

    def test_empty_inventory_is_a_high_gap_not_success(self):
        discovery = self._connect_with_version(CRDB_VERSION)
        discovery.results["analysis_results"] = {
            "schema": {"table_analysis": []},
            "features": {},
        }
        discovery._add_cockroach_gaps()
        types = {g["type"] for g in discovery.results["analysis_gaps"]}
        assert "cockroach_unassessed" in types
        assert "no_logical_replication" in types
        unassessed = next(
            g
            for g in discovery.results["analysis_gaps"]
            if g["type"] == "cockroach_unassessed"
        )
        assert unassessed["severity"] == "high"
        assert "could not be read" in unassessed["description"]
        path = next(
            g
            for g in discovery.results["analysis_gaps"]
            if g["type"] == "no_logical_replication"
        )
        assert "COPY" in path["description"]
        assert "pgcopydb" in path["description"]
        assert "not provided" in path["description"]

    def test_skipped_modules_do_not_claim_unread_inventory(self):
        """A focused run must not report schema/features gaps it never ran."""
        discovery = self._connect_with_version(CRDB_VERSION)
        discovery.results["analysis_results"] = {"config": {}}
        discovery._add_cockroach_gaps()
        types = {g["type"] for g in discovery.results["analysis_gaps"]}
        assert "cockroach_unassessed" not in types
        assert "no_logical_replication" not in types

    def test_run_analysis_invokes_cockroach_gaps_only_for_cockroachdb(self):
        """Changing the engine guard must stop the CockroachDB gap pass."""
        crdb = self._connect_with_version(CRDB_VERSION)
        with patch.object(crdb, "_add_cockroach_gaps") as add_gaps:
            crdb.run_analysis([], reuse_connection=True)
        add_gaps.assert_called_once()

        postgres = self._connect_with_version(PG_VERSION)
        with patch.object(postgres, "_add_cockroach_gaps") as add_gaps:
            postgres.run_analysis([], reuse_connection=True)
        add_gaps.assert_not_called()

    def test_default_run_analysis_emits_both_gaps_when_catalogs_are_empty(self):
        """No --analyzers filter: run_analysis() requests every module.

        An empty or unread catalog on that default path must still emit
        cockroach_unassessed and no_logical_replication. Changing the
        default module list or the engine == cockroachdb guard fails this.
        """

        class EmptyAnalyzer:
            def __init__(self, *args, **kwargs):
                self.errors = []
                self.warnings = []

            def analyze(self):
                return {}

        class EmptySchema(EmptyAnalyzer):
            def analyze(self):
                return {
                    "table_analysis": [],
                    "database_catalog": [{"read_status": "couldn't read"}],
                }

        discovery = self._connect_with_version(CRDB_VERSION)
        real = discovery._analyzer_classes()
        assert "schema" in real and "features" in real
        stubs = {name: EmptyAnalyzer for name in real}
        stubs["schema"] = EmptySchema
        with patch.object(discovery, "_analyzer_classes", return_value=stubs):
            results = discovery.run_analysis(reuse_connection=True)
        assert "schema" in results["analysis_results"]
        assert "features" in results["analysis_results"]
        types = {g["type"] for g in results["analysis_gaps"]}
        assert "cockroach_unassessed" in types
        assert "no_logical_replication" in types

    def test_focused_run_analysis_omits_gaps_for_excluded_modules(self):
        """--analyzers config must not invent schema/features unread gaps."""

        class EmptyAnalyzer:
            def __init__(self, *args, **kwargs):
                self.errors = []
                self.warnings = []

            def analyze(self):
                return {}

        discovery = self._connect_with_version(CRDB_VERSION)
        with patch.object(
            discovery,
            "_analyzer_classes",
            return_value={"config": EmptyAnalyzer, "schema": EmptyAnalyzer},
        ):
            results = discovery.run_analysis(["config"], reuse_connection=True)
        types = {g["type"] for g in results["analysis_gaps"]}
        assert "cockroach_unassessed" not in types
        assert "no_logical_replication" not in types

    def test_privilege_and_over_replica_gaps_are_never_silent(self):
        discovery = self._connect_with_version(CRDB_VERSION)
        discovery.results["analysis_results"] = {
            "schema": {"table_analysis": [{"table_name": "t"}]},
            "config": {
                "discovery_privileges": {
                    "gaps": [
                        {
                            "privilege": "VIEWJOB",
                            "description": "VIEWJOB is missing. SHOW JOBS silently returns 0 rows.",
                            "severity": "high",
                        }
                    ]
                },
                "cockroach_cluster": {
                    "over_replicated_zones": [
                        {
                            "target": "DATABASE mr_app",
                            "num_replicas": 5,
                            "node_count": 3,
                        }
                    ]
                },
            },
        }
        discovery._add_cockroach_gaps()
        types = {g["type"] for g in discovery.results["analysis_gaps"]}
        assert "missing_privilege" in types
        assert "over_replicated_zone" in types
        viewjob = next(
            g
            for g in discovery.results["analysis_gaps"]
            if g["type"] == "missing_privilege"
        )
        assert "VIEWJOB" in viewjob["description"]
        assert "silently" in viewjob["description"]
        zone = next(
            g
            for g in discovery.results["analysis_gaps"]
            if g["type"] == "over_replicated_zone"
        )
        assert zone["severity"] == "medium"
        assert "under-replicated" in zone["impact"]
        analysis_errors = [
            g
            for g in discovery.results["analysis_gaps"]
            if g["type"] == "analysis_error"
        ]
        assert analysis_errors == []

    def test_sizing_errors_are_high_severity(self):
        discovery = PostgreSQLDiscovery({"host": "h"})
        gap_type, severity = discovery._categorize_error(
            "unknown function: pg_relation_size()"
        )
        assert gap_type == "sizing_unavailable"
        assert severity == "high"

    def _run_analysis(self, version, set_error=None):
        discovery = self._connect_with_version(version)
        analyzer = MagicMock()
        cursor = analyzer.cursor.return_value.__enter__.return_value

        def execute(sql, params=None):
            if set_error and "allow_unsafe_internals" in sql:
                raise Exception(set_error)

        cursor.execute.side_effect = execute
        with patch(
            "planetscale_discovery.database.discovery.psycopg2.connect",
            return_value=analyzer,
        ):
            results = discovery.run_analysis([])
        statements = [c.args[0] for c in cursor.execute.call_args_list]
        return results, statements

    def test_v26_analyzer_session_turns_on_unsafe_internals(self):
        results, statements = self._run_analysis(CRDB_26_VERSION)
        assert "SET allow_unsafe_internals = on" in statements
        assert results["connection_info"]["allow_unsafe_internals"] == "on"
        types = {g["type"] for g in results["analysis_gaps"]}
        assert "cockroach_internals_blocked" not in types

    def test_v26_session_that_cannot_turn_it_on_is_a_high_gap(self):
        results, _ = self._run_analysis(CRDB_26_VERSION, set_error="permission denied")
        assert "allow_unsafe_internals" not in results["connection_info"]
        blocked = [
            g
            for g in results["analysis_gaps"]
            if g["type"] == "cockroach_internals_blocked"
        ]
        assert len(blocked) == 1
        assert blocked[0]["severity"] == "high"
        assert "26.2.10" in blocked[0]["description"]
        assert "allow_unsafe_internals" in blocked[0]["impact"]

    def test_v25_without_the_variable_is_not_a_gap(self):
        results, statements = self._run_analysis(
            CRDB_VERSION, set_error=UNKNOWN_VARIABLE
        )
        assert "SET allow_unsafe_internals = on" in statements
        assert "allow_unsafe_internals" not in results["connection_info"]
        types = {g["type"] for g in results["analysis_gaps"]}
        assert "cockroach_internals_blocked" not in types

    def test_postgres_never_sets_unsafe_internals(self):
        results, statements = self._run_analysis(PG_VERSION)
        assert not any("allow_unsafe_internals" in s for s in statements)
        assert "allow_unsafe_internals" not in results["connection_info"]

    def test_restricted_internals_errors_are_high_gaps(self):
        discovery = PostgreSQLDiscovery({"host": "h"})
        gap_type, severity = discovery._categorize_error(
            "InsufficientPrivilege: Access to crdb_internal and system is restricted."
        )
        assert (gap_type, severity) == ("cockroach_internals_blocked", "high")


class TestUnsafeInternals:
    def test_lockdown_starts_at_26_1(self):
        assert not restricts_internals_by_default(None)
        assert not restricts_internals_by_default(detect_cockroach_flavor(CRDB_VERSION))
        assert not restricts_internals_by_default({"major": 25, "minor": 4})
        assert restricts_internals_by_default({"major": 26, "minor": 1})
        assert restricts_internals_by_default(detect_cockroach_flavor(CRDB_26_VERSION))

    def test_set_commits_outside_autocommit(self):
        conn = MagicMock(autocommit=False)
        assert allow_unsafe_internals(conn) is None
        conn.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            "SET allow_unsafe_internals = on"
        )
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_failed_set_rolls_back_outside_autocommit(self):
        conn = MagicMock(autocommit=False)
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = Exception(
            UNKNOWN_VARIABLE
        )
        assert allow_unsafe_internals(conn) == UNKNOWN_VARIABLE
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_autocommit_session_is_left_alone(self):
        conn = MagicMock(autocommit=True)
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = Exception(
            UNKNOWN_VARIABLE
        )
        assert allow_unsafe_internals(conn) == UNKNOWN_VARIABLE
        conn.rollback.assert_not_called()
        conn.commit.assert_not_called()


class TestDiscoveryRoGrants:
    def test_system_grant_recipe_is_three_and_excludes_viewclustermetadata(self):
        from pathlib import Path

        from planetscale_discovery.database.cockroach_privileges import (
            GRANT_SQL,
            REQUIRED_SYSTEM_GRANTS,
        )

        assert REQUIRED_SYSTEM_GRANTS == (
            "VIEWACTIVITYREDACTED",
            "VIEWCLUSTERSETTING",
            "VIEWJOB",
        )
        assert GRANT_SQL.count("GRANT SYSTEM") == 3
        assert "VIEWCLUSTERMETADATA" not in GRANT_SQL
        root = Path(__file__).resolve().parents[2] / "planetscale_discovery"
        hits = [
            str(path.relative_to(root.parent))
            for path in root.rglob("*.py")
            if "VIEWCLUSTERMETADATA" in path.read_text()
        ]
        assert hits == []
