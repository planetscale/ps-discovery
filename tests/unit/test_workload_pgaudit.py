import json

import pytest

from planetscale_discovery.workload.logs.pgaudit import (
    DEFAULT_PREFIX,
    ObjectLoggingError,
    build_prefix_re,
    new_summary,
    read_jsonl_records,
    read_log_records,
    sniff_prefix,
)


def _audit_line(pid, statement_id, sql, audit_type="SESSION", command="SELECT"):
    return (
        f"2024-01-15 10:30:00.123 UTC [{pid}] LOG:  "
        f"AUDIT: {audit_type},{statement_id},1,READ,{command},,,{sql},<not logged>"
    )


class TestSniffPrefix:
    def test_sniffs_the_prefix_from_a_sample_of_audit_lines(self):
        lines = [_audit_line(12345, i, "SELECT 1") for i in range(1, 4)]
        prefix = sniff_prefix(lines)
        assert prefix is not None
        matcher = build_prefix_re(prefix)
        match = matcher.match(lines[0])
        assert match is not None
        assert match.group("process_id") == "12345"


class TestReadLogRecords:
    def test_a_normal_audit_record_parses_into_a_csvlog_shaped_record(self):
        lines = [_audit_line(12345, 1, "SELECT * FROM t")]
        records = list(read_log_records(lines, prefix=DEFAULT_PREFIX))
        assert len(records) == 1
        record = records[0]
        assert record["message"] == "statement: SELECT * FROM t"
        assert record["command_tag"] == "SELECT"
        assert record["session_line_num"] == "1"
        assert record["detail"] == ""

    def test_a_repeated_statement_id_in_the_same_session_raises(self):
        line = _audit_line(12345, 1, "SELECT 1")
        with pytest.raises(ObjectLoggingError):
            list(read_log_records([line, line], prefix=DEFAULT_PREFIX))

    def test_an_object_audit_type_raises_directly(self):
        line = _audit_line(12345, 1, "SELECT * FROM t", audit_type="OBJECT")
        with pytest.raises(ObjectLoggingError):
            list(read_log_records([line], prefix=DEFAULT_PREFIX))


def _chunk_payload(session, statement_id, chunk_count, chunk_index, statement_part):
    return {
        "jsonPayload": {
            "command": "SELECT",
            "databaseSessionId": session,
            "statementId": statement_id,
            "chunkCount": chunk_count,
            "chunkIndex": chunk_index,
            "auditType": "SESSION",
            "statement": statement_part,
            "parameter": "",
            "user": "u",
            "database": "d",
        },
        "timestamp": "2024-01-15T10:30:00Z",
    }


class TestReadJsonlRecords:
    def test_a_chunked_record_numbered_from_one_is_reassembled(self):
        lines = [
            json.dumps(_chunk_payload("sess1", "1", 3, 1, "SELECT ")) + "\n",
            json.dumps(_chunk_payload("sess1", "1", 3, 2, "* FROM ")) + "\n",
            json.dumps(_chunk_payload("sess1", "1", 3, 3, "t")) + "\n",
        ]
        summary = new_summary()
        records = list(read_jsonl_records(lines, summary=summary))
        assert len(records) == 1
        assert records[0]["message"] == "statement: SELECT * FROM t"
        assert records[0]["session_id"] == "sess1"
        assert summary["malformed"] == 0

    def test_a_chunked_record_numbered_from_zero_is_reassembled(self):
        lines = [
            json.dumps(_chunk_payload("sess1", "1", 2, 0, "SELECT * FROM ")) + "\n",
            json.dumps(_chunk_payload("sess1", "1", 2, 1, "t")) + "\n",
        ]
        summary = new_summary()
        records = list(read_jsonl_records(lines, summary=summary))
        assert len(records) == 1
        assert records[0]["message"] == "statement: SELECT * FROM t"
        assert summary["malformed"] == 0

    def test_a_chunk_group_with_a_gap_is_malformed(self):
        summary = new_summary()
        lines = [
            json.dumps(_chunk_payload("sess1", "1", 2, 1, "SELECT * FROM ")) + "\n",
            json.dumps(_chunk_payload("sess1", "1", 2, 3, "t")) + "\n",
        ]
        records = list(read_jsonl_records(lines, summary=summary))
        assert records == []
        assert summary["malformed"] == 1

    def test_an_incomplete_chunk_group_is_counted(self):
        summary = new_summary()
        lines = [
            json.dumps(_chunk_payload("sess1", "1", 2, 1, "SELECT * FROM ")) + "\n",
        ]
        records = list(read_jsonl_records(lines, summary=summary))
        assert records == []
        assert summary["incomplete_chunks"] == 1

    def test_a_json_array_document_is_read(self):
        doc = [
            _chunk_payload("sess1", "1", 1, 0, "SELECT 1"),
            _chunk_payload("sess2", "2", 1, 0, "SELECT 2"),
        ]
        text = json.dumps(doc, indent=2)
        lines = text.splitlines(keepends=True)
        summary = new_summary()
        records = list(read_jsonl_records(lines, summary=summary))
        assert {r["session_id"] for r in records} == {"sess1", "sess2"}
        assert summary["array_bytes"] > 0
