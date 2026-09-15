from planetscale_discovery.workload.logs.stderrlog import (
    DEFAULT_PREFIX,
    _command_tag,
    build_prefix_re,
    oldest_first,
    read_records,
)


class TestBuildPrefixRe:
    def test_the_default_prefix_matches_a_real_line(self):
        matcher = build_prefix_re(DEFAULT_PREFIX)
        match = matcher.match("2024-01-15 10:30:00.123 UTC [12345] ")
        assert match is not None
        assert match.group("process_id") == "12345"

    def test_percent_q_makes_the_remainder_optional(self):
        matcher = build_prefix_re("%m %q[%p] ")
        without_tail = matcher.match("2024-01-15 10:30:00.123 UTC ")
        assert without_tail is not None
        assert without_tail.group("process_id") is None

        with_tail = matcher.match("2024-01-15 10:30:00.123 UTC [999] ")
        assert with_tail is not None
        assert with_tail.group("process_id") == "999"

    def test_c_and_v_are_captured(self):
        matcher = build_prefix_re("%c %v ")
        match = matcher.match("5f1.3 12/34 ")
        assert match is not None
        assert match.group("session_id") == "5f1.3"
        assert match.group("virtual_transaction_id") == "12/34"


class TestReadRecords:
    def _line(self, tail):
        return f"2024-01-15 10:30:00.123 UTC [12345] {tail}"

    def test_a_normal_statement_line(self):
        lines = [self._line("LOG:  statement: SELECT 1")]
        records = list(read_records(lines))
        assert len(records) == 1
        assert records[0]["message"] == "statement: SELECT 1"
        assert records[0]["command_tag"] == "SELECT"

    def test_a_statement_detail_pairing_after_an_error_line(self):
        lines = [
            self._line('ERROR:  syntax error at or near "FROM"'),
            self._line("STATEMENT:  SELECT FROM t"),
            self._line("DETAIL:  Parameters: $1 = '5'"),
        ]
        records = list(read_records(lines))
        assert len(records) == 1
        assert records[0]["message"] == "statement: SELECT FROM t"
        assert records[0]["detail"] == "Parameters: $1 = '5'"

    def test_a_multiline_continuation_is_appended(self):
        lines = [
            self._line("LOG:  statement: SELECT 1,"),
            "\t2, 3",
        ]
        records = list(read_records(lines))
        assert len(records) == 1
        assert records[0]["message"] == "statement: SELECT 1,\n2, 3"

    def test_a_line_that_does_not_match_the_prefix_is_dropped(self):
        lines = ["this line has no recognizable prefix at all"]
        records = list(read_records(lines))
        assert records == []


class TestOldestFirst:
    def test_sorts_by_the_given_key(self):
        entries = [
            {"time": "2024-01-15", "message": "second"},
            {"time": "2024-01-10", "message": "first"},
        ]
        assert oldest_first(entries) == ["first", "second"]


class TestCommandTag:
    def test_a_statement_uses_the_sql_kind(self):
        assert _command_tag("statement: BEGIN") == "BEGIN"
        assert _command_tag("statement: SELECT 1") == "SELECT"

    def test_a_non_statement_message_uses_its_first_word(self):
        assert _command_tag("connection received") == "CONNECTION"
