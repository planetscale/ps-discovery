import logging
from typing import Any, Dict, List, Optional

from planetscale_discovery.workload.burst.collector import LOG_SERVER, WORK_SCHEMA
from planetscale_discovery.workload.burst.sql import SafeCursor


def find_leftovers(
    connection, logger: Optional[logging.Logger] = None
) -> List[Dict[str, Any]]:
    """Name the log_fdw objects an interrupted collect left on the server."""
    sql = SafeCursor(connection, logger=logger)
    found: List[Dict[str, Any]] = []

    schema = sql.one_or_none(
        "SELECT 1 AS found FROM pg_catalog.pg_namespace WHERE nspname = %s",
        (WORK_SCHEMA,),
    )
    if schema:
        found.append(
            {
                "kind": "schema",
                "name": WORK_SCHEMA,
                "drop": f"DROP SCHEMA IF EXISTS {WORK_SCHEMA} CASCADE",
            }
        )

    server = sql.one_or_none(
        "SELECT 1 AS found FROM pg_catalog.pg_foreign_server WHERE srvname = %s",
        (LOG_SERVER,),
    )
    if server:
        found.append(
            {
                "kind": "server",
                "name": LOG_SERVER,
                "drop": f"DROP SERVER IF EXISTS {LOG_SERVER} CASCADE",
            }
        )

    return found


def drop_leftovers(
    connection, logger: Optional[logging.Logger] = None
) -> Dict[str, Any]:
    """Drop what find_leftovers reported. Leaves log_fdw itself installed."""
    log = logger or logging.getLogger(__name__)
    leftovers = find_leftovers(connection, logger=logger)
    result: Dict[str, Any] = {"found": leftovers, "dropped": [], "failed": []}
    if not leftovers:
        return result

    sql = SafeCursor(connection, logger=logger)
    if not sql.execute("SET default_transaction_read_only = off"):
        result["failed"] = [
            dict(item, why="the session is read-only") for item in leftovers
        ]
        return result
    # Committed, or a failing DROP rolls the SET back and the next reads read-only.
    sql.commit()

    for item in leftovers:
        if sql.execute(item["drop"]):
            result["dropped"].append(item)
            log.info(f"dropped {item['kind']} {item['name']}")
        else:
            result["failed"].append(dict(item, why=sql.errors[-1]))
    sql.commit()
    return result
