"""A sniffed prefix must not silently lose statements.

Both bugs here were found by capturing a real server log and comparing the
statement count against the file. Neither raised anything.
"""

from planetscale_discovery.workload.logs.pgaudit import sniff_prefix
from planetscale_discovery.workload.logs.stderrlog import _command_tag, read_records
from planetscale_discovery.workload.logs.transactions import (
    group_by_session,
    transactions,
)

# Prefix '%m [%p] %c %v %Q ', with negative query ids as compute_query_id emits.
WINDOW = [
    "2026-09-15 01:02:10.750 UTC [100] 6aa898eb.64 3/353 4854991825702068830 "
    "LOG:  duration: 0.048 ms  statement: BEGIN;",
    "2026-09-15 01:02:10.751 UTC [100] 6aa898eb.64 3/353 -2436581052853006246 "
    "LOG:  duration: 0.206 ms  statement: INSERT INTO orders (tenant_id) VALUES (7);",
    "2026-09-15 01:02:10.751 UTC [100] 6aa898eb.64 3/353 -7146793572562687268 "
    "LOG:  duration: 0.082 ms  statement: INSERT INTO order_items (order_id) VALUES (1);",
    "2026-09-15 01:02:10.753 UTC [100] 6aa898eb.64 3/0 6097083398544187049 "
    "LOG:  duration: 2.289 ms  statement: COMMIT;",
]


class TestANegativeQueryIdDoesNotDropTheLine:
    """The bug: %Q was templated as %P (\\d*), so ~half of all lines vanished."""

    def test_every_statement_survives(self):
        prefix = sniff_prefix(WINDOW)
        records = list(read_records(WINDOW, prefix))
        assert len(records) == len(WINDOW)

    def test_the_virtual_transaction_id_is_recovered(self):
        prefix = sniff_prefix(WINDOW)
        assert "%v" in prefix
        records = list(read_records(WINDOW, prefix))
        assert records[0]["virtual_transaction_id"] == "3/353"

    def test_an_unreadable_line_is_counted_rather_than_dropped_in_silence(self):
        summary = {}
        list(read_records(WINDOW, "%m [%p] ", summary))
        assert summary["unparsed"] == len(WINDOW)


class TestAQueryIdFieldAcceptsASign:
    """The bug returns if %Q goes back to \\d+, which no sniffed prefix pins."""

    def test_a_negative_query_id_survives_an_explicit_percent_q(self):
        records = list(read_records(WINDOW, "%m [%p] %c %v %Q "))
        assert len(records) == len(WINDOW)
        assert records[1]["query_id"] == "-2436581052853006246"


class TestATransactionSynonymOpensATransaction:
    """The bug: START/END derive tags that BEGIN_TAGS and END_TAGS never match."""

    SYNONYM_WINDOW = [
        line.replace("BEGIN;", "START TRANSACTION;").replace("COMMIT;", "END;")
        for line in WINDOW
    ]

    def test_the_synonyms_derive_the_tag_postgresql_logs(self):
        assert _command_tag("statement: START TRANSACTION;") == "BEGIN"
        assert _command_tag("statement: END;") == "COMMIT"
        assert _command_tag("statement: ABORT;") == "ROLLBACK"

    def test_the_writes_still_group_into_one_transaction(self):
        records = list(read_records(self.SYNONYM_WINDOW, "%m [%p] %c %v %Q "))
        shapes = [
            shape
            for session in group_by_session(records).values()
            for shape in transactions(session)
        ]
        assert len(shapes) == 1
        assert shapes[0]["closed"] is True
        assert shapes[0]["explicit"] is True


class TestBeginOpensATransaction:
    """The bug: statement_kind names no BEGIN, so the tag kept its semicolon."""

    def test_the_tag_carries_no_semicolon(self):
        assert _command_tag("statement: BEGIN;") == "BEGIN"

    def test_the_writes_group_into_one_transaction(self):
        prefix = sniff_prefix(WINDOW)
        records = list(read_records(WINDOW, prefix))
        for record in records:
            record["sql"] = record["message"].split("statement: ", 1)[-1]
        shapes = [
            shape
            for records in group_by_session(records).values()
            for shape in transactions(records)
        ]
        assert len(shapes) == 1
        assert shapes[0]["closed"] is True
        assert shapes[0]["explicit"] is True
        tables = " ".join(row["sql"] for row in shapes[0]["rows"])
        assert "orders" in tables and "order_items" in tables
