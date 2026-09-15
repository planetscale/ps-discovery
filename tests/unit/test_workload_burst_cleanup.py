"""A killed collect leaves objects on the server, and --cleanup drops them."""

from unittest.mock import MagicMock

import pytest

from planetscale_discovery.workload.burst import cleanup
from planetscale_discovery.workload.burst.cleanup import drop_leftovers, find_leftovers
from planetscale_discovery.workload.burst.collector import LOG_SERVER, WORK_SCHEMA


class FakeSql:
    def __init__(self, present=(), execute_ok=True):
        self.present = set(present)
        self.execute_ok = execute_ok
        self.errors = []
        self.execute_calls = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.execute_calls.append(sql)
        ok = self.execute_ok(sql) if callable(self.execute_ok) else self.execute_ok
        if not ok:
            self.errors.append(f"{sql.split()[0].lower()}: boom")
        return ok

    def commit(self):
        self.commits += 1
        self.execute_calls.append("COMMIT")

    def one_or_none(self, sql, params=None):
        if "pg_namespace" in sql:
            return {"found": 1} if "schema" in self.present else None
        if "pg_foreign_server" in sql:
            return {"found": 1} if "server" in self.present else None
        return None


@pytest.fixture
def fake(mocker):
    def build(present=(), execute_ok=True):
        sql = FakeSql(present=present, execute_ok=execute_ok)
        mocker.patch.object(cleanup, "SafeCursor", return_value=sql)
        return sql

    return build


class TestFindLeftovers:
    def test_a_clean_server_reports_nothing(self, fake):
        fake()
        assert find_leftovers(MagicMock()) == []

    def test_it_names_the_schema_and_the_server(self, fake):
        fake(present=("schema", "server"))
        found = find_leftovers(MagicMock())
        assert [item["name"] for item in found] == [WORK_SCHEMA, LOG_SERVER]
        assert [item["kind"] for item in found] == ["schema", "server"]


class TestDropLeftovers:
    def test_a_clean_server_runs_no_statement(self, fake):
        sql = fake()
        result = drop_leftovers(MagicMock())
        assert result == {"found": [], "dropped": [], "failed": []}
        assert sql.execute_calls == []

    def test_it_drops_what_it_found(self, fake):
        sql = fake(present=("schema", "server"))
        result = drop_leftovers(MagicMock())
        assert [item["name"] for item in result["dropped"]] == [
            WORK_SCHEMA,
            LOG_SERVER,
        ]
        assert not result["failed"]
        assert "SET default_transaction_read_only = off" in sql.execute_calls

    def test_a_read_only_session_fails_every_drop(self, fake):
        fake(present=("schema",), execute_ok=lambda sql: not sql.startswith("SET"))
        result = drop_leftovers(MagicMock())
        assert not result["dropped"]
        assert result["failed"][0]["why"] == "the session is read-only"

    def test_the_read_only_override_is_committed_before_the_drops(self, fake):
        """The bug: a failed DROP rolled the SET back, so the next DROP failed
        as read-only instead of naming its own reason."""
        sql = fake(present=("schema", "server"))
        drop_leftovers(MagicMock())
        kinds = [call.split()[0] for call in sql.execute_calls]
        assert kinds[:3] == ["SET", "COMMIT", "DROP"]

    def test_a_failed_drop_is_reported_rather_than_counted(self, fake):
        fake(present=("schema",), execute_ok=lambda sql: not sql.startswith("DROP"))
        result = drop_leftovers(MagicMock())
        assert not result["dropped"]
        assert result["failed"][0]["name"] == WORK_SCHEMA
