"""Tests for SafeCursor."""

from unittest.mock import MagicMock

from planetscale_discovery.workload.burst.sql import SafeCursor


def make_cursor(fetchone=None, fetchall=None, execute_side_effect=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall or []
    if execute_side_effect is not None:
        cursor.execute.side_effect = execute_side_effect
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    return cursor


def make_connection(cursor):
    connection = MagicMock()
    connection.cursor.return_value = cursor
    return connection


class TestExecute:
    def test_success_returns_true(self):
        connection = make_connection(make_cursor())
        safe = SafeCursor(connection)
        assert safe.execute("SELECT 1") is True
        assert safe.errors == []

    def test_failure_returns_false_rolls_back_and_records_error(self):
        cursor = make_cursor(execute_side_effect=RuntimeError("boom"))
        connection = make_connection(cursor)
        safe = SafeCursor(connection)
        assert safe.execute("DROP TABLE t") is False
        connection.rollback.assert_called_once()
        assert len(safe.errors) == 1
        assert "boom" in safe.errors[0]
        assert safe.errors[0].startswith("drop:")


class TestOne:
    def test_raises_on_failure(self):
        cursor = make_cursor(execute_side_effect=RuntimeError("bad query"))
        connection = make_connection(cursor)
        safe = SafeCursor(connection)
        try:
            safe.one("SELECT 1")
            assert False, "expected an exception"
        except RuntimeError:
            pass

    def test_returns_row_as_dict(self):
        cursor = make_cursor(fetchone={"n": 1})
        connection = make_connection(cursor)
        safe = SafeCursor(connection)
        assert safe.one("SELECT 1") == {"n": 1}

    def test_returns_none_for_no_row(self):
        cursor = make_cursor(fetchone=None)
        connection = make_connection(cursor)
        safe = SafeCursor(connection)
        assert safe.one("SELECT 1") is None


class TestOneOrNone:
    def test_returns_none_and_rolls_back_on_failure(self):
        cursor = make_cursor(execute_side_effect=RuntimeError("bad query"))
        connection = make_connection(cursor)
        safe = SafeCursor(connection)
        assert safe.one_or_none("SELECT 1") is None
        connection.rollback.assert_called_once()

    def test_returns_row_on_success(self):
        cursor = make_cursor(fetchone={"n": 1})
        connection = make_connection(cursor)
        safe = SafeCursor(connection)
        assert safe.one_or_none("SELECT 1") == {"n": 1}


class TestAll:
    def test_returns_multiple_rows(self):
        cursor = make_cursor(fetchall=[{"n": 1}, {"n": 2}])
        connection = make_connection(cursor)
        safe = SafeCursor(connection)
        assert safe.all("SELECT * FROM t") == [{"n": 1}, {"n": 2}]


class TestCommitAndRollback:
    def test_commit_swallows_exception(self):
        connection = MagicMock()
        connection.commit.side_effect = RuntimeError("gone")
        safe = SafeCursor(connection)
        safe.commit()

    def test_commit_or_raise_raises(self):
        connection = MagicMock()
        connection.commit.side_effect = RuntimeError("gone")
        safe = SafeCursor(connection)
        try:
            safe.commit_or_raise()
            assert False, "expected an exception"
        except RuntimeError:
            pass

    def test_rollback_swallows_exception(self):
        connection = MagicMock()
        connection.rollback.side_effect = RuntimeError("gone")
        safe = SafeCursor(connection)
        safe.rollback()
