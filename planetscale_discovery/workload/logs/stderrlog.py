import re
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from planetscale_discovery.common.sanitize import statement_kind
from planetscale_discovery.workload.logs.csvlog import (
    EXECUTE_RE,
    PARAMETERS_RE,
    STATEMENT_RE,
)

_ZONE = r"(?: [A-Za-z0-9+\-]+(?::\d{2})?)?"

_WORD = r"[^\s\[\],:]*"

ESCAPES: Dict[str, Tuple[str, Optional[str]]] = {
    "m": (r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+" + _ZONE, "log_time"),
    "t": (r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}" + _ZONE, "log_time"),
    "n": (r"\d+\.\d+", "log_time"),
    "p": (r"\d+", "process_id"),
    "c": (r"[0-9a-f]+\.[0-9a-f]+", "session_id"),
    "v": (r"\d+/\d+", "virtual_transaction_id"),
    "x": (r"\d+", "transaction_id"),
    "u": (_WORD, "user_name"),
    "d": (_WORD, "database_name"),
    "a": (_WORD, "application_name"),
    "l": (r"\d+", "session_line_num"),
    "Q": (r"-?\d+", "query_id"),
    "e": (r"[0-9A-Z]{5}", "sql_state_code"),
    "b": (r"[\w ]+", "backend_type"),
    "h": (_WORD, None),
    "r": (r"\S+?\(\d+\)|[^\s,]+", None),
    "s": (r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}" + _ZONE, None),
    "i": (r"[\w ]*", None),
    "L": (_WORD, None),
    "P": (r"-?\d*", None),
}

LEVEL_RE = re.compile(
    r"(LOG|DETAIL|STATEMENT|ERROR|FATAL|PANIC|WARNING|NOTICE|INFO|HINT|CONTEXT"
    r"|DEBUG\d?):\s*(.*)$",
    re.S,
)

CONTINUATION = ("\t", "    ")

DEFAULT_PREFIX = "%m [%p] "

# PostgreSQL logs the command tag, so a synonym must derive the same tag here.
TAG_SYNONYMS = {"START": "BEGIN", "END": "COMMIT", "ABORT": "ROLLBACK"}


def build_prefix_re(prefix: str) -> "re.Pattern":
    pattern = ["^"]
    optional_from: Optional[int] = None
    captured: Set[str] = set()
    index = 0
    while index < len(prefix):
        char = prefix[index]
        if char != "%":
            pattern.append(re.escape(char))
            index += 1
            continue
        index += 1
        if index >= len(prefix):
            pattern.append(re.escape("%"))
            break
        code = prefix[index]
        index += 1
        if code == "%":
            pattern.append(re.escape("%"))
            continue
        if code == "q":
            optional_from = len(pattern)
            continue
        while code.isdigit() or code == "-":
            if index >= len(prefix):
                break
            code = prefix[index]
            index += 1
        fragment, field = ESCAPES.get(code, (r"\S*", None))
        if field and field not in captured:
            captured.add(field)
            pattern.append(f"(?P<{field}>{fragment})")
        else:
            pattern.append(f"(?:{fragment})")

    if optional_from is not None:
        tail = "".join(pattern[optional_from:])
        pattern = pattern[:optional_from] + [f"(?:{tail})?"]
    return re.compile("".join(pattern))


def read_records(
    lines: Iterable[str],
    prefix: str = DEFAULT_PREFIX,
    summary: Optional[Dict[str, int]] = None,
) -> Iterator[Dict[str, Any]]:
    matcher = build_prefix_re(prefix)
    pending: Optional[Dict[str, Any]] = None
    counter = 0
    if summary is not None:
        summary.setdefault("unparsed", 0)

    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            continue

        match = matcher.match(line)
        end = match.end() if match else 0
        level_match = LEVEL_RE.match(line[end:]) if match else None
        if match is None or level_match is None:
            if pending is not None and raw.startswith(CONTINUATION):
                pending["message"] += "\n" + line.strip()
            elif summary is not None and LEVEL_RE.search(line):
                summary["unparsed"] += 1
            continue

        level, body = level_match.group(1), level_match.group(2)
        fields = {k: v for k, v in match.groupdict().items() if v is not None}

        if level == "DETAIL":
            if pending is not None and PARAMETERS_RE.match(body.strip()):
                pending["detail"] = body.strip()
            continue

        if level == "STATEMENT":
            if pending is not None:
                yield pending
            counter += 1
            pending = _record(fields, "statement: " + body.strip(), counter)
            continue

        if level != "LOG":
            if pending is not None:
                yield pending
                pending = None
            continue

        if pending is not None:
            yield pending
        counter += 1
        pending = _record(fields, body, counter)

    if pending is not None:
        yield pending


def oldest_first(entries: List[Dict[str, Any]], key: str = "time") -> List[str]:
    ordered = sorted(entries, key=lambda entry: str(entry.get(key) or ""))
    return [str(entry.get("message") or "") for entry in ordered]


def _record(fields: Dict[str, str], message: str, counter: int) -> Dict[str, Any]:
    session = fields.get("session_id") or fields.get("process_id") or ""
    return {
        "log_time": fields.get("log_time"),
        "user_name": fields.get("user_name"),
        "database_name": fields.get("database_name"),
        "application_name": fields.get("application_name"),
        "session_id": session,
        "session_line_num": fields.get("session_line_num") or str(counter),
        "virtual_transaction_id": fields.get("virtual_transaction_id"),
        "transaction_id": fields.get("transaction_id"),
        "query_id": fields.get("query_id"),
        "command_tag": _command_tag(message),
        "message": message,
        "detail": "",
    }


def _command_tag(message: str) -> str:
    match = STATEMENT_RE.match(message) or EXECUTE_RE.match(message)
    sql = match.group(1) if match else message

    kind = statement_kind(sql)
    if kind and kind != "UNKNOWN":
        return TAG_SYNONYMS.get(kind, kind)

    first_word = str(sql or "").strip().split(None, 1)[:1]
    if not first_word:
        return ""
    # sqlparse names no BEGIN kind, so "BEGIN;" must not keep its semicolon.
    word = first_word[0].upper().strip(";,()")
    return TAG_SYNONYMS.get(word, word)
