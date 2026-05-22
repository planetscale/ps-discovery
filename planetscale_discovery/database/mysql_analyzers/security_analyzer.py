"""
MySQL Security Analysis Module
Security posture analysis for migration scoping. Collects aggregate user
and grant counts, auth plugin distribution, SSL configuration, password
policy, and the connected user's grants. Does not read password hashes.
"""

from typing import Dict, Any

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class MySQLSecurityAnalyzer(DatabaseAnalyzer):
    """Analyzes MySQL security configuration at an aggregate level."""

    def analyze(self) -> Dict[str, Any]:
        results = {
            "user_summary": self._get_user_summary(),
            "auth_plugins": self._get_auth_plugin_distribution(),
            "ssl_status": self._get_ssl_status(),
            "grant_summary": self._get_grant_summary(),
            "password_policy": self._get_password_policy(),
            "current_user_grants": self._get_current_user_grants(),
        }
        return results

    def _get_user_summary(self) -> Dict[str, Any]:
        """Get aggregate user counts without exposing individual usernames."""
        try:
            cursor = self.connection.cursor()

            # Total user count
            cursor.execute("SELECT COUNT(*) FROM mysql.user")
            total = cursor.fetchone()[0]

            # Users with no password
            cursor.execute(
                "SELECT COUNT(*) FROM mysql.user "
                "WHERE authentication_string = '' OR authentication_string IS NULL"
            )
            no_password = cursor.fetchone()[0]

            # Users that can connect from any host
            cursor.execute("SELECT COUNT(*) FROM mysql.user WHERE Host = '%'")
            wildcard_host = cursor.fetchone()[0]

            # Account lock status (MySQL 5.7.6+)
            locked = 0
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM mysql.user WHERE account_locked = 'Y'"
                )
                locked = cursor.fetchone()[0]
            except Exception:
                pass

            cursor.close()

            return {
                "total_users": total,
                "users_without_password": no_password,
                "users_with_wildcard_host": wildcard_host,
                "locked_accounts": locked,
            }

        except Exception as e:
            self.add_warning(f"User summary unavailable: {e}")
            return {"error": str(e), "note": "mysql.user may not be accessible"}

    def _get_auth_plugin_distribution(self) -> Dict[str, Any]:
        """Get distribution of authentication plugins in use."""
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT plugin, COUNT(*) AS user_count "
                "FROM mysql.user "
                "GROUP BY plugin "
                "ORDER BY user_count DESC"
            )
            rows = cursor.fetchall()
            cursor.close()

            plugins = {row[0]: row[1] for row in rows}

            return {
                "plugins": plugins,
                "total_plugins": len(plugins),
            }

        except Exception as e:
            self.add_warning(f"Auth plugin analysis unavailable: {e}")
            return {"error": str(e)}

    def _get_ssl_status(self) -> Dict[str, Any]:
        """Get SSL/TLS configuration status."""
        try:
            cursor = self.connection.cursor()

            # Check if SSL is available
            cursor.execute("SHOW GLOBAL VARIABLES LIKE 'have_ssl'")
            row = cursor.fetchone()
            have_ssl = row[1] if row else "DISABLED"

            # Check require_secure_transport (MySQL 5.7.8+)
            require_secure = False
            try:
                cursor.execute("SHOW GLOBAL VARIABLES LIKE 'require_secure_transport'")
                row = cursor.fetchone()
                if row:
                    require_secure = row[1].upper() == "ON"
            except Exception:
                pass

            # Get TLS version
            tls_version = ""
            try:
                cursor.execute("SHOW GLOBAL VARIABLES LIKE 'tls_version'")
                row = cursor.fetchone()
                if row:
                    tls_version = row[1]
            except Exception:
                pass

            # Count users that require SSL
            ssl_required_users = 0
            try:
                cursor.execute("SELECT COUNT(*) FROM mysql.user WHERE ssl_type != ''")
                ssl_required_users = cursor.fetchone()[0]
            except Exception:
                pass

            cursor.close()

            return {
                "ssl_available": have_ssl.upper() == "YES",
                "require_secure_transport": require_secure,
                "tls_versions": tls_version,
                "users_requiring_ssl": ssl_required_users,
            }

        except Exception as e:
            self.add_warning(f"SSL status unavailable: {e}")
            return {"error": str(e)}

    def _get_grant_summary(self) -> Dict[str, Any]:
        """Get aggregate grant distribution without exposing who has what."""
        try:
            cursor = self.connection.cursor()

            # Count users with global ALL PRIVILEGES
            cursor.execute(
                "SELECT COUNT(*) FROM mysql.user "
                "WHERE Select_priv='Y' AND Insert_priv='Y' AND Update_priv='Y' "
                "AND Delete_priv='Y' AND Create_priv='Y' AND Drop_priv='Y' "
                "AND Alter_priv='Y'"
            )
            broad_privileges = cursor.fetchone()[0]

            # Count users with SUPER privilege
            super_users = 0
            try:
                cursor.execute("SELECT COUNT(*) FROM mysql.user WHERE Super_priv='Y'")
                super_users = cursor.fetchone()[0]
            except Exception:
                pass

            # Count users with REPLICATION privileges
            repl_users = 0
            try:
                cursor.execute(
                    "SELECT COUNT(*) FROM mysql.user WHERE Repl_slave_priv='Y'"
                )
                repl_users = cursor.fetchone()[0]
            except Exception:
                pass

            # Count database-level grants
            db_grants = 0
            try:
                cursor.execute("SELECT COUNT(DISTINCT User) FROM mysql.db")
                db_grants = cursor.fetchone()[0]
            except Exception:
                pass

            cursor.close()

            return {
                "users_with_broad_privileges": broad_privileges,
                "users_with_super": super_users,
                "users_with_replication": repl_users,
                "users_with_db_level_grants": db_grants,
            }

        except Exception as e:
            self.add_warning(f"Grant summary unavailable: {e}")
            return {"error": str(e)}

    def _get_password_policy(self) -> Dict[str, Any]:
        """Get password validation policy settings if available."""
        try:
            cursor = self.connection.cursor()

            # Check if validate_password plugin/component is active
            policy = {}
            cursor.execute("SHOW GLOBAL VARIABLES LIKE 'validate_password%'")
            rows = cursor.fetchall()

            if rows:
                for row in rows:
                    key = (
                        row[0]
                        .replace("validate_password.", "")
                        .replace("validate_password_", "")
                    )
                    policy[key] = row[1]

            cursor.close()

            return {
                "validation_enabled": len(policy) > 0,
                "settings": policy,
            }

        except Exception as e:
            self.add_warning(f"Password policy unavailable: {e}")
            return {"validation_enabled": False, "settings": {}}

    def _get_current_user_grants(self) -> Dict[str, Any]:
        """Get grants for the currently connected user."""
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT CURRENT_USER()")
            current_user = cursor.fetchone()[0]

            cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
            rows = cursor.fetchall()
            cursor.close()

            grants = [row[0] for row in rows]

            return {
                "current_user": current_user,
                "grants": grants,
            }

        except Exception as e:
            self.add_warning(f"Current user grants unavailable: {e}")
            return {"error": str(e)}
