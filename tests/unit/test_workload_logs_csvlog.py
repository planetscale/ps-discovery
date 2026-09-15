import csv
import io
import logging

from planetscale_discovery.workload.logs.record import COLUMNS, WIDTH_PG13
from planetscale_discovery.workload.logs.csvlog import (
    _duration,
    _parameters,
    _row_to_dict,
    read_records,
    statements,
    to_statement,
)


def _row(width=WIDTH_PG13, **overrides):
    values = {name: "" for name in COLUMNS[:width]}
    values.update(overrides)
    return [values[name] for name in COLUMNS[:width]]


def _csv_line(row):
    buffer = io.StringIO()
    csv.writer(buffer).writerow(row)
    return buffer.getvalue()


class TestRowToDict:
    def test_a_known_width_maps_onto_columns(self):
        row = _row(session_id="5f1.3", message="statement: SELECT 1")
        record = _row_to_dict(row)
        assert record["session_id"] == "5f1.3"
        assert record["message"] == "statement: SELECT 1"

    def test_an_unknown_width_is_dropped_and_logged(self, caplog):
        with caplog.at_level(logging.WARNING, logger="planetscale_discovery"):
            record = _row_to_dict(["a", "b", "c"])
        assert record is None
        assert "3 columns" in caplog.text


class TestToStatement:
    def test_a_statement_line_yields_its_sql(self):
        record = _row_to_dict(_row(message="statement: SELECT 1"))
        statement = to_statement(record)
        assert statement["sql"] == "SELECT 1"

    def test_an_execute_unnamed_line_yields_its_sql(self):
        record = _row_to_dict(_row(message="execute <unnamed>: SELECT 2"))
        statement = to_statement(record)
        assert statement["sql"] == "SELECT 2"

    def test_a_parse_line_is_dropped(self):
        record = _row_to_dict(_row(message="parse <unnamed>: SELECT 1"))
        assert to_statement(record) is None

    def test_a_bind_line_is_dropped(self):
        record = _row_to_dict(_row(message="bind <unnamed>: SELECT 1"))
        assert to_statement(record) is None

    def test_an_error_with_no_statement_falls_back_to_the_query_column(self):
        record = _row_to_dict(
            _row(
                message='syntax error at or near "FROM"',
                error_severity="ERROR",
                query="SELECT FROM t",
            )
        )
        statement = to_statement(record)
        assert statement["sql"] == "SELECT FROM t"

    def test_a_non_error_with_no_statement_match_is_dropped(self):
        record = _row_to_dict(_row(message="connection received", query="SELECT 1"))
        assert to_statement(record) is None

    def test_parameters_are_extracted_from_detail(self):
        record = _row_to_dict(
            _row(
                message="execute <unnamed>: SELECT $1",
                detail="Parameters: $1 = '5'",
            )
        )
        statement = to_statement(record)
        assert statement["parameters"] == "$1 = '5'"


class TestDuration:
    def test_a_duration_prefix_is_parsed(self):
        assert _duration("duration: 12.345 ms  statement: SELECT 1") == 12.345

    def test_no_duration_prefix_is_none(self):
        assert _duration("statement: SELECT 1") is None


class TestParameters:
    def test_no_detail_is_none(self):
        assert _parameters(None) is None
        assert _parameters("") is None

    def test_a_non_parameters_detail_is_none(self):
        assert _parameters("some other detail") is None


class TestReadRecordsAndStatements:
    def test_read_records_skips_a_bad_width_row(self, caplog):
        lines = [_csv_line(["only", "three", "fields"])]
        with caplog.at_level(logging.WARNING, logger="planetscale_discovery"):
            records = list(read_records(lines))
        assert records == []

    def test_statements_end_to_end(self):
        lines = [_csv_line(_row(message="statement: SELECT 1"))]
        result = list(statements(read_records(lines)))
        assert len(result) == 1
        assert result[0]["sql"] == "SELECT 1"
