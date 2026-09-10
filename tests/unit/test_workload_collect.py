"""Tests for the workload collector, mainly its version-portable column list."""

import re

from planetscale_discovery.workload.collect import (
    COUNTER_COLUMNS,
    EXCLUDED_SCHEMAS,
    INDEX_SQL,
    TABLE_SQL,
    WorkloadCollector,
    build_statement_query,
)

# The columns each major actually has, trimmed to what the aliasing decides.
PG12 = [
    "userid",
    "dbid",
    "queryid",
    "query",
    "calls",
    "rows",
    "total_time",
    "min_time",
    "max_time",
    "mean_time",
    "stddev_time",
    "shared_blks_hit",
    "blk_read_time",
    "blk_write_time",
]
PG13 = [
    "userid",
    "dbid",
    "queryid",
    "query",
    "calls",
    "rows",
    "total_exec_time",
    "min_exec_time",
    "max_exec_time",
    "mean_exec_time",
    "plans",
    "total_plan_time",
    "wal_records",
    "wal_bytes",
    "shared_blks_hit",
    "blk_read_time",
]
PG17 = PG13 + ["toplevel", "stats_since", "shared_blk_read_time", "local_blks_hit"]


class TestBuildStatementQuery:
    def test_pg12_aliases_the_old_names(self):
        sql, present, missing = build_statement_query(PG12)
        assert "total_time AS total_exec_time" in sql
        assert "blk_read_time AS shared_blk_read_time" in sql
        assert "total_exec_time" in present

    def test_pg17_needs_no_aliasing(self):
        sql, present, _ = build_statement_query(PG17)
        assert "total_exec_time" in sql
        assert " AS total_exec_time" not in sql
        assert "shared_blk_read_time" in present

    def test_a_missing_column_is_selected_as_null_and_reported(self):
        """So a consumer can tell "value is zero" from "server cannot say"."""
        sql, present, missing = build_statement_query(PG12)
        assert "NULL AS wal_bytes" in sql
        assert "wal_bytes" in missing
        assert "wal_bytes" not in present

    def test_nothing_the_server_reports_reaches_the_sql(self):
        """The only SQL built by concatenation; names come from our own map."""
        from planetscale_discovery.workload.collect import COLUMN_ALIASES

        known = {c for candidates in COLUMN_ALIASES.values() for c in candidates}
        known |= set(COLUMN_ALIASES)
        sql, _, _ = build_statement_query(PG12 + ["query; DROP TABLE t --"])
        assert "DROP TABLE" not in sql
        for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql.split(" FROM ")[0]):
            if word in ("SELECT", "AS", "NULL"):
                continue
            assert word in known, f"{word} is not from COLUMN_ALIASES"

    def test_the_query_is_scoped_to_the_current_database(self):
        sql, _, _ = build_statement_query(PG17)
        assert "current_database()" in sql

    def test_the_row_limit_is_applied(self):
        sql, _, _ = build_statement_query(PG17, row_limit=25)
        assert "LIMIT 25" in sql

    def test_counter_columns_exclude_identity_and_lifetime_values(self):
        """min/max/mean/stddev are lifetime values, not counters to difference."""
        for name in (
            "queryid",
            "query",
            "min_exec_time",
            "max_exec_time",
            "mean_exec_time",
            "stddev_exec_time",
        ):
            assert name not in COUNTER_COLUMNS
        assert "calls" in COUNTER_COLUMNS


class TestSchemaScope:
    def _collector(self, schemas=None):
        from unittest.mock import MagicMock

        return WorkloadCollector(MagicMock(), schemas=schemas)

    def test_no_schemas_means_every_non_system_schema(self):
        assert self._collector().schemas is None

    def test_an_empty_list_is_not_a_narrow_scope(self):
        """[] would otherwise match nothing at all."""
        assert self._collector([]).schemas is None

    def test_an_explicit_list_narrows(self):
        assert self._collector(["app"]).schemas == ["app"]

    def test_system_schemas_are_excluded_by_the_queries(self):
        for sql in (TABLE_SQL, INDEX_SQL):
            assert "%(excluded)s" in sql
        for schema in ("pg_catalog", "information_schema", "pg_toast", "__neki"):
            assert schema in EXCLUDED_SCHEMAS


class TestStatementRows:
    def _collector(self):
        from unittest.mock import MagicMock

        return WorkloadCollector(MagicMock())

    def test_literals_are_redacted_and_the_kind_recorded(self):
        row = self._collector()._statement_row(
            {"query": "SELECT * FROM t WHERE a = 'secret'", "calls": 3}
        )
        assert "secret" not in row["query"]
        assert row["query_kind"] == "SELECT"
        assert row["counters"]["calls"] == 3

    def test_masked_text_is_flagged_rather_than_redacted(self):
        """A role that cannot see the text is a different fact from no text."""
        row = self._collector()._statement_row(
            {"query": "<insufficient privilege>", "calls": 1}
        )
        assert row["text_unavailable"] is True
        assert row["query"] is None
        assert row["query_kind"] is None

    def test_long_text_is_truncated_and_flagged(self):
        from unittest.mock import MagicMock

        collector = WorkloadCollector(MagicMock(), statement_text_max_chars=40)
        row = collector._statement_row(
            {"query": "SELECT " + ", ".join(["a"] * 50) + " FROM t", "calls": 1}
        )
        assert row["text_truncated"] is True
        assert len(row["query"]) == 40


class TestTableRows:
    def _collector(self):
        from unittest.mock import MagicMock

        return WorkloadCollector(MagicMock())

    def test_never_analyzed_is_unknown_not_zero(self):
        row = self._collector()._table_row(
            {"schemaname": "public", "relname": "t", "reltuples": -1, "idx_scan": 0}
        )
        assert row["estimated_rows"] is None
        assert row["never_analyzed"] is True

    def test_a_real_row_count_is_kept(self):
        row = self._collector()._table_row(
            {"schemaname": "public", "relname": "t", "reltuples": 500, "idx_scan": 7}
        )
        assert row["estimated_rows"] == 500
        assert row["never_analyzed"] is False
        assert row["counters"]["idx_scan"] == 7

    def test_a_null_counter_stays_none(self):
        """A NULL scan count is a permission problem, not zero scans."""
        row = self._collector()._table_row(
            {"schemaname": "public", "relname": "t", "reltuples": 1, "idx_scan": None}
        )
        assert row["counters"]["idx_scan"] is None
