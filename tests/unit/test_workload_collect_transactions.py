import logging

import psycopg2
import psycopg2.errors
import pytest

from planetscale_discovery.workload import cli_workload
from planetscale_discovery.workload.collect import (
    CLOCK_SQL,
    COLUMN_SQL,
    INDEX_SQL,
    PGSS_COLUMNS_SQL,
    PGSS_MAX_SQL,
    SERVER_SQL,
    TABLE_SQL,
    WorkloadCollector,
)

SERVER_ROW = {
    "database": "db1",
    "database_oid": 16384,
    "version_num": "170004",
    "is_replica": False,
}
TABLE_ROW = {
    "schemaname": "public",
    "relname": "orders",
    "reltuples": 10.0,
    "idx_scan": 3,
    "captured_at_server": "2026-09-23 15:24:52+00",
}
INDEX_ROW = {"schemaname": "public", "relname": "orders", "indexrelname": "orders_pkey"}
STATEMENT_ROW = {"query": "SELECT * FROM orders WHERE id = 7", "calls": 5}
COLUMN_ROW = {
    "schemaname": "public",
    "tablename": "orders",
    "attname": "id",
    "n_distinct": -1,
    "row_count": 10,
}


def what_is_read(sql):
    if sql == SERVER_SQL:
        return "server"
    if sql == TABLE_SQL:
        return "tables"
    if sql == INDEX_SQL:
        return "indexes"
    if sql == PGSS_COLUMNS_SQL:
        return "pgss_columns"
    if sql == COLUMN_SQL:
        return "columns"
    if sql == PGSS_MAX_SQL:
        return "pgss_max"
    if sql == CLOCK_SQL:
        return "clock"
    if "pg_extension" in sql:
        return "pgss_schema"
    if "dealloc" in sql:
        return "dealloc"
    return "statements"


ROWS = {
    "server": [SERVER_ROW],
    "tables": [TABLE_ROW],
    "indexes": [INDEX_ROW],
    "pgss_columns": [{"column_name": "query"}, {"column_name": "calls"}],
    "pgss_schema": [{"nspname": "public"}],
    "statements": [STATEMENT_ROW],
    "columns": [COLUMN_ROW],
    "pgss_max": [{"m": "5000"}],
    "dealloc": [{"dealloc": 0}],
    "clock": [{"captured_at_server": "2026-09-23 15:24:52+00"}],
}


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        name = what_is_read(sql)
        self.connection.events.append(("query", name))
        self.connection.in_transaction = True
        if name == self.connection.dies_on:
            self.connection.closed = 2
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        if name == self.connection.fails_on:
            raise psycopg2.errors.QueryCanceled("canceling statement due to timeout")
        self.rows = ROWS[name]

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, dies_on=None, fails_on=None):
        self.dies_on = dies_on
        self.fails_on = fails_on
        self.closed = 0
        self.in_transaction = False
        self.autocommit = False
        self.events = []

    def cursor(self):
        if self.closed:
            raise psycopg2.InterfaceError("connection already closed")
        return FakeCursor(self)

    def rollback(self):
        if self.closed:
            raise psycopg2.InterfaceError("connection already closed")
        self.in_transaction = False
        self.events.append(("end", None))


def collect(connection, logger=None):
    collector = WorkloadCollector(connection, logger=logger)
    real_row = collector._statement_row

    def recording_row(row):
        connection.events.append(("process", "statements"))
        return real_row(row)

    collector._statement_row = recording_row
    return collector.collect()


class TestEachReadIsItsOwnTransaction:
    def test_every_query_is_followed_by_the_end_of_its_transaction(self):
        connection = FakeConnection()
        collect(connection)
        queries = [
            i for i, (kind, _) in enumerate(connection.events) if kind == "query"
        ]
        for index in queries:
            assert connection.events[index + 1] == ("end", None)
        assert connection.in_transaction is False

    def test_a_failed_read_also_ends_its_transaction(self):
        connection = FakeConnection(fails_on="tables")
        with pytest.raises(psycopg2.errors.QueryCanceled):
            WorkloadCollector(connection)._rows(TABLE_SQL)
        assert connection.in_transaction is False

    def test_a_timed_out_read_does_not_stop_the_reads_after_it(self):
        connection = FakeConnection(fails_on="tables")
        snapshot = collect(connection)
        codes = {w["code"] for w in snapshot["warnings"]}
        assert "relation_read_failed" in codes
        assert "connection_lost" not in codes
        assert len(snapshot["statements"]) == 1
        assert len(snapshot["columns"]) == 1

    def test_statements_are_processed_after_every_read(self):
        connection = FakeConnection()
        collect(connection)
        kinds = [kind for kind, _ in connection.events if kind != "end"]
        first_process = kinds.index("process")
        assert "query" not in kinds[first_process:]


class TestALostConnectionKeepsWhatWasRead:
    def test_the_snapshot_is_returned_instead_of_raising(self):
        snapshot = collect(FakeConnection(dies_on="columns"))
        assert snapshot["status"] == "degraded"
        assert len(snapshot["tables"]) == 1
        assert len(snapshot["statements"]) == 1
        assert snapshot["columns"] == []

    def test_the_loss_is_reported_with_the_read_it_happened_in(self):
        snapshot = collect(FakeConnection(dies_on="columns"))
        codes = {w["code"]: w["detail"] for w in snapshot["warnings"]}
        assert "column statistics" in codes["connection_lost"]
        assert "column_stats_read_failed" in codes

    def test_the_reads_after_the_loss_are_skipped(self):
        connection = FakeConnection(dies_on="statements")
        snapshot = collect(connection)
        read = [name for kind, name in connection.events if kind == "query"]
        assert "columns" not in read
        assert "pgss_max" not in read
        codes = {w["code"]: w["detail"] for w in snapshot["warnings"]}
        assert "pg_stat_statements" in codes["connection_lost"]
        assert "not read" in codes["column_stats_read_failed"]

    def test_a_loss_before_the_statement_columns_is_not_reported_as_no_extension(self):
        snapshot = collect(FakeConnection(dies_on="indexes"))
        notes = {n["code"] for n in snapshot["notes"]}
        warnings = {w["code"] for w in snapshot["warnings"]}
        assert "pg_stat_statements_unavailable" not in notes
        assert {"connection_lost", "relation_read_failed"} <= warnings
        assert "statement_read_failed" in warnings

    def test_a_loss_on_the_first_read_still_returns_a_snapshot(self):
        snapshot = collect(FakeConnection(dies_on="server"))
        assert snapshot["status"] == "failed"
        assert snapshot["server"] == {}


class TestEachStepIsLogged:
    def _messages(self, caplog, dies_on=None):
        logger = logging.getLogger("test_workload_collect_transactions")
        with caplog.at_level(logging.INFO, logger=logger.name):
            collect(FakeConnection(dies_on=dies_on), logger=logger)
        return [record.getMessage() for record in caplog.records]

    def test_a_read_is_announced_before_it_runs_and_reported_after(self, caplog):
        messages = self._messages(caplog)
        start = messages.index("reading table statistics")
        assert messages[start + 1].startswith("reading table statistics: 1 rows in ")

    def test_the_redaction_is_announced_and_reported(self, caplog):
        messages = self._messages(caplog)
        start = messages.index("redacting 1 statements")
        assert messages[start + 1].startswith("redacting 1 statements: done in ")

    def test_a_failed_read_is_logged_with_the_error(self, caplog):
        messages = self._messages(caplog, dies_on="columns")
        assert any(
            m.startswith("reading column statistics: failed after ")
            and "server closed the connection" in m
            for m in messages
        )


class TestTheSchemaStepRunsInAutocommit:
    def test_autocommit_is_on_during_the_schema_and_off_after(self, mocker):
        connection = FakeConnection()
        seen = []

        def run_analysis(modules, reuse_connection):
            seen.append(connection.autocommit)
            return {"analysis_results": {"schema": {"tables": []}}}

        discovery = mocker.patch(
            "planetscale_discovery.database.discovery.PostgreSQLDiscovery"
        )
        discovery.return_value.run_analysis.side_effect = run_analysis
        config = mocker.Mock()
        schema = cli_workload._collect_schema(connection, config, mocker.Mock())
        assert seen == [True]
        assert connection.autocommit is False
        assert schema == {"tables": []}

    def test_a_connection_closed_during_the_schema_does_not_raise(self, mocker):
        connection = FakeConnection()

        def run_analysis(modules, reuse_connection):
            connection.closed = 2
            return {"analysis_results": {"schema": {}}}

        discovery = mocker.patch(
            "planetscale_discovery.database.discovery.PostgreSQLDiscovery"
        )
        discovery.return_value.run_analysis.side_effect = run_analysis
        assert (
            cli_workload._collect_schema(connection, mocker.Mock(), mocker.Mock()) == {}
        )
