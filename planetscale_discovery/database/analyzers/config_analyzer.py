"""
PostgreSQL Configuration Analysis Module
Analyzes PostgreSQL server configuration, runtime settings, and version information.
"""

from typing import Dict, Any, List

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class ConfigAnalyzer(DatabaseAnalyzer):
    """Analyzes PostgreSQL configuration and version information."""

    def __init__(self, connection, config=None, logger=None):
        super().__init__(connection, config, logger)
        self._version_num = None

    def _get_server_version_num(self) -> int:
        """Get PostgreSQL version number (cached)."""
        if self._version_num is None:
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute("SHOW server_version_num")
                    self._version_num = int(cursor.fetchone()["server_version_num"])
            except Exception as e:
                self.logger.warning(f"Could not get server version: {e}")
                self._version_num = 0
        return self._version_num

    def analyze(self) -> Dict[str, Any]:
        """Run complete configuration analysis."""
        results = {
            "version_info": self._get_version_info(),
            "server_settings": self._get_server_settings(),
            "runtime_settings": self._get_runtime_settings(),
            "modified_settings": self._get_modified_settings(),
            "restart_required_settings": self._get_restart_required_settings(),
            "memory_settings": self._get_memory_settings(),
            "connection_settings": self._get_connection_settings(),
            "wal_settings": self._get_wal_settings(),
            "replication_settings": self._get_replication_settings(),
            "security_settings": self._get_security_settings(),
            "performance_settings": self._get_performance_settings(),
        }

        return results

    def _get_version_info(self) -> Dict[str, Any]:
        """Get PostgreSQL version and build information."""
        try:
            with self.connection.cursor() as cursor:
                # Get version string
                cursor.execute("SELECT version()")
                version_string = cursor.fetchone()["version"]

                # Get numeric version
                cursor.execute("SHOW server_version_num")
                version_num = cursor.fetchone()["server_version_num"]

                # Parse version components
                # PostgreSQL 10+ uses Major.Minor format, no separate patch level
                major_version = int(version_num) // 10000
                if major_version >= 10:
                    # Modern format: 170004 = version 17.4
                    minor_version = int(version_num) % 10000
                    patch_version = 0  # No patch level in PostgreSQL 10+
                else:
                    # Legacy format (pre-10): Major.Minor.Patch
                    minor_version = (int(version_num) % 10000) // 100
                    patch_version = int(version_num) % 100

                # Get compile-time settings
                compile_info = {}
                try:
                    cursor.execute("""
                        SELECT name, setting
                        FROM pg_settings
                        WHERE name IN (
                            'block_size', 'integer_datetimes', 'max_function_args',
                            'max_identifier_length', 'max_index_keys', 'segment_size',
                            'wal_block_size', 'wal_segment_size'
                        )
                    """)
                    compile_info = {
                        row["name"]: row["setting"] for row in cursor.fetchall()
                    }
                except Exception as e:
                    self.logger.warning(f"Could not get compile info: {e}")

                return {
                    "version_string": version_string,
                    "version_num": int(version_num),
                    "major_version": major_version,
                    "minor_version": minor_version,
                    "patch_version": patch_version,
                    "compile_info": compile_info,
                }

        except Exception as e:
            self.logger.error(f"Failed to get version info: {e}")
            return {"error": str(e)}

    def _get_server_settings(self) -> List[Dict[str, Any]]:
        """Get all PostgreSQL server settings."""
        try:
            # pending_restart column added in PostgreSQL 9.5
            version_num = self._get_server_version_num()
            has_pending_restart = version_num >= 90500

            with self.connection.cursor() as cursor:
                pending_restart_col = (
                    "pending_restart"
                    if has_pending_restart
                    else "false as pending_restart"
                )
                cursor.execute(f"""
                    SELECT
                        name,
                        setting,
                        unit,
                        category,
                        short_desc,
                        extra_desc,
                        context,
                        vartype,
                        source,
                        min_val,
                        max_val,
                        enumvals,
                        boot_val,
                        reset_val,
                        sourcefile,
                        sourceline,
                        {pending_restart_col}
                    FROM pg_settings
                    ORDER BY category, name
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get server settings: {e}")
            return []

    def _get_runtime_settings(self) -> Dict[str, Any]:
        """Get current runtime settings vs configured values."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        name,
                        setting,
                        boot_val,
                        reset_val,
                        source,
                        sourcefile,
                        sourceline,
                        context
                    FROM pg_settings
                    WHERE setting != boot_val OR source != 'default'
                    ORDER BY name
                """)

                runtime_changes = [dict(row) for row in cursor.fetchall()]

                # Get session-level settings
                cursor.execute(
                    "SELECT name, setting FROM pg_settings WHERE source = 'session'"
                )
                session_settings = {
                    row["name"]: row["setting"] for row in cursor.fetchall()
                }

                return {
                    "runtime_changes": runtime_changes,
                    "session_settings": session_settings,
                }

        except Exception as e:
            self.logger.error(f"Failed to get runtime settings: {e}")
            return {"error": str(e)}

    def _get_modified_settings(self) -> List[Dict[str, Any]]:
        """Get settings that differ from default values."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        name,
                        setting,
                        boot_val,
                        unit,
                        source,
                        sourcefile,
                        sourceline
                    FROM pg_settings
                    WHERE source != 'default'
                    ORDER BY name
                """)

                # Handle potential encoding issues in sourcefile column
                # Some managed PostgreSQL services have invalid UTF-8 bytes in sourcefile
                results = []
                try:
                    rows = cursor.fetchall()
                except UnicodeDecodeError:
                    # If we hit encoding errors, query without sourcefile which has the issue
                    self.logger.warning(
                        "Encoding error reading pg_settings, querying without sourcefile column"
                    )
                    cursor.execute("""
                        SELECT
                            name,
                            setting,
                            boot_val,
                            unit,
                            source,
                            NULL as sourcefile,
                            sourceline
                        FROM pg_settings
                        WHERE source != 'default'
                        ORDER BY name
                    """)
                    rows = cursor.fetchall()

                for row in rows:
                    row_dict = dict(row)
                    # Ensure sourcefile is properly decoded or set to None if it fails
                    if row_dict.get("sourcefile"):
                        try:
                            # If it's bytes, try to decode with error handling
                            if isinstance(row_dict["sourcefile"], bytes):
                                row_dict["sourcefile"] = row_dict["sourcefile"].decode(
                                    "utf-8", errors="replace"
                                )
                        except Exception:
                            row_dict["sourcefile"] = None
                    results.append(row_dict)

                return results

        except Exception as e:
            self.logger.error(f"Failed to get modified settings: {e}")
            return []

    def _get_restart_required_settings(self) -> List[Dict[str, Any]]:
        """Get settings that require restart to take effect."""
        try:
            # pending_restart column added in PostgreSQL 9.5
            version_num = self._get_server_version_num()
            has_pending_restart = version_num >= 90500

            with self.connection.cursor() as cursor:
                if has_pending_restart:
                    cursor.execute("""
                        SELECT
                            name,
                            setting,
                            context,
                            pending_restart
                        FROM pg_settings
                        WHERE context = 'postmaster' OR pending_restart = true
                        ORDER BY name
                    """)
                else:
                    # For older versions, only check context
                    cursor.execute("""
                        SELECT
                            name,
                            setting,
                            context,
                            false as pending_restart
                        FROM pg_settings
                        WHERE context = 'postmaster'
                        ORDER BY name
                    """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get restart-required settings: {e}")
            return []

    def _get_memory_settings(self) -> Dict[str, Any]:
        """Get memory-related configuration settings."""
        memory_params = [
            "shared_buffers",
            "work_mem",
            "maintenance_work_mem",
            "effective_cache_size",
            "temp_buffers",
            "max_connections",
            "shared_preload_libraries",
            "huge_pages",
            "temp_file_limit",
        ]

        try:
            with self.connection.cursor() as cursor:
                placeholders = ",".join(["%s"] * len(memory_params))
                cursor.execute(
                    f"""
                    SELECT name, setting, unit, source, context
                    FROM pg_settings
                    WHERE name IN ({placeholders})
                """,
                    memory_params,
                )

                settings = {row["name"]: dict(row) for row in cursor.fetchall()}

                # Calculate memory usage in bytes for easier comparison
                memory_in_bytes = {}
                for param in memory_params:
                    if param in settings:
                        setting = settings[param]
                        value = setting["setting"]
                        unit = setting.get("unit", "")

                        if unit == "8kB":
                            memory_in_bytes[param] = int(value) * 8 * 1024
                        elif unit == "kB":
                            memory_in_bytes[param] = int(value) * 1024
                        elif unit == "MB":
                            memory_in_bytes[param] = int(value) * 1024 * 1024
                        else:
                            memory_in_bytes[param] = value

                return {"settings": settings, "memory_in_bytes": memory_in_bytes}

        except Exception as e:
            self.logger.error(f"Failed to get memory settings: {e}")
            return {"error": str(e)}

    def _get_connection_settings(self) -> Dict[str, Any]:
        """Get connection and authentication related settings."""
        connection_params = [
            "max_connections",
            "superuser_reserved_connections",
            "listen_addresses",
            "port",
            "max_prepared_transactions",
            "authentication_timeout",
            "password_encryption",
            "db_user_namespace",
            "krb_server_keyfile",
            "krb_caseins_users",
            "ssl",
            "ssl_ca_file",
            "ssl_cert_file",
            "ssl_key_file",
            "tcp_keepalives_idle",
            "tcp_keepalives_interval",
            "tcp_keepalives_count",
        ]

        try:
            with self.connection.cursor() as cursor:
                placeholders = ",".join(["%s"] * len(connection_params))
                cursor.execute(
                    f"""
                    SELECT name, setting, unit, source, context
                    FROM pg_settings
                    WHERE name IN ({placeholders})
                """,
                    connection_params,
                )

                return {row["name"]: dict(row) for row in cursor.fetchall()}

        except Exception as e:
            self.logger.error(f"Failed to get connection settings: {e}")
            return {"error": str(e)}

    def _get_wal_settings(self) -> Dict[str, Any]:
        """Get Write-Ahead Logging related settings."""
        wal_params = [
            "wal_level",
            "fsync",
            "synchronous_commit",
            "wal_sync_method",
            "full_page_writes",
            "wal_compression",
            "wal_log_hints",
            "wal_buffers",
            "wal_writer_delay",
            "wal_writer_flush_after",
            "checkpoint_segments",
            "checkpoint_completion_target",
            "checkpoint_timeout",
            "checkpoint_warning",
            "max_wal_size",
            "min_wal_size",
            "archive_mode",
            "archive_command",
        ]

        try:
            with self.connection.cursor() as cursor:
                placeholders = ",".join(["%s"] * len(wal_params))
                cursor.execute(
                    f"""
                    SELECT name, setting, unit, source, context
                    FROM pg_settings
                    WHERE name IN ({placeholders})
                """,
                    wal_params,
                )

                return {row["name"]: dict(row) for row in cursor.fetchall()}

        except Exception as e:
            self.logger.error(f"Failed to get WAL settings: {e}")
            return {"error": str(e)}

    def _get_replication_settings(self) -> Dict[str, Any]:
        """Get replication related settings."""
        replication_params = [
            "max_wal_senders",
            "max_replication_slots",
            "wal_keep_segments",
            "wal_keep_size",
            "wal_sender_timeout",
            "max_standby_archive_delay",
            "max_standby_streaming_delay",
            "wal_receiver_status_interval",
            "hot_standby",
            "hot_standby_feedback",
            "wal_retrieve_retry_interval",
            "max_logical_replication_workers",
            "max_sync_workers_per_subscription",
        ]

        try:
            with self.connection.cursor() as cursor:
                placeholders = ",".join(["%s"] * len(replication_params))
                cursor.execute(
                    f"""
                    SELECT name, setting, unit, source, context
                    FROM pg_settings
                    WHERE name IN ({placeholders})
                """,
                    replication_params,
                )

                return {row["name"]: dict(row) for row in cursor.fetchall()}

        except Exception as e:
            self.logger.error(f"Failed to get replication settings: {e}")
            return {"error": str(e)}

    def _get_security_settings(self) -> Dict[str, Any]:
        """Get security related settings."""
        security_params = [
            "ssl",
            "ssl_cert_file",
            "ssl_key_file",
            "ssl_ca_file",
            "password_encryption",
            "row_security",
            "log_connections",
            "log_disconnections",
            "log_hostname",
            "log_statement",
            "log_min_duration_statement",
            "log_checkpoints",
            "log_lock_waits",
        ]

        try:
            with self.connection.cursor() as cursor:
                placeholders = ",".join(["%s"] * len(security_params))
                cursor.execute(
                    f"""
                    SELECT name, setting, unit, source, context
                    FROM pg_settings
                    WHERE name IN ({placeholders})
                """,
                    security_params,
                )

                return {row["name"]: dict(row) for row in cursor.fetchall()}

        except Exception as e:
            self.logger.error(f"Failed to get security settings: {e}")
            return {"error": str(e)}

    def _get_performance_settings(self) -> Dict[str, Any]:
        """Get performance tuning related settings."""
        performance_params = [
            "shared_buffers",
            "effective_cache_size",
            "work_mem",
            "maintenance_work_mem",
            "random_page_cost",
            "seq_page_cost",
            "cpu_tuple_cost",
            "cpu_index_tuple_cost",
            "cpu_operator_cost",
            "effective_io_concurrency",
            "max_worker_processes",
            "max_parallel_workers",
            "max_parallel_workers_per_gather",
            "parallel_tuple_cost",
            "parallel_setup_cost",
            "default_statistics_target",
            "constraint_exclusion",
            "cursor_tuple_fraction",
            "from_collapse_limit",
            "join_collapse_limit",
            "geqo",
            "geqo_threshold",
            "geqo_effort",
            "geqo_pool_size",
            "geqo_generations",
        ]

        try:
            with self.connection.cursor() as cursor:
                placeholders = ",".join(["%s"] * len(performance_params))
                cursor.execute(
                    f"""
                    SELECT name, setting, unit, source, context
                    FROM pg_settings
                    WHERE name IN ({placeholders})
                """,
                    performance_params,
                )

                return {row["name"]: dict(row) for row in cursor.fetchall()}

        except Exception as e:
            self.logger.error(f"Failed to get performance settings: {e}")
            return {"error": str(e)}
