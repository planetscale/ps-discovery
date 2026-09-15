import csv
import itertools
import json
import re
from collections import Counter
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from planetscale_discovery.workload.logs.stderrlog import (
    CONTINUATION,
    DEFAULT_PREFIX,
    LEVEL_RE,
    build_prefix_re,
)

AUDIT_COLUMNS = (
    "audit_type",
    "statement_id",
    "substatement_id",
    "audit_class",
    "command",
    "object_type",
    "object_name",
    "statement",
    "parameter",
)

AUDIT_RE = re.compile(r"^AUDIT:\s*(.*)$", re.S)

NOT_LOGGED = "<not logged>"

PARAMETER_SENTINELS = frozenset({"<not logged>", "<none>", "[not logged]", "[none]"})

SHAPE_COMMANDS = frozenset(
    {
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT",
        "RELEASE",
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
    }
)


_TIMESTAMP_MS = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+(?: [A-Za-z0-9+\-]+(?::\d{2})?)?"
)
_TIMESTAMP_S = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?: [A-Za-z0-9+\-]+(?::\d{2})?)?"
)
_SESSION_ID = re.compile(r"(?<![\d.])[0-9a-f]{4,}\.[0-9a-f]+(?![\d.])")
_VXID = re.compile(r"(?<![\d./])\d+/\d+(?![\d./])")
# Signed: an unsigned pattern drops every line whose %Q query id is negative.
_NUMBER = re.compile(r"-?\d+")

_USER_AT_DB = re.compile(r"[^\s\[\],:@]*@[^\s\[\],:]*")
_DB_USER_PAIR = re.compile(r"db=[^\s\[\],]+,user=[^\s\[\],]+")

_HOST_PORT = re.compile(
    r"[0-9A-Za-z._-]+\(\d+\)"
    r"|\[[0-9A-Fa-f:.]+\]\(\d+\)"
    r"|(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.]*\(\d+\)"
    r"|\d{1,3}(?:\.\d{1,3}){3}"
)

_SNIFF_SAMPLE = 25


def sniff_prefix(lines: Iterable[str]) -> Optional[str]:
    sample = []
    audit_lines = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip() or _level_start(line) is None:
            continue
        if len(sample) < _SNIFF_SAMPLE:
            sample.append(line)
        if "AUDIT:" in line and len(audit_lines) < _SNIFF_SAMPLE:
            audit_lines.append(line)
    candidates = audit_lines or sample
    best: Optional[str] = None
    best_hits = 0
    tried: Set[str] = set()
    for line in candidates:
        template = _template_from(line)
        if template is None or template in tried:
            continue
        tried.add(template)
        matcher = build_prefix_re(template)
        hits = sum(1 for other in sample if _prefix_matches(matcher, other))
        if best is None or hits > best_hits:
            best, best_hits = template, hits
    return best


_ADJACENT_ESCAPES = re.compile(r"%[a-zA-Z]%[a-zA-Z]")


def _template_from(line: str) -> Optional[str]:
    index = _level_start(line)
    head = line[: index if index is not None else len(line)]
    template = _HOST_PORT.sub("%r", head)
    template = _TIMESTAMP_MS.sub("%m", template, count=1)
    template = _TIMESTAMP_S.sub("%t", template, count=1)
    template = _SESSION_ID.sub("%c", template, count=1)
    template = _DB_USER_PAIR.sub("db=%d,user=%u", template, count=1)
    template = _USER_AT_DB.sub("%u@%d", template, count=1)
    template = _VXID.sub("%v", template, count=1)
    pid_seen = False

    def number(match: "re.Match") -> str:
        nonlocal pid_seen
        if pid_seen:
            return "%P"
        pid_seen = True
        return "%p"

    template = _NUMBER.sub(number, template)
    if _ADJACENT_ESCAPES.search(template):
        return None
    return template


_PREFIX_WINDOW = 2048


def _prefix_matches(matcher: "re.Pattern", line: str) -> bool:
    head = line[:_PREFIX_WINDOW]
    match = matcher.match(head)
    end = match.end() if match else 0
    return match is not None and LEVEL_RE.match(head[end:]) is not None


def _level_start(line: str) -> Optional[int]:
    match = LEVEL_RE.search(line)
    return match.start() if match else None


class ObjectLoggingError(ValueError):
    pass


def new_summary() -> Dict[str, Any]:
    return {
        "dropped": Counter(),
        "errors": Counter(),
        "substatements": 0,
        "malformed": 0,
        "incomplete_chunks": 0,
        "array_bytes": 0,
        "skipped": 0,
        "unmatched": 0,
    }


def loss_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key, value in summary.items():
        if isinstance(value, Counter):
            out[key] = dict(sorted(value.items()))
        else:
            out[key] = value
    return out


def read_log_records(
    lines: Iterable[str],
    prefix: str = DEFAULT_PREFIX,
    summary: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    counts = summary if summary is not None else new_summary()
    matcher = build_prefix_re(prefix)
    last: List[Tuple[str, str]] = []
    pending: Optional[Tuple[Dict[str, str], str]] = None

    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            continue

        match = matcher.match(line)
        end = match.end() if match else 0
        level_match = LEVEL_RE.match(line[end:]) if match else None
        if match is None or level_match is None:
            if pending is not None and raw.startswith(CONTINUATION):
                fields, text = pending
                pending = (fields, text + "\n" + line.strip())
            else:
                counts["unmatched"] += 1
            continue

        level, body = level_match.group(1), level_match.group(2)
        fields = {k: v for k, v in match.groupdict().items() if v is not None}

        if pending is not None:
            record = _csv_record(pending[0], pending[1], counts, last)
            if record is not None:
                yield record
            pending = None

        if level == "ERROR":
            session = fields.get("session_id") or fields.get("process_id") or ""
            counts["errors"][session] += 1
            continue

        audit = AUDIT_RE.match(body)
        if audit is None:
            continue

        pending = (fields, audit.group(1))

    if pending is not None:
        record = _csv_record(pending[0], pending[1], counts, last)
        if record is not None:
            yield record


def read_jsonl_records(
    lines: Iterable[str],
    summary: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    counts = summary if summary is not None else new_summary()
    last: List[Tuple[str, str]] = []
    pending_chunks: Dict[Tuple[str, str], Dict[int, Dict[str, Any]]] = {}

    for entry in _json_entries(lines, counts):
        if not isinstance(entry, dict):
            counts["skipped"] += 1
            continue
        payload = entry.get("jsonPayload")
        if not isinstance(payload, dict) or "command" not in payload:
            counts["skipped"] += 1
            continue
        payload = dict(payload)
        payload["_log_time"] = entry.get("timestamp")

        key = (
            str(payload.get("databaseSessionId") or ""),
            str(payload.get("statementId") or ""),
        )
        chunk_count = _int(payload.get("chunkCount")) or 1
        if chunk_count <= 1:
            record = _json_record(payload, counts, last)
            if record is not None:
                yield record
            continue

        index = _int(payload.get("chunkIndex"))
        if index is None:
            counts["malformed"] += 1
            pending_chunks.pop(key, None)
            continue
        group = pending_chunks.setdefault(key, {})
        group[index] = payload
        if len(group) == chunk_count:
            del pending_chunks[key]
            order = _chunk_order(group, chunk_count)
            if order is None:
                counts["malformed"] += 1
                continue
            merged = _merge_chunks([group[position] for position in order])
            record = _json_record(merged, counts, last)
            if record is not None:
                yield record

    counts["incomplete_chunks"] += len(pending_chunks)


def _chunk_order(group: Dict[int, Dict[str, Any]], chunk_count: int) -> Optional[range]:
    # Cloud SQL and AlloyDB number chunkIndex from 1; other producers from 0.
    for first in (1, 0):
        order = range(first, first + chunk_count)
        if all(position in group for position in order):
            return order
    return None


def _json_entries(lines: Iterable[str], counts: Dict[str, Any]) -> Iterator[Any]:
    iterator = iter(lines)
    head: List[str] = []
    for raw in iterator:
        head.append(raw)
        if raw.strip():
            break
    leading = "".join(head).lstrip()
    if not leading:
        return
    if leading.startswith("["):
        text = "".join(itertools.chain(head, iterator))
        counts["array_bytes"] = len(text)
        for element in _array_elements(text):
            try:
                yield json.loads(element)
            except ValueError:
                counts["skipped"] += 1
        return
    for raw in itertools.chain(head, iterator):
        text = raw.strip()
        if not text:
            continue
        try:
            yield json.loads(text)
        except ValueError:
            counts["skipped"] += 1


def _csv_record(
    fields: Dict[str, str],
    text: str,
    counts: Dict[str, Any],
    last: List[Tuple[str, str]],
) -> Optional[Dict[str, Any]]:
    try:
        row = next(csv.reader([text]))
    except csv.Error:
        counts["malformed"] += 1
        return None
    if len(row) != len(AUDIT_COLUMNS):
        counts["malformed"] += 1
        return None
    values = dict(zip(AUDIT_COLUMNS, row))
    session = fields.get("session_id") or fields.get("process_id") or ""
    return _to_record(
        session=session,
        statement_id=values["statement_id"],
        substatement_id=values["substatement_id"],
        audit_type=values["audit_type"],
        command=values["command"],
        sql=values["statement"],
        parameter=values["parameter"],
        base=fields,
        counts=counts,
        last=last,
        pid_identity=not fields.get("session_id"),
    )


def _json_record(
    payload: Dict[str, Any],
    counts: Dict[str, Any],
    last: List[Tuple[str, str]],
) -> Optional[Dict[str, Any]]:
    base = {
        "log_time": payload.get("_log_time"),
        "user_name": payload.get("user"),
        "database_name": payload.get("database"),
    }
    return _to_record(
        session=str(payload.get("databaseSessionId") or ""),
        statement_id=str(payload.get("statementId") or ""),
        substatement_id=str(payload.get("substatementId") or "1"),
        audit_type=str(payload.get("auditType") or "SESSION"),
        command=str(payload.get("command") or ""),
        sql=str(payload.get("statement") or ""),
        parameter=str(payload.get("parameter") or ""),
        base=base,
        counts=counts,
        last=last,
        pid_identity=False,
    )


def _to_record(
    session: str,
    statement_id: str,
    substatement_id: str,
    audit_type: str,
    command: str,
    sql: str,
    parameter: str,
    base: Dict[str, Any],
    counts: Dict[str, Any],
    last: List[Tuple[str, str]],
    pid_identity: bool,
) -> Optional[Dict[str, Any]]:
    if audit_type.strip().upper() == "OBJECT":
        raise ObjectLoggingError(
            "the log contains OBJECT audit records, which means "
            "pgaudit.log_relation = on: object logging emits one row per "
            "relation a statement touches and corrupts shape assembly. "
            "Re-capture with the burst recipe, which leaves log_relation off."
        )

    if substatement_id not in ("", "1"):
        counts["substatements"] += 1
        return None

    key = (session, statement_id)
    if last and last[-1] == key:
        if not session.strip("0"):
            counts["malformed"] += 1
            return None
        message = (
            f"statement id {statement_id} appears twice in session "
            f"{session}: one STATEMENT_ID per record is what "
            "session logging guarantees, and pgaudit.log_relation = on is "
            "what breaks that."
        )
        if pid_identity:
            message += (
                " A prefix without %c can also produce this; add %c to "
                "log_line_prefix for the capture window."
            )
        raise ObjectLoggingError(
            message + " Re-capture with object logging off rather than "
            "deduplicating here."
        )
    last[:] = [key]

    tag = command.strip().upper()
    if tag not in SHAPE_COMMANDS:
        counts["dropped"][tag] += 1
        return None

    if parameter.strip().lower() in PARAMETER_SENTINELS:
        parameter = ""
    return {
        "log_time": base.get("log_time"),
        "user_name": base.get("user_name"),
        "database_name": base.get("database_name"),
        "application_name": base.get("application_name"),
        "session_id": session,
        "session_line_num": statement_id,
        "virtual_transaction_id": base.get("virtual_transaction_id"),
        "transaction_id": base.get("transaction_id"),
        "query_id": base.get("query_id"),
        "command_tag": tag,
        "message": "statement: " + sql,
        "detail": f"Parameters: {parameter}" if parameter else "",
    }


def _array_elements(text: str) -> Iterator[str]:
    index = text.index("[") + 1
    element_start = index
    depth = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            if depth == 0:
                tail = text[element_start:index].strip()
                if tail:
                    yield tail
                return
            depth -= 1
        elif char == "," and depth == 0:
            yield text[element_start:index]
            element_start = index + 1
        index += 1
    tail = text[element_start:].strip()
    if tail:
        yield tail


def _merge_chunks(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(parts[0])
    merged["statement"] = "".join(str(p.get("statement") or "") for p in parts)
    parameter = "".join(str(p.get("parameter") or "") for p in parts)
    if parameter:
        merged["parameter"] = parameter
    return merged


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
