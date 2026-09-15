import csv
import io
import logging
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional

from planetscale_discovery.workload.logs.record import COLUMNS, KNOWN_WIDTHS

logger = logging.getLogger("planetscale_discovery")

STATEMENT_RE = re.compile(r"^(?:duration:\s*[\d.]+\s*ms\s+)?statement:\s*(.*)$", re.S)
EXECUTE_RE = re.compile(
    r"^(?:duration:\s*[\d.]+\s*ms\s+)?execute\s+(?:<unnamed>|[^:]+):\s*(.*)$", re.S
)
PARSE_RE = re.compile(r"^(?:duration:\s*[\d.]+\s*ms\s+)?parse\s+(?:<unnamed>|[^:]+):")
BIND_RE = re.compile(r"^(?:duration:\s*[\d.]+\s*ms\s+)?bind\s+(?:<unnamed>|[^:]+):")

DURATION_RE = re.compile(r"^duration:\s*([\d.]+)\s*ms")

PARAMETERS_RE = re.compile(r"^Parameters:\s*(.*)$", re.S)

ERROR_SEVERITIES = ("ERROR", "FATAL", "PANIC")


def read_records(source: Iterable[str]) -> Iterator[Dict[str, Any]]:
    for row in csv.reader(source):
        if not row:
            continue
        record = _row_to_dict(row)
        if record is not None:
            yield record


def read_path(path: str) -> Iterator[Dict[str, Any]]:
    with io.open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        for record in read_records(handle):
            yield record


def statements(records: Iterable[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    for record in records:
        statement = to_statement(record)
        if statement is not None:
            yield statement


def to_statement(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    message = str(record.get("message") or "")

    if PARSE_RE.match(message) or BIND_RE.match(message):
        return None

    match = STATEMENT_RE.match(message) or EXECUTE_RE.match(message)
    sql = match.group(1).strip() if match else ""

    if not sql:
        severity = str(record.get("error_severity") or "").upper()
        query = str(record.get("query") or "").strip()
        if severity in ERROR_SEVERITIES and query:
            sql = query
        else:
            return None

    return {
        "session_id": record.get("session_id"),
        "session_line_num": _int(record.get("session_line_num")),
        "log_time": record.get("log_time"),
        "user_name": record.get("user_name"),
        "database_name": record.get("database_name"),
        "application_name": record.get("application_name"),
        "command_tag": record.get("command_tag"),
        "virtual_transaction_id": record.get("virtual_transaction_id") or None,
        "transaction_id": _int(record.get("transaction_id")),
        "query_id": _int(record.get("query_id")),
        "duration_ms": _duration(message),
        "sql": sql,
        "parameters": _parameters(record.get("detail")),
    }


def _row_to_dict(row: List[str]) -> Optional[Dict[str, Any]]:
    width = len(row)
    if width not in KNOWN_WIDTHS:
        logger.warning(
            "csvlog row has %d columns, not a known width (%s); dropping",
            width,
            ", ".join(str(w) for w in KNOWN_WIDTHS),
        )
        return None
    return dict(zip(COLUMNS[:width], row))


def _duration(message: str) -> Optional[float]:
    match = DURATION_RE.match(message)
    return float(match.group(1)) if match else None


def _parameters(detail: Optional[str]) -> Optional[str]:
    text = str(detail or "").strip()
    if not text:
        return None
    match = PARAMETERS_RE.match(text)
    return match.group(1).strip() if match else None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
