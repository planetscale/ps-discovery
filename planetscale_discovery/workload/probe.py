"""Report whether workload capture can work on this server, and why not.

The failures are quiet: pg_stat_statements can be installed but not preloaded,
in which case it collects nothing forever, and without pg_monitor the view
returns masked text and the sample is silently biased.
"""

from typing import Any, Dict, List, Optional

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer
from planetscale_discovery.workload.collect import (
    PGSS_VIEW,
    pgss_relation_query,
    quote_qualified,
)

# What to do about each state, keyed by the state itself.
REMEDIATION = {
    "absent": (
        "pg_stat_statements is not available on this server. Install the "
        "PostgreSQL contrib package, add it to shared_preload_libraries, "
        "restart, then run: CREATE EXTENSION pg_stat_statements;"
    ),
    "not_installed": (
        "The library is loaded, so only the extension is missing. Run: "
        "CREATE EXTENSION pg_stat_statements;"
    ),
    "not_installed_not_preloaded": (
        "Two steps, in this order: add pg_stat_statements to "
        "shared_preload_libraries and restart the server, then run "
        "CREATE EXTENSION pg_stat_statements. Creating the extension alone "
        "collects nothing. On RDS and Aurora the library is normally preloaded "
        "already, so usually only the second step is needed."
    ),
    "not_preloaded": (
        "The extension exists but the library is not loaded, so the view stays "
        "empty. Add pg_stat_statements to shared_preload_libraries and restart. "
        "No change to the extension itself is needed."
    ),
    "unreadable": (
        "pg_stat_statements exists but this role cannot read it. Grant "
        "pg_monitor, or use a role that already has it."
    ),
    "tracking_disabled": (
        "pg_stat_statements is installed and readable, and recording nothing: "
        "pg_stat_statements.track is set to 'none'. Set it to 'top' to record "
        "statements, or 'all' to also record statements run inside functions "
        "and procedures. It takes effect without a restart, but the view starts "
        "empty, so allow traffic to accumulate before capturing."
    ),
}

# The pg_stat_statements.max default. At or below it, eviction is worth naming.
DEFAULT_PGSS_MAX = 5000

# pg_stat_statements is not a trusted extension, so CREATE EXTENSION needs superuser.
NEEDS_SUPERUSER = (
    " Note that pg_stat_statements is not a trusted extension, so "
    "CREATE EXTENSION requires a superuser. The connected role is not one, so "
    "this needs a superuser or your provider's control plane -- a parameter "
    "group on RDS and Aurora, a database flag on Cloud SQL and AlloyDB."
)


class CapabilityProbe(DatabaseAnalyzer):
    """One read of what this server can and cannot supply."""

    def analyze(self) -> Dict[str, Any]:
        return self.run()

    def run(self) -> Dict[str, Any]:
        identity = self._one(
            "SELECT current_database() AS database, current_user AS role,"
            " current_setting('server_version_num') AS version_num,"
            " current_setting('is_superuser') = 'on' AS is_superuser,"
            " pg_is_in_recovery() AS is_replica,"
            " pg_has_role(current_user, 'pg_monitor', 'USAGE') AS has_pg_monitor,"
            " pg_has_role(current_user, 'pg_read_all_stats', 'USAGE')"
            "   AS has_read_all_stats"
        )
        preload = self._one(
            "SELECT current_setting('shared_preload_libraries', true) AS libraries"
        )
        extension = self._one(
            "SELECT (SELECT extversion FROM pg_extension"
            "  WHERE extname = 'pg_stat_statements') AS installed_version,"
            " EXISTS (SELECT 1 FROM pg_available_extensions"
            "  WHERE name = 'pg_stat_statements') AS available"
        )

        libraries = str((preload or {}).get("libraries") or "")
        preloaded = "pg_stat_statements" in libraries
        installed = bool((extension or {}).get("installed_version"))
        available = bool((extension or {}).get("available"))
        readable = self._pgss_readable() if installed else False

        # track = none means the extension is installed, readable, and
        # recording nothing. The view stays empty however long the capture runs,
        # so it is a capability failure rather than a limitation.
        tracking_disabled = self._setting("pg_stat_statements.track") == "none"
        state = (
            "tracking_disabled"
            if installed and readable and tracking_disabled
            else _classify(installed, available, preloaded, readable)
        )
        remediation = REMEDIATION.get(state, "")
        if state != "ok" and not (identity or {}).get("is_superuser"):
            if "CREATE EXTENSION" in remediation:
                remediation += NEEDS_SUPERUSER

        gaps = self._gaps(identity or {})
        return {
            "server": {
                "database": (identity or {}).get("database"),
                "role": (identity or {}).get("role"),
                "version_num": _int((identity or {}).get("version_num")),
                "is_superuser": bool((identity or {}).get("is_superuser")),
                "is_replica": bool((identity or {}).get("is_replica")),
            },
            "pg_stat_statements": {
                "state": state,
                "installed": installed,
                "available": available,
                "preloaded": preloaded,
                "readable": readable,
                "installed_version": (extension or {}).get("installed_version"),
                "remediation": remediation,
            },
            # Relation counters need no extension, so this tier is available whenever.
            "can_collect_statements": state == "ok" and not tracking_disabled,
            "tracking_disabled": tracking_disabled,
            "can_collect_relations": bool(
                (identity or {}).get("has_pg_monitor")
                or (identity or {}).get("has_read_all_stats")
                or (identity or {}).get("is_superuser")
            ),
            "gaps": gaps,
        }

    def _gaps(self, identity: Dict[str, Any]) -> List[Dict[str, str]]:
        """Non-blocking limits, so a reader knows what the data cannot show."""
        gaps: List[Dict[str, str]] = []

        if not (
            identity.get("has_pg_monitor")
            or identity.get("has_read_all_stats")
            or identity.get("is_superuser")
        ):
            gaps.append(
                {
                    # Not the fatal E106. That one is "no statistics at all";
                    # this one is "statistics readable, other roles' text
                    # hidden", which is a capture that continues.
                    "code": "statement_text_may_be_masked",
                    "detail": (
                        "without pg_monitor or pg_read_all_stats, statement text "
                        "for other roles is masked, so the sample is biased "
                        "toward statements run by this role"
                    ),
                }
            )

        if identity.get("is_replica"):
            gaps.append(
                {
                    "code": "replica_target",
                    "detail": (
                        "this server is a standby, so its counters reflect only "
                        "what executed here. Capture the primary to see the "
                        "write workload"
                    ),
                }
            )

        # 'none' is not a lesser 'top'. It records nothing at all, so the view
        # stays empty and a capture would measure an idle database. That is a
        # capability failure, reported separately and fatally.
        track = self._setting("pg_stat_statements.track")
        if track == "top":
            gaps.append(
                {
                    "code": "nested_statements_not_tracked",
                    "detail": (
                        "pg_stat_statements.track is 'top', so a statement run "
                        "inside a function or procedure is counted against the "
                        "call rather than recorded on its own"
                    ),
                }
            )

        # Eviction is silent and unrecoverable: a statement dropped below max
        # before the first snapshot is one the capture never sees at all.
        tracked_max = _int(self._setting("pg_stat_statements.max"))
        if tracked_max and tracked_max <= DEFAULT_PGSS_MAX:
            gaps.append(
                {
                    "code": "statement_eviction_possible",
                    "detail": (
                        f"pg_stat_statements.max is {tracked_max}. On a busy "
                        "database PostgreSQL evicts the least-used statements "
                        "when the table is full, and an evicted statement is "
                        "one this capture never sees. Raise it to widen the "
                        "capture"
                    ),
                }
            )

        if self._setting("track_io_timing") == "off":
            gaps.append(
                {
                    "code": "io_timing_off",
                    "detail": (
                        "track_io_timing is off, so block read and write times "
                        "are reported as zero rather than measured"
                    ),
                }
            )

        gaps.append(
            {
                "code": "transaction_boundaries_invisible",
                "detail": (
                    "pg_stat_statements records statements, not transactions, so "
                    "which tables are written together in one transaction "
                    "cannot be determined from this data"
                ),
            }
        )
        return gaps

    def _pgss_readable(self) -> bool:
        # Schema-qualified, because search_path is pinned to pg_catalog and the
        # extension is installed into public by default. An unqualified name
        # reports "unreadable" for a view the role can read perfectly well.
        relation = self._pgss_relation()
        try:
            self._one(f"SELECT 1 FROM {relation} LIMIT 1")
            return True
        except Exception:
            # The failure aborts the transaction, so every later probe query
            # would fail too. This read is expected to fail; recover from it.
            try:
                self.connection.rollback()
            except Exception:
                pass
            return False

    def _pgss_relation(self) -> str:
        """The extension's view, schema-qualified where the catalog says so.

        Falls back to the bare name when the schema cannot be read, so a server
        that answers this query oddly still gets the pre-existing behaviour
        rather than being reported as having no extension.
        """
        try:
            row = self._one(pgss_relation_query())
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                pass
            return PGSS_VIEW
        schema = (row or {}).get("nspname")
        return quote_qualified(str(schema), PGSS_VIEW) if schema else PGSS_VIEW

    def _setting(self, name: str) -> Optional[str]:
        # current_setting with missing_ok, so an absent GUC is not an error.
        row = self._one("SELECT current_setting(%s, true) AS value", (name,))
        value = (row or {}).get("value")
        return None if value is None else str(value)

    def _one(self, sql: str, params=None) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params) if params else cursor.execute(sql)
            row = cursor.fetchone()
        return dict(row) if row else None


def _classify(installed, available, preloaded, readable) -> str:
    """The single state that decides which remediation applies."""
    if installed and preloaded and readable:
        return "ok"
    if installed and not preloaded:
        return "not_preloaded"
    if installed and not readable:
        return "unreadable"
    if not installed and available and preloaded:
        return "not_installed"
    if not installed and available:
        return "not_installed_not_preloaded"
    return "absent"


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
