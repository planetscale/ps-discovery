"""Tests for collect_pgaudit_file and collect_stderr_file."""

import csv
import io
import json

import pytest

from planetscale_discovery.workload.burst.importer import (
    MAX_EXPORT_BYTES,
    _clock_warnings,
    collect_csv_file,
    collect_pgaudit_file,
    collect_stderr_file,
    sniff_packaging,
)
from planetscale_discovery.workload.logs.record import COLUMNS, WIDTH_PG13


def write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


PGAUDIT_LOG = (
    "2024-01-01 00:00:00.000 UTC [111] LOG:  AUDIT: "
    'SESSION,1,1,READ,SELECT,,,"select 1",<not logged>\n'
    "2024-01-01 00:00:01.000 UTC [111] LOG:  AUDIT: "
    'SESSION,2,1,WRITE,INSERT,,,"insert into t values (1)",<not logged>\n'
)

PGAUDIT_NO_STATEMENTS_LOG = (
    "2024-01-01 00:00:00.000 UTC [111] LOG:  disconnection: session time: "
    "0:00:01.234 user=x database=y host=127.0.0.1\n"
    "2024-01-01 00:00:01.000 UTC [111] LOG:  disconnection: session time: "
    "0:00:01.234 user=x database=y host=127.0.0.1\n"
)

STDERR_LOG = (
    "2024-01-01 00:00:00.000 UTC [111] LOG:  statement: select 1\n"
    "2024-01-01 00:00:01.000 UTC [111] LOG:  statement: select 2\n"
)

STDERR_NO_STATEMENTS_LOG = (
    "2024-01-01 00:00:00.000 UTC [111] FATAL:  terminating connection due to "
    "administrator command\n"
    "2024-01-01 00:00:01.000 UTC [111] FATAL:  terminating connection due to "
    "administrator command\n"
)


class TestCollectPgauditFile:
    def test_reads_a_pgaudit_text_file(self, tmp_path):
        path = write(tmp_path, "pgaudit.log", PGAUDIT_LOG)
        result = collect_pgaudit_file(path, json_export=False)
        assert len(result["files_read"]) == 1
        assert len(result["statements"]) == 2
        assert result["sessions"]["statements"] == 2
        assert result["sessions"]["sessions"] == 1

    def test_reads_a_jsonl_export(self, tmp_path):
        record = {
            "timestamp": "2024-01-01T00:00:00Z",
            "jsonPayload": {
                "command": "SELECT",
                "statement": "select 1",
                "databaseSessionId": "abc",
                "statementId": "1",
                "auditType": "SESSION",
            },
        }
        path = write(tmp_path, "pgaudit.jsonl", json.dumps(record) + "\n")
        result = collect_pgaudit_file(path, json_export=True)
        assert len(result["statements"]) == 1
        assert result["statements"][0]["sql"] == "select 1"

    def test_already_read_short_circuits_without_reparsing(self, tmp_path):
        path = write(tmp_path, "pgaudit.log", "not valid pgaudit content at all")
        result = collect_pgaudit_file(path, json_export=False, already_read=[path])
        assert result["files_read"] == []
        assert result["files_skipped"] == [
            {"file": path, "why": "already read by an earlier burst"}
        ]
        assert result["statements"] == []

    def test_no_statements_raises_value_error_with_remediation(self, tmp_path):
        path = write(tmp_path, "pgaudit.log", PGAUDIT_NO_STATEMENTS_LOG)
        with pytest.raises(ValueError, match="no pgAudit statements found"):
            collect_pgaudit_file(path, json_export=False)


class TestCollectStderrFile:
    def test_reads_a_stderr_text_file(self, tmp_path):
        path = write(tmp_path, "postgresql.log", STDERR_LOG)
        result = collect_stderr_file(path)
        assert len(result["files_read"]) == 1
        assert len(result["statements"]) == 2
        assert result["sessions"]["statements"] == 2

    def test_no_statements_raises_value_error_with_remediation(self, tmp_path):
        path = write(tmp_path, "postgresql.log", STDERR_NO_STATEMENTS_LOG)
        with pytest.raises(ValueError, match="no statements found"):
            collect_stderr_file(path)


class TestAnOversizedExportIsRefused:
    """The bug: an import read the whole file into memory and died with
    MemoryError rather than naming a limit."""

    def test_a_file_over_the_ceiling_names_the_limit(self, tmp_path, mocker):
        path = write(tmp_path, "huge.log", "x")
        mocker.patch(
            "planetscale_discovery.workload.burst.importer.os.path.getsize",
            return_value=MAX_EXPORT_BYTES + 1,
        )
        with pytest.raises(ValueError, match="over the"):
            collect_stderr_file(path)


def _csvlog_line(**overrides):
    values = {name: "" for name in COLUMNS[:WIDTH_PG13]}
    values["log_time"] = "2024-01-01 00:00:00.000 UTC"
    values["error_severity"] = "LOG"
    values.update(overrides)
    buffer = io.StringIO()
    csv.writer(buffer).writerow([values[name] for name in COLUMNS[:WIDTH_PG13]])
    return buffer.getvalue()


class TestSniffPackaging:
    def test_a_leading_brace_is_json(self, tmp_path):
        path = write(
            tmp_path, "export.json", '{"jsonPayload": {"command": "SELECT"}}\n'
        )
        assert sniff_packaging(path) == "json"

    def test_a_csvlog_row_is_csv(self, tmp_path):
        path = write(
            tmp_path,
            "postgresql.csv",
            _csvlog_line(message="statement: select 1"),
        )
        assert sniff_packaging(path) == "csv"

    def test_a_csv_message_containing_a_severity_marker_stays_csv(self, tmp_path):
        """The bug: a csv message containing LOG: was read as plain text."""
        path = write(
            tmp_path,
            "postgresql.csv",
            _csvlog_line(message="statement: select 1 LOG: extra"),
        )
        assert sniff_packaging(path) == "csv"

    def test_a_leading_bracket_is_json(self, tmp_path):
        """The bug: a JSON array export was not recognized as json."""
        path = write(
            tmp_path, "export.json", '[{"jsonPayload": {"command": "SELECT"}}]\n'
        )
        assert sniff_packaging(path) == "json"

    def test_a_pretty_printed_array_is_json(self, tmp_path):
        path = write(
            tmp_path,
            "export.json",
            '[\n  {"jsonPayload": {"command": "SELECT"}}\n]\n',
        )
        assert sniff_packaging(path) == "json"

    def test_a_compact_array_longer_than_the_sample_is_json(self, tmp_path):
        """The bug: a one-line JSON array over 8192 bytes was not recognized."""
        records = [{"jsonPayload": {"command": "SELECT", "statement": "select 1"}}]
        records.extend({"n": i, "pad": "x" * 40} for i in range(200))
        text = json.dumps(records)
        assert len(text) > 8192
        path = write(tmp_path, "export.json", text)
        assert sniff_packaging(path) == "json"

    def test_a_pretty_object_longer_than_the_sample_is_json(self, tmp_path):
        """The bug: a pretty-printed object over 8192 bytes was not recognized."""
        payload = {"entries": [{"n": i, "statement": "select 1"} for i in range(200)]}
        text = json.dumps(payload, indent=2)
        assert len(text) > 8192
        path = write(tmp_path, "export.json", text)
        assert sniff_packaging(path) == "json"

    def test_a_bracket_prefix_is_text(self, tmp_path):
        """The bug: a log_line_prefix of [%p] was read as a JSON export."""
        path = write(
            tmp_path,
            "postgresql.log",
            "[4242] LOG:  statement: select 1\n",
        )
        assert sniff_packaging(path) == "text"

    def test_an_empty_file_is_not_json(self, tmp_path):
        """The bug: a blank file was reported as a JSON log export."""
        path = write(tmp_path, "empty.log", "")
        with pytest.raises(ValueError, match="no PostgreSQL log lines"):
            sniff_packaging(path)

    def test_a_known_width_row_without_a_timestamp_is_text(self, tmp_path):
        """The bug: a known-width row whose first field is not a timestamp was read as csv."""
        path = write(
            tmp_path,
            "postgresql.csv",
            _csvlog_line(
                log_time="not-a-timestamp",
                message="statement: select 1 LOG: extra",
            ),
        )
        assert sniff_packaging(path) == "text"

    def test_a_csv_error_falls_through_to_text(self, tmp_path):
        """The bug: a field over the csv limit escaped sniff as csv.Error."""
        path = write(
            tmp_path,
            "postgresql.log",
            "2024-01-01 00:00:00.000 UTC [111] LOG:  statement: select 1\n",
        )
        limit = csv.field_size_limit()
        csv.field_size_limit(8)
        try:
            assert sniff_packaging(path) == "text"
        finally:
            csv.field_size_limit(limit)

    def test_a_text_audit_line_with_commas_is_text(self, tmp_path):
        path = write(tmp_path, "postgresql.log", PGAUDIT_LOG)
        assert sniff_packaging(path) == "text"

    def test_a_file_with_no_log_line_raises(self, tmp_path):
        path = write(tmp_path, "notes.txt", "not a log\n")
        with pytest.raises(ValueError, match="no PostgreSQL log lines"):
            sniff_packaging(path)


class TestCollectCsvFile:
    def test_an_audit_csv_is_read_as_pgaudit(self, tmp_path):
        path = write(
            tmp_path,
            "postgresql.csv",
            _csvlog_line(
                session_id="5f1.3",
                message='AUDIT: SESSION,1,1,READ,SELECT,,,"select 1",<not logged>',
            ),
        )
        result = collect_csv_file(path, audit=True)
        assert result["statements"][0]["sql"] == "select 1"

    def test_a_statement_csv_is_read_as_statements(self, tmp_path):
        path = write(
            tmp_path,
            "postgresql.csv",
            _csvlog_line(message="statement: select 1"),
        )
        result = collect_csv_file(path, audit=False)
        assert result["statements"][0]["sql"] == "select 1"

    def test_a_crlf_inside_a_quoted_field_is_kept(self, tmp_path):
        """The bug: a CR LF inside a quoted csvlog field was stored as LF."""
        path = tmp_path / "postgresql.csv"
        path.write_bytes(
            _csvlog_line(message="statement: select 1\r\nfrom t").encode("utf-8")
        )
        result = collect_csv_file(str(path), audit=False)
        assert result["statements"][0]["sql"] == "select 1\r\nfrom t"


class TestClockWarnings:
    def test_unresolved_zone_abbreviation_warns(self):
        statements = [{"log_time": "2024-01-01 00:00:00 EST"}]
        warnings = _clock_warnings(statements, "2024-01-01 00:00:00 EST")
        assert any("zone abbreviation" in w for w in warnings)

    def test_no_parseable_timestamp_warns_about_client_clock(self):
        statements = [{"log_time": None}]
        warnings = _clock_warnings(statements, None)
        assert any("client clock" in w for w in warnings)

    def test_utc_timestamps_produce_no_zone_warning(self):
        statements = [{"log_time": "2024-01-01 00:00:00 UTC"}]
        warnings = _clock_warnings(statements, "2024-01-01 00:00:00 UTC")
        assert not any("zone abbreviation" in w for w in warnings)
