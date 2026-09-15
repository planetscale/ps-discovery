import re
from typing import Any, Dict, Iterator, List, Optional

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer
from planetscale_discovery.common.utils import generate_timestamp
from planetscale_discovery.workload.burst.sql import SafeCursor
from planetscale_discovery.workload.logs.csvlog import to_statement
from planetscale_discovery.workload.logs.record import COLUMNS, KNOWN_WIDTHS
from planetscale_discovery.workload.logs.timestamps import instant_utc
from planetscale_discovery.workload.logs.transactions import (
    group_by_session,
    transactions,
)

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_FAILED = "failed"

SOURCE_LIVE = "log_fdw"
SOURCE_FILE = "file"

WORK_SCHEMA = "ps_discovery_burst"
LOG_SERVER = "ps_discovery_log_server"

CSV_LOG_FILE = re.compile(r"\.csv$", re.I)

FETCH_SIZE = 2000


class BurstCollector(DatabaseAnalyzer):
    def __init__(
        self,
        connection,
        config=None,
        logger=None,
        already_read=(),
        since=None,
        until=None,
    ):
        super().__init__(connection, config, logger)
        self.already_read = set(already_read)
        self.since = since
        self.until = until
        self._sql = SafeCursor(connection, logger=self.logger)
        self._fdw_schema: Optional[str] = None
        self._setup_done = False
        self._created_extension = False
        self._since_instant = None
        self._until_instant = None
        self._skipped_before_watermark = 0
        self._skipped_after_watermark = 0
        self._log_tz: Optional[str] = None

    @property
    def errors_seen(self) -> List[str]:
        return self._sql.errors

    def analyze(self) -> Dict[str, Any]:
        return self.collect()

    def collect(self) -> Dict[str, Any]:
        started = generate_timestamp()
        result: Dict[str, Any] = {
            "kind": "burst",
            "schema_version": 1,
            "source": SOURCE_LIVE,
            "captured_at_client": started,
            "status": STATUS_FAILED,
            "files_read": [],
            "files_skipped": [],
            "statements": [],
            "warnings": [],
        }

        try:
            try:
                prepared = self._prepare()
            except Exception as exc:
                self.add_error("could not prepare the burst schema", exc)
                prepared = False
            if not prepared:
                result["warnings"] = list(self.errors_seen)
                result["window_start"], result["window_end"] = _event_window(
                    [], started
                )
                return result

            try:
                files = self._log_files()
            except Exception as exc:
                # Aborted by the failure, so _cleanup's first DROP would fail too.
                self._sql.rollback()
                self.add_error("could not list the server's log files", exc)
                result["warnings"] = list(self.errors_seen) + [str(exc)]
                result["window_start"], result["window_end"] = _event_window(
                    [], started
                )
                return result

            statements: List[Dict[str, Any]] = []
            log_timezone = self._log_timezone()
            self._log_tz = log_timezone
            self._since_instant = instant_utc(self.since, log_timezone)
            self._until_instant = instant_utc(self.until, log_timezone)
            self._skipped_before_watermark = 0
            self._skipped_after_watermark = 0
            for name, size in files:
                if not CSV_LOG_FILE.search(name):
                    result["files_skipped"].append(
                        {"file": name, "why": "not a csv log"}
                    )
                    continue
                # Only an unwatermarked read needs this; a watermark dedupes rows.
                if name in self.already_read and self._since_instant is None:
                    result["files_skipped"].append(
                        {"file": name, "why": "already read by an earlier burst"}
                    )
                    continue
                try:
                    read = list(self._read_file(name))
                except Exception as exc:
                    self.add_warning(f"could not read {name}: {exc}")
                    result["files_skipped"].append({"file": name, "why": str(exc)})
                    self._sql.rollback()
                    continue
                statements.extend(read)
                result["files_read"].append(
                    {"file": name, "bytes": size, "kept": len(read)}
                )
                self._sql.commit()

            result["statements"] = statements
            result["skipped_before_watermark"] = self._skipped_before_watermark
            result["skipped_after_watermark"] = self._skipped_after_watermark
            result["captured_at_server"] = self._server_now() or started
            result["log_timezone"] = log_timezone
            result["sessions"] = _session_summary(statements)
            result["status"] = STATUS_OK if result["files_read"] else STATUS_DEGRADED
            result["warnings"] = list(self.errors_seen) + _skip_warnings(result)
            result["window_start"], result["window_end"] = _event_window(
                statements, result["captured_at_server"], log_timezone
            )
        finally:
            self._cleanup()
        return result

    def _execute(self, sql: str, params=None) -> bool:
        if self._sql.execute(sql, params):
            return True
        error = self._sql.errors[-1]
        prefix = f"{sql.split()[0].lower()}: "
        prefix_len = len(prefix)
        exc_text = error[prefix_len:] if error.startswith(prefix) else error
        self.add_warning(f"{sql[:60]}: {exc_text}")
        return False

    def _prepare(self) -> bool:
        for sql in (
            "SET default_transaction_read_only = off",
            f"SET search_path = {WORK_SCHEMA}",
        ):
            if not self._execute(sql):
                return False
        self._sql.commit()
        self._created_extension = self._log_fdw_schema() is None
        # IF NOT EXISTS adopts a killed run's leftovers; drop them instead.
        self._execute(f"DROP SCHEMA IF EXISTS {WORK_SCHEMA} CASCADE")
        self._execute(f"DROP SERVER IF EXISTS {LOG_SERVER} CASCADE")
        self._sql.commit()
        for sql in (
            "CREATE EXTENSION IF NOT EXISTS log_fdw WITH SCHEMA public",
            f"CREATE SCHEMA IF NOT EXISTS {WORK_SCHEMA}",
        ):
            if not self._execute(sql):
                return False

        if not self._server_exists():
            if not self._execute(
                f"CREATE SERVER {LOG_SERVER} FOREIGN DATA WRAPPER log_fdw"
            ):
                return False

        self._fdw_schema = self._log_fdw_schema()
        if not self._fdw_schema:
            self.add_warning(
                "log_fdw is present but its schema is not in pg_extension, "
                "so its functions cannot be called"
            )
            self._sql.rollback()
            return False

        self._sql.commit()
        self._setup_done = True
        return True

    def _log_fdw_schema(self) -> Optional[str]:
        row = self._sql.one(
            "SELECT n.nspname AS schema FROM pg_catalog.pg_extension e"
            " JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace"
            " WHERE e.extname = 'log_fdw'"
        )
        return str(row["schema"]) if row and row.get("schema") else None

    def _server_exists(self) -> bool:
        row = self._sql.one(
            "SELECT 1 AS found FROM pg_catalog.pg_foreign_server WHERE srvname = %s",
            (LOG_SERVER,),
        )
        return bool(row)

    def _log_files(self) -> List[tuple]:
        rows = self._sql.all(
            f'SELECT * FROM "{self._fdw_schema}".list_postgres_log_files() ORDER BY 1'
        )
        files = []
        for row in rows:
            values = list(row.values())
            name = str(values[0])
            size = values[1] if len(values) > 1 else None
            files.append((name, size))
        return files

    def _read_file(self, name: str) -> Iterator[Dict[str, Any]]:
        bare = _table_name(name)
        table = f"{WORK_SCHEMA}.{bare}"
        self._execute(f"DROP FOREIGN TABLE IF EXISTS {table}")
        if not self._execute(
            f'SELECT "{self._fdw_schema}".create_foreign_table_for_log_file(%s, %s, %s)',
            (bare, LOG_SERVER, name),
        ):
            raise RuntimeError(
                f"could not map {name} to a foreign table; the role needs "
                "rds_superuser, and a GRANT does not reach an already-open session"
            )
        try:
            width = self._column_count(table)
            if width not in KNOWN_WIDTHS:
                raise ValueError(
                    f"{name} produced {width} columns, which is not a csvlog "
                    "shape (23, 24 or 26)"
                )
            for row in self._stream(table, width):
                statement = to_statement(row)
                if statement is None:
                    continue
                if self._before_watermark(statement.get("log_time")):
                    self._skipped_before_watermark += 1
                    continue
                if self._after_watermark(statement.get("log_time")):
                    self._skipped_after_watermark += 1
                    continue
                statement["log_file"] = name
                yield statement
        finally:
            self._execute(f"DROP FOREIGN TABLE IF EXISTS {table}")

    def _before_watermark(self, log_time: Any) -> bool:
        instant = instant_utc(log_time, self._log_tz)
        if self._since_instant is None or instant is None:
            return False
        return instant <= self._since_instant

    def _after_watermark(self, log_time: Any) -> bool:
        instant = instant_utc(log_time, self._log_tz)
        if self._until_instant is None or instant is None:
            return False
        return instant > self._until_instant

    def _column_count(self, table: str) -> int:
        schema, _, name = table.partition(".")
        row = self._sql.one(
            "SELECT count(*) AS columns FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s",
            (schema, name),
        )
        return int((row or {}).get("columns") or 0)

    def _stream(self, table: str, width: int) -> Iterator[Dict[str, Any]]:
        columns = ", ".join(COLUMNS[:width])
        name = f"ps_burst_{table.rsplit('.', 1)[-1]}"
        with self.connection.cursor(name=name) as cursor:
            cursor.itersize = FETCH_SIZE
            cursor.execute(
                f"SELECT {columns} FROM {table} ORDER BY session_id, session_line_num"
            )
            for row in cursor:
                yield dict(row) if not isinstance(row, dict) else row

    def _cleanup(self) -> None:
        if not self._setup_done:
            return
        self._execute(f"DROP SCHEMA IF EXISTS {WORK_SCHEMA} CASCADE")
        self._execute(f"DROP SERVER IF EXISTS {LOG_SERVER}")
        if self._created_extension:
            self._execute("DROP EXTENSION IF EXISTS log_fdw")
        self._sql.commit()

    def _server_now(self) -> Optional[str]:
        try:
            row = self._sql.one("SELECT clock_timestamp() AS now")
        except Exception:
            return None
        value = (row or {}).get("now")
        return str(value) if value is not None else None

    def _log_timezone(self) -> Optional[str]:
        try:
            row = self._sql.one("SHOW log_timezone")
        except Exception:
            return None
        if not row:
            return None
        value = list(row.values())[0]
        return str(value) if value else None


def _skip_warnings(result: Dict[str, Any]) -> List[str]:
    skipped = result.get("files_skipped") or []
    if result.get("files_read") or not skipped:
        return []
    reasons = sorted({str(entry.get("why") or "") for entry in skipped})
    return [
        f"no log file was read: {len(skipped)} file(s) were skipped "
        f"({'; '.join(reasons)})"
    ]


def _event_window(
    statements: List[Dict[str, Any]],
    fallback: Optional[str],
    log_timezone: Optional[str] = None,
) -> "tuple[str, str]":
    instants = [
        instant
        for instant in (
            instant_utc(statement.get("log_time"), log_timezone)
            for statement in statements
        )
        if instant is not None
    ]
    if not instants:
        stamp = fallback or generate_timestamp()
        return stamp, stamp
    start = min(instants).isoformat().replace("+00:00", "Z")
    end = max(instants).isoformat().replace("+00:00", "Z")
    return start, end


def _table_name(log_file: str) -> str:
    return "log_" + re.sub(r"[^A-Za-z0-9]+", "_", log_file).strip("_").lower()[:48]


def _session_summary(statements: List[Dict[str, Any]]) -> Dict[str, Any]:
    sessions = group_by_session(statements)
    shapes = [
        transaction
        for records in sessions.values()
        for transaction in transactions(records)
    ]
    explicit = [t for t in shapes if t["explicit"]]
    return {
        "sessions": len(sessions),
        "statements": len(statements),
        "with_values": sum(1 for s in statements if s.get("parameters")),
        "transactions": len(shapes),
        "explicit_transactions": len(explicit),
        "closed_transactions": sum(1 for t in explicit if t["closed"]),
        "open_transactions": sum(1 for t in explicit if not t["closed"]),
    }
