"""Minimum grants for a non-admin CockroachDB discovery user.

COMPAT.md gotcha 14: discovery_ro needs CONNECT/USAGE/SELECT per database
and schema, plus VIEWACTIVITYREDACTED, VIEWCLUSTERSETTING, and VIEWJOB.
Without VIEWJOB, SHOW JOBS silently returns 0 rows. Recommend
default_transaction_read_only=on because public allows CREATE by default.

A missing privilege is an analysis gap, never a silent zero.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

REQUIRED_OBJECT_GRANTS = ("CONNECT", "USAGE", "SELECT")
REQUIRED_SYSTEM_GRANTS = (
    "VIEWACTIVITYREDACTED",
    "VIEWCLUSTERSETTING",
    "VIEWJOB",
)
ROLE_OPTION = "default_transaction_read_only=on"

GRANT_SQL = """
-- Non-admin discovery role. Repeat GRANT CONNECT/USAGE/SELECT per database.
CREATE ROLE discovery_ro;
GRANT CONNECT ON DATABASE {db} TO discovery_ro;
GRANT USAGE ON SCHEMA public TO discovery_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO discovery_ro;
GRANT SYSTEM VIEWACTIVITYREDACTED TO discovery_ro;
GRANT SYSTEM VIEWCLUSTERSETTING TO discovery_ro;
GRANT SYSTEM VIEWJOB TO discovery_ro;
ALTER ROLE discovery_ro SET default_transaction_read_only = on;
"""


def _privilege_names(rows: List[Dict[str, Any]]) -> List[str]:
    names = []
    for row in rows:
        value = (
            row.get("privilege_type")
            or row.get("privilege")
            or row.get("privileges")
            or row.get("permission")
        )
        if value is None:
            continue
        text = str(value).upper()
        for part in text.replace("{", "").replace("}", "").replace('"', "").split(","):
            token = part.strip()
            if token:
                names.append(token)
    return names


def probe_discovery_privileges(
    connection: Any,
    add_error: Optional[Callable] = None,
    add_warning: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Probe the current role. Missing privileges become gaps, not zeros."""
    add_error = add_error or (lambda *_a, **_k: None)
    add_warning = add_warning or (lambda *_a, **_k: None)
    gaps: List[Dict[str, Any]] = []
    system_grants: List[str] = []
    role_options = ""
    is_admin = False

    def fetch(sql: str) -> List[Dict[str, Any]]:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]

    def add_gap(privilege: str, message: str) -> None:
        add_error(message)
        if not any(g.get("privilege") == privilege for g in gaps):
            gaps.append(
                {
                    "type": "missing_privilege",
                    "privilege": privilege,
                    "severity": "high",
                    "description": message,
                }
            )

    try:
        rows = fetch("SHOW SYSTEM GRANTS FOR CURRENT_USER")
        system_grants = _privilege_names(rows)
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        add_warning(f"SHOW SYSTEM GRANTS failed: {exc}")

    have = {g.upper() for g in system_grants}

    try:
        admin_rows = fetch("SELECT crdb_internal.is_admin() AS is_admin")
        is_admin = bool(next(iter(admin_rows[0].values()))) if admin_rows else False
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    try:
        role_rows = fetch("SHOW ROLES")
        current = None
        try:
            current_rows = fetch("SELECT current_user")
            current = next(iter(current_rows[0].values())) if current_rows else None
        except Exception:
            current = None
        if current and str(current).lower() in ("root", "admin"):
            is_admin = True
        for row in role_rows:
            name = row.get("role_name") or row.get("username") or row.get("role")
            if current and name == current:
                role_options = str(row.get("options") or row.get("role_options") or "")
                members = row.get("member_of") or row.get("members") or []
                if isinstance(members, str):
                    members = [members]
                if "ADMIN" in role_options.upper() or any(
                    str(m).lower() == "admin" for m in members
                ):
                    is_admin = True
                break
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass

    jobs_status = "ok"
    jobs_error = None
    job_count = None
    try:
        job_rows = fetch("SELECT job_id FROM [SHOW JOBS]")
        job_count = len(job_rows)
        if job_count == 0 and not is_admin and "VIEWJOB" not in have:
            jobs_status = "couldn't read"
            jobs_error = (
                "SHOW JOBS returned 0 rows. Grant VIEWJOB; without it the "
                "result is silently empty."
            )
            add_gap("VIEWJOB", jobs_error)
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        jobs_status = "couldn't read"
        jobs_error = str(exc)
        add_gap("VIEWJOB", f"Couldn't read SHOW JOBS: {exc}")

    settings_ok = False
    try:
        fetch("SHOW CLUSTER SETTING kv.rangefeed.enabled")
        settings_ok = True
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        if not is_admin:
            add_gap(
                "VIEWCLUSTERSETTING",
                f"Couldn't read cluster settings ({exc}). Grant VIEWCLUSTERSETTING.",
            )

    activity_ok = False
    try:
        fetch("SELECT 1 FROM crdb_internal.gossip_nodes LIMIT 1")
        activity_ok = True
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        if not is_admin:
            add_gap(
                "VIEWACTIVITYREDACTED",
                (
                    f"Couldn't read cluster activity catalogs ({exc}). "
                    "Grant VIEWACTIVITYREDACTED. Table sizes from "
                    "tenant_span_stats still work on CockroachDB 25.2."
                ),
            )

    if not is_admin:
        if "VIEWJOB" not in have and jobs_status != "ok":
            add_gap(
                "VIEWJOB",
                "VIEWJOB is missing. SHOW JOBS silently returns 0 rows "
                "without it, so changefeeds and logical-replication jobs "
                "cannot be trusted as empty.",
            )
        if "VIEWCLUSTERSETTING" not in have and not settings_ok:
            add_gap(
                "VIEWCLUSTERSETTING",
                "VIEWCLUSTERSETTING is missing. Cluster settings such as "
                "kv.rangefeed.enabled cannot be read.",
            )
        if "VIEWACTIVITYREDACTED" not in have and not activity_ok:
            add_gap(
                "VIEWACTIVITYREDACTED",
                "VIEWACTIVITYREDACTED is missing. Some cluster activity "
                "catalogs cannot be read. Table sizes from tenant_span_stats "
                "still work on CockroachDB 25.2.",
            )

    read_only = "default_transaction_read_only" in role_options.lower()
    if not read_only:
        try:
            setting_rows = fetch("""
                SELECT settings
                FROM system.database_role_settings
                WHERE role_name = current_user
                """)
            for row in setting_rows:
                settings = row.get("settings") or []
                text = (
                    ",".join(str(s) for s in settings)
                    if isinstance(settings, (list, tuple))
                    else str(settings)
                )
                compact = text.replace(" ", "").lower()
                if "default_transaction_read_only=on" in compact:
                    read_only = True
                    break
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
    if not read_only:
        try:
            show_rows = fetch("SHOW default_transaction_read_only")
            if show_rows:
                value = str(next(iter(show_rows[0].values()))).lower()
                read_only = value in ("on", "true", "1")
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
    if not is_admin and not read_only:
        add_warning(
            "Set default_transaction_read_only=on on the discovery role "
            "(public allows CREATE by default). The setting lives in "
            "system.database_role_settings, not SHOW ROLES options."
        )

    return {
        "required_object_grants": list(REQUIRED_OBJECT_GRANTS),
        "required_system_grants": list(REQUIRED_SYSTEM_GRANTS),
        "system_grants": system_grants,
        "is_admin": is_admin,
        "jobs_status": jobs_status,
        "jobs_error": jobs_error,
        "job_count": job_count,
        "role_options": role_options,
        "default_transaction_read_only": read_only,
        "grant_sql": GRANT_SQL.strip(),
        "gaps": gaps,
    }


def looks_unsupported(exc: BaseException) -> bool:
    """v24.3 has no vector index, RLS, or CREATE POLICY. Treat as absent.

    Only the signals documented for those version gaps count. A syntax
    error in this tool's SQL, or a renamed catalog column, is a failed
    read, not "feature absent".
    """
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "unimplemented",
            "unknown function",
            "unknown column",
            "42883",
            "42703",
            "0a000",
        )
    )
