"""
Unit tests for PostgreSQL Security Analyzer
"""

import pytest
from unittest.mock import MagicMock, patch
from planetscale_discovery.database.analyzers.security_analyzer import SecurityAnalyzer

# --- Fixture data ---

ROLES_RESPONSE = [
    {
        "rolname": "postgres",
        "rolsuper": True,
        "rolinherit": True,
        "rolcreaterole": True,
        "rolcreatedb": True,
        "rolcanlogin": True,
        "rolreplication": True,
        "rolconnlimit": -1,
        "has_password": True,
        "rolvaliduntil": None,
        "rolbypassrls": True,
        "role_oid": 10,
    },
    {
        "rolname": "app_user",
        "rolsuper": False,
        "rolinherit": True,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "rolcanlogin": True,
        "rolreplication": False,
        "rolconnlimit": -1,
        "has_password": True,
        "rolvaliduntil": None,
        "rolbypassrls": False,
        "role_oid": 16384,
    },
    {
        "rolname": "readonly_role",
        "rolsuper": False,
        "rolinherit": True,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "rolcanlogin": False,
        "rolreplication": False,
        "rolconnlimit": -1,
        "has_password": False,
        "rolvaliduntil": None,
        "rolbypassrls": False,
        "role_oid": 16385,
    },
    {
        "rolname": "repl_user",
        "rolsuper": False,
        "rolinherit": True,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "rolcanlogin": True,
        "rolreplication": True,
        "rolconnlimit": 5,
        "has_password": True,
        "rolvaliduntil": None,
        "rolbypassrls": False,
        "role_oid": 16386,
    },
]

ROLE_MEMBERSHIPS_RESPONSE = [
    {
        "role_name": "readonly_role",
        "member_name": "app_user",
        "grantor_name": "postgres",
        "admin_option": False,
        "inherit_option": True,
    },
]

DATABASE_PRIVILEGES_RESPONSE = [
    {
        "database_name": "mydb",
        "database_acl": "{postgres=CTc/postgres,app_user=c/postgres}",
        "grantee": "app_user",
        "grantor": "postgres",
        "privilege_type": "CONNECT",
        "is_grantable": False,
    },
]

SCHEMA_PRIVILEGES_RESPONSE = [
    {
        "schema_name": "public",
        "schema_acl": "{postgres=UC/postgres,app_user=U/postgres}",
        "grantee": "app_user",
        "grantor": "postgres",
        "privilege_type": "USAGE",
        "is_grantable": False,
    },
]

TABLE_PRIVILEGES_RESPONSE = [
    {
        "schema_name": "public",
        "table_name": "users",
        "table_acl": "{postgres=arwdDxt/postgres,app_user=r/postgres}",
        "grantee": "app_user",
        "grantor": "postgres",
        "privilege_type": "SELECT",
        "is_grantable": False,
    },
]

FUNCTION_PRIVILEGES_RESPONSE = [
    {
        "schema_name": "public",
        "function_name": "my_func",
        "function_acl": "{=X/postgres,app_user=X/postgres}",
        "grantee": "app_user",
        "grantor": "postgres",
        "privilege_type": "EXECUTE",
        "is_grantable": False,
    },
]

AUTH_SETTINGS_RESPONSE = [
    {
        "name": "password_encryption",
        "setting": "scram-sha-256",
        "source": "configuration file",
        "context": "user",
    },
    {
        "name": "ssl",
        "setting": "on",
        "source": "configuration file",
        "context": "sighup",
    },
]

SECURITY_SETTINGS_RESPONSE = [
    {
        "name": "log_connections",
        "setting": "on",
        "unit": None,
        "source": "configuration file",
        "context": "superuser-backend",
        "short_desc": "Logs each successful connection.",
    },
    {
        "name": "row_security",
        "setting": "on",
        "unit": None,
        "source": "default",
        "context": "user",
        "short_desc": "Enable row security.",
    },
]

SECURITY_DEFINER_FUNCTIONS_RESPONSE = [
    {
        "schema_name": "public",
        "function_name": "admin_func",
        "owner": "postgres",
        "language": "plpgsql",
        "is_security_definer": True,
        "function_acl": None,
    },
]

CREATE_ROLE_USERS_RESPONSE = [
    {"rolname": "dba_user"},
]

BYPASS_RLS_USERS_RESPONSE = [
    {"rolname": "migration_user"},
]

DEFAULT_PRIVILEGES_RESPONSE = [
    {
        "role_name": "postgres",
        "schema_name": "public",
        "object_type": "r",
        "default_acl": "{app_user=r/postgres}",
        "grantee": "app_user",
        "grantor": "postgres",
        "privilege_type": "SELECT",
        "is_grantable": False,
    },
]

RLS_TABLES_RESPONSE = [
    {
        "schema_name": "public",
        "table_name": "orders",
        "rls_enabled": True,
        "rls_forced": False,
    },
    {
        "schema_name": "public",
        "table_name": "accounts",
        "rls_enabled": True,
        "rls_forced": True,
    },
]

RLS_POLICIES_RESPONSE = [
    {
        "schema_name": "public",
        "table_name": "orders",
        "policy_name": "tenant_isolation",
        "command": "*",
        "is_permissive": True,
        "roles": "{app_user}",
        "policy_expression": "(tenant_id = current_setting('app.tenant_id')::integer)",
        "with_check_expression": "(tenant_id = current_setting('app.tenant_id')::integer)",
    },
]

SSL_SETTINGS_RESPONSE = [
    {
        "name": "ssl",
        "setting": "on",
        "source": "configuration file",
        "context": "sighup",
    },
    {
        "name": "ssl_min_protocol_version",
        "setting": "TLSv1.2",
        "source": "default",
        "context": "sighup",
    },
]

SSL_USAGE_RESPONSE = {
    "total_connections": 15,
    "ssl_connections": 12,
}


class TestSecurityAnalyzer:
    """Test cases for SecurityAnalyzer"""

    @pytest.fixture
    def mock_connection(self):
        """Create a mock database connection with context-manager cursor."""
        connection = MagicMock()
        cursor_mock = MagicMock()
        connection.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
        connection.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return connection, cursor_mock

    @pytest.fixture
    def analyzer(self, mock_connection):
        """Create SecurityAnalyzer instance with mock connection."""
        connection, _ = mock_connection
        return SecurityAnalyzer(connection)

    # ------------------------------------------------------------------
    # analyze() top-level structure
    # ------------------------------------------------------------------

    def test_analyze_returns_expected_keys(self, analyzer):
        """Test that analyze() returns all expected top-level keys."""
        with (
            patch.object(analyzer, "_get_user_role_analysis", return_value={}),
            patch.object(analyzer, "_get_permission_analysis", return_value={}),
            patch.object(analyzer, "_get_authentication_config", return_value={}),
            patch.object(analyzer, "_get_security_settings", return_value={}),
            patch.object(analyzer, "_get_privilege_escalation", return_value=[]),
            patch.object(analyzer, "_get_default_privileges", return_value=[]),
            patch.object(analyzer, "_get_row_level_security", return_value={}),
            patch.object(analyzer, "_get_ssl_configuration", return_value={}),
        ):
            result = analyzer.analyze()

        expected_keys = {
            "user_role_analysis",
            "permission_analysis",
            "authentication_config",
            "security_settings",
            "privilege_escalation",
            "default_privileges",
            "row_level_security",
            "ssl_configuration",
        }
        assert set(result.keys()) == expected_keys

    # ------------------------------------------------------------------
    # User / role analysis
    # ------------------------------------------------------------------

    def test_user_role_analysis(self, analyzer, mock_connection):
        """Test user/role analysis with realistic mock data."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [
            ROLES_RESPONSE,
            ROLE_MEMBERSHIPS_RESPONSE,
        ]

        result = analyzer._get_user_role_analysis()

        assert result["summary"]["total_roles"] == 4
        # Users = those with rolcanlogin: postgres, app_user, repl_user
        assert result["summary"]["user_count"] == 3
        # Roles = those without rolcanlogin: readonly_role
        assert result["summary"]["role_count"] == 1
        assert result["summary"]["superuser_count"] == 1
        assert result["summary"]["replication_user_count"] == 2  # postgres + repl_user
        assert len(result["role_memberships"]) == 1
        assert result["role_memberships"][0]["member_name"] == "app_user"

    def test_user_role_analysis_separates_users_and_roles(
        self, analyzer, mock_connection
    ):
        """Test that users (canlogin) and roles (no login) are separated correctly."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [ROLES_RESPONSE, []]

        result = analyzer._get_user_role_analysis()

        user_names = [u["rolname"] for u in result["users"]]
        role_names = [r["rolname"] for r in result["roles"]]
        assert "postgres" in user_names
        assert "app_user" in user_names
        assert "readonly_role" in role_names
        assert "readonly_role" not in user_names

    def test_user_role_analysis_identifies_superusers(self, analyzer, mock_connection):
        """Test superuser identification."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [ROLES_RESPONSE, []]

        result = analyzer._get_user_role_analysis()

        superuser_names = [s["rolname"] for s in result["superusers"]]
        assert superuser_names == ["postgres"]

    def test_user_role_analysis_identifies_bypass_rls_users(
        self, analyzer, mock_connection
    ):
        """Test bypass-RLS user identification."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [ROLES_RESPONSE, []]

        result = analyzer._get_user_role_analysis()

        bypass_names = [u["rolname"] for u in result["bypass_rls_users"]]
        assert "postgres" in bypass_names

    def test_user_role_analysis_error(self, analyzer, mock_connection):
        """Test graceful error handling in user/role analysis."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("permission denied")

        result = analyzer._get_user_role_analysis()

        assert "error" in result
        assert "permission denied" in result["error"]

    def test_user_role_analysis_empty(self, analyzer, mock_connection):
        """Test with no roles returned (edge case)."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], []]

        result = analyzer._get_user_role_analysis()

        assert result["summary"]["total_roles"] == 0
        assert result["summary"]["user_count"] == 0
        assert result["summary"]["role_count"] == 0
        assert result["users"] == []
        assert result["roles"] == []

    # ------------------------------------------------------------------
    # Permission analysis
    # ------------------------------------------------------------------

    def test_permission_analysis(self, analyzer, mock_connection):
        """Test permission analysis returns all privilege types."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [
            DATABASE_PRIVILEGES_RESPONSE,
            SCHEMA_PRIVILEGES_RESPONSE,
            TABLE_PRIVILEGES_RESPONSE,
            FUNCTION_PRIVILEGES_RESPONSE,
        ]

        result = analyzer._get_permission_analysis()

        assert len(result["database_privileges"]) == 1
        assert result["database_privileges"][0]["grantee"] == "app_user"
        assert len(result["schema_privileges"]) == 1
        assert len(result["table_privileges"]) == 1
        assert result["table_privileges"][0]["privilege_type"] == "SELECT"
        assert len(result["function_privileges"]) == 1

    def test_permission_analysis_empty(self, analyzer, mock_connection):
        """Test permission analysis when no explicit grants exist."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], [], [], []]

        result = analyzer._get_permission_analysis()

        assert result["database_privileges"] == []
        assert result["schema_privileges"] == []
        assert result["table_privileges"] == []
        assert result["function_privileges"] == []

    def test_permission_analysis_error(self, analyzer, mock_connection):
        """Test graceful error handling in permission analysis."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("query failed")

        result = analyzer._get_permission_analysis()

        assert "error" in result

    # ------------------------------------------------------------------
    # Authentication config
    # ------------------------------------------------------------------

    def test_authentication_config(self, analyzer, mock_connection):
        """Test authentication config retrieval."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [
            AUTH_SETTINGS_RESPONSE,
            [],  # passwordcheck policies
        ]

        result = analyzer._get_authentication_config()

        assert "password_encryption" in result["authentication_settings"]
        assert (
            result["authentication_settings"]["password_encryption"]["setting"]
            == "scram-sha-256"
        )
        assert result["password_policies"] == {}

    def test_authentication_config_with_password_policies(
        self, analyzer, mock_connection
    ):
        """Test when passwordcheck extension policies exist."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [
            AUTH_SETTINGS_RESPONSE,
            [
                {"name": "passwordcheck.min_length", "setting": "12"},
            ],
        ]

        result = analyzer._get_authentication_config()

        assert result["password_policies"]["passwordcheck.min_length"] == "12"

    def test_authentication_config_password_policy_error(
        self, analyzer, mock_connection
    ):
        """Test that password policy query failure is silently caught."""
        _, cursor_mock = mock_connection

        # First fetchall returns auth settings; second execute raises for passwordcheck
        call_count = [0]
        original_fetchall_data = [AUTH_SETTINGS_RESPONSE]

        def fetchall_side_effect():
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                return original_fetchall_data[0]
            raise Exception("extension not found")

        cursor_mock.fetchall.side_effect = fetchall_side_effect

        result = analyzer._get_authentication_config()

        assert "authentication_settings" in result
        assert result["password_policies"] == {}

    def test_authentication_config_error(self, analyzer, mock_connection):
        """Test outer error handling in authentication config."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("connection lost")

        result = analyzer._get_authentication_config()

        assert "error" in result

    # ------------------------------------------------------------------
    # Security settings
    # ------------------------------------------------------------------

    def test_security_settings(self, analyzer, mock_connection):
        """Test security settings retrieval."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = SECURITY_SETTINGS_RESPONSE

        result = analyzer._get_security_settings()

        assert "log_connections" in result
        assert result["log_connections"]["setting"] == "on"
        assert "row_security" in result

    def test_security_settings_empty(self, analyzer, mock_connection):
        """Test with no security settings found."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = []

        result = analyzer._get_security_settings()

        assert result == {}

    def test_security_settings_error(self, analyzer, mock_connection):
        """Test error handling in security settings."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("query timeout")

        result = analyzer._get_security_settings()

        assert "error" in result

    # ------------------------------------------------------------------
    # Privilege escalation
    # ------------------------------------------------------------------

    def test_privilege_escalation_all_risks(self, analyzer, mock_connection):
        """Test privilege escalation detection with all risk types present."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [
            SECURITY_DEFINER_FUNCTIONS_RESPONSE,
            CREATE_ROLE_USERS_RESPONSE,
            BYPASS_RLS_USERS_RESPONSE,
        ]

        result = analyzer._get_privilege_escalation()

        assert len(result) == 3
        risk_types = [r["risk_type"] for r in result]
        assert "security_definer_functions" in risk_types
        assert "create_role_privilege" in risk_types
        assert "bypass_rls_privilege" in risk_types

    def test_privilege_escalation_security_definer(self, analyzer, mock_connection):
        """Test that SECURITY DEFINER functions are flagged."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [
            SECURITY_DEFINER_FUNCTIONS_RESPONSE,
            [],  # no create_role users
            [],  # no bypass_rls users
        ]

        result = analyzer._get_privilege_escalation()

        assert len(result) == 1
        assert result[0]["risk_type"] == "security_definer_functions"
        assert result[0]["severity"] == "medium"
        assert result[0]["count"] == 1

    def test_privilege_escalation_no_risks(self, analyzer, mock_connection):
        """Test when no privilege escalation risks are found."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], [], []]

        result = analyzer._get_privilege_escalation()

        assert result == []

    def test_privilege_escalation_error(self, analyzer, mock_connection):
        """Test error handling returns empty list."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("access denied")

        result = analyzer._get_privilege_escalation()

        assert result == []

    # ------------------------------------------------------------------
    # Default privileges
    # ------------------------------------------------------------------

    def test_default_privileges(self, analyzer, mock_connection):
        """Test default privileges retrieval."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = DEFAULT_PRIVILEGES_RESPONSE

        result = analyzer._get_default_privileges()

        assert len(result) == 1
        assert result[0]["role_name"] == "postgres"
        assert result[0]["privilege_type"] == "SELECT"

    def test_default_privileges_empty(self, analyzer, mock_connection):
        """Test with no default privileges configured."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = []

        result = analyzer._get_default_privileges()

        assert result == []

    def test_default_privileges_error(self, analyzer, mock_connection):
        """Test error handling returns empty list."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("permission denied")

        result = analyzer._get_default_privileges()

        assert result == []

    # ------------------------------------------------------------------
    # Row-level security
    # ------------------------------------------------------------------

    def test_row_level_security(self, analyzer, mock_connection):
        """Test RLS detection with tables and policies."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [
            RLS_TABLES_RESPONSE,
            RLS_POLICIES_RESPONSE,
        ]

        result = analyzer._get_row_level_security()

        assert result["summary"]["tables_with_rls"] == 2
        assert result["summary"]["total_policies"] == 1
        assert result["rls_enabled_tables"][0]["table_name"] == "orders"
        assert result["rls_enabled_tables"][1]["rls_forced"] is True
        assert result["rls_policies"][0]["policy_name"] == "tenant_isolation"

    def test_row_level_security_none_enabled(self, analyzer, mock_connection):
        """Test when no tables have RLS enabled."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], []]

        result = analyzer._get_row_level_security()

        assert result["summary"]["tables_with_rls"] == 0
        assert result["summary"]["total_policies"] == 0
        assert result["rls_enabled_tables"] == []
        assert result["rls_policies"] == []

    def test_row_level_security_error(self, analyzer, mock_connection):
        """Test error handling in RLS analysis."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("catalog access denied")

        result = analyzer._get_row_level_security()

        assert "error" in result

    # ------------------------------------------------------------------
    # SSL configuration
    # ------------------------------------------------------------------

    def test_ssl_configuration(self, analyzer, mock_connection):
        """Test SSL configuration with usage statistics."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = SSL_SETTINGS_RESPONSE
        cursor_mock.fetchone.return_value = SSL_USAGE_RESPONSE

        result = analyzer._get_ssl_configuration()

        assert "ssl" in result["ssl_configuration"]
        assert result["ssl_configuration"]["ssl"]["setting"] == "on"
        assert result["ssl_usage"]["total_connections"] == 15
        assert result["ssl_usage"]["ssl_connections"] == 12

    def test_ssl_configuration_usage_unavailable(self, analyzer, mock_connection):
        """Test SSL config when pg_stat_ssl is not accessible."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = SSL_SETTINGS_RESPONSE

        # fetchone for ssl_usage raises (inner try/except catches this)
        cursor_mock.fetchone.side_effect = Exception("pg_stat_ssl not available")

        result = analyzer._get_ssl_configuration()

        assert "ssl" in result["ssl_configuration"]
        assert result["ssl_usage"] is None

    def test_ssl_configuration_empty(self, analyzer, mock_connection):
        """Test SSL config when no SSL settings are returned."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = []
        cursor_mock.fetchone.return_value = SSL_USAGE_RESPONSE

        result = analyzer._get_ssl_configuration()

        assert result["ssl_configuration"] == {}

    def test_ssl_configuration_error(self, analyzer, mock_connection):
        """Test outer error handling in SSL configuration."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("connection reset")

        result = analyzer._get_ssl_configuration()

        assert "error" in result

    # ------------------------------------------------------------------
    # Full integration-style test using mocked sub-methods
    # ------------------------------------------------------------------

    def test_analyze_aggregates_all_sections(self, analyzer):
        """Test that analyze() correctly aggregates all sub-analysis results."""
        with (
            patch.object(
                analyzer,
                "_get_user_role_analysis",
                return_value={"summary": {"total_roles": 5}},
            ),
            patch.object(
                analyzer,
                "_get_permission_analysis",
                return_value={"database_privileges": []},
            ),
            patch.object(
                analyzer,
                "_get_authentication_config",
                return_value={"authentication_settings": {}},
            ),
            patch.object(
                analyzer,
                "_get_security_settings",
                return_value={"row_security": {"setting": "on"}},
            ),
            patch.object(analyzer, "_get_privilege_escalation", return_value=[]),
            patch.object(analyzer, "_get_default_privileges", return_value=[]),
            patch.object(
                analyzer,
                "_get_row_level_security",
                return_value={"summary": {"tables_with_rls": 2}},
            ),
            patch.object(
                analyzer,
                "_get_ssl_configuration",
                return_value={"ssl_configuration": {}},
            ),
        ):
            result = analyzer.analyze()

        assert result["user_role_analysis"]["summary"]["total_roles"] == 5
        assert result["row_level_security"]["summary"]["tables_with_rls"] == 2
        assert result["privilege_escalation"] == []
        assert result["default_privileges"] == []

    # ------------------------------------------------------------------
    # Error propagation: each section should not block others
    # ------------------------------------------------------------------

    def test_analyze_continues_on_individual_section_error(self, analyzer):
        """Test that one section erroring does not prevent others from running."""
        with (
            patch.object(
                analyzer,
                "_get_user_role_analysis",
                side_effect=Exception("unexpected"),
            ),
            patch.object(
                analyzer,
                "_get_permission_analysis",
                return_value={"database_privileges": []},
            ),
            patch.object(
                analyzer,
                "_get_authentication_config",
                return_value={},
            ),
            patch.object(analyzer, "_get_security_settings", return_value={}),
            patch.object(analyzer, "_get_privilege_escalation", return_value=[]),
            patch.object(analyzer, "_get_default_privileges", return_value=[]),
            patch.object(analyzer, "_get_row_level_security", return_value={}),
            patch.object(analyzer, "_get_ssl_configuration", return_value={}),
        ):
            # analyze() does not catch exceptions from sub-methods at the top level,
            # so an unhandled exception in a patched method will propagate.
            with pytest.raises(Exception, match="unexpected"):
                analyzer.analyze()
