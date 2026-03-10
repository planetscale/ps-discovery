"""
PostgreSQL Security Analysis Module
Analyzes security configuration, users, roles, and permissions.
"""

from typing import Dict, Any, List

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class SecurityAnalyzer(DatabaseAnalyzer):
    """Analyzes PostgreSQL security configuration and access control."""

    def __init__(self, connection, config=None, logger=None):
        super().__init__(connection, config, logger)

    def analyze(self) -> Dict[str, Any]:
        """Run complete security analysis."""
        results = {
            "user_role_analysis": self._get_user_role_analysis(),
            "permission_analysis": self._get_permission_analysis(),
            "authentication_config": self._get_authentication_config(),
            "security_settings": self._get_security_settings(),
            "privilege_escalation": self._get_privilege_escalation(),
            "default_privileges": self._get_default_privileges(),
            "row_level_security": self._get_row_level_security(),
            "ssl_configuration": self._get_ssl_configuration(),
        }

        return results

    def _get_user_role_analysis(self) -> Dict[str, Any]:
        """Analyze users and roles."""
        try:
            with self.connection.cursor() as cursor:
                # Get all roles with their attributes
                cursor.execute("""
                    SELECT
                        rolname,
                        rolsuper,
                        rolinherit,
                        rolcreaterole,
                        rolcreatedb,
                        rolcanlogin,
                        rolreplication,
                        rolconnlimit,
                        rolpassword IS NOT NULL as has_password,
                        rolvaliduntil,
                        rolbypassrls,
                        oid as role_oid
                    FROM pg_roles
                    ORDER BY rolname
                """)
                all_roles = [dict(row) for row in cursor.fetchall()]

                # Separate users (can login) from roles
                users = [role for role in all_roles if role["rolcanlogin"]]
                roles = [role for role in all_roles if not role["rolcanlogin"]]

                # Get role memberships
                cursor.execute("""
                    SELECT
                        r.rolname as role_name,
                        m.rolname as member_name,
                        grantor.rolname as grantor_name,
                        pgr.admin_option,
                        pgr.inherit_option
                    FROM pg_auth_members pgr
                    JOIN pg_roles r ON pgr.roleid = r.oid
                    JOIN pg_roles m ON pgr.member = m.oid
                    JOIN pg_roles grantor ON pgr.grantor = grantor.oid
                    ORDER BY r.rolname, m.rolname
                """)
                role_memberships = [dict(row) for row in cursor.fetchall()]

                # Identify superusers and high-privilege users
                superusers = [role for role in all_roles if role["rolsuper"]]
                create_role_users = [
                    role for role in all_roles if role["rolcreaterole"]
                ]
                replication_users = [
                    role for role in all_roles if role["rolreplication"]
                ]
                bypass_rls_users = [role for role in all_roles if role["rolbypassrls"]]

                return {
                    "all_roles": all_roles,
                    "users": users,
                    "roles": roles,
                    "role_memberships": role_memberships,
                    "superusers": superusers,
                    "create_role_users": create_role_users,
                    "replication_users": replication_users,
                    "bypass_rls_users": bypass_rls_users,
                    "summary": {
                        "total_roles": len(all_roles),
                        "user_count": len(users),
                        "role_count": len(roles),
                        "superuser_count": len(superusers),
                        "create_role_count": len(create_role_users),
                        "replication_user_count": len(replication_users),
                    },
                }

        except Exception as e:
            self.logger.error(f"Failed to get user/role analysis: {e}")
            return {"error": str(e)}

    def _get_permission_analysis(self) -> Dict[str, Any]:
        """Analyze permissions and privileges."""
        try:
            with self.connection.cursor() as cursor:
                # Database privileges
                cursor.execute("""
                    SELECT
                        datname as database_name,
                        datacl as database_acl,
                        (aclexplode(datacl)).grantee::regrole as grantee,
                        (aclexplode(datacl)).grantor::regrole as grantor,
                        (aclexplode(datacl)).privilege_type as privilege_type,
                        (aclexplode(datacl)).is_grantable as is_grantable
                    FROM pg_database
                    WHERE datname NOT IN ('template0', 'template1')
                    AND datacl IS NOT NULL
                """)
                database_privileges = [dict(row) for row in cursor.fetchall()]

                # Schema privileges
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        n.nspacl as schema_acl,
                        (aclexplode(n.nspacl)).grantee::regrole as grantee,
                        (aclexplode(n.nspacl)).grantor::regrole as grantor,
                        (aclexplode(n.nspacl)).privilege_type as privilege_type,
                        (aclexplode(n.nspacl)).is_grantable as is_grantable
                    FROM pg_namespace n
                    WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    AND n.nspacl IS NOT NULL
                """)
                schema_privileges = [dict(row) for row in cursor.fetchall()]

                # Table privileges
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        c.relacl as table_acl,
                        (aclexplode(c.relacl)).grantee::regrole as grantee,
                        (aclexplode(c.relacl)).grantor::regrole as grantor,
                        (aclexplode(c.relacl)).privilege_type as privilege_type,
                        (aclexplode(c.relacl)).is_grantable as is_grantable
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE c.relkind IN ('r', 'v', 'm', 'S', 'f')
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    AND c.relacl IS NOT NULL
                    ORDER BY n.nspname, c.relname
                """)
                table_privileges = [dict(row) for row in cursor.fetchall()]

                # Function privileges
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        p.proname as function_name,
                        p.proacl as function_acl,
                        (aclexplode(p.proacl)).grantee::regrole as grantee,
                        (aclexplode(p.proacl)).grantor::regrole as grantor,
                        (aclexplode(p.proacl)).privilege_type as privilege_type,
                        (aclexplode(p.proacl)).is_grantable as is_grantable
                    FROM pg_proc p
                    JOIN pg_namespace n ON p.pronamespace = n.oid
                    WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    AND p.proacl IS NOT NULL
                    ORDER BY n.nspname, p.proname
                """)
                function_privileges = [dict(row) for row in cursor.fetchall()]

                return {
                    "database_privileges": database_privileges,
                    "schema_privileges": schema_privileges,
                    "table_privileges": table_privileges,
                    "function_privileges": function_privileges,
                }

        except Exception as e:
            self.logger.error(f"Failed to get permission analysis: {e}")
            return {"error": str(e)}

    def _get_authentication_config(self) -> Dict[str, Any]:
        """Analyze authentication configuration."""
        try:
            with self.connection.cursor() as cursor:
                # Get authentication-related settings
                auth_settings = [
                    "password_encryption",
                    "authentication_timeout",
                    "ssl",
                    "ssl_cert_file",
                    "ssl_key_file",
                    "ssl_ca_file",
                    "krb_server_keyfile",
                    "krb_caseins_users",
                    "db_user_namespace",
                ]

                cursor.execute(
                    """
                    SELECT name, setting, source, context
                    FROM pg_settings
                    WHERE name = ANY(%s)
                """,
                    (auth_settings,),
                )

                auth_config = {row["name"]: dict(row) for row in cursor.fetchall()}

                # Get password policies (if passwordcheck extension is loaded)
                password_policies = {}
                try:
                    cursor.execute("""
                        SELECT name, setting
                        FROM pg_settings
                        WHERE name LIKE 'passwordcheck.%'
                    """)
                    password_policies = {
                        row["name"]: row["setting"] for row in cursor.fetchall()
                    }
                except Exception:
                    pass

                return {
                    "authentication_settings": auth_config,
                    "password_policies": password_policies,
                }

        except Exception as e:
            self.logger.error(f"Failed to get authentication config: {e}")
            return {"error": str(e)}

    def _get_security_settings(self) -> Dict[str, Any]:
        """Get security-related configuration settings."""
        try:
            with self.connection.cursor() as cursor:
                security_settings = [
                    "log_connections",
                    "log_disconnections",
                    "log_hostname",
                    "log_statement",
                    "log_min_duration_statement",
                    "log_checkpoints",
                    "log_lock_waits",
                    "log_temp_files",
                    "row_security",
                    "ssl_prefer_server_ciphers",
                    "ssl_min_protocol_version",
                    "ssl_max_protocol_version",
                    "ssl_ecdh_curve",
                    "ssl_dh_params_file",
                ]

                cursor.execute(
                    """
                    SELECT name, setting, unit, source, context, short_desc
                    FROM pg_settings
                    WHERE name = ANY(%s)
                """,
                    (security_settings,),
                )

                return {row["name"]: dict(row) for row in cursor.fetchall()}

        except Exception as e:
            self.logger.error(f"Failed to get security settings: {e}")
            return {"error": str(e)}

    def _get_privilege_escalation(self) -> List[Dict[str, Any]]:
        """Identify potential privilege escalation risks."""
        try:
            with self.connection.cursor() as cursor:
                risks = []

                # Functions with SECURITY DEFINER
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        p.proname as function_name,
                        r.rolname as owner,
                        l.lanname as language,
                        p.prosecdef as is_security_definer,
                        p.proacl as function_acl
                    FROM pg_proc p
                    JOIN pg_namespace n ON p.pronamespace = n.oid
                    JOIN pg_roles r ON p.proowner = r.oid
                    JOIN pg_language l ON p.prolang = l.oid
                    WHERE p.prosecdef = true
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, p.proname
                """)

                security_definer_functions = [dict(row) for row in cursor.fetchall()]
                if security_definer_functions:
                    risks.append(
                        {
                            "risk_type": "security_definer_functions",
                            "severity": "medium",
                            "description": "Functions with SECURITY DEFINER that run with elevated privileges",
                            "count": len(security_definer_functions),
                            "details": security_definer_functions,
                        }
                    )

                # Users with CREATEROLE privilege
                cursor.execute("""
                    SELECT rolname
                    FROM pg_roles
                    WHERE rolcreaterole = true
                    AND NOT rolsuper
                """)

                create_role_users = [row["rolname"] for row in cursor.fetchall()]
                if create_role_users:
                    risks.append(
                        {
                            "risk_type": "create_role_privilege",
                            "severity": "high",
                            "description": "Non-superusers with CREATEROLE privilege can create and manage other roles",
                            "count": len(create_role_users),
                            "details": create_role_users,
                        }
                    )

                # Users with BYPASSRLS privilege
                cursor.execute("""
                    SELECT rolname
                    FROM pg_roles
                    WHERE rolbypassrls = true
                    AND NOT rolsuper
                """)

                bypass_rls_users = [row["rolname"] for row in cursor.fetchall()]
                if bypass_rls_users:
                    risks.append(
                        {
                            "risk_type": "bypass_rls_privilege",
                            "severity": "high",
                            "description": "Users that can bypass row-level security policies",
                            "count": len(bypass_rls_users),
                            "details": bypass_rls_users,
                        }
                    )

                return risks

        except Exception as e:
            self.logger.error(f"Failed to get privilege escalation analysis: {e}")
            return []

    def _get_default_privileges(self) -> List[Dict[str, Any]]:
        """Get default privileges configuration."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        r.rolname as role_name,
                        n.nspname as schema_name,
                        defaclobjtype as object_type,
                        defaclacl as default_acl,
                        (aclexplode(defaclacl)).grantee::regrole as grantee,
                        (aclexplode(defaclacl)).grantor::regrole as grantor,
                        (aclexplode(defaclacl)).privilege_type as privilege_type,
                        (aclexplode(defaclacl)).is_grantable as is_grantable
                    FROM pg_default_acl d
                    JOIN pg_roles r ON d.defaclrole = r.oid
                    LEFT JOIN pg_namespace n ON d.defaclnamespace = n.oid
                    WHERE defaclacl IS NOT NULL
                    ORDER BY r.rolname, n.nspname, defaclobjtype
                """)

                return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Failed to get default privileges: {e}")
            return []

    def _get_row_level_security(self) -> Dict[str, Any]:
        """Analyze row-level security configuration."""
        try:
            with self.connection.cursor() as cursor:
                # Tables with RLS enabled
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        c.relrowsecurity as rls_enabled,
                        c.relforcerowsecurity as rls_forced
                    FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE c.relkind = 'r'
                    AND (c.relrowsecurity = true OR c.relforcerowsecurity = true)
                    AND n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname
                """)
                rls_tables = [dict(row) for row in cursor.fetchall()]

                # RLS policies
                cursor.execute("""
                    SELECT
                        n.nspname as schema_name,
                        c.relname as table_name,
                        pol.polname as policy_name,
                        pol.polcmd as command,
                        pol.polpermissive as is_permissive,
                        pol.polroles as roles,
                        pg_get_expr(pol.polqual, pol.polrelid) as policy_expression,
                        pg_get_expr(pol.polwithcheck, pol.polrelid) as with_check_expression
                    FROM pg_policy pol
                    JOIN pg_class c ON pol.polrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE n.nspname NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
                    ORDER BY n.nspname, c.relname, pol.polname
                """)
                rls_policies = [dict(row) for row in cursor.fetchall()]

                return {
                    "rls_enabled_tables": rls_tables,
                    "rls_policies": rls_policies,
                    "summary": {
                        "tables_with_rls": len(rls_tables),
                        "total_policies": len(rls_policies),
                    },
                }

        except Exception as e:
            self.logger.error(f"Failed to get row-level security analysis: {e}")
            return {"error": str(e)}

    def _get_ssl_configuration(self) -> Dict[str, Any]:
        """Get SSL/TLS configuration details."""
        try:
            with self.connection.cursor() as cursor:
                ssl_settings = [
                    "ssl",
                    "ssl_cert_file",
                    "ssl_key_file",
                    "ssl_ca_file",
                    "ssl_crl_file",
                    "ssl_prefer_server_ciphers",
                    "ssl_min_protocol_version",
                    "ssl_max_protocol_version",
                    "ssl_ecdh_curve",
                    "ssl_dh_params_file",
                    "ssl_passphrase_command",
                    "ssl_passphrase_command_supports_reload",
                ]

                cursor.execute(
                    """
                    SELECT name, setting, source, context
                    FROM pg_settings
                    WHERE name = ANY(%s)
                """,
                    (ssl_settings,),
                )

                ssl_config = {row["name"]: dict(row) for row in cursor.fetchall()}

                # Check if SSL is actually being used
                try:
                    cursor.execute("""
                        SELECT
                            COUNT(*) as total_connections,
                            COUNT(*) FILTER (WHERE ssl = true) as ssl_connections
                        FROM pg_stat_ssl
                        JOIN pg_stat_activity ON pg_stat_ssl.pid = pg_stat_activity.pid
                    """)
                    ssl_usage = dict(cursor.fetchone())
                except Exception:
                    ssl_usage = None

                return {"ssl_configuration": ssl_config, "ssl_usage": ssl_usage}

        except Exception as e:
            self.logger.error(f"Failed to get SSL configuration: {e}")
            return {"error": str(e)}
