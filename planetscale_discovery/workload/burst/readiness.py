import logging
from typing import Any, Dict, List, Optional

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer
from planetscale_discovery.workload.burst.sql import SafeCursor

LOG_SETTINGS = (
    "log_destination",
    "logging_collector",
    "log_line_prefix",
    "log_min_duration_statement",
    "log_statement",
    "log_duration",
    "log_transaction_sample_rate",
    "log_min_duration_sample",
    "log_statement_sample_rate",
    "log_parameter_max_length",
    "compute_query_id",
    "log_rotation_age",
    "log_rotation_size",
)

TRANSACTION_SAMPLING_MIN_VERSION = 120000

QUERY_ID_MIN_VERSION = 140000

WHERE_TO_SET = {
    "rds": "in a custom DB parameter group on the instance or cluster",
    "cloudsql": "as a database flag on the Cloud SQL instance",
    "self_managed": "in postgresql.conf, or with ALTER SYSTEM followed by a reload",
    "unknown": "wherever this server's configuration is managed",
}

RESTART_REQUIRED = ("logging_collector",)

RDS_RICH_LOG_LINE_PREFIX = "%m:%r:%u@%d:[%p]:%l:%e:%s:%v:%x:%c:%q%a:"

PGAUDIT_SETTINGS = (
    "pgaudit.log",
    "pgaudit.log_parameter",
    "pgaudit.log_relation",
)

PGAUDIT_RECOMMENDATION_INSTALLED = (
    "pgAudit is installed, so a capture costs two ALTER ROLEs and a reset -- "
    "no restart, no downtime, nothing permanent: "
    "ALTER ROLE app SET pgaudit.log = 'read,write,misc'; "
    "ALTER ROLE app SET pgaudit.log_parameter = 'on'; capture the window; then "
    "ALTER ROLE app RESET pgaudit.log and ALTER ROLE app RESET "
    "pgaudit.log_parameter. Role settings bind at connection time, so the "
    "capture ramps in as the connection pool recycles. Keep "
    "pgaudit.log_relation off (object logging corrupts transaction-shape "
    "assembly) and log_statement at 'none' during the window, or every "
    "statement is logged twice. Then export the log from the provider's "
    "console or logging API and name the file in capture_log_file."
)
PGAUDIT_RECOMMENDATION_AVAILABLE = (
    "pgAudit is available on this server but not installed. The one-time "
    "enablement loads the library and runs CREATE EXTENSION pgaudit -- a "
    "parameter group on RDS and Aurora, the cloudsql.enable_pgaudit flag on "
    "Cloud SQL, alloydb.enable_pgaudit on AlloyDB. This is the only step that "
    "can need a restart; after it every pgAudit setting is role-scoped and "
    "changeable without one, and a capture is two ALTER ROLEs."
)
PGAUDIT_RECOMMENDATION_ABSENT = {
    "rds": (
        "pgAudit is not available on this server. Set capture_log_source: "
        "log_fdw to read the log over this connection instead. It creates a "
        "work schema and a foreign server for the length of each collect, and "
        "it reads the whole log rather than one role's traffic; "
        "docs/providers/aws.md compares the two."
    ),
    "default": (
        "pgAudit is not available on this server. Set "
        "log_min_duration_statement = 0 for the window, export the log, and "
        "read it with capture_log_source: stderr."
    ),
}


class LogCaptureProbe(DatabaseAnalyzer):
    def __init__(
        self,
        connection,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        super().__init__(connection, config, logger)
        self._sql = SafeCursor(connection, logger=logger)

    def analyze(self) -> Dict[str, Any]:
        return self.run()

    def run(self) -> Dict[str, Any]:
        settings = {name: self._setting(name) for name in LOG_SETTINGS}
        version = _int(self._setting("server_version_num")) or 0
        provider = self._provider()
        destinations = _destinations(settings.get("log_destination"))
        prefix = _prefix_escapes(settings.get("log_line_prefix"))
        log_fdw = self._log_fdw()

        requirements = self._requirements(
            settings, version, provider, destinations, prefix, log_fdw
        )
        pgaudit = self._pgaudit(provider)
        notes = self._notes(settings, version, provider, destinations, prefix)
        notes.extend(self._pgaudit_notes(pgaudit))
        return {
            "provider": provider,
            "settings": settings,
            "destinations": destinations,
            "prefix": prefix,
            "log_fdw": log_fdw,
            "pgaudit": pgaudit,
            "retention": self._retention(provider),
            "requirements": requirements,
            "tiers": _tiers(requirements),
            "notes": notes,
        }

    def _requirements(
        self,
        settings: Dict[str, Optional[str]],
        version: int,
        provider: str,
        destinations: Dict[str, bool],
        prefix: Dict[str, bool],
        log_fdw: str,
    ) -> Dict[str, Dict[str, Any]]:
        structured = destinations["csvlog"] or destinations["jsonlog"]

        return {
            "statement_log": _requirement(
                available=_logging_statements(settings),
                detail=self._statement_log_detail(settings),
                fix=(
                    "Set log_min_duration_statement = 0 for every statement, or "
                    "log_transaction_sample_rate above 0 for whole sampled "
                    "transactions. Both take effect on reload, with no restart."
                ),
                where=provider,
            ),
            "session_identity": _requirement(
                available=structured or prefix["session_id"] or prefix["virtual_txid"],
                setting=(
                    "logging_collector"
                    if provider == "self_managed"
                    and settings.get("logging_collector") == "off"
                    else "log_destination"
                ),
                detail=(
                    "csvlog and jsonlog carry session_id, virtual_transaction_id "
                    "and transaction_id as columns, independent of "
                    "log_line_prefix"
                    if structured
                    else f"log_line_prefix is {settings.get('log_line_prefix')!r}"
                ),
                fix=(
                    "Add csvlog to log_destination, which carries the session and "
                    "transaction columns whatever the prefix says. Failing that, "
                    "put %c or %v in log_line_prefix -- a process id is not a "
                    "session id once a connection pooler is in front."
                    + (
                        " RDS accepts only two values for that prefix; the richer "
                        f"one is {RDS_RICH_LOG_LINE_PREFIX} and it carries both."
                        if provider == "rds"
                        else ""
                    )
                ),
                where=provider,
            ),
            "whole_transactions": _requirement(
                available=version >= TRANSACTION_SAMPLING_MIN_VERSION,
                detail=(
                    "log_transaction_sample_rate = "
                    f"{settings.get('log_transaction_sample_rate')}"
                    if version >= TRANSACTION_SAMPLING_MIN_VERSION
                    else "log_transaction_sample_rate needs PostgreSQL 12 or later"
                ),
                fix=(
                    "Raise log_transaction_sample_rate for the burst window, then "
                    "return it to 0. It samples transactions rather than "
                    "statements, so a sampled transaction arrives complete. A "
                    "duration threshold cannot substitute: it keeps the slow "
                    "statement and drops the fast ones from the same transaction."
                ),
                where=provider,
            ),
            "values": _requirement(
                available=settings.get("log_parameter_max_length") not in (None, "0"),
                detail=(
                    "log_parameter_max_length = "
                    f"{settings.get('log_parameter_max_length')}"
                    if settings.get("log_parameter_max_length") is not None
                    else ("log_parameter_max_length could not be read on this server")
                ),
                fix=(
                    "Set log_parameter_max_length = -1 to log bind values in "
                    "full. Note this governs bind parameters only: a client that "
                    "builds its SQL by interpolating literals client-side carries "
                    "them in the statement text regardless of this setting."
                ),
                where=provider,
            ),
            "readable_over_sql": _requirement(
                available=log_fdw == "installed",
                detail=f"log_fdw is {log_fdw}",
                fix=(
                    "Only capture_log_source: log_fdw needs this. On RDS and "
                    "Aurora, CREATE EXTENSION log_fdw makes the server's own "
                    "log readable through a foreign table over this same "
                    "connection, at the cost of a work schema and a foreign "
                    "server for the length of each collect. The default "
                    "source, pgAudit, reads a log you export and writes "
                    "nothing to the database."
                ),
                where=provider,
            ),
            "aggregate_join": _requirement(
                available=(
                    version >= QUERY_ID_MIN_VERSION
                    and settings.get("compute_query_id") in ("on", "auto", "regress")
                    and (structured or prefix["query_id"])
                ),
                detail=_aggregate_join_detail(
                    version, settings.get("compute_query_id"), structured, prefix
                ),
                fix=_aggregate_join_fix(
                    version, settings.get("compute_query_id"), structured, prefix
                ),
                where=provider,
            ),
        }

    def _statement_log_detail(self, settings: Dict[str, Optional[str]]) -> str:
        parts = []
        for name in (
            "log_min_duration_statement",
            "log_statement",
            "log_transaction_sample_rate",
        ):
            parts.append(f"{name} = {settings.get(name)}")
        return ", ".join(parts)

    def _notes(
        self,
        settings: Dict[str, Optional[str]],
        version: int,
        provider: str,
        destinations: Dict[str, bool],
        prefix: Dict[str, bool],
    ) -> List[Dict[str, str]]:
        notes: List[Dict[str, str]] = []

        log_statement_on = (settings.get("log_statement") or "none") != "none"
        logs_durations = _logs_every_statement(
            settings.get("log_min_duration_statement")
        )

        if log_statement_on and not logs_durations:
            notes.append(
                {
                    "code": "log_statement_loses_query_id_on_simple_protocol",
                    "detail": (
                        f"log_statement is {settings.get('log_statement')!r}. "
                        "Statements issued over the simple query protocol are "
                        "logged with query_id 0 and cannot be joined to "
                        "pg_stat_statements; extended-protocol statements are "
                        "unaffected. Prefer log_min_duration_statement = 0, "
                        "which carries a real identifier for both"
                    ),
                }
            )

        if log_statement_on and logs_durations:
            notes.append(
                {
                    "code": "log_statement_splits_text_from_query_id",
                    "detail": (
                        "log_statement and log_min_duration_statement are both "
                        "on. PostgreSQL then drops the statement text from the "
                        "duration line, so a simple-protocol statement lands its "
                        "text on a query_id = 0 row and its identifier on "
                        "another row carrying no text. Set log_statement = none"
                    ),
                }
            )

        if provider == "rds" and destinations["csvlog"] and destinations["stderr"]:
            notes.append(
                {
                    "code": "rds_writes_both_destinations",
                    "detail": (
                        "RDS re-adds stderr alongside csvlog, so every event is "
                        "written twice and the log directory grows at roughly "
                        "double the rate a capture would suggest"
                    ),
                }
            )

        if not (destinations["csvlog"] or destinations["jsonlog"]) and not (
            prefix["session_id"] or prefix["virtual_txid"]
        ):
            notes.append(
                {
                    "code": "no_session_identity_in_log",
                    "detail": (
                        "stderr logging with a prefix carrying neither %c nor %v "
                        "produces statements that cannot be grouped into "
                        "transactions, which is the single most expensive thing "
                        "for a capture to be missing"
                    ),
                }
            )

        if settings.get("log_parameter_max_length") == "0":
            notes.append(
                {
                    "code": "bind_values_suppressed",
                    "detail": (
                        "log_parameter_max_length is 0, so bind values are not "
                        "logged. Transaction shapes still work; per-value access "
                        "skew and transaction coherence do not"
                    ),
                }
            )

        if version and version < QUERY_ID_MIN_VERSION:
            notes.append(
                {
                    "code": "query_id_unavailable",
                    "detail": (
                        "PostgreSQL 14 is the first version to expose the query "
                        "identifier to log output, so a log window from this "
                        "server cannot be matched to the aggregate totals by id"
                    ),
                }
            )

        return notes

    def _provider(self) -> str:
        if self._setting("rds.extensions") is not None:
            return "rds"
        if self._setting("cloudsql.iam_authentication") is not None:
            return "cloudsql"
        if self._setting("data_directory") is not None:
            return "self_managed"
        return "unknown"

    def _retention(self, provider: str) -> Dict[str, Any]:
        if provider != "rds":
            return {"known": False, "detail": "not reported by this server"}
        minutes = _int(self._setting("rds.log_retention_period"))
        if minutes is None:
            return {"known": False, "detail": "rds.log_retention_period is not set"}
        return {
            "known": True,
            "minutes": minutes,
            "days": round(minutes / 1440, 1),
            "detail": (
                f"rds.log_retention_period is {minutes} minutes; read the log "
                "inside that window or the file is gone"
            ),
        }

    def _log_fdw(self) -> str:
        row = self._sql.one_or_none(
            "SELECT (SELECT extversion FROM pg_extension"
            "  WHERE extname = 'log_fdw') AS installed_version,"
            " EXISTS (SELECT 1 FROM pg_available_extensions"
            "  WHERE name = 'log_fdw') AS available"
        )
        if row is None:
            return "absent"
        if row.get("installed_version"):
            return "installed"
        return "available" if row.get("available") else "absent"

    def _pgaudit(self, provider: str = "unknown") -> Dict[str, Any]:
        row = self._sql.one_or_none(
            "SELECT (SELECT extversion FROM pg_extension"
            "  WHERE extname = 'pgaudit') AS installed_version,"
            " EXISTS (SELECT 1 FROM pg_available_extensions"
            "  WHERE name = 'pgaudit') AS available"
        )
        installed_version = (row or {}).get("installed_version")
        available = bool((row or {}).get("available"))
        if installed_version:
            state = "installed"
            recommendation = PGAUDIT_RECOMMENDATION_INSTALLED
        elif available:
            state = "available"
            recommendation = PGAUDIT_RECOMMENDATION_AVAILABLE
        else:
            state = "absent"
            recommendation = PGAUDIT_RECOMMENDATION_ABSENT.get(
                provider, PGAUDIT_RECOMMENDATION_ABSENT["default"]
            )
        return {
            "state": state,
            "installed": bool(installed_version),
            "available": available,
            "installed_version": installed_version,
            "settings": {name: self._setting(name) for name in PGAUDIT_SETTINGS},
            "connected_role_is_superuser": self._setting("is_superuser") == "on",
            "recommendation": recommendation,
        }

    def _pgaudit_notes(self, pgaudit: Dict[str, Any]) -> List[Dict[str, str]]:
        notes: List[Dict[str, str]] = []
        if pgaudit["state"] == "absent":
            return notes
        if pgaudit["connected_role_is_superuser"]:
            notes.append(
                {
                    "code": "pgaudit_superuser_not_audited",
                    "detail": (
                        "the connected role is a superuser, and pgAudit does not "
                        "reliably audit superusers. If the application connects "
                        "as a superuser too, its traffic will be missing from a "
                        "burst -- role-scope the burst to a non-superuser "
                        "application role"
                    ),
                }
            )
        if (
            pgaudit["state"] == "installed"
            and (pgaudit["settings"].get("pgaudit.log_relation") or "").lower() == "on"
        ):
            notes.append(
                {
                    "code": "pgaudit_object_logging_corrupts_shapes",
                    "detail": (
                        "pgaudit.log_relation is on. Object logging emits one "
                        "entry per relation for a single statement, which "
                        "corrupts transaction-shape assembly -- the burst recipe "
                        "requires it off"
                    ),
                }
            )
        return notes

    def _setting(self, name: str) -> Optional[str]:
        row = self._sql.one_or_none(
            "SELECT current_setting(%s, true) AS value", (name,)
        )
        value = (row or {}).get("value")
        return None if value is None else str(value)


QUERY_ID_CARRIERS = (
    "The identifier pg_stat_statements aggregates under has to reach the log "
    "too, or a log row cannot be joined to its aggregate row and the capture's "
    "coverage of the window stays assumed rather than measured. Add csvlog or "
    "jsonlog to log_destination, which carry the column outright, or put %Q in "
    "log_line_prefix."
)


def _query_id_computed(version: int, compute: Optional[str]) -> bool:
    return version >= QUERY_ID_MIN_VERSION and compute in ("on", "auto", "regress")


def _aggregate_join_detail(version, compute, structured, prefix) -> str:
    if not _query_id_computed(version, compute):
        return f"compute_query_id = {compute}"
    if structured or prefix["query_id"]:
        return f"compute_query_id = {compute}, and the log carries it"
    return (
        f"compute_query_id = {compute}, but the log does not carry the "
        "identifier: log_destination is stderr and log_line_prefix has no %Q"
    )


def _aggregate_join_fix(version, compute, structured, prefix) -> str:
    if not _query_id_computed(version, compute):
        return (
            "Set compute_query_id = on. The identifier it computes is the one "
            "pg_stat_statements aggregates under, so a log row joins to its "
            "aggregate row on an integer and the capture's coverage of the "
            "whole window becomes measurable rather than assumed."
        )
    return QUERY_ID_CARRIERS


def _requirement(
    available: bool,
    detail: str,
    fix: str,
    where: str,
    setting: Optional[str] = None,
) -> Dict[str, Any]:
    remediation = ""
    if not available:
        remediation = f"{fix} Set it {WHERE_TO_SET[where]}."
        if setting in RESTART_REQUIRED:
            remediation = remediation.replace(
                "followed by a reload", "followed by a restart"
            )
            remediation += (
                " It changes only at server start, so it needs a restart, "
                "not a reload."
            )
    return {"available": available, "detail": detail, "remediation": remediation}


def _destinations(value: Optional[str]) -> Dict[str, bool]:
    parts = {part.strip() for part in (value or "").split(",")}
    return {
        "stderr": "stderr" in parts,
        "csvlog": "csvlog" in parts,
        "jsonlog": "jsonlog" in parts,
        "syslog": "syslog" in parts,
    }


def _prefix_escapes(prefix: Optional[str]) -> Dict[str, Any]:
    text = prefix or ""
    return {
        "raw": text,
        "session_id": "%c" in text,
        "virtual_txid": "%v" in text,
        "txid": "%x" in text,
        "query_id": "%Q" in text,
        "timestamp": "%m" in text or "%t" in text,
    }


def _logging_statements(settings: Dict[str, Optional[str]]) -> bool:
    if _logs_every_statement(settings.get("log_min_duration_statement")):
        return True
    if (settings.get("log_statement") or "none") != "none":
        return True
    return _positive(settings.get("log_transaction_sample_rate"))


def _tiers(requirements: Dict[str, Dict[str, Any]]) -> Dict[str, bool]:
    have = {name: bool(req["available"]) for name, req in requirements.items()}
    return {
        "transaction_shapes": have["statement_log"] and have["session_identity"],
        "unbiased_transactions": have["session_identity"]
        and have["whole_transactions"],
        "access_skew": have["statement_log"] and have["values"],
        "collectable_over_sql": have["readable_over_sql"],
        "coverage_against_aggregate": have["aggregate_join"],
    }


def _num(value: Optional[str]) -> Optional[float]:
    text = str(value or "").strip()
    digits = ""
    for char in text:
        if char.isdigit() or char in "+-." or (char == "e" and digits):
            digits += char
        else:
            break
    try:
        return float(digits)
    except (TypeError, ValueError):
        return None


def _positive(value: Optional[str]) -> bool:
    number = _num(value)
    return number is not None and number > 0


def _logs_every_statement(value: Optional[str]) -> bool:
    # log_min_duration_statement is "on" at 0 and "off" at -1, not the reverse.
    number = _num(value)
    return number is not None and number >= 0


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
