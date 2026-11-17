"""CockroachDB detection and version parsing.

CockroachDB speaks the PostgreSQL wire protocol and advertises
``server_version_num = 130000``. The only reliable engine signal is
``version()``, which starts with ``CockroachDB``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

COCKROACH_VERSION_RE = re.compile(
    r"CockroachDB(?:\s+\w+)?\s+v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)",
    re.IGNORECASE,
)

SYSTEM_SCHEMAS = (
    "information_schema",
    "pg_catalog",
    "pg_toast",
    "crdb_internal",
    "pg_extension",
)

SYSTEM_DATABASES = (
    "system",
    "postgres",
    "defaultdb",
    "template0",
    "template1",
)


def is_system_database(
    name: Optional[str], current_database: Optional[str] = None
) -> bool:
    """True when discovery should skip ``name``.

    Every cluster ships ``defaultdb`` and ``postgres`` empty, so they are skipped
    unless they are the connected database. ``system`` is always skipped.
    """
    if name == "system":
        return True
    return name in SYSTEM_DATABASES and name != current_database


def quote_ident(name: str) -> str:
    """Quote a SQL identifier, escaping embedded double quotes."""
    return '"' + str(name).replace('"', '""') + '"'


def user_database_sql(column: str) -> str:
    """SQL condition on ``column``: true where ``is_system_database`` is false."""
    listed = ", ".join(f"'{s}'" for s in SYSTEM_DATABASES)
    return (
        f"({column} NOT IN ({listed}) "
        f"OR ({column} = current_database() AND {column} <> 'system'))"
    )


_SYSTEM_ZONE_RE = re.compile(
    r"^(RANGE\s+(meta|liveness|system|timeseries|tenants)|"
    r"DATABASE\s+system|TABLE\s+system\.|INDEX\s+system\.)",
    re.I,
)

_PARTITION_OF_RE = re.compile(
    r"^PARTITION\s+.+\s+OF\s+(?:INDEX|TABLE)\s+(\S+)",
    re.I,
)


def _qualified_database(qualified: str) -> Optional[str]:
    """Database of ``db.schema.table`` or ``db.schema.table@idx`` when present."""
    name = (qualified or "").split("@", 1)[0]
    parts = name.split(".")
    if len(parts) >= 3:
        return parts[0]
    return None


def is_user_zone_target(
    target: Optional[str], current_database: Optional[str] = None
) -> bool:
    """True when a SHOW ZONE CONFIGURATIONS target is a user object in this database.

    ``PARTITION … OF INDEX db.schema.table@idx`` is scoped by the index's
    database, not treated as a cluster-wide object.
    """
    name = (target or "").strip()
    if not name or name in (".default", "RANGE default"):
        return False
    if _SYSTEM_ZONE_RE.match(name):
        return False
    if not current_database:
        return True

    partition = _PARTITION_OF_RE.match(name)
    if partition:
        qualified = partition.group(1)
        db = _qualified_database(qualified)
        if db:
            return db == current_database and not is_system_database(
                db, current_database
            )
        first = qualified.split(".")[0].split("@", 1)[0]
        if is_system_database(first, current_database):
            return False
        return True

    parts = name.split()
    kind = parts[0].upper() if parts else ""
    rest = (parts[-1] if len(parts) > 1 else name).split("@", 1)[0]
    first = rest.split(".")[0] if rest else ""
    db = _qualified_database(rest)

    if kind == "DATABASE":
        return first == current_database
    if kind in ("TABLE", "INDEX"):
        if db:
            return db == current_database and not is_system_database(
                db, current_database
            )
        if is_system_database(first, current_database):
            return False
        if first in SYSTEM_SCHEMAS or first == "public" or first == current_database:
            return True
        return False

    if db:
        return db == current_database and not is_system_database(db, current_database)
    if is_system_database(first, current_database):
        return False
    return True


def is_cockroach_version_string(version_string: Optional[str]) -> bool:
    """Return True when ``version()`` identifies CockroachDB."""
    if not version_string:
        return False
    return "cockroachdb" in version_string.lower()


def parse_cockroach_version(version_string: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse ``CockroachDB CCL v25.2.4 ...`` into major/minor/patch."""
    if not version_string:
        return None
    match = COCKROACH_VERSION_RE.search(version_string)
    if not match:
        if is_cockroach_version_string(version_string):
            return {
                "engine": "cockroachdb",
                "version": "unknown",
                "major": 0,
                "minor": 0,
                "patch": 0,
                "version_string": version_string,
            }
        return None
    return {
        "engine": "cockroachdb",
        "version": f"{match.group('major')}.{match.group('minor')}.{match.group('patch')}",
        "major": int(match.group("major")),
        "minor": int(match.group("minor")),
        "patch": int(match.group("patch")),
        "version_string": version_string,
    }


def detect_cockroach_flavor(version_string: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return flavor metadata when the server is CockroachDB, else None."""
    parsed = parse_cockroach_version(version_string)
    if not parsed:
        return None
    return {
        "engine": "cockroachdb",
        "name": "CockroachDB",
        "version": parsed["version"],
        "major": parsed["major"],
        "minor": parsed["minor"],
        "patch": parsed["patch"],
        "version_string": version_string,
        "pg_compat_version_num": 130000,
        "pg_compat_note": (
            "CockroachDB advertises server_version_num=130000 (PostgreSQL 13). "
            "Do not treat that as the product version."
        ),
    }


def restricts_internals_by_default(flavor: Optional[Dict[str, Any]]) -> bool:
    """True from v26.1, where ``allow_unsafe_internals`` defaults to off."""
    if not flavor:
        return False
    return (flavor.get("major", 0), flavor.get("minor", 0)) >= (26, 1)


def allow_unsafe_internals(connection: Any) -> Optional[str]:
    """Turn on ``allow_unsafe_internals`` for this session.

    From v26.1 most ``crdb_internal`` tables and builtins fail with SQLSTATE
    42501 unless it is on. Before v25.4 the variable does not exist and the SET
    fails. Returns None on success, else the error. Every read made with it on
    is logged as ``unsafe_internals_accessed`` on the SENSITIVE_ACCESS channel.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET allow_unsafe_internals = on")
    except Exception as exc:
        if not connection.autocommit:
            connection.rollback()
        return str(exc).strip()
    if not connection.autocommit:
        connection.commit()
    return None
