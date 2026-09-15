"""collect reads the query log when the config asks for it, and never fails on it."""

import pytest

from planetscale_discovery.config.config_manager import WorkloadConfig
from planetscale_discovery.workload import cli_workload
from planetscale_discovery.workload.cli_workload import (
    EXIT_OK,
    EXIT_USAGE,
    _collect,
)
from planetscale_discovery.workload.store import WorkloadStore


class _Args:
    def __init__(self, session):
        self.session = str(session)


class _Database:
    def __init__(self, workload):
        self.workload = workload
        self.schemas = None
        self.statement_timeout = "300s"


class _Config:
    engine = "postgres"

    def __init__(self, workload):
        self.database = _Database(workload)


SNAPSHOT = {
    "status": "ok",
    "statements": [],
    "tables": [],
    "indexes": [],
    "duration_ms": 1,
    "captured_at_server": "2026-09-14T10:00:00Z",
    "warnings": [],
}


@pytest.fixture
def session(tmp_path):
    store = WorkloadStore(tmp_path / "wl")
    store.create()
    store.write_schema({})
    return store


def _no_snapshot_connection(mocker):
    mocker.patch.object(cli_workload, "_connect", return_value=mocker.Mock())
    collector = mocker.Mock()
    collector.collect.return_value = dict(SNAPSHOT)
    mocker.patch.object(cli_workload, "_new_collector", return_value=collector)


class TestTheLogIsOffByDefault:
    def test_collect_takes_a_snapshot_and_does_not_wait(self, session, mocker):
        _no_snapshot_connection(mocker)
        wait = mocker.patch.object(cli_workload, "_wait", return_value=False)

        assert (
            _collect(_Args(session.directory), _Config(WorkloadConfig()), mocker.Mock())
            == EXIT_OK
        )
        assert len(session.snapshot_paths()) == 1
        assert not session.burst_paths()
        wait.assert_not_called()


class TestAnUnreadableLogIsNotAFailure:
    """The bug: a log that cannot be read turns a good snapshot into exit 5."""

    def test_collect_still_exits_zero_and_keeps_the_snapshot(self, session, mocker):
        _no_snapshot_connection(mocker)
        mocker.patch.object(cli_workload, "_wait", return_value=False)
        mocker.patch(
            "planetscale_discovery.workload.burst.BurstCollector",
            side_effect=RuntimeError("log_fdw is not installed"),
        )
        logger = mocker.Mock()
        workload = WorkloadConfig(
            capture_log=True, capture_log_source="log_fdw", capture_log_seconds=10
        )

        assert _collect(_Args(session.directory), _Config(workload), logger) == EXIT_OK
        assert len(session.snapshot_paths()) == 1
        assert not session.burst_paths()
        assert logger.warning.called

    def test_a_failed_burst_is_not_stored_as_a_used_window(self, session, mocker):
        """The bug: a stored failed burst reports a window with no statements."""
        _no_snapshot_connection(mocker)
        mocker.patch.object(cli_workload, "_wait", return_value=False)
        collector = mocker.Mock()
        collector.collect.return_value = {
            "status": "failed",
            "window_start": "2026-09-14T10:00:00Z",
            "window_end": "2026-09-14T10:10:00Z",
            "statements": [],
            "sessions": {},
            "files_read": [],
            "warnings": ["log_fdw is not installed"],
        }
        mocker.patch(
            "planetscale_discovery.workload.burst.BurstCollector",
            return_value=collector,
        )
        logger = mocker.Mock()
        workload = WorkloadConfig(
            capture_log=True, capture_log_source="log_fdw", capture_log_seconds=10
        )

        assert _collect(_Args(session.directory), _Config(workload), logger) == EXIT_OK
        assert len(session.snapshot_paths()) == 1
        assert not session.burst_paths()
        assert logger.warning.called


class TestAnExportedLogNeedsItsFile:
    def test_a_file_source_with_no_file_is_a_usage_error(self, session, mocker):
        workload = WorkloadConfig(capture_log=True, capture_log_source="pgaudit")
        logger = mocker.Mock()

        assert (
            _collect(_Args(session.directory), _Config(workload), logger) == EXIT_USAGE
        )
        assert "capture_log_file" in logger.error.call_args[0][0]

    def test_a_file_source_still_takes_its_snapshot(self, session, mocker, tmp_path):
        """The bug: three exported windows produce no window to difference them against."""
        _no_snapshot_connection(mocker)
        export = tmp_path / "exported.log"
        export.write_text("")
        workload = WorkloadConfig(
            capture_log=True,
            capture_log_source="stderr",
            capture_log_file=str(export),
        )

        _collect(_Args(session.directory), _Config(workload), mocker.Mock())
        assert len(session.snapshot_paths()) == 1

    def test_an_unreadable_file_is_a_warning_not_a_non_zero_exit(
        self, session, mocker, tmp_path
    ):
        """The bug: the snapshot was stored and collect still exited 1, so
        cron read a finished capture as a fault."""
        _no_snapshot_connection(mocker)
        workload = WorkloadConfig(
            capture_log=True,
            capture_log_source="stderr",
            capture_log_file=str(tmp_path / "missing.log"),
        )
        logger = mocker.Mock()

        assert _collect(_Args(session.directory), _Config(workload), logger) == EXIT_OK
        assert len(session.snapshot_paths()) == 1
        assert logger.warning.called

    def test_a_file_already_read_stores_no_second_burst(
        self, session, mocker, tmp_path
    ):
        """The bug: hourly cron on one file stored an empty burst per run, and
        finalize then reported every window as a partial read."""
        _no_snapshot_connection(mocker)
        export = tmp_path / "exported.log"
        export.write_text(
            "2026-09-14 10:00:00.000 UTC [1] LOG:  statement: SELECT 1;\n"
        )
        workload = WorkloadConfig(
            capture_log=True,
            capture_log_source="stderr",
            capture_log_file=str(export),
        )
        args, config = _Args(session.directory), _Config(workload)

        assert _collect(args, config, mocker.Mock()) == EXIT_OK
        assert len(session.burst_paths()) == 1

        assert _collect(args, config, mocker.Mock()) == EXIT_OK
        assert len(session.burst_paths()) == 1

    def test_a_file_source_survives_a_dead_connection(self, session, mocker, tmp_path):
        mocker.patch.object(
            cli_workload, "_connect", side_effect=OSError("no route to host")
        )
        export = tmp_path / "exported.log"
        export.write_text("")
        workload = WorkloadConfig(
            capture_log=True,
            capture_log_source="stderr",
            capture_log_file=str(export),
        )
        logger = mocker.Mock()

        _collect(_Args(session.directory), _Config(workload), logger)
        assert not session.snapshot_paths()
        assert logger.warning.called


class TestTheWindowIsBounded:
    def test_the_burst_is_read_over_the_window_the_wait_held(self, session, mocker):
        _no_snapshot_connection(mocker)
        mocker.patch.object(cli_workload, "_wait", return_value=False)
        mocker.patch.object(cli_workload, "_utc_now", side_effect=["START", "END"])
        collector = mocker.Mock()
        collector.collect.return_value = {
            "status": "ok",
            "window_start": "2026-09-14T10:00:00Z",
            "window_end": "2026-09-14T10:10:00Z",
            "statements": [],
            "sessions": {},
            "files_read": ["a"],
        }
        burst = mocker.patch(
            "planetscale_discovery.workload.burst.BurstCollector",
            return_value=collector,
        )
        workload = WorkloadConfig(
            capture_log=True, capture_log_source="log_fdw", capture_log_seconds=10
        )

        _collect(_Args(session.directory), _Config(workload), mocker.Mock())

        assert burst.call_args.kwargs["since"] == "START"
        assert burst.call_args.kwargs["until"] == "END"
        assert len(session.burst_paths()) == 1


class TestPgauditIsTheDefaultSource:
    """The bug: turning capture_log on wrote to the customer's database."""

    def test_the_default_source_reads_a_file(self):
        assert WorkloadConfig().capture_log_source == "pgaudit"

    def test_auto_still_means_the_live_log_fdw_read(self):
        workload = WorkloadConfig(capture_log=True, capture_log_source="auto")
        config = _Config(workload)
        assert cli_workload._workload_config(config).capture_log_source == "log_fdw"

    def test_a_file_source_with_no_file_warns_at_init(self, mocker):
        workload = WorkloadConfig(capture_log=True)
        logger = mocker.Mock()

        cli_workload._report_log_readiness(mocker.Mock(), workload, logger)

        assert "capture_log_file names no file" in logger.warning.call_args[0][0]


class TestCleanupDropsWhatAKilledCollectLeft:
    def test_it_is_a_flag_on_init_and_needs_no_session(self, mocker):
        drop = mocker.patch(
            "planetscale_discovery.workload.burst.drop_leftovers",
            return_value={
                "found": [{"kind": "schema", "name": "ps_discovery_burst"}],
                "dropped": [{"kind": "schema", "name": "ps_discovery_burst"}],
                "failed": [],
            },
        )
        mocker.patch.object(cli_workload, "_connect", return_value=mocker.Mock())

        class Args:
            cleanup = True
            session = None

        assert (
            cli_workload._init(Args(), _Config(WorkloadConfig()), mocker.Mock())
            == EXIT_OK
        )
        assert drop.called

    def test_a_drop_that_failed_is_not_reported_as_success(self, mocker):
        mocker.patch(
            "planetscale_discovery.workload.burst.drop_leftovers",
            return_value={
                "found": [{"kind": "server", "name": "ps_discovery_log_server"}],
                "dropped": [],
                "failed": [
                    {
                        "kind": "server",
                        "name": "ps_discovery_log_server",
                        "why": "must be owner",
                    }
                ],
            },
        )
        mocker.patch.object(cli_workload, "_connect", return_value=mocker.Mock())

        class Args:
            cleanup = True
            session = None

        assert (
            cli_workload._init(Args(), _Config(WorkloadConfig()), mocker.Mock())
            == EXIT_USAGE
        )


class TestTheNewSettingsAreValidated:
    """The bug: a typo is discovered days later, after a capture collected nothing."""

    @pytest.mark.parametrize(
        "yaml_body, expected",
        (
            ("capture_log_seconds: 4", "between 10 and 3600"),
            ("capture_log_source: pgaudit_json", "must be one of"),
            ("capture_log_source: logfdw", "must be one of"),
        ),
    )
    def test_a_bad_value_is_rejected_at_load(self, tmp_path, yaml_body, expected):
        from planetscale_discovery.config.config_manager import ConfigManager

        path = tmp_path / "config.yaml"
        path.write_text(
            "database:\n  host: localhost\n  workload:\n    " + yaml_body + "\n"
        )
        with pytest.raises(ValueError, match=expected):
            ConfigManager(str(path)).load_config()


class TestAnInterruptedWaitStoresWhatItHeld:
    def test_it_is_a_shorter_window_not_an_error(self, session, mocker):
        _no_snapshot_connection(mocker)
        mocker.patch.object(cli_workload, "_wait", return_value=True)
        collector = mocker.Mock()
        collector.collect.return_value = {
            "status": "ok",
            "window_start": "2026-09-14T10:00:00Z",
            "window_end": "2026-09-14T10:00:30Z",
            "statements": [],
            "sessions": {},
            "files_read": ["a"],
        }
        mocker.patch(
            "planetscale_discovery.workload.burst.BurstCollector",
            return_value=collector,
        )
        workload = WorkloadConfig(
            capture_log=True, capture_log_source="log_fdw", capture_log_seconds=600
        )

        assert (
            _collect(_Args(session.directory), _Config(workload), mocker.Mock())
            == EXIT_OK
        )
        assert len(session.burst_paths()) == 1
