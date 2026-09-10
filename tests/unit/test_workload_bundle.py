"""Tests for the bundle writer."""

import json

import pytest

from planetscale_discovery.workload.bundle import (
    PLANNABLE_KINDS,
    render_cardinality,
    render_workload_sql,
    write_bundle,
)

SCHEMA = {
    "table_analysis": [
        {
            "schema_name": "public",
            "table_name": "orders",
            "table_type": "r",
            "estimated_rows": 1000,
            "columns": [
                {"column_name": "id", "data_type": "bigint", "not_null": True},
                {"column_name": "tenant_id", "data_type": "uuid", "not_null": True},
            ],
        }
    ],
    "index_analysis": [],
    "constraint_analysis": [],
}


def stmt(sid, kind="SELECT", query="SELECT $1 FROM orders", calls=10):
    return {
        "id": sid,
        "query": query,
        "query_kind": kind,
        "queryids": ["1"],
        "counters": {"calls": calls, "total_exec_time": 5.0, "rows": calls},
        "mean_exec_time": 0.5,
    }


def merged(statements, basis="windowed", covered=60.0, **extra):
    result = {
        "usable": True,
        "basis": basis,
        "covered_seconds": covered,
        "statements": {s["id"]: s for s in statements},
        "totals": {
            "distinct_statements": len(statements),
            "calls": sum(s["counters"]["calls"] for s in statements),
        },
        "coverage": {"intervals_used": 1, "intervals_skipped": []},
        "tables": {},
        "indexes": {},
        "lifetime": {"tables": {}, "indexes": {}},
        "resets_observed": 0,
    }
    result.update(extra)
    return result


class TestEverythingIsEmitted:
    def test_no_cap_is_applied(self):
        """Ranking is the planner's job, so nothing is dropped by volume."""
        statements = [stmt(f"s{i:03d}", calls=i) for i in range(300)]
        sql = render_workload_sql(statements, 60.0)
        assert sql.count("-- neki:stmt") == 300

    def test_the_summary_counts_every_statement_it_was_given(self, tmp_path):
        statements = [stmt(str(i)) for i in range(50)]
        summary = write_bundle(tmp_path, merged(statements), SCHEMA, "t")
        assert summary["statements"]["collected"] == 50
        assert summary["statements"]["in_workload_sql"] == 50

    def test_low_call_statements_are_kept(self, tmp_path):
        manifest = write_bundle(
            tmp_path, merged([stmt("a", calls=1), stmt("b", calls=99999)]), SCHEMA, "t"
        )
        assert manifest["statements"]["in_workload_sql"] == 2


class TestPlannableFilter:
    def test_only_query_patterns_reach_the_log(self, tmp_path):
        """DDL and transaction control are not query patterns to route."""
        statements = [
            stmt("sel", "SELECT"),
            stmt("ins", "INSERT", "INSERT INTO orders VALUES ($1)"),
            stmt("ddl", "CREATE", "CREATE INDEX i ON orders (id)"),
            stmt("com", "COMMIT", "COMMIT"),
            stmt("unk", "UNKNOWN", "SET x = $1"),
        ]
        manifest = write_bundle(tmp_path, merged(statements), SCHEMA, "t")
        assert manifest["statements"]["in_workload_sql"] == 2
        assert manifest["statements"]["excluded_from_workload_sql"] == 3

    def test_an_excluded_statement_is_counted_in_the_summary(self, tmp_path):
        """The bundle holds planner input only, so the count is the record."""
        summary = write_bundle(
            tmp_path,
            merged([stmt("ddl", "CREATE", "CREATE INDEX i ON orders (id)")]),
            SCHEMA,
            "t",
        )
        assert summary["statements"]["collected"] == 1
        assert summary["statements"]["in_workload_sql"] == 0
        assert summary["statements"]["excluded_from_workload_sql"] == 1

    def test_the_allowlist_covers_dml_and_select(self):
        assert PLANNABLE_KINDS == {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"}

    def test_a_statement_with_no_text_is_excluded(self, tmp_path):
        entry = stmt("a")
        entry["query"] = None
        manifest = write_bundle(tmp_path, merged([entry]), SCHEMA, "t")
        assert manifest["statements"]["in_workload_sql"] == 0


class TestWorkloadSql:
    def test_records_are_terminated_once(self):
        sql = render_workload_sql([stmt("a", query="SELECT $1 FROM t;")], 60.0)
        assert ";;" not in sql

    def test_metric_headers_carry_the_counts(self):
        sql = render_workload_sql([stmt("a", calls=42)], 60.0)
        assert "-- neki:metrics calls=42" in sql

    def test_the_window_is_stated(self):
        assert "39 seconds" in render_workload_sql([stmt("a")], 39.4)

    def test_cumulative_is_labelled(self):
        assert "not a measured window" in render_workload_sql([stmt("a")], None)

    def test_output_is_deterministic(self):
        statements = [stmt("b"), stmt("a"), stmt("c")]
        assert render_workload_sql(statements, 60.0) == render_workload_sql(
            list(reversed(statements)), 60.0
        )


class TestCardinality:
    def test_nested_by_schema_then_table(self):
        stats, _ = render_cardinality(SCHEMA)
        assert stats == {"public": {"orders": 1000.0}}

    def test_negative_reltuples_becomes_zero(self):
        schema = {
            "table_analysis": [
                {
                    "schema_name": "public",
                    "table_name": "t",
                    "table_type": "r",
                    "estimated_rows": -1,
                }
            ]
        }
        stats, meta = render_cardinality(schema)
        assert stats["public"]["t"] == 0.0
        assert meta["never_analyzed"] == ["public.t"]

    def test_partitioned_parents_are_excluded(self):
        schema = {
            "table_analysis": [
                {
                    "schema_name": "public",
                    "table_name": "p",
                    "table_type": "p",
                    "estimated_rows": 5,
                }
            ]
        }
        stats, meta = render_cardinality(schema)
        assert stats == {}
        assert meta["excluded_non_ordinary_tables"] == 1

    def test_system_schemas_are_excluded(self):
        schema = {
            "table_analysis": [
                {
                    "schema_name": "pg_catalog",
                    "table_name": "t",
                    "table_type": "r",
                    "estimated_rows": 5,
                }
            ]
        }
        assert render_cardinality(schema)[0] == {}

    def test_no_extra_keys_reach_the_file(self, tmp_path):
        """A stray field risks a strict parse on the consumer's side."""
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        payload = json.loads((tmp_path / "plantest_counts.json").read_text())
        assert set(payload) == {"public"}

    def test_both_filenames_are_written_identically(self, tmp_path):
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        base = (tmp_path / "plantest_counts.json").read_text()
        card = (tmp_path / "plantest_counts-card.json").read_text()
        assert base == card


class TestBundleFiles:
    def test_the_bundle_holds_only_these_files(self, tmp_path):
        """Every file is either planner input or a record of how it was taken."""
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        written = {p.name for p in tmp_path.iterdir()}
        assert written == {
            # Planner input.
            "workload.sql",
            "schema.sql",
            "plantest_counts.json",
            "plantest_counts-card.json",
            # How the capture was taken, and what its data looks like.
            "manifest.json",
            "column_stats.json",
            "table_activity.json",
            "README.md",
            ".gitignore",
        }

    def test_every_expected_file_is_written(self, tmp_path):
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        for name in (
            "workload.sql",
            "schema.sql",
            "plantest_counts.json",
            "plantest_counts-card.json",
            "manifest.json",
            "column_stats.json",
            "table_activity.json",
            "README.md",
            ".gitignore",
        ):
            assert (tmp_path / name).exists(), name

    def test_files_are_owner_only(self, tmp_path):
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        for path in tmp_path.iterdir():
            if path.is_file():
                assert path.stat().st_mode & 0o777 == 0o600, path.name

    def test_the_bundle_cannot_be_committed_by_accident(self, tmp_path):
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        assert (tmp_path / ".gitignore").read_text() == "*\n"

    def test_no_topology_is_written(self, tmp_path):
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        assert not (tmp_path / "topology.json").exists()

    def test_the_readme_summarises_what_was_collected(self, tmp_path):
        write_bundle(tmp_path, merged([stmt("a"), stmt("b")]), SCHEMA, "t")
        readme = (tmp_path / "README.md").read_text()
        assert "What was collected" in readme
        assert "Queries kept, to plan the sharding scheme from | 2" in readme
        # The file list must name every file the bundle actually holds.
        for name in ("workload.sql", "schema.sql", "manifest.json"):
            assert name in readme

    def test_the_readme_says_where_to_send_the_bundle(self, tmp_path):
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        readme = (tmp_path / "README.md").read_text()
        assert "migration engineer" in readme
        assert "tar -czf" in readme

    def test_the_readme_documents_no_external_tool(self, tmp_path):
        """This repository documents its own output, not another tool's usage."""
        write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        readme = (tmp_path / "README.md").read_text().lower()
        for flag in ("--log", "--schema", "--cardinality", "--topology", "-o "):
            assert flag not in readme


class TestCaveats:
    def test_cumulative_basis_is_called_out(self, tmp_path):
        manifest = write_bundle(
            tmp_path, merged([stmt("a")], basis="cumulative", covered=None), SCHEMA, "t"
        )
        caveat = next(c for c in manifest["caveats"] if "measured window" in c)
        # Names the cause, and does not imply the statistics were cleared.
        assert "Only one snapshot" in caveat
        assert "lifetime totals" in caveat
        assert "reset" not in caveat.lower()

    def test_a_reset_is_disclosed(self, tmp_path):
        manifest = write_bundle(
            tmp_path, merged([stmt("a")], resets_observed=2), SCHEMA, "t"
        )
        assert any("reset 2 time" in c for c in manifest["caveats"])

    def test_the_transaction_blind_spot_is_always_stated(self, tmp_path):
        """Its absence must not read as a clean bill of health."""
        manifest = write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        assert any(
            "records statements, not transactions" in c for c in manifest["caveats"]
        )

    def test_never_analyzed_tables_are_disclosed(self, tmp_path):
        schema = {
            "table_analysis": [
                {
                    "schema_name": "public",
                    "table_name": "t",
                    "table_type": "r",
                    "estimated_rows": -1,
                    "columns": [{"column_name": "id", "data_type": "bigint"}],
                }
            ],
            "index_analysis": [],
            "constraint_analysis": [],
        }
        manifest = write_bundle(tmp_path, merged([stmt("a")]), schema, "t")
        assert any("never been analyzed" in c for c in manifest["caveats"])


class TestSchemaParseGate:
    def _schema_with_a_bad_index(self):
        return {
            "table_analysis": SCHEMA["table_analysis"],
            "index_analysis": [
                {
                    "schema_name": "public",
                    "table_name": "orders",
                    "index_name": "broken",
                    "index_definition": "CREATE INDEX broken ON GARBAGE ((",
                }
            ],
            "constraint_analysis": [],
        }

    def test_a_failing_statement_leaves_schema_sql(self, tmp_path):
        pytest.importorskip("pglast")
        write_bundle(
            tmp_path, merged([stmt("a")]), self._schema_with_a_bad_index(), "t"
        )
        assert "GARBAGE" not in (tmp_path / "schema.sql").read_text()

    def test_it_is_counted_in_the_summary(self, tmp_path):
        """It is left out of schema.sql, so the count is how anyone knows."""
        pytest.importorskip("pglast")
        summary = write_bundle(
            tmp_path, merged([stmt("a")]), self._schema_with_a_bad_index(), "t"
        )
        assert summary["schema"]["parse_failures"] == 1
        assert "GARBAGE" not in (tmp_path / "schema.sql").read_text()

    def test_what_remains_parses(self, tmp_path):
        parser = pytest.importorskip("pglast.parser")
        write_bundle(
            tmp_path, merged([stmt("a")]), self._schema_with_a_bad_index(), "t"
        )
        parser.parse_sql((tmp_path / "schema.sql").read_text())


class TestForeignStatementFilter:
    """Statements naming no captured relation are not this application's workload.

    On a real capture, 21% of statements were monitoring, psql and this tool's
    own catalog reads. Every one of the planner's 11 "cannot run" errors came
    from them. The planner cannot plan a relation that schema.sql does not hold.
    """

    def _statements(self):
        return [
            stmt("app", query="SELECT $1 FROM orders WHERE tenant_id = $2"),
            stmt("qualified", query="SELECT $1 FROM public.orders"),
            stmt("catalog", query="SELECT $1 FROM pg_catalog.pg_attribute a"),
            stmt("pgss", query="SELECT $1 FROM pg_stat_statements LIMIT $2"),
            stmt("psql", query='SELECT d.datname AS "Name" FROM pg_database d'),
        ]

    def test_only_application_statements_reach_the_log(self, tmp_path):
        manifest = write_bundle(tmp_path, merged(self._statements()), SCHEMA, "t")
        assert manifest["statements"]["in_workload_sql"] == 2
        assert manifest["statements"]["excluded_not_this_application"] == 3

    def test_the_log_holds_no_catalog_query(self, tmp_path):
        write_bundle(tmp_path, merged(self._statements()), SCHEMA, "t")
        sql = (tmp_path / "workload.sql").read_text()
        assert "pg_catalog" not in sql
        assert "pg_stat_statements" not in sql
        assert "orders" in sql

    def test_they_are_counted_in_the_summary(self, tmp_path):
        summary = write_bundle(tmp_path, merged(self._statements()), SCHEMA, "t")
        assert summary["statements"]["collected"] == 5
        assert summary["statements"]["excluded_not_this_application"] == 3

    def test_the_exclusion_is_disclosed(self, tmp_path):
        manifest = write_bundle(tmp_path, merged(self._statements()), SCHEMA, "t")
        assert any(
            "name no table or view from your schema" in c for c in manifest["caveats"]
        )

    def test_an_empty_schema_disables_the_filter(self, tmp_path):
        """With no known tables, every statement would otherwise be dropped."""
        empty = {"table_analysis": [], "index_analysis": [], "constraint_analysis": []}
        manifest = write_bundle(tmp_path, merged(self._statements()), empty, "t")
        assert manifest["statements"]["in_workload_sql"] == 5

    def test_a_table_name_must_match_as_a_word(self, tmp_path):
        """Substring matching would keep anything containing the name."""
        from planetscale_discovery.workload.bundle import (
            _touches_a_known_table,
            known_table_names,
        )

        known = known_table_names(SCHEMA)
        assert _touches_a_known_table("SELECT $1 FROM orders", known)
        assert _touches_a_known_table("SELECT $1 FROM public.orders", known)
        assert not _touches_a_known_table("SELECT $1 FROM reorders_archive", known)
        assert not _touches_a_known_table("SELECT $1 FROM my_orders", known)

    def test_matching_is_case_insensitive(self, tmp_path):
        from planetscale_discovery.workload.bundle import (
            _touches_a_known_table,
            known_table_names,
        )

        assert _touches_a_known_table(
            "SELECT $1 FROM ORDERS", known_table_names(SCHEMA)
        )


class TestTheFinalizeSummaryPrints:
    """The printed summary reads the returned dict, so a removed key breaks it.

    A unit test that only inspects the dict cannot catch that; this walks the
    same path `finalize` does.
    """

    def test_every_field_the_summary_prints_exists(self, tmp_path, capsys):
        from planetscale_discovery.workload.cli_workload import _print_summary

        summary = write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        _print_summary(summary, tmp_path)
        out = capsys.readouterr().out
        assert "Bundle written to" in out
        assert "queries:" in out
        assert "schema:" in out


class TestAnIdleDatabaseSaysSo:
    """An empty bundle and a full one look identical in a file listing."""

    def test_a_capture_with_no_calls_is_called_out(self, tmp_path):
        summary = write_bundle(tmp_path, merged([]), SCHEMA, "t")
        assert any("No queries ran" in c for c in summary["caveats"])

    def test_a_capture_with_traffic_is_not(self, tmp_path):
        summary = write_bundle(tmp_path, merged([stmt("a")]), SCHEMA, "t")
        assert not any("No queries ran" in c for c in summary["caveats"])
