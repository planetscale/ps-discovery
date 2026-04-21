"""
Tests for MySQL Security Analyzer
"""

from unittest.mock import MagicMock
from planetscale_discovery.database.mysql_analyzers.security_analyzer import (
    MySQLSecurityAnalyzer,
)


class TestMySQLSecurityAnalyzer:
    """MySQL Security Analyzer tests"""

    def _make_analyzer(self):
        mock_conn = MagicMock()
        return MySQLSecurityAnalyzer(mock_conn)

    def test_instantiation(self):
        analyzer = self._make_analyzer()
        assert analyzer is not None

    def test_analyze_returns_expected_keys(self):
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []
        analyzer.connection.cursor.return_value = cursor

        result = analyzer.analyze()
        assert "user_summary" in result
        assert "auth_plugins" in result
        assert "ssl_status" in result
        assert "grant_summary" in result
        assert "password_policy" in result

    def test_user_summary_counts(self):
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        # Total users, no password, wildcard host, locked
        cursor.fetchone.side_effect = [(15,), (2,), (3,), (1,)]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_user_summary()
        assert result["total_users"] == 15
        assert result["users_without_password"] == 2
        assert result["users_with_wildcard_host"] == 3
        assert result["locked_accounts"] == 1

    def test_user_summary_no_usernames_exposed(self):
        """Verify no individual usernames appear in results."""
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(5,), (0,), (1,), (0,)]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_user_summary()
        result_str = str(result)
        # Should only contain counts, not names
        assert "total_users" in result
        assert isinstance(result["total_users"], int)
        # No username-like strings
        assert "root" not in result_str
        assert "admin" not in result_str

    def test_user_summary_handles_permission_error(self):
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Access denied for user")
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_user_summary()
        assert "error" in result
        assert len(analyzer.warnings) > 0

    def test_auth_plugin_distribution(self):
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("caching_sha2_password", 10),
            ("mysql_native_password", 5),
        ]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_auth_plugin_distribution()
        assert result["total_plugins"] == 2
        assert result["plugins"]["caching_sha2_password"] == 10
        assert result["plugins"]["mysql_native_password"] == 5

    def test_ssl_status(self):
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            ("have_ssl", "YES"),  # have_ssl
            ("require_secure_transport", "ON"),  # require_secure
            ("tls_version", "TLSv1.2,TLSv1.3"),  # tls_version
            (3,),  # ssl_required_users count
        ]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_ssl_status()
        assert result["ssl_available"] is True
        assert result["require_secure_transport"] is True
        assert "TLSv1.2" in result["tls_versions"]
        assert result["users_requiring_ssl"] == 3

    def test_ssl_status_disabled(self):
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            ("have_ssl", "DISABLED"),
            ("require_secure_transport", "OFF"),
            ("tls_version", ""),
            (0,),
        ]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_ssl_status()
        assert result["ssl_available"] is False
        assert result["require_secure_transport"] is False

    def test_grant_summary(self):
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        # broad privs, super, repl, db-level
        cursor.fetchone.side_effect = [(3,), (2,), (1,), (5,)]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_grant_summary()
        assert result["users_with_broad_privileges"] == 3
        assert result["users_with_super"] == 2
        assert result["users_with_replication"] == 1
        assert result["users_with_db_level_grants"] == 5

    def test_password_policy_enabled(self):
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("validate_password.policy", "MEDIUM"),
            ("validate_password.length", "8"),
        ]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_password_policy()
        assert result["validation_enabled"] is True
        assert "policy" in result["settings"]
        assert result["settings"]["policy"] == "MEDIUM"

    def test_password_policy_not_installed(self):
        analyzer = self._make_analyzer()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_password_policy()
        assert result["validation_enabled"] is False
        assert result["settings"] == {}
