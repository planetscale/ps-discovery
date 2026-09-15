"""Tests for BurstCollector."""

from unittest.mock import MagicMock

from planetscale_discovery.workload.burst.collector import (
    STATUS_FAILED,
    BurstCollector,
    _event_window,
    _session_summary,
    _table_name,
)
from planetscale_discovery.workload.logs.timestamps import instant_utc


class FakeSql:
    def __init__(self, execute_ok=True, one_map=None, one_raises=False, all_map=None):
        self.errors = []
        self.execute_calls = []
        self.commits = 0
        self.rollbacks = 0
        self.execute_ok = execute_ok
        self.one_map = one_map or {}
        self.one_raises = one_raises
        self.all_map = all_map or {}

    def execute(self, sql, params=None):
        self.execute_calls.append(sql)
        ok = self.execute_ok(sql) if callable(self.execute_ok) else self.execute_ok
        if not ok:
            self.errors.append(f"{sql.split()[0].lower()}: boom")
        return ok

    def commit(self):
        self.commits += 1

    def commit_or_raise(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def one(self, sql, params=None):
        if self.one_raises:
            raise RuntimeError("one boom")
        for key, value in self.one_map.items():
            if key in sql:
                return value
        return None

    def one_or_none(self, sql, params=None):
        try:
            return self.one(sql, params)
        except Exception:
            return None

    def all(self, sql, params=None):
        for key, value in self.all_map.items():
            if key in sql:
                return value
        return []


def make_collector(**kwargs):
    return BurstCollector(MagicMock(), **kwargs)


class TestPrepare:
    def test_succeeds_through_the_full_sequence(self):
        collector = make_collector()
        collector._sql = FakeSql(
            execute_ok=True,
            one_map={
                "pg_foreign_server": None,
                "pg_extension": {"schema": "public"},
            },
        )
        assert collector._prepare() is True
        assert collector._setup_done is True
        assert collector._fdw_schema == "public"

    def test_returns_false_when_a_step_fails(self):
        collector = make_collector()
        collector._sql = FakeSql(
            execute_ok=lambda sql: "CREATE SCHEMA" not in sql,
            one_map={
                "pg_foreign_server": None,
                "pg_extension": {"schema": "public"},
            },
        )
        assert collector._prepare() is False
        assert collector._setup_done is False

    def test_returns_false_when_log_fdw_schema_is_missing(self):
        collector = make_collector()
        collector._sql = FakeSql(
            execute_ok=True,
            one_map={
                "pg_foreign_server": None,
                "pg_extension": None,
            },
        )
        assert collector._prepare() is False
        assert collector._setup_done is False


class TestCollectDegradesOnPrepareException:
    """_prepare() calls self._sql.one(), which raises rather than returning.

    collect() must convert that into a degraded result, not let it propagate.
    """

    def test_collect_returns_a_result_dict_instead_of_raising(self):
        collector = make_collector()
        collector._sql = FakeSql(execute_ok=True, one_raises=True)
        result = collector.collect()
        assert isinstance(result, dict)
        assert result["status"] == STATUS_FAILED
        assert result["statements"] == []

    def test_no_cleanup_is_attempted_since_setup_never_completed(self):
        collector = make_collector()
        collector._sql = FakeSql(execute_ok=True, one_raises=True)
        collector.collect()
        assert collector._setup_done is False


class TestAFailedLogFileListingRollsBack:
    """The bug: the aborted transaction made _cleanup's first DROP fail too,
    so the work schema survived the run."""

    def test_the_transaction_is_rolled_back_before_cleanup(self):
        collector = make_collector()
        collector._sql = FakeSql(
            execute_ok=True,
            one_map={
                "pg_foreign_server": None,
                "pg_extension": {"schema": "public"},
            },
        )

        def boom(sql, params=None):
            raise RuntimeError("list_postgres_log_files() is not callable")

        collector._sql.all = boom
        rollbacks_before = collector._sql.rollbacks

        result = collector.collect()

        assert result["status"] == STATUS_FAILED
        assert collector._sql.rollbacks > rollbacks_before
        drops = [c for c in collector._sql.execute_calls if c.startswith("DROP SCHEMA")]
        assert drops


class TestBeforeWatermark:
    def test_at_or_before_the_watermark_is_true(self):
        collector = make_collector()
        since = instant_utc("2024-01-01 00:30:00 UTC")
        collector._since_instant = since
        collector._log_tz = None
        assert collector._before_watermark("2024-01-01 00:30:00 UTC") is True
        assert collector._before_watermark("2024-01-01 00:00:00 UTC") is True

    def test_after_the_watermark_is_false(self):
        collector = make_collector()
        collector._since_instant = instant_utc("2024-01-01 00:30:00 UTC")
        collector._log_tz = None
        assert collector._before_watermark("2024-01-01 01:00:00 UTC") is False

    def test_no_watermark_set_is_always_false(self):
        collector = make_collector()
        collector._since_instant = None
        collector._log_tz = None
        assert collector._before_watermark("2024-01-01 00:00:00 UTC") is False

    def test_unparseable_log_time_is_false(self):
        collector = make_collector()
        collector._since_instant = instant_utc("2024-01-01 00:30:00 UTC")
        collector._log_tz = None
        assert collector._before_watermark("not a timestamp") is False


class TestUpperWatermark:
    def test_a_row_after_the_upper_bound_is_excluded(self):
        collector = make_collector()
        collector._until_instant = instant_utc("2024-01-01 01:00:00 UTC")
        collector._log_tz = None
        assert collector._after_watermark("2024-01-01 01:00:01 UTC") is True

    def test_a_row_at_the_upper_bound_is_kept(self):
        collector = make_collector()
        collector._until_instant = instant_utc("2024-01-01 01:00:00 UTC")
        collector._log_tz = None
        assert collector._after_watermark("2024-01-01 01:00:00 UTC") is False

    def test_no_upper_bound_keeps_everything(self):
        collector = make_collector()
        collector._until_instant = None
        collector._log_tz = None
        assert collector._after_watermark("2099-01-01 00:00:00 UTC") is False


class TestReadFile:
    def test_drops_a_row_before_the_watermark_and_tags_kept_rows(self):
        collector = make_collector()
        collector._since_instant = instant_utc("2024-01-01 00:30:00 UTC")
        collector._log_tz = None
        collector._fdw_schema = "public"
        collector._execute = lambda sql, params=None: True
        collector._column_count = lambda table: 26

        dropped_row = {
            "log_time": "2024-01-01 00:00:00 UTC",
            "session_id": "1.1",
            "session_line_num": "1",
            "command_tag": "SELECT",
            "message": "statement: select 1",
            "detail": "",
        }
        kept_row = {
            "log_time": "2024-01-01 01:00:00 UTC",
            "session_id": "1.1",
            "session_line_num": "2",
            "command_tag": "SELECT",
            "message": "statement: select 2",
            "detail": "",
        }
        collector._stream = lambda table, width: iter([dropped_row, kept_row])

        results = list(collector._read_file("some.csv"))

        assert len(results) == 1
        assert results[0]["log_file"] == "some.csv"
        assert results[0]["sql"] == "select 2"
        assert collector._skipped_before_watermark == 1


class TestEventWindow:
    def test_min_and_max_across_statements(self):
        statements = [
            {"log_time": "2024-01-01 02:00:00 UTC"},
            {"log_time": "2024-01-01 00:00:00 UTC"},
            {"log_time": "2024-01-01 01:00:00 UTC"},
        ]
        start, end = _event_window(statements, "fallback")
        assert start == "2024-01-01T00:00:00Z"
        assert end == "2024-01-01T02:00:00Z"

    def test_falls_back_when_no_statement_has_a_parseable_timestamp(self):
        statements = [{"log_time": "garbage"}, {"log_time": None}]
        start, end = _event_window(statements, "2024-01-01T00:00:00Z")
        assert start == "2024-01-01T00:00:00Z"
        assert end == "2024-01-01T00:00:00Z"


class TestTableName:
    def test_sanitizes_special_characters(self):
        name = _table_name("/var/log/postgresql/postgresql-2024-01-01_000000.csv")
        assert name.startswith("log_")
        assert all(c.isalnum() or c == "_" for c in name)

    def test_truncates_to_48_characters_after_the_prefix(self):
        name = _table_name("a" * 200 + ".csv")
        assert len(name) == len("log_") + 48


def _stmt(session_id, line, tag, parameters=None):
    return {
        "session_id": session_id,
        "session_line_num": line,
        "command_tag": tag,
        "sql": "",
        "parameters": parameters,
    }


class TestSessionSummary:
    def test_counts_sessions_statements_transactions_and_values(self):
        statements = [
            _stmt("1.1", 1, "BEGIN"),
            _stmt("1.1", 2, "INSERT", parameters="1, 'x'"),
            _stmt("1.1", 3, "COMMIT"),
            _stmt("2.1", 1, "BEGIN"),
            _stmt("2.1", 2, "UPDATE"),
            _stmt("3.1", 1, "SELECT"),
        ]
        summary = _session_summary(statements)
        assert summary["sessions"] == 3
        assert summary["statements"] == 6
        assert summary["with_values"] == 1
        assert summary["transactions"] == 3
        assert summary["explicit_transactions"] == 2
        assert summary["closed_transactions"] == 1
        assert summary["open_transactions"] == 1
