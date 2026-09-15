import re
from typing import Any, Dict, Iterable, List, Optional

BEGIN_TAGS = ("BEGIN",)
END_TAGS = ("COMMIT", "ROLLBACK")

SAVEPOINT_ROLLBACK_RE = re.compile(r"\bTO\s+(?:SAVEPOINT\s+)?\S+", re.I)


def group_by_session(
    statements_in: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    sessions: Dict[str, List[Dict[str, Any]]] = {}
    for statement in statements_in:
        session = str(statement.get("session_id") or "")
        sessions.setdefault(session, []).append(statement)
    for records in sessions.values():
        records.sort(key=_line_order)
    return sessions


def _line_order(statement: Dict[str, Any]) -> tuple:
    # pgAudit and stderr carry the line number as text, so "10" sorts before "2".
    value = statement.get("session_line_num")
    try:
        return (0, int(value), "")
    except (TypeError, ValueError):
        return (1, 0, str(value or ""))


def transactions(session_statements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for statement in session_statements:
        tag = str(statement.get("command_tag") or "").upper()

        if tag in BEGIN_TAGS:
            if current is not None:
                current["closed"] = False
                result.append(current)
            current = _new_transaction(statement)
            continue

        if tag in END_TAGS:
            if tag == "ROLLBACK" and _is_savepoint_rollback(statement):
                if current is None:
                    result.append(_autocommit(statement))
                    continue
                current["statements"].append(statement)
                current["rows"].append(statement)
                continue

            if current is None:
                current = _new_transaction(statement)
            else:
                current["rows"].append(statement)
            current["end_tag"] = tag
            current["closed"] = True
            result.append(current)
            current = None
            continue

        if current is None:
            result.append(_autocommit(statement))
            continue
        current["statements"].append(statement)
        current["rows"].append(statement)

    if current is not None:
        current["closed"] = False
        result.append(current)
    return result


def _is_savepoint_rollback(statement: Dict[str, Any]) -> bool:
    sql = str(statement.get("sql") or "")
    return bool(SAVEPOINT_ROLLBACK_RE.search(sql))


def _new_transaction(opener: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": opener.get("session_id"),
        "first_line": opener.get("session_line_num"),
        "virtual_transaction_id": opener.get("virtual_transaction_id"),
        "explicit": True,
        "closed": False,
        "end_tag": None,
        "statements": [],
        "rows": [opener],
    }


def _autocommit(statement: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": statement.get("session_id"),
        "first_line": statement.get("session_line_num"),
        "virtual_transaction_id": statement.get("virtual_transaction_id"),
        "explicit": False,
        "closed": True,
        "end_tag": None,
        "statements": [statement],
        "rows": [statement],
    }
