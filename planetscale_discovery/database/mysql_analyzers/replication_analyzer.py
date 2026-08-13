"""
MySQL Replication Analysis Module
Collects replica status, binary log info, and binlog configuration.
"""

from typing import Dict, Any, List

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class MySQLReplicationAnalyzer(DatabaseAnalyzer):
    """Analyzes MySQL replication configuration and status."""

    def analyze(self) -> Dict[str, Any]:
        results = {
            "replica_status": self._get_replica_status(),
            "binary_logs": self._get_binary_logs(),
            "binary_log_status": self._get_binary_log_status(),
            "binlog_format": self._get_binlog_format(),
            "binlog_retention": self._get_binlog_retention(),
        }
        return results

    def _get_replica_status(self) -> Dict[str, Any]:
        """Get replica status. Try modern syntax first, fall back for older versions."""
        # Try SHOW REPLICA STATUS (MySQL 8.0.22+), then SHOW SLAVE STATUS
        for query in ["SHOW REPLICA STATUS", "SHOW SLAVE STATUS"]:
            try:
                cursor = self.connection.cursor()
                cursor.execute(query)
                if not cursor.description:
                    cursor.close()
                    continue
                columns = [desc[0] for desc in cursor.description]
                row = cursor.fetchone()
                cursor.close()
                if row:
                    status = {}
                    for i, col in enumerate(columns):
                        val = row[i]
                        status[col] = str(val) if val is not None else None
                    return status
                return {}
            except Exception:
                continue

        return {}

    def _get_binary_logs(self) -> List[Dict[str, Any]]:
        """Get binary log files. Try modern syntax first."""
        for query in ["SHOW BINARY LOGS", "SHOW MASTER LOGS"]:
            try:
                cursor = self.connection.cursor()
                cursor.execute(query)
                if not cursor.description:
                    cursor.close()
                    continue
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                cursor.close()
                logs = []
                for row in rows:
                    entry = dict(zip(columns, row))
                    # Normalize column names
                    logs.append(
                        {
                            "file": entry.get("Log_name", entry.get("File", "")),
                            "file_size": int(entry.get("File_size", 0)),
                        }
                    )
                return logs
            except Exception:
                continue

        return []

    def _get_binary_log_status(self) -> Dict[str, Any]:
        """Get current binary log position. Try modern syntax first."""
        for query in ["SHOW BINARY LOG STATUS", "SHOW MASTER STATUS"]:
            try:
                cursor = self.connection.cursor()
                cursor.execute(query)
                if not cursor.description:
                    cursor.close()
                    continue
                columns = [desc[0] for desc in cursor.description]
                row = cursor.fetchone()
                cursor.close()
                if row:
                    return {
                        col: (str(row[i]) if row[i] is not None else None)
                        for i, col in enumerate(columns)
                    }
                return {}
            except Exception:
                continue

        return {}

    def _get_binlog_retention(self) -> Dict[str, Any]:
        """Extract binlog retention settings from global variables.

        Each variable gets its own try. No server has both: binlog_expire_logs_seconds
        is 8.0+, and expire_logs_days was removed in 8.4 -- so a missing variable
        must not skip the read of the other one.
        """
        retention: Dict[str, Any] = {}
        failures: List[str] = []

        # MySQL 8.0+ uses binlog_expire_logs_seconds (default 2592000 = 30 days).
        # Absent before 8.0 -- log at debug, then fall through to expire_logs_days.
        try:
            secs = self._select_scalar("SELECT @@binlog_expire_logs_seconds")
            if secs is not None:
                secs = int(secs)
                retention["expire_logs_seconds"] = secs
                if secs > 0:
                    retention["retention_hours"] = round(secs / 3600, 1)
        except Exception as e:
            self.logger.debug(f"Could not read @@binlog_expire_logs_seconds: {e}")
            failures.append(f"binlog_expire_logs_seconds: {e}")

        # Older MySQL uses expire_logs_days (deprecated in 8.0, removed in 8.4).
        # A missing variable raises — log at debug so we don't mask other errors
        # (permission, connection) silently, but don't surface as a warning since
        # `binlog_expire_logs_seconds` above is the modern replacement.
        try:
            days = self._select_scalar("SELECT @@expire_logs_days")
            if days is not None:
                days = int(days)
                retention["expire_logs_days"] = days
                # If seconds didn't set retention, derive from days
                if "retention_hours" not in retention and days > 0:
                    retention["retention_hours"] = days * 24.0
        except Exception as e:
            self.logger.debug(f"Could not read @@expire_logs_days: {e}")
            failures.append(f"expire_logs_days: {e}")

        # Neither variable readable: report that, rather than implying no retention.
        if not retention:
            self.add_error(
                "Failed to get binlog retention, no retention variable could be "
                f"read ({'; '.join(failures)})"
            )
            return {}

        if not retention.get("retention_hours"):
            retention["retention_hours"] = 0
            retention["warning"] = "No binlog retention configured or binlog disabled"

        return retention

    def _select_scalar(self, query: str) -> Any:
        """Run a single-value SELECT on its own cursor and return that value."""
        cursor = self.connection.cursor()
        try:
            cursor.execute(query)
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            cursor.close()

    def _get_binlog_format(self) -> str:
        """Get binlog format from variables."""
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT @@binlog_format")
            row = cursor.fetchone()
            cursor.close()
            return row[0] if row else ""
        except Exception:
            return ""
