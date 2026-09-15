import os
import re
from typing import Any, Callable, Dict, Iterator, List, Optional, TextIO

from planetscale_discovery.common.utils import generate_timestamp
from planetscale_discovery.workload.burst.collector import (
    SOURCE_FILE,
    STATUS_DEGRADED,
    STATUS_OK,
    _event_window,
    _session_summary,
)
from planetscale_discovery.workload.logs.csvlog import to_statement
from planetscale_discovery.workload.logs.pgaudit import (
    loss_summary,
    new_summary,
    read_jsonl_records,
    read_log_records,
    sniff_prefix,
)
from planetscale_discovery.workload.logs.stderrlog import (
    read_records as stderr_records,
)

_ARRAY_WARN_BYTES = 100 * 1024 * 1024

MAX_EXPORT_BYTES = 2 * 1024 * 1024 * 1024


def collect_pgaudit_file(
    path: str, *, json_export: bool, already_read=(), logger=None
) -> Dict[str, Any]:
    summary = new_summary()

    def records(handle: TextIO) -> Iterator[Dict[str, Any]]:
        if json_export:
            return read_jsonl_records(handle, summary)
        return read_log_records(handle, _sniffed_prefix(handle, logger), summary)

    burst = _collect_file(
        path,
        records,
        "no pgAudit statements found in it; a capture needs pgaudit.log "
        "enabled for the window (the burst recipe is 'read,write,misc')",
        warnings_from=lambda: _pgaudit_warnings(summary),
        already_read=already_read,
    )
    if burst.get("files_read"):
        burst["loss_summary"] = loss_summary(summary)
    return burst


def collect_stderr_file(path: str, already_read=(), logger=None) -> Dict[str, Any]:
    summary: Dict[str, int] = {}

    def records(handle: TextIO) -> Iterator[Dict[str, Any]]:
        return stderr_records(handle, _sniffed_prefix(handle, logger), summary)

    return _collect_file(
        path,
        records,
        "no statements found in it; expected log_statement or "
        "log_min_duration_statement output ('statement: ...' / "
        "'duration: ... ms' lines) for the window",
        warnings_from=lambda: _unparsed_warning(summary),
        already_read=already_read,
    )


def _unparsed_warning(summary: Dict[str, int]) -> List[str]:
    unparsed = summary.get("unparsed") or 0
    if not unparsed:
        return []
    return [
        f"{unparsed} log line(s) did not match the log_line_prefix read from "
        "the file and were left out. The prefix is inferred, so a field it "
        "could not name makes those lines unreadable and the window partial."
    ]


def _sniffed_prefix(handle: TextIO, logger) -> str:
    prefix = sniff_prefix(handle)
    if prefix is None:
        raise ValueError(
            "no PostgreSQL log lines found in it; expected server-log "
            "text with a severity marker (LOG:, ERROR:, ...)"
        )
    if logger:
        logger.info(f"log_line_prefix read from the file as {prefix!r}")
    handle.seek(0)
    return prefix


def _collect_file(
    path: str,
    records_from: Callable[[TextIO], Iterator[Dict[str, Any]]],
    empty_error: str,
    warnings_from: Optional[Callable[[], List[str]]] = None,
    already_read=(),
) -> Dict[str, Any]:
    started = generate_timestamp()
    if os.path.realpath(path) in {os.path.realpath(known) for known in already_read}:
        return {
            "kind": "burst",
            "schema_version": 1,
            "source": SOURCE_FILE,
            "captured_at_client": started,
            "captured_at_server": started,
            "status": STATUS_DEGRADED,
            "files_read": [],
            "files_skipped": [
                {"file": path, "why": "already read by an earlier burst"}
            ],
            "statements": [],
            "sessions": _session_summary([]),
            "warnings": [],
            "window_start": started,
            "window_end": started,
        }
    size = os.path.getsize(path)
    if size > MAX_EXPORT_BYTES:
        raise ValueError(
            f"it is {size / (1024 ** 3):.1f} GB, over the "
            f"{MAX_EXPORT_BYTES // (1024 ** 3)} GB an import reads into memory. "
            "Export a shorter window, or split the file and collect once per "
            "part"
        )
    statements: List[Dict[str, Any]] = []
    newest: Optional[str] = None

    with open(path, encoding="utf-8", errors="replace") as handle:
        for record in records_from(handle):
            statement = to_statement(record)
            if statement is None:
                continue
            statement["log_file"] = path
            statements.append(statement)
            log_time = statement.get("log_time")
            if log_time and (newest is None or str(log_time) > newest):
                newest = str(log_time)

    if not statements:
        raise ValueError(empty_error)

    warnings = list(warnings_from()) if warnings_from else []
    warnings += _clock_warnings(statements, newest)

    window_start, window_end = _event_window(statements, newest or started)

    return {
        "kind": "burst",
        "schema_version": 1,
        "source": SOURCE_FILE,
        "captured_at_client": started,
        "captured_at_server": newest or started,
        "status": STATUS_OK,
        "files_read": [{"file": path, "bytes": size, "kept": len(statements)}],
        "files_skipped": [],
        "statements": statements,
        "sessions": _session_summary(statements),
        "warnings": warnings,
        "window_start": window_start,
        "window_end": window_end,
    }


def _clock_warnings(
    statements: List[Dict[str, Any]], newest: Optional[str]
) -> List[str]:
    warnings = []
    if any(
        _unresolved_zone(str(statement.get("log_time") or ""))
        for statement in statements
    ):
        warnings.append(
            "some timestamps carry a zone abbreviation (not UTC/GMT) that "
            "could not be resolved without the server's log_timezone; they "
            "render without an offset. Log in UTC for the capture window."
        )
    if newest is None:
        warnings.append(
            "no parsed record carried a timestamp, so captured_at_server is "
            "the client clock. Put %m or %t in log_line_prefix for the window "
            "bounds to come from the log itself."
        )
    return warnings


def _pgaudit_warnings(summary: Dict[str, Any]) -> List[str]:
    warnings = []
    dropped = summary["dropped"]
    if dropped:
        tags = ", ".join(f"{tag} x{count}" for tag, count in dropped.most_common())
        warnings.append(
            f"{sum(dropped.values())} audit record(s) outside the shape "
            f"commands were dropped ({tags})"
        )
    errors = sum(summary["errors"].values())
    if errors:
        warnings.append(
            f"{errors} ERROR line(s) were counted but not attached: pgAudit "
            "logs no statement for one that never executed"
        )
    for key, wording in (
        ("malformed", "audit record(s) did not parse and were dropped"),
        ("substatements", "function-body record(s) were dropped"),
        ("incomplete_chunks", "chunked statement(s) never completed"),
        ("skipped", "non-audit line(s) in the export were skipped"),
        ("unmatched", "log line(s) did not match the sniffed prefix"),
    ):
        count = summary[key]
        if count:
            warnings.append(f"{count} {wording}")
    if summary.get("array_bytes", 0) > _ARRAY_WARN_BYTES:
        warnings.append(
            "the export was one JSON array over 100 MB and was parsed whole "
            "in memory; docs/providers/gcp.md shows a jq one-liner that "
            "converts it to JSONL first"
        )
    return warnings


_ZONE_ABBREV = re.compile(r"\s[A-Z]{2,5}$")


def _unresolved_zone(stamp: str) -> bool:
    return bool(_ZONE_ABBREV.search(stamp)) and not stamp.endswith((" UTC", " GMT"))
