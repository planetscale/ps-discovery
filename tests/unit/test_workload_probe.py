"""Tests for the capability probe."""

from unittest.mock import MagicMock

import pytest

from planetscale_discovery.workload.probe import CapabilityProbe, _classify

BASE_IDENTITY = {
    "database": "app",
    "role": "discovery",
    "version_num": "170000",
    "is_superuser": False,
    "is_replica": False,
    "has_pg_monitor": True,
    "has_read_all_stats": True,
}


def make_probe(
    identity=None,
    preloaded=True,
    installed_version="1.11",
    available=True,
    readable=True,
    settings=None,
):
    """A probe whose queries are answered by intent, not by SQL text."""
    probe = CapabilityProbe(MagicMock())
    identity = {**BASE_IDENTITY, **(identity or {})}
    settings = settings or {}

    def one(sql, params=None):
        if "current_user AS role" in sql:
            return identity
        if "shared_preload_libraries" in sql:
            return {"libraries": "pg_stat_statements" if preloaded else "pg_cron"}
        if "pg_available_extensions" in sql:
            return {"installed_version": installed_version, "available": available}
        if "FROM pg_stat_statements LIMIT" in sql:
            if not readable:
                raise RuntimeError("permission denied")
            return {"?column?": 1}
        if "current_setting(%s, true)" in sql:
            return {"value": settings.get(params[0])}
        return None

    probe._one = one  # type: ignore[assignment]
    return probe


class TestClassify:
    @pytest.mark.parametrize(
        "installed,available,preloaded,readable,expected",
        [
            (True, True, True, True, "ok"),
            (True, True, False, True, "not_preloaded"),
            (True, True, True, False, "unreadable"),
            (False, True, True, False, "not_installed"),
            (False, True, False, False, "not_installed_not_preloaded"),
            (False, False, False, False, "absent"),
        ],
    )
    def test_states(self, installed, available, preloaded, readable, expected):
        assert _classify(installed, available, preloaded, readable) == expected


class TestRemediation:
    def test_a_working_server_can_collect(self):
        result = make_probe().run()
        assert result["can_collect_statements"] is True
        assert result["pg_stat_statements"]["state"] == "ok"

    def test_installed_but_not_preloaded_says_not_to_recreate(self):
        """The dangerous case: CREATE EXTENSION works, then nothing accumulates."""
        result = make_probe(preloaded=False).run()
        pgss = result["pg_stat_statements"]
        assert pgss["state"] == "not_preloaded"
        assert "shared_preload_libraries" in pgss["remediation"]
        assert "CREATE EXTENSION" not in pgss["remediation"]

    def test_a_non_superuser_is_told_who_can_create_the_extension(self):
        """It is not a trusted extension, so the advice would otherwise fail."""
        result = make_probe(installed_version=None).run()
        pgss = result["pg_stat_statements"]
        assert "not a trusted extension" in pgss["remediation"]
        assert "control plane" in pgss["remediation"]

    def test_a_superuser_is_not_told_to_ask_someone_else(self):
        result = make_probe(
            identity={"is_superuser": True}, installed_version=None
        ).run()
        assert "trusted extension" not in result["pg_stat_statements"]["remediation"]

    def test_relations_are_collectable_without_the_extension(self):
        result = make_probe(installed_version=None, available=False).run()
        assert result["can_collect_statements"] is False
        assert result["can_collect_relations"] is True


class TestGaps:
    def _codes(self, result):
        return {g["code"] for g in result["gaps"]}

    def test_missing_statistics_privileges_is_reported(self):
        result = make_probe(
            identity={"has_pg_monitor": False, "has_read_all_stats": False}
        ).run()
        # The gap is the softer case: statistics readable, other roles' text
        # hidden. Losing the statistics views entirely is fatal, and reported by
        # can_collect_relations rather than as a gap.
        assert "statement_text_may_be_masked" in self._codes(result)
        assert result["can_collect_relations"] is False

    def test_a_replica_is_reported(self):
        result = make_probe(identity={"is_replica": True}).run()
        assert "replica_target" in self._codes(result)

    def test_track_top_is_reported(self):
        result = make_probe(settings={"pg_stat_statements.track": "top"}).run()
        assert "nested_statements_not_tracked" in self._codes(result)

    def test_track_all_is_not_reported(self):
        result = make_probe(settings={"pg_stat_statements.track": "all"}).run()
        assert "nested_statements_not_tracked" not in self._codes(result)

    def test_io_timing_off_is_reported(self):
        result = make_probe(settings={"track_io_timing": "off"}).run()
        assert "io_timing_off" in self._codes(result)

    def test_the_default_statement_cap_is_reported(self):
        """Eviction is silent, so the risk has to be named up front."""
        result = make_probe(settings={"pg_stat_statements.max": "5000"}).run()
        assert "statement_eviction_possible" in self._codes(result)

    def test_a_raised_statement_cap_is_not_reported(self):
        result = make_probe(settings={"pg_stat_statements.max": "20000"}).run()
        assert "statement_eviction_possible" not in self._codes(result)

    def test_an_unreadable_statement_cap_is_not_reported(self):
        """A missing setting must not be read as a cap of zero."""
        assert "statement_eviction_possible" not in self._codes(make_probe().run())

    def test_the_transaction_blind_spot_is_always_reported(self):
        """Its absence must not read as a clean bill of health."""
        assert "transaction_boundaries_invisible" in self._codes(make_probe().run())


class TestAnUnreadableViewDoesNotBreakTheRestOfTheProbe:
    """Reading pg_stat_statements is expected to fail, so it must not be fatal.

    On a server where the extension exists but the library is not preloaded, the
    read raises and PostgreSQL aborts the transaction. Every later query then
    fails with "current transaction is aborted", which turned the one state the
    probe exists to report into a crash.
    """

    def test_the_transaction_is_rolled_back(self):
        probe = make_probe(readable=False)
        connection = MagicMock()
        probe.connection = connection
        probe.run()
        connection.rollback.assert_called_once()

    def test_the_state_and_the_gaps_still_come_back(self):
        probe = make_probe(readable=False, settings={"track_io_timing": "off"})
        probe.connection = MagicMock()
        result = probe.run()
        assert result["pg_stat_statements"]["state"] == "unreadable"
        assert result["can_collect_statements"] is False
        assert "io_timing_off" in [g["code"] for g in result["gaps"]]
