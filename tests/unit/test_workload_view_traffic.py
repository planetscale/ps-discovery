"""An application that reads through a view must keep that traffic.

The schema analyzer selects ``relkind IN ('r','p')`` for ``table_analysis``, so
views live in ``view_analysis``. A statement that names only a view therefore
named no "known table", and the query log dropped it. Putting the name back is
half the fix: without the view definition in ``schema.sql`` the planner cannot
resolve the relation either, so the drop would only move one stage later.
"""

import json

from planetscale_discovery.workload.bundle import (
    _touches_a_known_table,
    known_table_names,
    write_bundle,
)
from planetscale_discovery.workload.schema_sql import (
    render_create_view,
    render_schema_sql,
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
    "view_analysis": [
        {
            "schema_name": "public",
            "view_name": "open_orders",
            "view_type": "v",
            "view_definition": (
                " SELECT orders.id,\n    orders.tenant_id\n"
                "   FROM public.orders\n  WHERE (orders.id > 0);"
            ),
        },
        {
            "schema_name": "reporting",
            "view_name": "open_orders_by_tenant",
            "view_type": "v",
            "view_definition": (
                " SELECT open_orders.tenant_id, count(*) AS total\n"
                "   FROM public.open_orders\n  GROUP BY open_orders.tenant_id;"
            ),
        },
    ],
}


def stmt(sid, query, kind="SELECT", calls=10):
    return {
        "id": sid,
        "query": query,
        "query_kind": kind,
        "queryids": ["1"],
        "counters": {"calls": calls, "total_exec_time": 5.0, "rows": calls},
        "mean_exec_time": 0.5,
    }


def merged(statements):
    return {
        "usable": True,
        "basis": "windowed",
        "covered_seconds": 60.0,
        "statements": {s["id"]: s for s in statements},
        "totals": {"distinct_statements": len(statements), "calls": 10},
        "coverage": {"intervals_used": 1, "intervals_skipped": []},
        "tables": {},
        "indexes": {},
        "lifetime": {"tables": {}, "indexes": {}},
        "resets_observed": 0,
    }


class TestKnownNamesIncludeViews:
    def test_a_view_name_is_known(self):
        known = known_table_names(SCHEMA)
        assert "open_orders" in known
        assert "public.open_orders" in known

    def test_a_view_in_another_schema_is_qualified(self):
        assert "reporting.open_orders_by_tenant" in known_table_names(SCHEMA)

    def test_a_statement_naming_only_a_view_is_kept(self):
        known = known_table_names(SCHEMA)
        assert _touches_a_known_table("SELECT $1 FROM open_orders", known)

    def test_an_unrelated_relation_is_still_excluded(self):
        known = known_table_names(SCHEMA)
        assert not _touches_a_known_table("SELECT $1 FROM pg_stat_activity", known)

    def test_an_extension_view_in_a_user_schema_is_not_the_application(self):
        """CREATE EXTENSION puts public.pg_stat_statements beside the app's own
        tables. Counting it would put this tool's own reads back in the log."""
        schema = dict(
            SCHEMA,
            view_analysis=SCHEMA["view_analysis"]
            + [
                {
                    "schema_name": "public",
                    "view_name": "pg_stat_statements",
                    "view_type": "v",
                    "view_definition": " SELECT * FROM pg_stat_statements(true);",
                }
            ],
        )
        known = known_table_names(schema)
        assert "public.pg_stat_statements" not in known
        assert not _touches_a_known_table(
            "SELECT $1 FROM pg_stat_statements LIMIT $2", known
        )
        assert ("public", "pg_stat_statements") not in render_schema_sql(schema)[
            "views"
        ]


class TestViewTrafficReachesTheLog:
    def _statements(self):
        return [
            stmt("view_only", "SELECT $1 FROM public.open_orders WHERE id = $2"),
            stmt("table", "SELECT $1 FROM orders WHERE tenant_id = $2"),
            stmt("catalog", "SELECT $1 FROM pg_catalog.pg_attribute a"),
        ]

    def test_the_view_statement_is_not_dropped(self, tmp_path):
        manifest = write_bundle(tmp_path, merged(self._statements()), SCHEMA, "t")
        assert manifest["statements"]["in_workload_sql"] == 2
        assert "open_orders" in (tmp_path / "workload.sql").read_text()

    def test_the_catalog_statement_is_still_dropped(self, tmp_path):
        write_bundle(tmp_path, merged(self._statements()), SCHEMA, "t")
        assert "pg_catalog" not in (tmp_path / "workload.sql").read_text()


class TestSchemaSqlCarriesViews:
    def test_the_view_is_rendered(self):
        result = render_schema_sql(SCHEMA)
        assert "CREATE VIEW public.open_orders AS" in result["sql"]
        assert result["views"] == [
            ("public", "open_orders"),
            ("reporting", "open_orders_by_tenant"),
        ]

    def test_a_view_follows_the_table_it_reads(self):
        sql = render_schema_sql(SCHEMA)["sql"]
        assert sql.index("CREATE TABLE") < sql.index("CREATE VIEW")

    def test_a_view_follows_the_view_it_reads(self):
        sql = render_schema_sql(SCHEMA)["sql"]
        assert sql.index("CREATE VIEW public.open_orders AS") < sql.index(
            "CREATE VIEW reporting.open_orders_by_tenant AS"
        )

    def test_the_server_definition_is_used_verbatim(self):
        sql = render_schema_sql(SCHEMA)["sql"]
        assert "WHERE (orders.id > 0)" in sql

    def test_a_statement_is_terminated_once(self):
        view = SCHEMA["view_analysis"][0]
        assert render_create_view(view).count(";") == 1

    def test_a_materialized_view_says_so(self):
        view = dict(SCHEMA["view_analysis"][0], view_type="m")
        assert render_create_view(view).startswith("CREATE MATERIALIZED VIEW")

    def test_a_view_with_no_definition_is_skipped(self):
        """A role that cannot read the definition is not a reason to fail."""
        schema = dict(
            SCHEMA,
            view_analysis=[
                {
                    "schema_name": "public",
                    "view_name": "hidden",
                    "view_type": "v",
                    "view_definition": None,
                }
            ],
        )
        result = render_schema_sql(schema)
        assert result["views"] == []
        assert any(s["table"] == "public.hidden" for s in result["skipped"])

    def test_a_system_schema_view_is_excluded(self):
        schema = dict(
            SCHEMA,
            view_analysis=SCHEMA["view_analysis"]
            + [
                {
                    "schema_name": "pg_catalog",
                    "view_name": "pg_tables",
                    "view_type": "v",
                    "view_definition": " SELECT 1;",
                }
            ],
        )
        assert ("pg_catalog", "pg_tables") not in render_schema_sql(schema)["views"]

    def test_the_manifest_counts_them(self, tmp_path):
        manifest = write_bundle(tmp_path, merged([]), SCHEMA, "t")
        assert manifest["schema"]["views"] == 2

    def test_a_view_is_not_a_table_in_the_cardinality_file(self, tmp_path):
        """Row counts come from reltuples on ordinary tables only."""
        write_bundle(tmp_path, merged([]), SCHEMA, "t")
        counts = json.loads((tmp_path / "plantest_counts.json").read_text())
        assert counts == {"public": {"orders": 1000}}
