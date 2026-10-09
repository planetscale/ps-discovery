"""collect reads the query log when the config asks for it, and never fails on it."""

import csv
import io
import json

import pytest

from planetscale_discovery.config.config_manager import WorkloadConfig
from planetscale_discovery.workload import cli_workload
from planetscale_discovery.workload.cli_workload import (
    EXIT_OK,
    EXIT_USAGE,
    _collect,
)
from planetscale_discovery.workload.logs.record import COLUMNS, WIDTH_PG13
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
            capture_log=True,
            capture_log_type="statement",
            capture_log_source="log_fdw",
            capture_log_seconds=10,
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
            capture_log=True,
            capture_log_type="statement",
            capture_log_source="log_fdw",
            capture_log_seconds=10,
        )

        assert _collect(_Args(session.directory), _Config(workload), logger) == EXIT_OK
        assert len(session.snapshot_paths()) == 1
        assert not session.burst_paths()
        assert logger.warning.called


class TestAnExportedLogNeedsItsFile:
    def test_a_file_source_with_no_file_is_a_usage_error(self, session, mocker):
        workload = WorkloadConfig(capture_log=True, capture_log_type="statement")
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
            capture_log_type="statement",
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
            capture_log_type="statement",
            capture_log_file=str(tmp_path / "missing.log"),
        )
        logger = mocker.Mock()

        assert _collect(_Args(session.directory), _Config(workload), logger) == EXIT_OK
        assert len(session.snapshot_paths()) == 1
        assert logger.warning.called

    def test_a_sniffed_log_with_no_statements_keeps_the_snapshot(
        self, session, mocker, tmp_path
    ):
        """The bug: a sniffed log with no statements failed collect after the snapshot was stored."""
        _no_snapshot_connection(mocker)
        export = tmp_path / "exported.log"
        export.write_text(
            "2024-01-01 00:00:00.000 UTC [111] FATAL:  terminating connection "
            "due to administrator command\n"
        )
        workload = WorkloadConfig(
            capture_log=True,
            capture_log_type="statement",
            capture_log_file=str(export),
        )

        assert (
            _collect(_Args(session.directory), _Config(workload), mocker.Mock())
            == EXIT_OK
        )
        assert len(session.snapshot_paths()) == 1
        assert not session.burst_paths()

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
            capture_log_type="statement",
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
            capture_log_type="statement",
            capture_log_file=str(export),
        )
        logger = mocker.Mock()

        _collect(_Args(session.directory), _Config(workload), logger)
        assert not session.snapshot_paths()
        assert logger.warning.called


class TestAStatementJsonExportIsNotRead:
    def test_no_window_is_stored(self, session, mocker, tmp_path):
        """The bug: a statement capture stored a burst from a JSON export that held a statement."""
        _no_snapshot_connection(mocker)
        export = tmp_path / "export.json"
        export.write_text(
            json.dumps(
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "jsonPayload": {
                        "command": "SELECT",
                        "statement": "select 1",
                        "databaseSessionId": "abc",
                        "statementId": "1",
                        "auditType": "SESSION",
                    },
                }
            )
            + "\n"
        )
        workload = WorkloadConfig(
            capture_log=True,
            capture_log_type="statement",
            capture_log_file=str(export),
        )
        logger = mocker.Mock()

        assert _collect(_Args(session.directory), _Config(workload), logger) == EXIT_OK
        assert len(session.snapshot_paths()) == 1
        assert not session.burst_paths()
        assert (
            "read only when capture_log_type is pgaudit"
            in logger.warning.call_args[0][0]
        )


def _csvlog_line(**overrides):
    values = {name: "" for name in COLUMNS[:WIDTH_PG13]}
    values["log_time"] = "2024-01-01 00:00:00.000 UTC"
    values["error_severity"] = "LOG"
    values.update(overrides)
    buffer = io.StringIO()
    csv.writer(buffer).writerow([values[name] for name in COLUMNS[:WIDTH_PG13]])
    return buffer.getvalue()


def _collect_export(session, mocker, tmp_path, name, content, log_type, logger=None):
    _no_snapshot_connection(mocker)
    export = tmp_path / name
    export.write_text(content, encoding="utf-8")
    workload = WorkloadConfig(
        capture_log=True,
        capture_log_type=log_type,
        capture_log_file=str(export),
    )
    return _collect(
        _Args(session.directory),
        _Config(workload),
        logger if logger is not None else mocker.Mock(),
    )


class TestTheReaderFollowsTheSniffedPackaging:
    """The bug: the file was classified, then collect called the reader for a
    different packaging or type, and the window was empty."""

    def test_a_pgaudit_text_file_is_read(self, session, mocker, tmp_path):
        content = (
            "2024-01-01 00:00:00.000 UTC [111] LOG:  AUDIT: "
            'SESSION,1,1,READ,SELECT,,,"select 1",<not logged>\n'
        )
        code = _collect_export(
            session, mocker, tmp_path, "postgresql.log", content, "pgaudit"
        )
        assert code == EXIT_OK
        assert session.read_bursts()[0]["statements"][0]["sql"] == "select 1"

    def test_a_statement_csv_is_read(self, session, mocker, tmp_path):
        code = _collect_export(
            session,
            mocker,
            tmp_path,
            "postgresql.csv",
            _csvlog_line(message="statement: select 1"),
            "statement",
        )
        assert code == EXIT_OK
        assert session.read_bursts()[0]["statements"][0]["sql"] == "select 1"

    def test_a_pgaudit_csv_is_read(self, session, mocker, tmp_path):
        code = _collect_export(
            session,
            mocker,
            tmp_path,
            "postgresql.csv",
            _csvlog_line(
                session_id="5f1.3",
                message='AUDIT: SESSION,1,1,READ,SELECT,,,"select 1",<not logged>',
            ),
            "pgaudit",
        )
        assert code == EXIT_OK
        assert session.read_bursts()[0]["statements"][0]["sql"] == "select 1"

    def test_a_pgaudit_json_export_is_read(self, session, mocker, tmp_path):
        content = json.dumps(
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "jsonPayload": {
                    "command": "SELECT",
                    "statement": "select 1",
                    "databaseSessionId": "abc",
                    "statementId": "1",
                    "auditType": "SESSION",
                },
            }
        )
        code = _collect_export(
            session, mocker, tmp_path, "pgaudit.jsonl", content + "\n", "pgaudit"
        )
        assert code == EXIT_OK
        assert session.read_bursts()[0]["statements"][0]["sql"] == "select 1"


class TestAnObjectAuditRecordStoresNoWindow:
    """The bug: object logging escaped collect, so cron read a finished
    snapshot as a fault."""

    def test_a_pgaudit_text_file_exits_zero_and_keeps_the_snapshot(
        self, session, mocker, tmp_path
    ):
        logger = mocker.Mock()
        content = (
            "2024-01-01 00:00:00.000 UTC [111] LOG:  AUDIT: "
            'OBJECT,1,1,READ,SELECT,,,"select 1",<not logged>\n'
        )
        code = _collect_export(
            session,
            mocker,
            tmp_path,
            "postgresql.log",
            content,
            "pgaudit",
            logger=logger,
        )
        assert code == EXIT_OK
        assert len(session.snapshot_paths()) == 1
        assert not session.burst_paths()
        assert "cannot be used" in logger.warning.call_args[0][0]


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
            capture_log=True,
            capture_log_type="statement",
            capture_log_source="log_fdw",
            capture_log_seconds=10,
        )

        _collect(_Args(session.directory), _Config(workload), mocker.Mock())

        assert burst.call_args.kwargs["since"] == "START"
        assert burst.call_args.kwargs["until"] == "END"
        assert len(session.burst_paths()) == 1


class TestASuccessfulBurstReportsItsLoss:
    def test_warnings_are_logged_and_the_burst_is_stored(self, session, mocker):
        """The bug: a successful pgAudit read stored the window and logged no loss warning."""
        _no_snapshot_connection(mocker)
        mocker.patch.object(cli_workload, "_wait", return_value=False)
        collector = mocker.Mock()
        collector.collect.return_value = {
            "status": "ok",
            "window_start": "2026-09-14T10:00:00Z",
            "window_end": "2026-09-14T10:10:00Z",
            "statements": [],
            "sessions": {},
            "files_read": ["a"],
            "warnings": ["1 audit record(s) did not parse and were dropped"],
        }
        mocker.patch(
            "planetscale_discovery.workload.burst.BurstCollector",
            return_value=collector,
        )
        logger = mocker.Mock()
        workload = WorkloadConfig(
            capture_log=True,
            capture_log_type="pgaudit",
            capture_log_source="log_fdw",
            capture_log_seconds=10,
        )

        assert _collect(_Args(session.directory), _Config(workload), logger) == EXIT_OK
        assert len(session.burst_paths()) == 1
        logger.warning.assert_any_call(
            "1 audit record(s) did not parse and were dropped"
        )


class TestTheDefaultReadsAFile:
    """The bug: turning capture_log on wrote to the customer's database."""

    def test_the_default_source_reads_a_file(self):
        assert WorkloadConfig().capture_log_source == "file"

    def test_the_type_has_no_default(self):
        assert WorkloadConfig().capture_log_type is None

    def test_a_file_source_with_no_file_warns_at_init(self, mocker):
        workload = WorkloadConfig(capture_log=True, capture_log_type="statement")
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
            ("capture_log_source: pgaudit", "must be one of"),
            ("capture_log_source: pgaudit-json", "must be one of"),
            ("capture_log_source: stderr", "must be one of"),
            ("capture_log_source: auto", "must be one of"),
            ("capture_log_type: csv", "must be one of"),
            ("capture_log: true", "capture_log_type is required"),
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

    @pytest.mark.parametrize(
        "yaml_body, log_type, source",
        (
            (
                "capture_log: true\n"
                "    capture_log_type: statement\n"
                "    capture_log_source: file",
                "statement",
                "file",
            ),
            (
                "capture_log: true\n"
                "    capture_log_type: pgaudit\n"
                "    capture_log_source: log_fdw",
                "pgaudit",
                "log_fdw",
            ),
        ),
    )
    def test_a_legal_combination_loads(self, tmp_path, yaml_body, log_type, source):
        """The bug: a legal type and source were rejected at load."""
        from planetscale_discovery.config.config_manager import ConfigManager

        path = tmp_path / "config.yaml"
        path.write_text(
            "database:\n  host: localhost\n  workload:\n    " + yaml_body + "\n"
        )
        workload = ConfigManager(str(path)).load_config().database.workload
        assert workload.capture_log is True
        assert workload.capture_log_type == log_type
        assert workload.capture_log_source == source


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
            capture_log=True,
            capture_log_type="statement",
            capture_log_source="log_fdw",
            capture_log_seconds=600,
        )

        assert (
            _collect(_Args(session.directory), _Config(workload), mocker.Mock())
            == EXIT_OK
        )
        assert len(session.burst_paths()) == 1
