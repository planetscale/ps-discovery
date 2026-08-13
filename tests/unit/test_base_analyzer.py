"""
Unit tests for the shared analyzer base classes (error detail, query probing)
"""

from unittest.mock import MagicMock

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class _StubAnalyzer(DatabaseAnalyzer):
    def analyze(self):
        return {}


def _analyzer(cursor=None):
    connection = MagicMock()
    if cursor is not None:
        connection.cursor.return_value = cursor
    return _StubAnalyzer(connection)


class TestAddError:
    """The exception detail is what makes an error diagnosable."""

    def test_exception_detail_is_recorded(self):
        analyzer = _analyzer()

        analyzer.add_error(
            "Query execution failed: SELECT generation_expression ...",
            Exception("Unknown column 'generation_expression' in 'field list'"),
        )

        entry = analyzer.errors[0]
        assert entry["exception"] == (
            "Unknown column 'generation_expression' in 'field list'"
        )
        assert entry["exception_type"] == "Exception"

    def test_message_without_exception_still_works(self):
        analyzer = _analyzer()

        analyzer.add_error("Something went wrong")

        assert analyzer.errors[0]["message"] == "Something went wrong"
        assert "exception" not in analyzer.errors[0]


class TestExecuteQuery:
    """execute_query swallows errors by default, and can be asked not to."""

    def test_failure_records_the_reason_and_the_statement(self):
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Unknown column 'x' in 'field list'")
        analyzer = _analyzer(cursor)

        result = analyzer.execute_query("SELECT\n    x\nFROM t")

        assert result == []
        entry = analyzer.errors[0]
        assert entry["exception"] == "Unknown column 'x' in 'field list'"
        # Whitespace collapsed so the truncated snippet stays readable
        assert "SELECT x FROM t" in entry["message"]

    def test_raise_on_error_propagates_and_records_nothing(self):
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Unknown column 'x' in 'field list'")
        analyzer = _analyzer(cursor)

        try:
            analyzer.execute_query("SELECT x FROM t", raise_on_error=True)
        except Exception as e:
            assert "Unknown column" in str(e)
        else:
            raise AssertionError("expected the query error to propagate")

        # A probe that is expected to fail must not pollute the error list
        assert analyzer.errors == []
