"""The ``ps-discovery workload`` subcommand: init, collect, finalize, status.

Cron is the intended driver over a few days. Every collect writes its own
timestamped file and never modifies an existing one, so there is nothing to
lock. Nothing here writes into the discovery output.
"""

import argparse
import json
import signal
import time
from pathlib import Path
from typing import Any, Dict

from planetscale_discovery import __version__
from planetscale_discovery.config.config_manager import WorkloadConfig
from planetscale_discovery.workload.bundle import bundle_dir_name, write_bundle
from planetscale_discovery.workload.codes import label
from planetscale_discovery.workload.collect import (
    WorkloadCollector,
    end_transaction,
    logged_step,
)
from planetscale_discovery.workload.merge import merge_snapshots
from planetscale_discovery.workload.probe import CapabilityProbe
from planetscale_discovery.workload.store import WorkloadStore

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_SESSION = 2
EXIT_CAP_REACHED = 3
EXIT_CAPABILITY = 5

WORKLOAD_COMMANDS = ("init", "collect", "finalize", "status")

SOURCE_LIVE = "log_fdw"
SOURCE_LIVE_ALIAS = "auto"
SOURCE_PGAUDIT_JSON = "pgaudit-json"
SOURCE_STDERR = "stderr"

# The reuse boundary: the schema comes from the existing analyzer, not a new query.
CATALOG_MODULES = ["schema"]

# Applied to this tool's own connection only; never ALTER SYSTEM or ALTER ROLE.
SESSION_SETTINGS = (
    ("default_transaction_read_only", "on"),
    ("statement_timeout", None),
    ("lock_timeout", "1000"),
    ("idle_in_transaction_session_timeout", "30000"),
    ("jit", "off"),
    # Pinned: a role default naming pg_catalog late lets a planted table win.
    ("search_path", "pg_catalog"),
)

EPILOG = """
Typical use, over two or three days:

  ps-discovery workload init     --session ./workload-session
  ps-discovery workload collect  --session ./workload-session
  ps-discovery workload finalize --session ./workload-session

  ps-discovery workload status   --session ./workload-session  (no connection)

  ps-discovery workload init --check                 (reports, changes nothing)
  ps-discovery workload init --cleanup               (drops what a kill left)

./config.yaml is found on its own. Pass --config only for a different file.

--session names a directory this tool creates and owns, holding the snapshots
and the captured schema. Give every command the same one. Name it whatever you
like. It is safe to delete once you have the bundle.

Each 'collect' takes one snapshot and exits; it does no scheduling of its own.
Two snapshots are the minimum, since a window needs two readings to difference.
'init' prints a crontab line that collects hourly.

Set 'capture_log: true' under database.workload to also read the server's query
log, which carries the transaction shapes and the literal values that
pg_stat_statements does not record. The default source is pgAudit: you export
the window and name the file in capture_log_file. On RDS and Aurora,
capture_log_source: log_fdw reads the log over this connection instead, and
each collect then watches it for capture_log_seconds. 'init --check' reports
whether this server can supply it.
"""


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    """Run options that apply to a capture.

    Deliberately not the shared discovery set. That set carries ``--engine``,
    ``--providers``, ``--analyzers`` and four MySQL SSL options, none of which
    this command reads: capture is PostgreSQL only, it runs no analyzer, and it
    writes to ``--session`` rather than an output directory. Listing them in
    ``--help`` advertises support that does not exist.

    Defaults are suppressed so an option given before the subcommand survives.
    """
    group = parser.add_argument_group("Common Options")
    group.add_argument(
        "--config",
        "-c",
        type=str,
        default=argparse.SUPPRESS,
        help="Configuration file (YAML). Defaults to ./config.yaml if present.",
    )
    group.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=argparse.SUPPRESS,
        help="Logging level",
    )
    group.add_argument(
        "--log-file",
        type=str,
        default=argparse.SUPPRESS,
        help="Log file path",
    )


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    """PostgreSQL connection options, overriding the config file."""
    group = parser.add_argument_group("Database Connection Options")
    group.add_argument("--host", help="Database server host")
    group.add_argument("--port", "-p", type=int, help="Database server port")
    group.add_argument("--database", "-d", help="Database name")
    group.add_argument("--username", "-u", help="Database username")
    group.add_argument(
        "--password", "-W", action="store_true", help="Prompt for password"
    )


def add_workload_parser(subparsers) -> None:
    """Register the workload subcommand tree."""
    workload = subparsers.add_parser(
        "workload",
        help="Capture query workload for Neki sharding design (opt-in)",
        description=(
            "Capture a Postgres query workload over a few days and write the "
            "input files a sharding planner reads. Opt-in: nothing here runs "
            "as part of a normal discovery run."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = workload.add_subparsers(dest="workload_command", required=True)

    for name, help_text in (
        ("init", "Check the server, store the schema, take the first snapshot"),
        ("collect", "Append one snapshot (safe from cron)"),
        ("finalize", "Difference the snapshots and write the bundle"),
        ("status", "Report on a session (no database connection needed)"),
    ):
        command = commands.add_parser(name, help=help_text)
        _add_run_args(command)
        command.add_argument(
            "--session",
            required=name != "init",
            help=(
                "Directory holding this capture's snapshots, any query log it "
                "read, and the schema, created by 'workload init'. Give every "
                "command the same one."
            ),
        )
        if name != "status":
            _add_connection_args(command)

        group = command.add_argument_group("Workload Options")
        if name == "init":
            group.add_argument(
                "--check",
                action="store_true",
                default=argparse.SUPPRESS,
                help=(
                    "Report what this server can supply and exit. Creates no "
                    "session and changes nothing. Needs no --session."
                ),
            )
            group.add_argument(
                "--json",
                action="store_true",
                default=argparse.SUPPRESS,
                help="With --check, emit the report as JSON, for sweeping an estate",
            )
            group.add_argument(
                "--cleanup",
                action="store_true",
                default=argparse.SUPPRESS,
                help=(
                    "Drop the objects an interrupted log_fdw collect left on "
                    "the server, then exit. Needs no --session."
                ),
            )
            group.add_argument(
                "--force",
                action="store_true",
                default=argparse.SUPPRESS,
                help="Re-initialize, moving any existing session aside",
            )
            group.add_argument(
                "--allow-replica",
                action="store_true",
                default=argparse.SUPPRESS,
                help="Collect from a standby, whose counters are local to it",
            )
        if name == "finalize":
            group.add_argument(
                "--out",
                type=str,
                default=argparse.SUPPRESS,
                help="Bundle directory (default: <session>/workload-<capture id>)",
            )
            group.add_argument(
                "--allow-partial",
                action="store_true",
                default=argparse.SUPPRESS,
                help="Report cumulative totals from a single snapshot",
            )


def handle_workload(args, config, logger) -> int:
    """Dispatch a workload subcommand. Returns the process exit code."""
    command = getattr(args, "workload_command", None)
    if command == "status":
        return _status(args, logger)

    # The workload flags carry no --engine, but a config file or a flag given
    # before the subcommand still can. Capture reads pg_stat_statements and the
    # PostgreSQL catalogs, so say so rather than run a PostgreSQL capture
    # against a request for MySQL.
    if str(getattr(config, "engine", "postgres")).lower() == "mysql":
        logger.error(
            "workload capture supports PostgreSQL only, and the engine is set "
            "to mysql. Remove 'engine: mysql' from the config file, or use a "
            "config file for the PostgreSQL database you want to capture."
        )
        return EXIT_USAGE

    if not _workload_config(config).enabled:
        logger.error(
            "workload capture is disabled. Set database.workload.enabled: "
            "true in the config file to run this command."
        )
        return EXIT_USAGE

    if command == "init":
        return _init(args, config, logger)
    if command == "collect":
        return _collect(args, config, logger)
    if command == "finalize":
        return _finalize(args, config, logger)
    logger.error(f"unknown workload command {command!r}")
    return EXIT_USAGE


def _workload_config(config) -> WorkloadConfig:
    """The workload block, or its defaults for a config that predates it."""
    # Type-checked, or a stub config answers getattr for every field.
    candidate = getattr(getattr(config, "database", None), "workload", None)
    workload = candidate if isinstance(candidate, WorkloadConfig) else WorkloadConfig()
    if workload.capture_log_source == SOURCE_LIVE_ALIAS:
        workload.capture_log_source = SOURCE_LIVE
    return workload


def _connect(config, logger):
    """Open the read-only collection connection."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    timeout = getattr(config.database, "statement_timeout", "300s")
    settings = [
        (name, timeout if value is None else value) for name, value in SESSION_SETTINGS
    ]
    params = dict(
        host=config.database.host,
        port=config.database.port,
        dbname=config.database.database,
        user=config.database.username,
        password=config.database.password,
        sslmode=config.database.ssl_mode,
        connect_timeout=getattr(config.database, "connection_timeout", 30),
        application_name=f"ps-discovery-workload/{__version__}",
        cursor_factory=RealDictCursor,
    )

    # Startup parameters cover the very first query, so they are preferred.
    options = " ".join(f"-c {name}={value}" for name, value in settings)
    try:
        return psycopg2.connect(options=options, **params)
    except psycopg2.OperationalError as e:
        # A pooler permits only a small allowlist and fails the connection.
        if "unsupported startup parameter" not in str(e):
            raise
        logger.info(
            "a connection pooler rejected the session settings as startup "
            "parameters; reconnecting and applying them with SET"
        )

    connection = psycopg2.connect(**params)
    with connection.cursor() as cursor:
        for name, value in settings:
            try:
                cursor.execute(f"SET {name} = %s", (value,))
            except Exception as e:  # pragma: no cover - pooler dependent
                logger.warning(f"could not set {name}: {e}")
        # Verified, because in transaction pooling a SET may not persist.
        cursor.execute("SHOW default_transaction_read_only")
        row = dict(cursor.fetchone())
        if str(list(row.values())[0]).lower() != "on":
            logger.warning(
                "default_transaction_read_only did not take on this connection, "
                "so the server-side write guard is not in place"
            )
    connection.commit()
    return connection


def _collect_schema(connection, config, logger) -> Dict[str, Any]:
    """Run the existing schema analyzer once, and keep its output."""
    from planetscale_discovery.database.discovery import PostgreSQLDiscovery

    discovery = PostgreSQLDiscovery(
        {
            "host": config.database.host,
            "port": config.database.port,
            "database": config.database.database,
            "user": config.database.username,
            "password": config.database.password,
            "sslmode": config.database.ssl_mode,
        },
        statement_timeout=getattr(config.database, "statement_timeout", "300s"),
    )
    discovery.connection = connection
    end_transaction(connection)
    connection.autocommit = True
    try:
        with logged_step(logger, "collecting the schema"):
            results = discovery.run_analysis(CATALOG_MODULES, reuse_connection=True)
    finally:
        if not connection.closed:
            connection.autocommit = False
    return (results.get("analysis_results") or {}).get("schema") or {}


def _new_collector(connection, config, logger) -> WorkloadCollector:
    workload = _workload_config(config)
    return WorkloadCollector(
        connection,
        logger=logger,
        schemas=workload.schemas or getattr(config.database, "schemas", None),
        row_limit=workload.statement_row_limit,
        statement_text_max_chars=workload.statement_text_max_chars,
    )


def _init(args, config, logger) -> int:
    if getattr(args, "cleanup", False):
        return _cleanup(args, config, logger)
    if getattr(args, "check", False):
        return _check(args, config, logger)
    if not getattr(args, "session", None):
        logger.error(
            "give --session to initialize a capture, or --check to report on "
            "the server without creating one."
        )
        return EXIT_USAGE

    store = WorkloadStore(args.session)
    if store.exists() and not getattr(args, "force", False):
        logger.error(
            f"a session already exists at {args.session}. Use 'workload collect' "
            "to add a snapshot, or --force to start over."
        )
        return EXIT_USAGE
    if store.exists():
        moved = Path(f"{args.session}.bak-{time.strftime('%Y%m%dT%H%M%SZ')}")
        Path(args.session).rename(moved)
        logger.warning(f"moved the existing session to {moved}")

    connection = _connect(config, logger)
    try:
        with logged_step(logger, "checking what this server supports"):
            probe = CapabilityProbe(connection, logger=logger).run()
        end_transaction(connection)

        if not probe["can_collect_relations"]:
            logger.error(
                f"{label('insufficient_statistics_privileges')}: this role "
                "cannot read the statistics views, so nothing can be collected. "
                "Grant pg_monitor to the collecting role."
            )
            return EXIT_CAPABILITY

        # Fatal. A sharding scheme is planned from the query workload, and
        # pg_stat_statements is the only place that workload exists. Without it
        # there is nothing to plan against, so this stops rather than producing
        # a bundle that cannot be used.
        if not probe["can_collect_statements"]:
            pgss = probe["pg_stat_statements"]
            # The states are not interchangeable: one means the view cannot
            # be read, the other that it is read fine and holds nothing.
            problem = (
                "is recording nothing on this server"
                if pgss["state"] == "tracking_disabled"
                else "cannot be read on this server"
            )
            logger.error(
                f"{label(pgss['state'])}: pg_stat_statements is required, and "
                f"{problem}. Without it there is no record of the query "
                "workload, and a sharding scheme cannot be planned without one."
            )
            logger.error(f"  {pgss['remediation']}")
            return EXIT_CAPABILITY

        if probe["server"]["is_replica"] and not getattr(args, "allow_replica", False):
            logger.error(
                f"{label('replica_target')}: this server is a standby. Its "
                "counters reflect only what executed here, so capture the "
                "primary instead, or pass --allow-replica."
            )
            return EXIT_CAPABILITY

        for gap in probe["gaps"]:
            detail = gap["detail"]
            # Reached only when --allow-replica was given, since the check above
            # stops otherwise. Say why the capture is continuing.
            if gap["code"] == "replica_target":
                detail += ". Continuing because --allow-replica was given"
            logger.warning(f"{label(gap['code'])}: {detail}")

        workload = _workload_config(config)
        if workload.capture_log:
            with logged_step(logger, "checking whether the query log can be read"):
                _report_log_readiness(connection, workload, logger)
            end_transaction(connection)

        store.create()
        schema = _collect_schema(connection, config, logger)
        with logged_step(logger, f"writing the schema to {store.schema_path}"):
            store.write_schema(schema)

        with logged_step(logger, "taking the baseline snapshot"):
            snapshot = _new_collector(connection, config, logger).collect()
        _report_snapshot(snapshot, store.append_snapshot(snapshot), logger)
        logger.info(f"session initialized at {args.session}")
        _print_cron_hint(args.session, workload)
        return EXIT_OK
    finally:
        connection.close()


def _report_log_readiness(connection, workload, logger) -> None:
    """Say at init whether this server can supply what capture_log asks for."""
    if workload.capture_log_source != SOURCE_LIVE:
        if not workload.capture_log_file:
            logger.warning(
                f"capture_log is on and capture_log_source is "
                f"'{workload.capture_log_source}', which reads an exported "
                "log, but capture_log_file names no file. Every collect will "
                "stop with a usage error until it does. Export the window "
                "first; see docs/workload_capture.md."
            )
            return
        logger.info(
            f"capture_log is on, reading {workload.capture_log_source} records "
            f"from {workload.capture_log_file}"
        )
        return

    from planetscale_discovery.workload.burst import LogCaptureProbe

    log = LogCaptureProbe(connection, logger=logger).run()
    if log["tiers"]["collectable_over_sql"]:
        logger.info(
            "capture_log is on and this server's log is readable over this "
            f"connection; each collect will watch it for "
            f"{workload.capture_log_seconds}s"
        )
        return
    logger.warning(
        "capture_log is on, but this server's log cannot be read over this "
        "connection, so every collect will record a snapshot only. Run "
        "'workload init --check' for what would have to change, or export the "
        "log and set capture_log_source and capture_log_file."
    )


def _collect(args, config, logger) -> int:
    store = WorkloadStore(args.session)
    # Never auto-init: that session would have no baseline and no schema.
    if not store.exists():
        logger.error(
            f"no workload session at {args.session}. Run 'ps-discovery workload "
            f"init --session {args.session}' first, which takes the baseline "
            "snapshot that deltas are measured from."
        )
        return EXIT_NO_SESSION

    workload = _workload_config(config)
    snapshots = len(store.snapshot_paths())
    if snapshots >= workload.max_snapshots:
        logger.error(
            f"this session holds {snapshots} snapshots, at the max_snapshots "
            f"limit of {workload.max_snapshots}. Run 'workload finalize'."
        )
        return EXIT_CAP_REACHED
    megabytes = store.total_bytes() / (1024 * 1024)
    if megabytes >= workload.max_session_mb:
        logger.error(
            f"this session is {megabytes:.0f} MB, at the max_session_mb limit of "
            f"{workload.max_session_mb}. Run 'workload finalize'."
        )
        return EXIT_CAP_REACHED

    from_file = workload.capture_log and workload.capture_log_source != SOURCE_LIVE
    _snapshot(store, config, logger, optional=from_file)

    # The snapshot is stored, so an unreadable log is never a non-zero exit.
    if from_file:
        return _collect_exported_log(store, workload, logger)
    if workload.capture_log:
        _capture_log_window(store, config, workload, logger)
    return EXIT_OK


def _snapshot(store, config, logger, optional: bool) -> None:
    """Append one snapshot. When optional, a dead connection is a warning."""
    try:
        connection = _connect(config, logger)
    except Exception as exc:
        if not optional:
            raise
        logger.warning(
            f"no database connection, so this collect records the query log "
            f"only and takes no snapshot: {exc}"
        )
        return
    try:
        with logged_step(logger, "taking a snapshot"):
            snapshot = _new_collector(connection, config, logger).collect()
        _report_snapshot(snapshot, store.append_snapshot(snapshot), logger)
    finally:
        connection.close()


def _report_snapshot(snapshot: Dict[str, Any], path, logger) -> None:
    for warning in snapshot.get("warnings") or []:
        logger.warning(f"{label(warning['code'])}: {warning['detail']}")
    logger.info(
        f"snapshot {path.name}: {snapshot['status']}, "
        f"{len(snapshot['statements'])} statements, "
        f"{len(snapshot['tables'])} tables, "
        f"{len(snapshot['indexes'])} indexes, {snapshot.get('duration_ms')}ms"
    )


def _capture_log_window(store, config, workload, logger) -> None:
    """Watch the server's log for capture_log_seconds, then read that window."""
    from planetscale_discovery.workload.burst import STATUS_FAILED, BurstCollector

    seconds = workload.capture_log_seconds
    since = _utc_now()
    logger.info(f"watching the query log for {seconds}s")
    if _wait(seconds):
        logger.warning(
            "interrupted, so the log window is shorter than "
            f"{seconds}s. Storing what it holds"
        )
    until = _utc_now()

    try:
        connection = _connect(config, logger)
    except Exception as exc:
        logger.warning(f"could not reconnect to read the query log: {exc}")
        return
    try:
        burst = BurstCollector(
            connection,
            config=vars(workload),
            logger=logger,
            already_read=store.log_files_read(),
            since=since,
            until=until,
        ).collect()
    except Exception as exc:
        logger.warning(f"the query log could not be read: {exc}")
        return
    finally:
        connection.close()

    if burst["status"] == STATUS_FAILED:
        logger.warning(
            "the query log could not be read, so this collect recorded a "
            "snapshot only. Run 'workload init --check' for what this server "
            "would need; log_fdw and csvlog are the usual gaps"
        )
        for warning in burst.get("warnings") or []:
            logger.warning(f"  {warning}")
        return
    _report_burst(burst, store.append_burst(burst), logger)


def _collect_exported_log(store, workload, logger) -> int:
    """Read a log the operator exported. Opens no database connection."""
    from planetscale_discovery.workload.burst import (
        ObjectLoggingError,
        collect_pgaudit_file,
        collect_stderr_file,
    )

    path = workload.capture_log_file
    if not path:
        logger.error(
            f"capture_log_source is '{workload.capture_log_source}', which "
            "reads an exported log, but capture_log_file names no file."
        )
        return EXIT_USAGE

    try:
        if workload.capture_log_source == SOURCE_STDERR:
            burst = collect_stderr_file(
                path, already_read=store.log_files_read(), logger=logger
            )
        else:
            burst = collect_pgaudit_file(
                path,
                json_export=workload.capture_log_source == SOURCE_PGAUDIT_JSON,
                already_read=store.log_files_read(),
                logger=logger,
            )
    except ObjectLoggingError as exc:
        logger.warning(f"{path} cannot be used, so no log window was recorded: {exc}")
        return EXIT_OK
    except (OSError, ValueError) as exc:
        logger.warning(
            f"could not read {path}, so this collect recorded a snapshot "
            f"only: {exc}"
        )
        return EXIT_OK

    for warning in burst.get("warnings") or []:
        logger.warning(warning)
    # Storing a burst with no file read reports a used window covering nothing.
    if not burst.get("files_read"):
        logger.warning(
            f"no new log records were read: {path} was already read in this "
            "session, and re-reading it would double every count."
        )
        return EXIT_OK
    _report_burst(burst, store.append_burst(burst), logger)
    return EXIT_OK


def _report_burst(burst: Dict[str, Any], path, logger) -> None:
    summary = burst.get("sessions") or {}
    logger.info(
        f"query log {path.name}: {summary.get('statements', 0)} statements "
        f"across {summary.get('sessions', 0)} sessions, "
        f"{summary.get('closed_transactions', 0)} complete transactions"
    )
    if summary.get("open_transactions"):
        logger.warning(
            f"{summary['open_transactions']} transaction(s) were still open at "
            "the edge of the window and are marked incomplete. A shape missing "
            "its write reads as read-only, so they are not counted as complete."
        )


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _wait(seconds: int) -> bool:
    """Sleep, returning True when a signal cut the wait short."""
    stopped = []

    def stop(signum, frame):
        stopped.append(signum)

    previous = [
        (number, signal.signal(number, stop))
        for number in (signal.SIGINT, signal.SIGTERM)
    ]
    try:
        deadline = time.monotonic() + seconds
        while not stopped:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))
    finally:
        for number, handler in previous:
            signal.signal(number, handler)
    return bool(stopped)


def _finalize(args, config, logger) -> int:
    store = WorkloadStore(args.session)
    if not store.exists():
        logger.error(f"no workload session at {args.session}")
        return EXIT_NO_SESSION

    snapshots = store.read_snapshots()
    if not snapshots:
        logger.error("this session has no snapshots yet")
        return EXIT_NO_SESSION

    merged = merge_snapshots(
        snapshots, allow_partial=getattr(args, "allow_partial", False)
    )
    if not merged["usable"]:
        logger.error(merged["reason"])
        return EXIT_USAGE

    workload = _workload_config(config)
    # The directory is named for the capture, so two bundles from the same
    # session, or from different databases, cannot be confused once they are
    # unpacked side by side. --out still wins when the operator names one.
    out_dir = Path(
        getattr(args, "out", None) or store.directory / bundle_dir_name(merged)
    )
    manifest = write_bundle(
        out_dir,
        merged,
        store.read_schema(),
        collector_version=__version__,
        target_schemas=workload.schemas or getattr(config.database, "schemas", None),
        bursts=store.read_bursts(),
    )
    _print_summary(manifest, out_dir, capture_log=workload.capture_log)
    return EXIT_OK


def _print_summary(
    manifest: Dict[str, Any], out_dir: Path, capture_log: bool = False
) -> None:
    print("")
    print(f"Bundle written to {out_dir}")
    if manifest["covered_seconds"]:
        minutes = manifest["covered_seconds"] / 60
        print(
            f"  measured over:  {minutes:,.0f} minutes of traffic, in "
            f"{manifest['coverage']['intervals_used']} interval(s)"
        )
    else:
        print("  measured over:  no window; these are lifetime totals")
    print(
        f"  queries:        {manifest['statements']['in_workload_sql']} kept "
        f"for planning, of {manifest['statements']['collected']} seen"
    )
    print(f"  times run:      {(manifest['totals'] or {}).get('calls', 0):,.0f}")
    print(
        f"  schema:         {manifest['schema']['tables']} tables, "
        f"{manifest['schema']['views']} views"
    )
    print(f"  row counts:     {manifest['cardinality']['table_count']} tables")
    if manifest["schema"]["parse_failures"]:
        print(
            f"  ! {manifest['schema']['parse_failures']} schema statement(s) "
            "could not be re-read and were left out"
        )
    burst = manifest.get("burst") or {}
    if burst.get("used") or burst.get("outside_window"):
        print(
            f"  query log:      {burst['used']} window(s) used, "
            f"{burst['outside_window']} outside this capture"
        )
    # Printed even when no window was read: the logging is on either way.
    if capture_log:
        print("")
        print("  The capture is over, so turn the logging back off:")
        print("")
        print("    ALTER ROLE app RESET pgaudit.log;")
        print("    ALTER ROLE app RESET pgaudit.log_parameter;")
        print("")
        print("  Without pgAudit, reset log_min_duration_statement instead,")
        print("  and run 'workload init --cleanup' to drop the objects a")
        print("  log_fdw capture creates on the server.")
    if manifest["caveats"]:
        print("")
        print("  Worth knowing about these numbers:")
        for caveat in manifest["caveats"]:
            print(f"  ! {caveat}")
    print("")
    print("Next, compress the bundle and send the archive to your PlanetScale")
    print("migration engineer. They use it to plan the sharding scheme for a")
    print("migration to Neki.")
    print("")
    print(f"  tar -czf {out_dir.name}.tar.gz -C {out_dir.parent} {out_dir.name}")
    print("")
    print(f"See {out_dir / 'README.md'} for what the bundle holds.")


def _cleanup(args, config, logger) -> int:
    """Drop the log_fdw objects an interrupted collect left behind."""
    from planetscale_discovery.workload.burst import drop_leftovers

    connection = _connect(config, logger)
    try:
        result = drop_leftovers(connection, logger=logger)
    finally:
        connection.close()

    print("")
    if not result["found"]:
        print("Nothing to clean up. This server holds no objects from a capture.")
        print("")
        _print_customer_cleanup()
        return EXIT_OK

    for item in result["dropped"]:
        print(f"dropped {item['kind']} {item['name']}")
    for item in result["failed"]:
        print(f"could not drop {item['kind']} {item['name']}: {item['why']}")
    print("")
    print("The log_fdw extension is left installed. Drop it with")
    print("DROP EXTENSION log_fdw if you do not want it.")
    print("")
    _print_customer_cleanup()
    return EXIT_USAGE if result["failed"] else EXIT_OK


def _print_customer_cleanup() -> None:
    """Print the logging reset, which this tool never runs itself."""
    print("Turn the statement logging back off in your own console:")
    print("")
    print("  ALTER ROLE app RESET pgaudit.log;")
    print("  ALTER ROLE app RESET pgaudit.log_parameter;")
    print("")
    print("Without pgAudit, reset log_min_duration_statement instead. Then")
    print("confirm the log volume stops growing. See")
    print("docs/workload_capture.md#turn-the-logging-back-off.")
    print("")


def _check(args, config, logger) -> int:
    """Report what this server can supply. Creates nothing, changes nothing."""
    from planetscale_discovery.workload.burst import LogCaptureProbe, find_leftovers

    connection = _connect(config, logger)
    try:
        capability = CapabilityProbe(connection, logger=logger).run()
        log = LogCaptureProbe(connection, logger=logger).run()
        leftovers = find_leftovers(connection, logger=logger)
    finally:
        connection.close()

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "capability": capability,
                    "log_capture": log,
                    "leftovers": leftovers,
                },
                indent=2,
                default=str,
            )
        )
        return EXIT_OK

    server = capability["server"]
    print("")
    print(
        f"server:    PostgreSQL {server['version_num']} on {log['provider']}, "
        f"database {server['database']}, role {server['role']}"
    )
    pgss = capability["pg_stat_statements"]
    print(f"aggregate: pg_stat_statements is {pgss['state']}")
    if pgss["state"] != "ok":
        print(f"           -> {pgss['remediation']}")

    print("")
    print("Query log capture: what this server can supply today")
    for name, label_text in (
        ("transaction_shapes", "which statements share a transaction"),
        ("unbiased_transactions", "whole transactions, not sampled statements"),
        ("access_skew", "the values queries carried"),
        ("collectable_over_sql", "the log readable over this connection"),
        ("coverage_against_aggregate", "log coverage measurable against totals"),
    ):
        mark = "yes" if log["tiers"][name] else "no "
        print(f"  {mark}  {label_text}")

    missing = [
        (name, req) for name, req in log["requirements"].items() if not req["available"]
    ]
    if missing:
        print("")
        print("What would have to change")
        for name, req in missing:
            print(f"  {name}: {req['detail']}")
            print(f"    -> {req['remediation']}")

    if log["notes"]:
        print("")
        print("Worth knowing")
        for note in log["notes"]:
            print(f"  ! {note['code']}: {note['detail']}")

    if log["retention"]["known"]:
        print("")
        print(f"Retention: {log['retention']['detail']}")

    pgaudit = log["pgaudit"]
    print("")
    print(f"pgAudit:   {pgaudit['state']} (the default source)")
    for name, value in pgaudit["settings"].items():
        if value is not None:
            print(f"           {name} = {value}")
    if pgaudit["recommendation"]:
        print(f"           -> {pgaudit['recommendation']}")

    if leftovers:
        print("")
        print("Leftovers: an interrupted capture left these on the server")
        for item in leftovers:
            print(f"           {item['kind']} {item['name']}")
        print("           -> run 'workload init --cleanup' to drop them")

    print("")
    print("This command changed nothing. Every setting above is yours to")
    print("change, in your own console.")
    print("")
    return EXIT_OK


def _status(args, logger) -> int:
    store = WorkloadStore(args.session)
    status = store.status()
    if not status["initialized"]:
        logger.error(f"no workload session at {args.session}")
        return EXIT_NO_SESSION
    print(json.dumps(status, indent=2, default=str))
    return EXIT_OK


def _print_cron_hint(session: str, workload: WorkloadConfig) -> None:
    """Print a ready-to-paste crontab line."""
    # cron starts in the home directory, so a relative --session and the
    # auto-discovery of ./config.yaml both need the cd to resolve.
    workdir = Path.cwd()
    command = (
        "./ps-discovery" if (workdir / "ps-discovery").exists() else "ps-discovery"
    )
    watching = workload.capture_log and workload.capture_log_source == SOURCE_LIVE
    # Minute 17, not the top of the hour when every other agent wakes up.
    print("")
    if watching:
        print(
            f"Each 'collect' takes one snapshot, then watches the query log for "
            f"{workload.capture_log_seconds}s. To collect hourly, add"
        )
    elif workload.capture_log:
        print(
            f"Each 'collect' takes one snapshot and reads "
            f"{workload.capture_log_file}. Point that at the window you exported"
        )
        print("before each run. To collect hourly, add")
    else:
        print("Each 'collect' takes one snapshot and exits. To collect hourly, add")
    print("this to the crontab of a user that can write the session directory:")
    print("")
    print(
        f"  17 * * * * cd {workdir} && "
        f"{command} workload collect --session {session}"
    )
    print("")
    print("Two snapshots are the minimum, since a window needs two readings to")
    print("difference. Hourly for two or three days is the intended shape, and")
    print("it should span a peak: a workload measured only at 03:00 is not the")
    print("one you have to shard.")
    if watching:
        print("")
        print(
            f"Keep the interval longer than capture_log_seconds "
            f"({workload.capture_log_seconds}s), or the next collect starts"
        )
        print("before this one returns.")
    print("")
    print(f"Then: {command} workload finalize --session {session}")
