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
        """Extract binlog retention settings from global variables."""
        try:
            retention: Dict[str, Any] = {}
            cursor = self.connection.cursor()

            # MySQL 8.0+ uses binlog_expire_logs_seconds (default 2592000 = 30 days)
            cursor.execute("SELECT @@binlog_expire_logs_seconds AS expire_seconds")
            row = cursor.fetchone()
            if row and row[0] is not None:
                secs = int(row[0])
                retention["expire_logs_seconds"] = secs
                if secs > 0:
                    retention["retention_hours"] = round(secs / 3600, 1)

            # Older MySQL uses expire_logs_days (deprecated in 8.0, removed in 8.4).
            # A missing variable raises — log at debug so we don't mask other errors
            # (permission, connection) silently, but don't surface as a warning since
            # `binlog_expire_logs_seconds` above is the modern replacement.
            try:
                cursor.execute("SELECT @@expire_logs_days AS expire_days")
                row = cursor.fetchone()
                if row and row[0] is not None:
                    days = int(row[0])
                    retention["expire_logs_days"] = days
                    # If seconds didn't set retention, derive from days
                    if "retention_hours" not in retention and days > 0:
                        retention["retention_hours"] = days * 24.0
            except Exception as e:
                self.logger.debug(f"Could not read @@expire_logs_days: {e}")

            cursor.close()

            # Total binlog size from binary_logs (already collected, but handy here)
            if not retention.get("retention_hours"):
                retention["retention_hours"] = 0
                retention["warning"] = (
                    "No binlog retention configured or binlog disabled"
                )

            return retention
        except Exception as e:
            self.add_error(f"Failed to get binlog retention: {e}", e)
            return {}

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
