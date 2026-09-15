"""Tests for collect_pgaudit_file and collect_stderr_file."""

import json

import pytest

from planetscale_discovery.workload.burst.importer import (
    MAX_EXPORT_BYTES,
    _clock_warnings,
    collect_pgaudit_file,
    collect_stderr_file,
)


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
