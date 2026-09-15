from planetscale_discovery.workload.logs.transactions import (
    group_by_session,
    transactions,
)


def _stmt(session_line_num, command_tag="SELECT", sql="", session_id="s1"):
    return {
        "session_id": session_id,
        "session_line_num": session_line_num,
        "command_tag": command_tag,
        "sql": sql,
    }


class TestTransactions:
    def test_an_explicit_begin_commit_pair_closes(self):
        result = transactions(
            [
                _stmt(1, "BEGIN"),
                _stmt(2, "UPDATE"),
                _stmt(3, "COMMIT"),
            ]
        )
        assert len(result) == 1
        txn = result[0]
        assert txn["closed"] is True
        assert txn["end_tag"] == "COMMIT"
        assert [s["command_tag"] for s in txn["statements"]] == ["UPDATE"]

    def test_an_explicit_begin_rollback_pair_closes(self):
        result = transactions([_stmt(1, "BEGIN"), _stmt(2, "ROLLBACK")])
        assert len(result) == 1
        assert result[0]["closed"] is True
        assert result[0]["end_tag"] == "ROLLBACK"

    def test_rollback_to_savepoint_does_not_close_the_transaction(self):
        result = transactions(
            [
                _stmt(1, "BEGIN"),
                _stmt(2, "ROLLBACK", sql="ROLLBACK TO SAVEPOINT sp1"),
                _stmt(3, "COMMIT"),
            ]
        )
        assert len(result) == 1
        txn = result[0]
        assert txn["closed"] is True
        assert txn["end_tag"] == "COMMIT"
        assert len(txn["statements"]) == 1

    def test_rollback_to_without_the_savepoint_keyword_does_not_close(self):
        result = transactions(
            [
                _stmt(1, "BEGIN"),
                _stmt(2, "UPDATE", sql="update orders set x=1"),
                _stmt(3, "ROLLBACK", sql="ROLLBACK TO sp1"),
                _stmt(4, "COMMIT"),
            ]
        )
        assert len(result) == 1
        assert result[0]["closed"] is True
        assert result[0]["end_tag"] == "COMMIT"

    def test_a_plain_rollback_still_closes_the_transaction(self):
        result = transactions(
            [
                _stmt(1, "BEGIN"),
                _stmt(2, "UPDATE", sql="update orders set x=1"),
                _stmt(3, "ROLLBACK", sql="ROLLBACK"),
            ]
        )
        assert len(result) == 1
        assert result[0]["end_tag"] == "ROLLBACK"

    def test_a_kept_transaction_carries_its_control_rows(self):
        result = transactions(
            [
                _stmt(1, "BEGIN"),
                _stmt(2, "UPDATE", sql="update orders set x=1"),
                _stmt(3, "COMMIT"),
            ]
        )
        assert [r["command_tag"] for r in result[0]["rows"]] == [
            "BEGIN",
            "UPDATE",
            "COMMIT",
        ]

    def test_a_savepoint_rollback_outside_any_transaction_is_autocommit(self):
        result = transactions([_stmt(1, "ROLLBACK", sql="ROLLBACK TO SAVEPOINT sp1")])
        assert len(result) == 1
        assert result[0]["explicit"] is False
        assert result[0]["closed"] is True

    def test_an_autocommit_statement_outside_any_transaction(self):
        result = transactions([_stmt(1, "SELECT")])
        assert len(result) == 1
        txn = result[0]
        assert txn["explicit"] is False
        assert txn["closed"] is True
        assert txn["statements"][0]["command_tag"] == "SELECT"

    def test_an_open_transaction_with_no_close_is_marked_unclosed(self):
        result = transactions([_stmt(1, "BEGIN"), _stmt(2, "UPDATE")])
        assert len(result) == 1
        assert result[0]["closed"] is False
        assert result[0]["end_tag"] is None


class TestGroupBySession:
    def test_groups_by_session_id_and_orders_by_line_num(self):
        statements_in = [
            _stmt(3, session_id="s1"),
            _stmt(1, session_id="s1"),
            _stmt(2, session_id="s2"),
        ]
        sessions = group_by_session(statements_in)
        assert set(sessions) == {"s1", "s2"}
        assert [s["session_line_num"] for s in sessions["s1"]] == [1, 3]

    def test_a_text_line_number_orders_numerically(self):
        """The bug: pgAudit's text ids sorted "10" before "2", so a
        twelve-statement transaction assembled as nine shapes."""
        rows = [_stmt(str(n)) for n in (1, 2, 10, 11, 3)]
        ordered = group_by_session(rows)["s1"]
        assert [s["session_line_num"] for s in ordered] == ["1", "2", "3", "10", "11"]

    def test_a_text_transaction_stays_one_shape(self):
        rows = [_stmt("1", "BEGIN")]
        rows += [
            _stmt(str(n), "INSERT", f"INSERT INTO t{n} VALUES (1)")
            for n in range(2, 12)
        ]
        rows += [_stmt("12", "COMMIT")]
        shapes = transactions(group_by_session(rows)["s1"])
        assert len(shapes) == 1
        assert shapes[0]["closed"] is True
        assert len(shapes[0]["rows"]) == 12

    def test_an_unparsable_line_number_still_sorts(self):
        rows = [_stmt("2"), _stmt("abc"), _stmt("1")]
        ordered = group_by_session(rows)["s1"]
        assert [s["session_line_num"] for s in ordered] == ["1", "2", "abc"]
