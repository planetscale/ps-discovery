"""
MySQL Configuration Analysis Module
Analyzes MySQL server configuration, version info, variables, and cloud platform detection.
"""

from typing import Dict, Any

from planetscale_discovery.common.base_analyzer import DatabaseAnalyzer


class MySQLConfigAnalyzer(DatabaseAnalyzer):
    """Analyzes MySQL configuration and version information."""

    _variables_cache: Dict[str, str]

    def analyze(self) -> Dict[str, Any]:
        cloud_platform = self._detect_cloud_platform()
        results = {
            "version_info": self._get_version_info(),
            "all_variables": self._get_all_variables(),
            "cloud_platform": cloud_platform,
        }

        # Platform-specific collection
        platform = cloud_platform.get("platform", "")
        if platform in ("aws_rds", "aws_aurora"):
            results["rds_config"] = self._get_rds_configuration()

        return results

    def _get_version_info(self) -> Dict[str, Any]:
        try:
            variables = self._get_all_variables()
            version = variables.get("version", "")
            version_comment = variables.get("version_comment", "")
            version_compile_os = variables.get("version_compile_os", "")
            version_compile_machine = variables.get("version_compile_machine", "")
            hostname = variables.get("hostname", "")
            datadir = variables.get("datadir", "")

            # Parse version into major.minor.patch
            parts = version.split("-")[0].split(".")
            major = int(parts[0]) if len(parts) > 0 else 0
            minor = int(parts[1]) if len(parts) > 1 else 0
            patch = int(parts[2]) if len(parts) > 2 else 0

            # Detect distribution from version string
            distribution = "MySQL"
            version_lower = version.lower()
            comment_lower = version_comment.lower()
            if "mariadb" in version_lower or "mariadb" in comment_lower:
                distribution = "MariaDB"
            elif "percona" in version_lower or "percona" in comment_lower:
                distribution = "Percona Server"
            elif "aurora" in comment_lower:
                distribution = "Amazon Aurora"

            return {
                "version_string": version,
                "version_comment": version_comment,
                "major_version": major,
                "minor_version": minor,
                "patch_version": patch,
                "version_num": major * 10000 + minor * 100 + patch,
                "distribution": distribution,
                "compile_os": version_compile_os,
                "compile_machine": version_compile_machine,
                "hostname": hostname,
                "datadir": datadir,
            }
        except Exception as e:
            self.add_error(f"Failed to get version info: {e}", e)
            return {"error": str(e)}

    def _get_all_variables(self) -> Dict[str, str]:
        if hasattr(self, "_variables_cache"):
            return self._variables_cache

        try:
            cursor = self.connection.cursor()
            cursor.execute("SHOW GLOBAL VARIABLES")
            rows = cursor.fetchall()
            cursor.close()
            self._variables_cache = {row[0]: row[1] for row in rows}
            return self._variables_cache
        except Exception as e:
            self.add_error(f"Failed to get variables: {e}", e)
            return {}

    def _detect_cloud_platform(self) -> Dict[str, Any]:
        variables = self._get_all_variables()
        version_comment = variables.get("version_comment", "").lower()
        datadir = variables.get("datadir", "").lower()
        version = variables.get("version", "").lower()

        platform = "on-premise"
        details = {}

        if "aurora_version" in variables or "aurora" in version_comment:
            platform = "aws_aurora"
            details["service"] = "Amazon Aurora MySQL"
            aurora_ver = variables.get("aurora_version", "")
            if aurora_ver:
                details["aurora_version"] = aurora_ver
        elif "rds" in version_comment or "/rdsdbdata/" in datadir:
            platform = "aws_rds"
            details["service"] = "Amazon RDS for MySQL"
        elif "cloud sql" in version_comment or (
            "/mysql/" in datadir and "cloudsql" in datadir
        ):
            platform = "gcp_cloudsql"
            details["service"] = "Google Cloud SQL for MySQL"
        elif (
            "planetscale" in version_comment
            or "vitess" in version.lower()
            or "/vt/vtdataroot/" in datadir
        ):
            platform = "planetscale"
            details["service"] = "PlanetScale (Vitess)"
        elif "azure" in version_comment:
            platform = "azure"
            details["service"] = "Azure Database for MySQL"
        elif "mariadb" in version.lower():
            details["note"] = "MariaDB distribution detected"

        return {
            "platform": platform,
            "details": details,
        }

    def _get_rds_configuration(self) -> Dict[str, Any]:
        """Collect RDS/Aurora-specific configuration via mysql.rds_show_configuration."""
        rds_config: Dict[str, Any] = {}

        try:
            cursor = self.connection.cursor()
            cursor.execute("CALL mysql.rds_show_configuration")
            rows = cursor.fetchall()
            cursor.close()

            for row in rows:
                # rds_show_configuration returns (name, value, description)
                if len(row) >= 2:
                    rds_config[row[0]] = {
                        "value": row[1],
                        "description": row[2] if len(row) > 2 else None,
                    }
        except Exception as e:
            self.add_warning(
                f"Could not read RDS configuration: {e}. "
                "Grant EXECUTE ON PROCEDURE mysql.rds_show_configuration "
                "to the discovery user for full RDS binlog retention data."
            )
            rds_config["error"] = str(e)

        return rds_config
