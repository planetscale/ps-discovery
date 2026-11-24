"""Unit tests for CockroachSchemaAnalyzer sizing and system-schema filters."""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

from planetscale_discovery.database.cockroach import (
    is_system_database,
    user_database_sql,
)
from planetscale_discovery.database.cockroach_analyzers.schema_analyzer import (
    CockroachSchemaAnalyzer,
)

DEFAULTDB_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "defaultdb_tables.json"
    ).read_text()
)


def _analyzer():
    connection = MagicMock()
    return CockroachSchemaAnalyzer(connection), connection


def test_pretty_bytes():
    analyzer, _ = _analyzer()
    assert analyzer._pretty_bytes(512) == "512 B"
    assert "KiB" in analyzer._pretty_bytes(2048)
    assert "GiB" in analyzer._pretty_bytes(2 * 1024**3)


def test_table_analysis_scopes_to_current_database(monkeypatch):
    analyzer, _ = _analyzer()
    seen = {}

    def fake_span(current_db_only=False):
        seen["current_db_only"] = current_db_only
        return []

    monkeypatch.setattr(analyzer, "_span_stats_by_table", fake_span)
    monkeypatch.setattr(analyzer, "_table_columns", lambda *a, **k: [])
    monkeypatch.setattr(analyzer, "_create_statement", lambda *a, **k: "")
    analyzer._get_table_analysis()
    assert seen["current_db_only"] is True


def test_table_analysis_uses_logical_live_bytes(monkeypatch):
    analyzer, _ = _analyzer()
    monkeypatch.setattr(
        analyzer,
        "_span_stats_by_table",
        lambda current_db_only=True: [
            {
                "database_name": "tpcc",
                "schema_name": "public",
                "table_name": "stock",
                "table_id": 100,
                "locality": None,
                "estimated_row_count": 5_000_000,
                "logical_live_bytes": 1_717_986_918,
                "live_bytes_all_replicas": 1_717_986_918,
                "disk_bytes_all_replicas": 400_000_000,
                "range_count": 12,
                "replica_count": 1,
            }
        ],
    )
    monkeypatch.setattr(analyzer, "_table_columns", lambda *a, **k: [])
    monkeypatch.setattr(analyzer, "_create_statement", lambda *a, **k: "")

    tables = analyzer._get_table_analysis()
    assert len(tables) == 1
    assert tables[0]["table_name"] == "stock"
    assert tables[0]["total_size_bytes"] == 1_717_986_918
    assert tables[0]["size_kind"] == "logical_live_bytes_1copy"
    assert tables[0]["estimated_rows"] == 5_000_000


def test_empty_table_analysis_records_error(monkeypatch):
    analyzer, _ = _analyzer()
    monkeypatch.setattr(analyzer, "_get_database_catalog", lambda: [])
    monkeypatch.setattr(analyzer, "_get_schema_inventory", lambda: [])
    monkeypatch.setattr(analyzer, "_get_temp_schema_counts", lambda: {})
    monkeypatch.setattr(analyzer, "_get_table_analysis", lambda: [])
    monkeypatch.setattr(analyzer, "_get_index_analysis", lambda: [])
    monkeypatch.setattr(analyzer, "_get_constraint_analysis", lambda: [])
    monkeypatch.setattr(analyzer, "_get_function_analysis", lambda: [])
    monkeypatch.setattr(analyzer, "_get_procedure_analysis", lambda: [])
    monkeypatch.setattr(analyzer, "_get_trigger_analysis", lambda: [])
    monkeypatch.setattr(analyzer, "_get_view_analysis", lambda: [])
    monkeypatch.setattr(analyzer, "_get_sequence_analysis", lambda: [])
    monkeypatch.setattr(analyzer, "_show_create_all_types", lambda: [])
    monkeypatch.setattr(analyzer, "_show_create_all_tables", lambda: [])
    monkeypatch.setattr(analyzer, "_get_routines", lambda: [])
    monkeypatch.setattr(analyzer, "_get_partition_analysis", lambda: [])
    monkeypatch.setattr(analyzer, "_get_inheritance_analysis", lambda: [])

    result = analyzer.analyze()
    assert result["table_analysis"] == []
    assert "size_baseline" not in result
    assert result["schema_source"]["pg_dump"] == "not working"
    assert result["schema_source"]["pgcopydb"] == "not working"
    assert "SHOW CREATE ALL TABLES" in result["schema_source"]["method"]
    assert "COPY (SELECT" in result["schema_source"]["copy"]
    assert any("could not be read" in e["message"] for e in analyzer.errors)


def test_two_databases_from_same_cluster_do_not_share_tables(monkeypatch):
    analyzer, conn = _analyzer()
    cursor = MagicMock()
    cursor.fetchone.return_value = {"current_database": "tpcc"}
    conn.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr(
        analyzer,
        "_span_stats_by_table",
        lambda current_db_only=True: [
            {
                "database_name": "tpcc",
                "schema_name": "public",
                "table_name": "warehouse",
                "table_id": 1,
                "locality": None,
                "estimated_row_count": 10,
                "logical_live_bytes": 1000,
                "live_bytes_all_replicas": 1000,
                "disk_bytes_all_replicas": 100,
                "range_count": 1,
                "replica_count": 1,
            },
            {
                "database_name": "mr_app",
                "schema_name": "public",
                "table_name": "regions",
                "table_id": 2,
                "locality": "REGIONAL BY ROW",
                "estimated_row_count": 4,
                "logical_live_bytes": 999_999,
                "live_bytes_all_replicas": 999_999,
                "disk_bytes_all_replicas": 50,
                "range_count": 1,
                "replica_count": 5,
            },
        ],
    )
    monkeypatch.setattr(analyzer, "_table_columns", lambda *a, **k: [])
    monkeypatch.setattr(analyzer, "_create_statement", lambda *a, **k: "")
    tables = analyzer._get_table_analysis()
    assert [t["table_name"] for t in tables] == ["warehouse"]
    assert sum(t["total_size_bytes"] for t in tables) == 1000
    assert all(t["database_name"] == "tpcc" for t in tables)


def test_database_catalog_scopes_to_current_database(monkeypatch):
    analyzer, conn = _analyzer()
    seen = {}

    def fake_span(current_db_only=False):
        seen["current_db_only"] = current_db_only
        return [
            {
                "database_name": "tpcc",
                "logical_live_bytes": 856_686_592,
                "replica_count": 3,
            },
            {
                "database_name": "mr_app",
                "logical_live_bytes": 1_468_006,
                "replica_count": 5,
            },
        ]

    monkeypatch.setattr(analyzer, "_span_stats_by_table", fake_span)
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {
            "database_name": "tpcc",
            "encoding": "UTF8",
            "collation": "en_US",
            "ctype": "en_US",
            "is_template": False,
            "allow_connections": True,
            "connection_limit": -1,
            "owner": "root",
            "tablespace": "pg_default",
        }
    ]
    conn.cursor.return_value.__enter__.return_value = cursor
    catalog = analyzer._get_database_catalog()
    assert seen["current_db_only"] is True
    assert [row["database_name"] for row in catalog] == ["tpcc"]
    assert catalog[0]["size_bytes"] == 856_686_592
    assert catalog[0]["read_status"] == "ok"
    sql = cursor.execute.call_args[0][0]
    assert "current_database()" in sql


def test_database_catalog_marks_unread_when_span_stats_fail(monkeypatch):
    analyzer, conn = _analyzer()

    def fail_span(current_db_only=False):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(analyzer, "_span_stats_by_table", fail_span)
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {
            "database_name": "compat_lab",
            "encoding": "UTF8",
            "collation": "en_US",
            "ctype": "en_US",
            "is_template": False,
            "allow_connections": True,
            "connection_limit": -1,
            "owner": "root",
            "tablespace": "pg_default",
        }
    ]
    conn.cursor.return_value.__enter__.return_value = cursor
    catalog = analyzer._get_database_catalog()
    assert [row["database_name"] for row in catalog] == ["compat_lab"]
    assert catalog[0]["size_bytes"] == 0
    assert catalog[0]["read_status"] == "couldn't read"


def test_span_stats_sql_filters_system_schemas():
    analyzer, conn = _analyzer()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn.cursor.return_value.__enter__.return_value = cursor
    analyzer._base_table_keys = lambda: set()
    analyzer._span_stats_by_table(current_db_only=True)
    sql = cursor.execute.call_args[0][0]
    assert "crdb_internal" in sql
    assert "tenant_span_stats" in sql
    assert (
        "NOT IN ('information_schema', 'pg_catalog', 'pg_toast', 'crdb_internal', 'pg_extension')"
        in sql
    )
    assert "current_database()" in sql


def test_span_stats_sql_keeps_the_connected_database():
    analyzer, conn = _analyzer()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn.cursor.return_value.__enter__.return_value = cursor
    analyzer._base_table_keys = lambda: set()
    analyzer._span_stats_by_table()
    assert user_database_sql("t.database_name") in cursor.execute.call_args[0][0]


def test_cluster_table_creates_reads_the_connected_database_only():
    """create_statements is scoped to current_database, not every user database."""
    analyzer, conn = _analyzer()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn.cursor.return_value.__enter__.return_value = cursor
    analyzer._cluster_table_creates()
    sql = cursor.execute.call_args[0][0]
    assert "current_database()" in sql
    assert "SHOW DATABASES" not in sql
    assert ".crdb_internal.create_statements" not in sql.replace(
        "crdb_internal.create_statements", ""
    )


def test_user_databases_keep_the_connected_defaultdb():
    analyzer, conn = _analyzer()
    cursor = MagicMock()
    cursor.fetchone.return_value = {"current_database": "defaultdb"}
    cursor.fetchall.return_value = [
        {"database_name": name} for name in ("defaultdb", "movr", "postgres", "system")
    ]
    conn.cursor.return_value.__enter__.return_value = cursor
    assert analyzer._user_databases() == ["defaultdb", "movr"]


def test_database_catalog_keeps_the_connected_defaultdb(monkeypatch):
    analyzer, conn = _analyzer()
    monkeypatch.setattr(
        analyzer,
        "_span_stats_by_table",
        lambda current_db_only=False: [
            {
                "database_name": "defaultdb",
                "logical_live_bytes": 1_048_576,
                "replica_count": 1,
            }
        ],
    )
    cursor = MagicMock()
    cursor.fetchone.return_value = {"current_database": "defaultdb"}
    cursor.fetchall.return_value = [
        {
            "database_name": "defaultdb",
            "encoding": "UTF8",
            "collation": "en_US",
            "ctype": "en_US",
            "is_template": False,
            "allow_connections": True,
            "connection_limit": -1,
            "owner": "root",
            "tablespace": "pg_default",
        }
    ]
    conn.cursor.return_value.__enter__.return_value = cursor
    catalog = analyzer._get_database_catalog()
    catalog_query = next(
        call
        for call in cursor.execute.call_args_list
        if "FROM pg_database" in call[0][0]
    )
    excluded = catalog_query[0][1][0]
    assert "defaultdb" not in excluded
    assert "system" in excluded
    assert catalog[0]["size_bytes"] == 1_048_576


def test_defaultdb_fixture_tables_are_discovered(monkeypatch):
    """Tables that live in the connected defaultdb must appear in table_analysis."""
    analyzer, _ = _analyzer()
    fixture_tables = DEFAULTDB_FIXTURE["tables"]
    monkeypatch.setattr(
        analyzer, "_current_database", lambda: DEFAULTDB_FIXTURE["connected_database"]
    )
    monkeypatch.setattr(
        analyzer,
        "_span_stats_by_table",
        lambda current_db_only=True: list(fixture_tables),
    )
    monkeypatch.setattr(analyzer, "_table_columns", lambda *a, **k: [])
    monkeypatch.setattr(analyzer, "_create_statement", lambda *a, **k: "")

    tables = analyzer._get_table_analysis()
    names = {row["table_name"] for row in tables}
    assert names == {"orders", "customers"}
    assert all(row["database_name"] == "defaultdb" for row in tables)
    assert sum(row["total_size_bytes"] for row in tables) == 733745
    assert not is_system_database("defaultdb", "defaultdb")


def test_defaultdb_span_sql_keeps_connected_user_tables():
    """The rendered user_database_sql keeps defaultdb when it is current_database()."""

    class FakeCursor:
        def __init__(self):
            self.sql = ""
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            self.sql = sql
            if "tenant_span_stats" in sql:
                or_current = re.search(
                    r"OR \(t\.database_name = current_database\(\) AND t\.database_name <> 'system'\)",
                    sql,
                )
                excluded = re.findall(r"t\.database_name NOT IN \(([^)]*)\)", sql)
                skipped = re.findall(r"'([^']+)'", excluded[0]) if excluded else []
                rows = []
                for row in DEFAULTDB_FIXTURE["tables"]:
                    if row["database_name"] not in skipped or (
                        or_current
                        and row["database_name"] == "defaultdb"
                        and "defaultdb" != "system"
                    ):
                        rows.append(row)
                if "t.database_name = current_database()" in sql:
                    rows = [row for row in rows if row["database_name"] == "defaultdb"]
                self.rows = rows
            elif "AS current_database" in sql:
                self.rows = [{"current_database": "defaultdb"}]
            elif "create_statements" in sql:
                self.rows = []
            elif sql.strip().startswith("SHOW DATABASES"):
                self.rows = [
                    {"database_name": name}
                    for name in ("defaultdb", "postgres", "system")
                ]
            else:
                self.rows = []

        def fetchall(self):
            return list(self.rows)

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class FakeConn:
        def cursor(self, *a, **k):
            return FakeCursor()

    analyzer = CockroachSchemaAnalyzer(FakeConn())
    tables = analyzer._span_stats_by_table(current_db_only=True)
    assert {row["table_name"] for row in tables} == {"orders", "customers"}


def test_show_create_all_types_is_captured():
    analyzer, conn = _analyzer()
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {
            "create_statement": "CREATE TYPE public.order_status AS ENUM ('open', 'closed')"
        }
    ]
    conn.cursor.return_value.__enter__.return_value = cursor
    rows = analyzer._show_create_all_types()
    cursor.execute.assert_called_with("SHOW CREATE ALL TYPES")
    assert rows == [
        {
            "create_statement": "CREATE TYPE public.order_status AS ENUM ('open', 'closed')"
        }
    ]


def test_current_database_failure_records_a_warning():
    analyzer, conn = _analyzer()
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("connection lost")
    conn.cursor.return_value.__enter__.return_value = cursor

    assert analyzer._current_database() is None
    assert analyzer.warnings
    messages = [entry["message"] for entry in analyzer.warnings]
    assert any("current_database() failed" in message for message in messages)
    assert any("defaultdb" in message and "postgres" in message for message in messages)


def test_view_rows_carry_a_null_extension():
    """Catches a CockroachDB view row without the extension field that a
    PostgreSQL view row carries. CockroachDB has no extensions."""
    analyzer, conn = _analyzer()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn.cursor.return_value.__enter__.return_value = cursor

    analyzer._get_view_analysis()

    sql = " ".join(cursor.execute.call_args[0][0].split())
    assert "NULL::text as extension FROM pg_class c" in sql
