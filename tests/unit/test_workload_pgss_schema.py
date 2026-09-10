"""pg_stat_statements is found by schema, not by search_path.

CREATE EXTENSION installs the view into public by default, and this tool pins
search_path to pg_catalog so a planted table cannot shadow a catalog read. An
unqualified reference therefore fails with 'relation "pg_stat_statements" does
not exist' on an ordinary install. Measured against a live PostgreSQL 18: the
probe reported "unreadable" and advised granting pg_monitor, for a view the role
could already read, and every capture silently fell back to table counters.
"""

from unittest.mock import MagicMock

from planetscale_discovery.workload.collect import (
    PGSS_VIEW,
    WorkloadCollector,
    build_statement_query,
    pgss_relation_query,
    quote_qualified,
)

COLUMNS = ["userid", "dbid", "queryid", "query", "calls", "total_exec_time"]


class TestTheRelationIsQualified:
    def test_the_schema_comes_from_the_catalog(self):
        sql = pgss_relation_query()
        assert "pg_extension" in sql
        assert "pg_namespace" in sql
        assert "extname = 'pg_stat_statements'" in sql

    def test_a_qualified_name_reaches_the_query(self):
        sql, _, _ = build_statement_query(
            COLUMNS, relation='"public"."pg_stat_statements"'
        )
        assert 'FROM "public"."pg_stat_statements"' in sql

    def test_an_unusual_schema_is_honoured(self):
        """A customer can install the extension anywhere."""
        sql, _, _ = build_statement_query(
            COLUMNS, relation=quote_qualified("monitoring", PGSS_VIEW)
        )
        assert 'FROM "monitoring"."pg_stat_statements"' in sql

    def test_a_quote_in_the_schema_name_is_doubled(self):
        assert quote_qualified('we"ird', PGSS_VIEW) == '"we""ird"."pg_stat_statements"'

    def test_a_quote_in_the_relation_name_is_doubled(self):
        assert quote_qualified("public", 'we"ird') == '"public"."we""ird"'

    def test_the_bare_name_is_still_the_default(self):
        """So a caller that cannot resolve the schema keeps the old behaviour."""
        sql, _, _ = build_statement_query(COLUMNS)
        assert "FROM pg_stat_statements" in sql


class TestTheCollectorResolvesIt:
    def _collector(self, rows):
        collector = WorkloadCollector(MagicMock())
        collector._rows = MagicMock(return_value=rows)
        return collector

    def test_it_reads_the_schema_from_the_catalog(self):
        collector = self._collector([{"nspname": "public"}])
        assert collector._pgss_relation() == '"public"."pg_stat_statements"'

    def test_a_non_default_schema_is_used(self):
        collector = self._collector([{"nspname": "extensions"}])
        assert collector._pgss_relation() == '"extensions"."pg_stat_statements"'

    def test_no_row_falls_back_to_the_bare_name(self):
        assert self._collector([])._pgss_relation() == PGSS_VIEW

    def test_a_failed_lookup_falls_back_to_the_bare_name(self):
        """Never worse than before the schema was resolved."""
        collector = WorkloadCollector(MagicMock())
        collector._rows = MagicMock(side_effect=Exception("nope"))
        assert collector._pgss_relation() == PGSS_VIEW

    def test_info_relation_matches_the_resolved_schema(self):
        collector = self._collector([{"nspname": "extensions"}])
        assert (
            collector._pgss_relation(f"{PGSS_VIEW}_info")
            == '"extensions"."pg_stat_statements_info"'
        )

    def test_info_relation_falls_back_to_the_bare_name(self):
        queries = []
        collector = self._collector([])
        collector._rows = MagicMock(side_effect=lambda sql: queries.append(sql) or [])
        collector._pgss_info()
        assert f"FROM {PGSS_VIEW}_info" in queries[-1]
