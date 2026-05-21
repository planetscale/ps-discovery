"""
MySQL Environment Discovery Tool
Main coordinator for comprehensive MySQL environment analysis.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any

from .mysql_analyzers.config_analyzer import MySQLConfigAnalyzer
from .mysql_analyzers.schema_analyzer import MySQLSchemaAnalyzer
from .mysql_analyzers.performance_analyzer import MySQLPerformanceAnalyzer
from .mysql_analyzers.replication_analyzer import MySQLReplicationAnalyzer
from .mysql_analyzers.feature_analyzer import MySQLFeatureAnalyzer
from .mysql_analyzers.security_analyzer import MySQLSecurityAnalyzer


class MySQLDiscoveryTool:
    """MySQL discovery tool wrapper for unified CLI."""

    def __init__(self, config):
        self.config = config

    def discover(self):
        config_dict = {
            "host": self.config.mysql.host,
            "port": self.config.mysql.port,
            "user": self.config.mysql.username,
            "password": self.config.mysql.password,
            "database": self.config.mysql.database,
            "ssl_mode": self.config.mysql.ssl_mode,
            "ssl_ca": self.config.mysql.ssl_ca,
            "ssl_cert": self.config.mysql.ssl_cert,
            "ssl_key": self.config.mysql.ssl_key,
            "connect_timeout": self.config.mysql.connection_timeout,
        }

        discovery = MySQLDiscovery(config_dict)
        if not discovery.connect():
            raise ConnectionError("Failed to connect to MySQL database")

        try:
            analysis_modules = getattr(
                self.config,
                "mysql_analyzers",
                [
                    "config",
                    "schema",
                    "performance",
                    "replication",
                    "security",
                    "features",
                ],
            )

            return discovery.run_analysis(analysis_modules)
        finally:
            discovery.close()


class MySQLDiscovery:
    """Main MySQL environment discovery coordinator."""

    def __init__(self, connection_params: Dict[str, Any]):
        self.connection_params = connection_params
        self.connection = None
        self.results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "discovery_version": "1.0.0",
            "engine": "mysql",
            "connection_info": {},
            "analysis_results": {},
            "analysis_gaps": [],
        }
        self.logger = logging.getLogger(__name__)

    def connect(self) -> bool:
        try:
            import pymysql  # type: ignore[import-untyped]

            params = {
                "host": self.connection_params.get("host", "localhost"),
                "port": self.connection_params.get("port", 3306),
                "user": self.connection_params.get("user", "root"),
                "password": self.connection_params.get("password", ""),
                "connect_timeout": self.connection_params.get("connect_timeout", 30),
                "charset": "utf8mb4",
                "autocommit": True,
            }

            # SSL handling — map MySQL-style ssl_mode to PyMySQL params.
            #   disabled              -> ssl_disabled=True, no TLS
            #   preferred / required  -> ssl={}, TLS on, no cert chain verification
            #   verify-ca             -> ssl_ca required, verify chain only
            #   verify-identity       -> ssl_ca required, verify chain + hostname
            ssl_mode = (self.connection_params.get("ssl_mode", "") or "").lower()
            ssl_ca = self.connection_params.get("ssl_ca")
            ssl_cert = self.connection_params.get("ssl_cert")
            ssl_key = self.connection_params.get("ssl_key")

            if ssl_mode in ("", "disabled"):
                params["ssl_disabled"] = True
            elif ssl_mode in ("preferred", "required"):
                params["ssl"] = {}
            elif ssl_mode in ("verify-ca", "verify-identity"):
                if not ssl_ca:
                    self.logger.error(
                        f"ssl_mode={ssl_mode!r} requires ssl_ca to be set "
                        "(--mysql-ssl-ca or MYSQL_SSL_CA)"
                    )
                    return False
                ssl_dict: Dict[str, Any] = {
                    "ca": ssl_ca,
                    "check_hostname": ssl_mode == "verify-identity",
                }
                if ssl_cert:
                    ssl_dict["cert"] = ssl_cert
                if ssl_key:
                    ssl_dict["key"] = ssl_key
                params["ssl"] = ssl_dict
            else:
                self.logger.warning(
                    f"Unknown ssl_mode {ssl_mode!r}; treating as disabled"
                )
                params["ssl_disabled"] = True

            # Connect to specific database, or information_schema as fallback
            db = self.connection_params.get("database", "")
            params["database"] = db if db else "information_schema"

            self.connection = pymysql.connect(**params)

            # Get connection info
            cursor = self.connection.cursor()
            cursor.execute("SELECT VERSION(), CURRENT_USER(), DATABASE()")
            row = cursor.fetchone()
            cursor.close()

            self.results["connection_info"] = {
                "version_string": row[0] if row else "",
                "current_user": row[1] if row else "",
                "current_database": row[2] if row else None,
            }

            self.logger.info(f"Connected to MySQL: {row[0] if row else 'unknown'}")
            return True

        except ImportError:
            self.logger.error(
                "PyMySQL is not installed. Install with: pip install 'planetscale-discovery-tools[mysql]'"
            )
            return False
        except Exception as e:
            self.logger.error(f"Failed to connect to MySQL: {e}")
            return False

    def run_analysis(self, modules: list = None) -> Dict[str, Any]:
        if not self.connection:
            raise RuntimeError("No database connection established")

        available_modules = {
            "config": MySQLConfigAnalyzer,
            "schema": MySQLSchemaAnalyzer,
            "performance": MySQLPerformanceAnalyzer,
            "replication": MySQLReplicationAnalyzer,
            "security": MySQLSecurityAnalyzer,
            "features": MySQLFeatureAnalyzer,
        }

        if modules is None:
            modules = list(available_modules.keys())

        self.logger.info(f"Running MySQL analysis modules: {modules}")

        for module_name in modules:
            if module_name not in available_modules:
                self.logger.warning(f"Unknown MySQL module: {module_name}")
                continue

            try:
                self.logger.info(f"Running {module_name} analysis...")
                analyzer = available_modules[module_name](self.connection)
                module_results = analyzer.analyze()
                self.results["analysis_results"][module_name] = module_results

                # Extract gaps
                self._extract_analysis_gaps(module_name, module_results)

                self.logger.info(f"Completed {module_name} analysis")

            except Exception as e:
                self.logger.error(f"Failed to run {module_name} analysis: {e}")
                self.results["analysis_results"][module_name] = {
                    "error": str(e),
                    "status": "failed",
                }
                self._add_analysis_gap(
                    module_name,
                    "module_failure",
                    f"Entire {module_name} analysis failed",
                    str(e),
                    "high",
                )

        return self.results

    def _add_analysis_gap(
        self,
        module: str,
        gap_type: str,
        description: str,
        error_msg: str,
        severity: str,
    ):
        self.results["analysis_gaps"].append(
            {
                "module": module,
                "type": gap_type,
                "description": description,
                "error_message": error_msg,
                "severity": severity,
            }
        )

    def _extract_analysis_gaps(self, module_name: str, module_results: dict):
        if not isinstance(module_results, dict):
            return
        self._scan_for_errors(module_name, module_results, "")

    def _scan_for_errors(self, module_name: str, data: Any, path: str):
        if isinstance(data, dict):
            if "error" in data:
                feature_name = path or "general"
                self._add_analysis_gap(
                    module_name,
                    "analysis_error",
                    f"Failed to analyze {feature_name} in {module_name}",
                    str(data["error"]),
                    "medium",
                )
                return  # don't recurse into an error subtree
            for key, value in data.items():
                new_path = f"{path}.{key}" if path else key
                self._scan_for_errors(module_name, value, new_path)
        elif isinstance(data, list):
            for i, item in enumerate(data):
                self._scan_for_errors(module_name, item, f"{path}[{i}]")

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None
