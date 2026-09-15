from typing import Any, Optional, TypedDict

COLUMNS = (
    "log_time",
    "user_name",
    "database_name",
    "process_id",
    "connection_from",
    "session_id",
    "session_line_num",
    "command_tag",
    "session_start_time",
    "virtual_transaction_id",
    "transaction_id",
    "error_severity",
    "sql_state_code",
    "message",
    "detail",
    "hint",
    "internal_query",
    "internal_query_pos",
    "context",
    "query",
    "query_pos",
    "location",
    "application_name",
    "backend_type",
    "leader_pid",
    "query_id",
)

WIDTH_PG12 = 23
WIDTH_PG13 = 24
WIDTH_PG14 = 26
KNOWN_WIDTHS = (WIDTH_PG12, WIDTH_PG13, WIDTH_PG14)


class StatementRecord(TypedDict, total=False):
    session_id: Optional[str]
    session_line_num: Optional[int]
    log_time: Any
    user_name: Optional[str]
    database_name: Optional[str]
    application_name: Optional[str]
    command_tag: Optional[str]
    virtual_transaction_id: Optional[str]
    transaction_id: Optional[int]
    query_id: Optional[int]
    duration_ms: Optional[float]
    sql: str
    parameters: Optional[str]
