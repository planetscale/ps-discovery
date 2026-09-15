"""Tests for LogCaptureProbe."""

from unittest.mock import MagicMock

from planetscale_discovery.workload.burst.readiness import (
    RDS_RICH_LOG_LINE_PREFIX,
    LogCaptureProbe,
    _destinations,
    _logs_every_statement,
    _num,
    _positive,
    _prefix_escapes,
    _tiers,
)


class TestSetting:
    def test_returns_none_when_the_read_fails(self):
        probe = LogCaptureProbe(MagicMock())
        probe._sql = MagicMock(one_or_none=MagicMock(return_value=None))
        assert probe._setting("log_statement") is None

    def test_returns_the_value_on_success(self):
        probe = LogCaptureProbe(MagicMock())
        probe._sql = MagicMock(one_or_none=MagicMock(return_value={"value": "0"}))
        assert probe._setting("log_min_duration_statement") == "0"


class TestLogFdw:
    def _probe(self, row):
        probe = LogCaptureProbe(MagicMock())
        probe._sql = MagicMock(one_or_none=MagicMock(return_value=row))
        return probe

    def test_absent_on_a_failed_read(self):
        assert self._probe(None)._log_fdw() == "absent"

    def test_installed_when_installed_version_is_set(self):
        row = {"installed_version": "1.0", "available": True}
        assert self._probe(row)._log_fdw() == "installed"

    def test_available_when_only_the_availability_flag_is_set(self):
        row = {"installed_version": None, "available": True}
        assert self._probe(row)._log_fdw() == "available"

    def test_absent_when_neither_is_set(self):
        row = {"installed_version": None, "available": False}
        assert self._probe(row)._log_fdw() == "absent"


class TestPgaudit:
    def _probe(self, installed_version=None, available=False, is_superuser="off"):
        probe = LogCaptureProbe(MagicMock())

        def one_or_none(sql, params=None):
            if "pg_extension" in sql:
                return {"installed_version": installed_version, "available": available}
            if "current_setting" in sql:
                name = params[0]
                if name == "is_superuser":
                    return {"value": is_superuser}
                return {"value": None}
            return None

        probe._sql = MagicMock()
        probe._sql.one_or_none.side_effect = one_or_none
        return probe

    def test_installed_state(self):
        result = self._probe(installed_version="1.6", available=True)._pgaudit()
        assert result["state"] == "installed"
        assert result["installed"] is True

    def test_available_state(self):
        result = self._probe(installed_version=None, available=True)._pgaudit()
        assert result["state"] == "available"
        assert result["installed"] is False

    def test_absent_state(self):
        result = self._probe(installed_version=None, available=False)._pgaudit()
        assert result["state"] == "absent"

    def test_connected_role_is_superuser_true(self):
        result = self._probe(is_superuser="on")._pgaudit()
        assert result["connected_role_is_superuser"] is True

    def test_connected_role_is_superuser_false(self):
        result = self._probe(is_superuser="off")._pgaudit()
        assert result["connected_role_is_superuser"] is False


class TestRequirements:
    def _requirements(self, settings, version, provider, log_fdw):
        probe = LogCaptureProbe(MagicMock())
        destinations = _destinations(settings.get("log_destination"))
        prefix = _prefix_escapes(settings.get("log_line_prefix"))
        return probe._requirements(
            settings, version, provider, destinations, prefix, log_fdw
        )

    def test_a_fully_capable_rds_host(self):
        settings = {
            "log_destination": "csvlog",
            "logging_collector": "on",
            "log_line_prefix": RDS_RICH_LOG_LINE_PREFIX,
            "log_min_duration_statement": "0",
            "log_statement": "none",
            "log_transaction_sample_rate": "1",
            "log_parameter_max_length": "-1",
            "compute_query_id": "on",
        }
        req = self._requirements(settings, 170000, "rds", "installed")
        assert req["statement_log"]["available"] is True
        assert req["session_identity"]["available"] is True
        assert req["whole_transactions"]["available"] is True
        assert req["values"]["available"] is True
        assert req["readable_over_sql"]["available"] is True
        assert req["aggregate_join"]["available"] is True

    def test_a_bare_minimum_self_managed_host(self):
        settings = {
            "log_destination": "stderr",
            "logging_collector": "off",
            "log_line_prefix": "%t ",
            "log_min_duration_statement": "-1",
            "log_statement": "none",
            "log_transaction_sample_rate": "0",
            "log_parameter_max_length": "0",
            "compute_query_id": "off",
        }
        req = self._requirements(settings, 110000, "self_managed", "absent")
        assert req["statement_log"]["available"] is False
        assert req["session_identity"]["available"] is False
        assert req["whole_transactions"]["available"] is False
        assert req["values"]["available"] is False
        assert req["readable_over_sql"]["available"] is False
        assert req["aggregate_join"]["available"] is False


class TestNotes:
    def _codes(self, settings, version=170000, provider="self_managed"):
        probe = LogCaptureProbe(MagicMock())
        destinations = _destinations(settings.get("log_destination"))
        prefix = _prefix_escapes(settings.get("log_line_prefix"))
        notes = probe._notes(settings, version, provider, destinations, prefix)
        return {note["code"] for note in notes}

    def test_log_statement_loses_query_id_on_simple_protocol(self):
        settings = {
            "log_statement": "all",
            "log_min_duration_statement": "-1",
            "log_destination": "stderr",
            "log_line_prefix": "%t ",
        }
        codes = self._codes(settings)
        assert "log_statement_loses_query_id_on_simple_protocol" in codes
        assert "log_statement_splits_text_from_query_id" not in codes

    def test_log_statement_splits_text_from_query_id(self):
        settings = {
            "log_statement": "all",
            "log_min_duration_statement": "0",
            "log_destination": "stderr",
            "log_line_prefix": "%t ",
        }
        codes = self._codes(settings)
        assert "log_statement_splits_text_from_query_id" in codes
        assert "log_statement_loses_query_id_on_simple_protocol" not in codes

    def test_no_session_identity_in_log(self):
        settings = {
            "log_statement": "none",
            "log_min_duration_statement": "-1",
            "log_destination": "stderr",
            "log_line_prefix": "%t ",
        }
        assert "no_session_identity_in_log" in self._codes(settings)

    def test_a_session_id_in_the_prefix_suppresses_the_note(self):
        settings = {
            "log_statement": "none",
            "log_min_duration_statement": "-1",
            "log_destination": "stderr",
            "log_line_prefix": "%c ",
        }
        assert "no_session_identity_in_log" not in self._codes(settings)


class TestPgauditNotes:
    def test_pgaudit_superuser_not_audited(self):
        probe = LogCaptureProbe(MagicMock())
        pgaudit = {
            "state": "installed",
            "connected_role_is_superuser": True,
            "settings": {},
        }
        codes = {note["code"] for note in probe._pgaudit_notes(pgaudit)}
        assert "pgaudit_superuser_not_audited" in codes

    def test_pgaudit_object_logging_corrupts_shapes(self):
        probe = LogCaptureProbe(MagicMock())
        pgaudit = {
            "state": "installed",
            "connected_role_is_superuser": False,
            "settings": {"pgaudit.log_relation": "on"},
        }
        codes = {note["code"] for note in probe._pgaudit_notes(pgaudit)}
        assert "pgaudit_object_logging_corrupts_shapes" in codes

    def test_an_absent_pgaudit_produces_no_notes(self):
        probe = LogCaptureProbe(MagicMock())
        pgaudit = {
            "state": "absent",
            "connected_role_is_superuser": True,
            "settings": {"pgaudit.log_relation": "on"},
        }
        assert probe._pgaudit_notes(pgaudit) == []


class TestTiers:
    def test_the_boolean_combination_logic(self):
        requirements = {
            "statement_log": {"available": True},
            "session_identity": {"available": True},
            "whole_transactions": {"available": False},
            "values": {"available": True},
            "readable_over_sql": {"available": False},
            "aggregate_join": {"available": True},
        }
        assert _tiers(requirements) == {
            "transaction_shapes": True,
            "unbiased_transactions": False,
            "access_skew": True,
            "collectable_over_sql": False,
            "coverage_against_aggregate": True,
        }


class TestDestinations:
    def test_parses_a_comma_separated_list(self):
        assert _destinations("csvlog,stderr") == {
            "stderr": True,
            "csvlog": True,
            "jsonlog": False,
            "syslog": False,
        }

    def test_a_missing_value_is_all_false(self):
        assert _destinations(None) == {
            "stderr": False,
            "csvlog": False,
            "jsonlog": False,
            "syslog": False,
        }


class TestPrefixEscapes:
    def test_detects_each_escape(self):
        result = _prefix_escapes("%m %c %v %x %Q")
        assert result["session_id"] is True
        assert result["virtual_txid"] is True
        assert result["txid"] is True
        assert result["query_id"] is True
        assert result["timestamp"] is True

    def test_a_missing_prefix_is_all_false(self):
        assert _prefix_escapes(None) == {
            "raw": "",
            "session_id": False,
            "virtual_txid": False,
            "txid": False,
            "query_id": False,
            "timestamp": False,
        }


class TestLogsEveryStatement:
    def test_zero_is_on(self):
        assert _logs_every_statement("0") is True

    def test_negative_one_is_off(self):
        assert _logs_every_statement("-1") is False

    def test_a_positive_value_is_on(self):
        assert _logs_every_statement("500") is True

    def test_a_missing_value_is_off(self):
        assert _logs_every_statement(None) is False


class TestPositive:
    def test_a_positive_number(self):
        assert _positive("0.5") is True

    def test_zero_is_not_positive(self):
        assert _positive("0") is False

    def test_a_missing_value_is_not_positive(self):
        assert _positive(None) is False


class TestNum:
    def test_parses_the_leading_number(self):
        assert _num("123abc") == 123.0

    def test_a_missing_value_returns_none(self):
        assert _num(None) is None

    def test_non_numeric_text_returns_none(self):
        assert _num("abc") is None
