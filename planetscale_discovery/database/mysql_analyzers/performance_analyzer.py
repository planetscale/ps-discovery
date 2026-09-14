"""
MySQL Performance Analysis Module
Collects status counters (two snapshots) and processlist summary.
"""

import time
from typing import Dict, Any

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class MySQLPerformanceAnalyzer(DatabaseAnalyzer):
    """Analyzes MySQL performance via SHOW GLOBAL STATUS and SHOW PROCESSLIST."""

    # SHOW GLOBAL STATUS and SHOW PROCESSLIST are separate round trips, so a
    # busy server can legitimately gain or lose a few connections in between.
    # Only a gap wider than this indicates the processlist itself is filtered.
    PROCESSLIST_CHURN_TOLERANCE = 5

    def __init__(self, connection, config=None, logger=None):
        super().__init__(connection, config or {}, logger)
        self.sleep_interval = (config or {}).get("status_sleep_interval", 1)

    def analyze(self) -> Dict[str, Any]:
        # The status snapshot is reused to sanity-check the processlist below,
        # so it must be collected first. No extra round trip is needed.
        status_counters = self._get_status_counters()
        results = {
            "status_counters": status_counters,
            "processlist_summary": self._get_processlist_summary(status_counters),
            "lock_analysis": self._get_lock_analysis(),
        }
        return results

    @staticmethod
    def _threads_connected(status_counters: Any) -> Any:
        """Pull Threads_connected out of an already-collected status snapshot."""
        if not isinstance(status_counters, dict):
            return None
        entry = (status_counters.get("counters") or {}).get("Threads_connected")
        if isinstance(entry, dict):
            return entry.get("current")
        return None

    def _collect_status(self) -> Dict[str, str]:
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW GLOBAL STATUS")
            rows = cursor.fetchall()
            cursor.close()
            return {row[0]: row[1] for row in rows}
        except Exception as e:
            self.add_error(f"Failed to collect status: {e}", e)
            return {}

    def _get_status_counters(self) -> Dict[str, Any]:
        """Collect two snapshots of SHOW GLOBAL STATUS with a sleep interval."""
        try:
            status1 = self._collect_status()
            if not status1:
                return {"error": "Failed to collect first status snapshot"}

            uptime_str = status1.get("Uptime", "0")
            uptime = int(uptime_str) if uptime_str.isdigit() else 0
            uptime_days = uptime / 86400.0 if uptime > 0 else 1.0

            # Sleep and collect second snapshot for per-second rates
            time.sleep(self.sleep_interval)
            status2 = self._collect_status()

            # Build counters with per-day and per-second rates
            counters = {}
            for key, val_str in status1.items():
                if not val_str.isdigit():
                    continue
                val = int(val_str)
                entry = {
                    "current": val,
                    "per_day": round(val / uptime_days) if uptime_days > 0 else 0,
                }

                # Calculate per-second from delta between snapshots
                if status2 and key in status2 and status2[key].isdigit():
                    val2 = int(status2[key])
                    delta = val2 - val
                    if delta >= 0 and self.sleep_interval > 0:
                        entry["per_second"] = round(delta / self.sleep_interval, 2)

                counters[key] = entry

            # Add non-numeric status values (e.g., Ssl_cipher)
            non_numeric = {}
            for key, val_str in status1.items():
                if not val_str.isdigit() and val_str:
                    non_numeric[key] = val_str

            return {
                "uptime_seconds": uptime,
                "counters": counters,
                "non_numeric": non_numeric,
            }
        except Exception as e:
            self.add_error(f"Failed to get status counters: {e}", e)
            return {"error": str(e)}

    def _get_processlist_summary(self, status_counters: Any = None) -> Dict[str, Any]:
        """Collect SHOW FULL PROCESSLIST and summarize by command/user/host/db/state."""
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW FULL PROCESSLIST")
            columns = (
                [desc[0] for desc in cursor.description] if cursor.description else []
            )
            rows = cursor.fetchall()
            cursor.close()

            processes = [dict(zip(columns, row)) for row in rows]

            by_command: Dict[str, int] = {}
            by_user: Dict[str, int] = {}
            by_host: Dict[str, int] = {}
            by_db: Dict[str, int] = {}
            by_state: Dict[str, int] = {}

            for proc in processes:
                cmd = str(proc.get("Command", "Unknown"))
                by_command[cmd] = by_command.get(cmd, 0) + 1

                user = str(proc.get("User", "Unknown"))
                by_user[user] = by_user.get(user, 0) + 1

                host = str(proc.get("Host", "Unknown"))
                # Strip port from host
                if ":" in host:
                    host = host.rsplit(":", 1)[0]
                by_host[host] = by_host.get(host, 0) + 1

                db = proc.get("db") or proc.get("DB") or "NULL"
                db = str(db) if db is not None else "NULL"
                by_db[db] = by_db.get(db, 0) + 1

                state = proc.get("State") or proc.get("state") or ""
                state = str(state) if state else "(no state)"
                by_state[state] = by_state.get(state, 0) + 1

            summary = {
                "total_processes": len(processes),
                "by_command": dict(sorted(by_command.items(), key=lambda x: -x[1])),
                "by_user": dict(sorted(by_user.items(), key=lambda x: -x[1])),
                "by_host": dict(sorted(by_host.items(), key=lambda x: -x[1])),
                "by_db": dict(sorted(by_db.items(), key=lambda x: -x[1])),
                "by_state": dict(sorted(by_state.items(), key=lambda x: -x[1])),
            }

            # Without PROCESS, SHOW PROCESSLIST returns only the current user's
            # own threads and raises no error, so these counts silently describe
            # one account instead of the server. Threads_connected comes from
            # SHOW GLOBAL STATUS, which needs no privilege, and gives us a
            # server-wide number to check them against.
            threads_connected = self._threads_connected(status_counters)
            if threads_connected is not None:
                summary["threads_connected"] = threads_connected
                if (
                    threads_connected
                    > len(processes) + self.PROCESSLIST_CHURN_TOLERANCE
                ):
                    summary["processlist_truncated"] = True
                    self.add_warning(
                        f"Processlist shows {len(processes)} of "
                        f"{threads_connected} connected threads; the summary "
                        "covers only the current user's own connections. "
                        "Grant PROCESS for a server-wide view."
                    )
                else:
                    summary["processlist_truncated"] = False

            return summary
        except Exception as e:
            self.add_error(f"Failed to get processlist summary: {e}", e)
            return {"error": str(e)}

    def _get_lock_analysis(self) -> Dict[str, Any]:
        """Collect InnoDB lock/deadlock stats from status counters and performance_schema."""
        result: Dict[str, Any] = {}

        # Extract lock-related counters from SHOW GLOBAL STATUS
        try:
            cursor = self.connection.cursor()
            lock_vars = [
                "Innodb_row_lock_waits",
                "Innodb_row_lock_time",
                "Innodb_row_lock_time_avg",
                "Innodb_row_lock_time_max",
                "Innodb_row_lock_current_waits",
                "Innodb_deadlocks",
                "Table_locks_immediate",
                "Table_locks_waited",
            ]
            placeholders = ", ".join(f"'{v}'" for v in lock_vars)
            cursor.execute(
                f"SHOW GLOBAL STATUS WHERE Variable_name IN ({placeholders})"
            )
            rows = cursor.fetchall()
            cursor.close()

            counters = {}
            for row in rows:
                val = row[1]
                counters[row[0]] = int(val) if val.isdigit() else val
            result["counters"] = counters
        except Exception as e:
            self.add_error(f"Failed to get lock status counters: {e}", e)
            result["counters"] = {}

        # Try performance_schema.data_lock_waits for current lock waits (MySQL 8.0+)
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT COUNT(*) AS active_lock_waits
                FROM performance_schema.data_lock_waits
                """)
            row = cursor.fetchone()
            cursor.close()
            result["active_lock_waits"] = int(row[0]) if row else 0
        except Exception:
            # performance_schema.data_lock_waits may not exist or be accessible
            result["active_lock_waits"] = None

        # Try to get recent deadlock info from SHOW ENGINE INNODB STATUS
        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW ENGINE INNODB STATUS")
            row = cursor.fetchone()
            cursor.close()
            if row:
                status_text = str(row[2]) if len(row) > 2 else ""
                result["has_recent_deadlock"] = (
                    "LATEST DETECTED DEADLOCK" in status_text
                )
            else:
                result["has_recent_deadlock"] = None
        except Exception:
            # May not have PROCESS privilege
            result["has_recent_deadlock"] = None

        return result
