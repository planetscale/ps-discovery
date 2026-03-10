"""
Configuration management for PlanetScale Discovery Tools.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import json

from ..common.utils import load_config_file


@dataclass
class DataSizeConfig:
    """Data size analysis configuration."""

    enabled: bool = False
    sample_percent: int = 10
    max_table_size_gb: int = 10
    target_tables: List[str] = field(default_factory=list)
    target_schemas: List[str] = field(default_factory=lambda: ["public"])
    check_column_types: List[str] = field(
        default_factory=lambda: ["text", "bytea", "json", "jsonb", "character varying"]
    )
    size_thresholds: Dict[str, int] = field(
        default_factory=lambda: {"1kb": 1024, "64kb": 65536}
    )


@dataclass
class DatabaseConfig:
    """Database connection configuration."""

    host: str = "localhost"
    port: int = 5432
    database: str = ""
    username: str = ""
    password: str = ""
    ssl_mode: str = "prefer"
    connection_timeout: int = 30
    data_size: DataSizeConfig = field(default_factory=DataSizeConfig)


@dataclass
class AWSConfig:
    """AWS provider configuration."""

    enabled: bool = False
    regions: List[str] = field(default_factory=list)
    profile: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None
    role_arn: Optional[str] = None
    external_id: Optional[str] = None
    resources: Dict[str, List[str]] = field(default_factory=dict)
    discover_all: bool = True


@dataclass
class GCPConfig:
    """GCP provider configuration."""

    enabled: bool = False
    project_id: str = ""
    regions: List[str] = field(default_factory=list)
    service_account_key: Optional[str] = None
    application_default: bool = False
    resources: Dict[str, List[str]] = field(default_factory=dict)
    discover_all: bool = True


@dataclass
class SupabaseConfig:
    """Supabase provider configuration."""

    enabled: bool = False
    access_token: Optional[str] = None
    project_ref: Optional[str] = None
    organization_id: Optional[str] = None
    discover_all: bool = True


@dataclass
class HerokuConfig:
    """Heroku provider configuration."""

    enabled: bool = False
    api_key: Optional[str] = None
    target_app: Optional[str] = None
    discover_all: bool = True


@dataclass
class OutputConfig:
    """Output configuration."""

    output_dir: str = "./discovery_output"


@dataclass
class DiscoveryConfig:
    """Main discovery configuration."""

    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    aws: AWSConfig = field(default_factory=AWSConfig)
    gcp: GCPConfig = field(default_factory=GCPConfig)
    supabase: SupabaseConfig = field(default_factory=SupabaseConfig)
    heroku: HerokuConfig = field(default_factory=HerokuConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    modules: List[str] = field(default_factory=lambda: ["database", "cloud"])
    log_level: str = "INFO"
    log_file: Optional[str] = None
    target_database: Optional[str] = None


class ConfigManager:
    """Configuration manager for discovery tools."""

    def __init__(self, config_path: Optional[str] = None):
        """Initialize configuration manager."""
        self.config_path = config_path
        self.config: Optional[DiscoveryConfig] = None

    def load_config(
        self, config_path: Optional[str] = None, validate: bool = True
    ) -> DiscoveryConfig:
        """Load configuration from file or environment."""
        if config_path:
            self.config_path = config_path

        if self.config_path and Path(self.config_path).exists():
            # Load from file
            config_data = load_config_file(self.config_path)
            try:
                self.config = self._parse_config_dict(config_data)
            except Exception as e:
                docs_base = "https://github.com/planetscale/ps-discovery/blob/main"
                raise ValueError(
                    f"Failed to parse configuration file '{self.config_path}': {e}\n"
                    "\n"
                    "Hint: Check that all config sections have valid values. "
                    "Empty sections (e.g. 'credentials:' with no value) should "
                    "be removed or filled in.\n"
                    "\n"
                    "Quick fix: Run 'ps-discovery config-template' to "
                    "generate a valid example configuration.\n"
                    "\n"
                    "Provider setup guides:\n"
                    f"  AWS:      {docs_base}/docs/providers/aws.md\n"
                    f"  GCP:      {docs_base}/docs/providers/gcp.md\n"
                    f"  Supabase: {docs_base}/docs/providers/supabase.md\n"
                    f"  Heroku:   {docs_base}/docs/providers/heroku.md"
                ) from e
        else:
            # Load from environment or use defaults
            self.config = self._load_from_environment()

        if validate:
            self._validate_config()
        return self.config

    def _parse_config_dict(self, config_data: Dict[str, Any]) -> DiscoveryConfig:
        """Parse configuration dictionary into DiscoveryConfig."""
        # Parse database config
        db_config = DatabaseConfig()
        if "database" in config_data:
            db_data = config_data["database"] or {}
            db_config.host = db_data.get("host", db_config.host)
            db_config.port = db_data.get("port", db_config.port)
            db_config.database = db_data.get("database", db_config.database)
            db_config.username = db_data.get("username", db_config.username)
            db_config.password = db_data.get("password", db_config.password)
            db_config.ssl_mode = db_data.get("ssl_mode", db_config.ssl_mode)
            db_config.connection_timeout = db_data.get(
                "connection_timeout", db_config.connection_timeout
            )

            # Parse data_size config
            if "data_size" in db_data:
                ds_data = db_data["data_size"] or {}
                data_size_config = DataSizeConfig()
                data_size_config.enabled = ds_data.get(
                    "enabled", data_size_config.enabled
                )
                data_size_config.sample_percent = ds_data.get(
                    "sample_percent", data_size_config.sample_percent
                )
                data_size_config.max_table_size_gb = ds_data.get(
                    "max_table_size_gb", data_size_config.max_table_size_gb
                )
                data_size_config.target_tables = ds_data.get(
                    "target_tables", data_size_config.target_tables
                )
                data_size_config.target_schemas = ds_data.get(
                    "target_schemas", data_size_config.target_schemas
                )
                data_size_config.check_column_types = ds_data.get(
                    "check_column_types", data_size_config.check_column_types
                )
                data_size_config.size_thresholds = ds_data.get(
                    "size_thresholds", data_size_config.size_thresholds
                )
                db_config.data_size = data_size_config

        # Parse AWS config
        aws_config = AWSConfig()
        if "providers" in config_data and "aws" in (config_data["providers"] or {}):
            aws_data = config_data["providers"]["aws"] or {}
            aws_config.enabled = aws_data.get("enabled", aws_config.enabled)
            aws_config.regions = aws_data.get("regions", aws_config.regions)
            aws_config.discover_all = aws_data.get(
                "discover_all", aws_config.discover_all
            )
            aws_config.resources = aws_data.get("resources", aws_config.resources)

            # Parse credentials
            if "credentials" in aws_data:
                creds = aws_data["credentials"] or {}
                aws_config.profile = creds.get("profile")
                aws_config.access_key_id = creds.get("access_key_id")
                aws_config.secret_access_key = creds.get("secret_access_key")
                aws_config.session_token = creds.get("session_token")
                aws_config.role_arn = creds.get("role_arn")
                aws_config.external_id = creds.get("external_id")

        # Parse GCP config
        gcp_config = GCPConfig()
        if "providers" in config_data and "gcp" in (config_data["providers"] or {}):
            gcp_data = config_data["providers"]["gcp"] or {}
            gcp_config.enabled = gcp_data.get("enabled", gcp_config.enabled)
            gcp_config.project_id = gcp_data.get("project_id", gcp_config.project_id)
            gcp_config.regions = gcp_data.get("regions", gcp_config.regions)
            gcp_config.discover_all = gcp_data.get(
                "discover_all", gcp_config.discover_all
            )
            gcp_config.resources = gcp_data.get("resources", gcp_config.resources)

            # Parse credentials
            if "credentials" in gcp_data:
                creds = gcp_data["credentials"] or {}
                gcp_config.service_account_key = creds.get("service_account_key")
                gcp_config.application_default = creds.get("application_default", False)

        # Parse Supabase config
        supabase_config = SupabaseConfig()
        if "providers" in config_data and "supabase" in (
            config_data["providers"] or {}
        ):
            supabase_data = config_data["providers"]["supabase"] or {}
            supabase_config.enabled = supabase_data.get(
                "enabled", supabase_config.enabled
            )
            supabase_config.access_token = supabase_data.get("access_token")
            supabase_config.project_ref = supabase_data.get("project_ref")
            supabase_config.organization_id = supabase_data.get("organization_id")
            supabase_config.discover_all = supabase_data.get(
                "discover_all", supabase_config.discover_all
            )

        # Parse Heroku config
        heroku_config = HerokuConfig()
        if "providers" in config_data and "heroku" in (config_data["providers"] or {}):
            heroku_data = config_data["providers"]["heroku"] or {}
            heroku_config.enabled = heroku_data.get("enabled", heroku_config.enabled)
            heroku_config.api_key = heroku_data.get("api_key")
            heroku_config.target_app = heroku_data.get("target_app")
            heroku_config.discover_all = heroku_data.get(
                "discover_all", heroku_config.discover_all
            )

        # Parse output config
        output_config = OutputConfig()
        if "output" in config_data:
            output_data = config_data["output"] or {}
            output_config.output_dir = output_data.get(
                "output_dir", output_config.output_dir
            )

        return DiscoveryConfig(
            database=db_config,
            aws=aws_config,
            gcp=gcp_config,
            supabase=supabase_config,
            heroku=heroku_config,
            output=output_config,
            modules=config_data.get("modules", ["database", "cloud"]),
            log_level=config_data.get("log_level", "INFO"),
            log_file=config_data.get("log_file"),
            target_database=config_data.get("target_database"),
        )

    def _load_from_environment(self) -> DiscoveryConfig:
        """Load configuration from environment variables."""
        # Database configuration
        db_config = DatabaseConfig(
            host=os.getenv("PGHOST", "localhost"),
            port=int(os.getenv("PGPORT", "5432")),
            database=os.getenv("PGDATABASE", ""),
            username=os.getenv("PGUSER", ""),
            password=os.getenv("PGPASSWORD", ""),
            ssl_mode=os.getenv("PGSSLMODE", "prefer"),
        )

        # AWS configuration
        aws_config = AWSConfig(
            enabled=os.getenv("AWS_ENABLED", "false").lower() == "true",
            regions=(
                os.getenv("AWS_REGIONS", "").split(",")
                if os.getenv("AWS_REGIONS")
                else []
            ),
            access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            session_token=os.getenv("AWS_SESSION_TOKEN"),
            profile=os.getenv("AWS_PROFILE"),
        )

        # GCP configuration
        gcp_config = GCPConfig(
            enabled=os.getenv("GCP_ENABLED", "false").lower() == "true",
            project_id=os.getenv("GCP_PROJECT_ID", ""),
            regions=(
                os.getenv("GCP_REGIONS", "").split(",")
                if os.getenv("GCP_REGIONS")
                else []
            ),
            service_account_key=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
            application_default=os.getenv("GCP_USE_ADC", "false").lower() == "true",
        )

        # Supabase configuration
        supabase_config = SupabaseConfig(
            enabled=os.getenv("SUPABASE_ENABLED", "false").lower() == "true",
            access_token=os.getenv("SUPABASE_ACCESS_TOKEN"),
            project_ref=os.getenv("SUPABASE_PROJECT_REF"),
            organization_id=os.getenv("SUPABASE_ORGANIZATION_ID"),
        )

        # Heroku configuration
        heroku_config = HerokuConfig(
            enabled=os.getenv("HEROKU_ENABLED", "false").lower() == "true",
            api_key=os.getenv("HEROKU_API_KEY"),
            target_app=os.getenv("HEROKU_TARGET_APP"),
        )

        # Output configuration
        output_config = OutputConfig(
            output_dir=os.getenv("DISCOVERY_OUTPUT_DIR", "./discovery_output"),
        )

        return DiscoveryConfig(
            database=db_config,
            aws=aws_config,
            gcp=gcp_config,
            supabase=supabase_config,
            heroku=heroku_config,
            output=output_config,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=os.getenv("LOG_FILE"),
            target_database=os.getenv("TARGET_DATABASE"),
        )

    def _validate_config(self) -> None:
        """Validate configuration settings."""
        if not self.config:
            raise ValueError("Configuration not loaded")

        errors = []

        # Validate modules
        valid_modules = {"database", "cloud"}
        for module in self.config.modules:
            if module not in valid_modules:
                errors.append(f"Invalid module: {module}")

        # Validate database config if database module enabled
        if "database" in self.config.modules:
            if not self.config.database.database:
                errors.append("Database name is required for database discovery")

        # Validate cloud providers if cloud module enabled
        if "cloud" in self.config.modules:
            if not (
                self.config.aws.enabled
                or self.config.gcp.enabled
                or self.config.supabase.enabled
                or self.config.heroku.enabled
            ):
                errors.append(
                    "At least one cloud provider must be enabled for cloud discovery.\n"
                    "  Enable a provider in your config file (e.g. providers.aws.enabled: true)\n"
                    "  or pass --providers on the command line (e.g. --providers heroku).\n"
                    "  Supported providers: aws, gcp, supabase, heroku"
                )

        if errors:
            raise ValueError("Configuration validation failed:\n" + "\n".join(errors))

    def save_config_template(
        self, output_path: str, providers: list[str] = None
    ) -> None:
        """Save a configuration template file.

        Args:
            output_path: Path where the template should be saved
            providers: List of cloud providers to include (aws, gcp, supabase, heroku).
                      If None or empty, no cloud provider sections are generated.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if providers is None:
            providers = []

        # Normalize provider names
        providers = [p.strip().lower() for p in providers]

        if output_path.suffix.lower() in [".yaml", ".yml"]:
            # Start with base template
            template_yaml = """# PlanetScale Discovery Configuration
# Generated for your selected providers

# Database connection settings (required for database discovery)
database:
  host: localhost
  port: 5432
  database: your_database_name
  username: your_username
  password: your_password
  ssl_mode: prefer  # Options: disable, allow, prefer, require, verify-ca, verify-full
"""

            # Add cloud provider sections if any were specified
            if providers:
                template_yaml += "\n# Cloud provider settings\nproviders:\n"

                if "aws" in providers:
                    template_yaml += """  aws:
    enabled: true
    regions:
      - us-west-2
      - us-east-1
    credentials:
      # Use AWS CLI profile (recommended)
      profile: default
      # Or use access keys (not recommended for production):
      # access_key_id: your_access_key
      # secret_access_key: your_secret_key
    discover_all: true  # Discover all RDS/Aurora instances in specified regions
    # Or specify specific resources:
    # resources:
    #   rds_instances:
    #     - my-rds-instance
    #   aurora_clusters:
    #     - my-aurora-cluster

"""

                if "gcp" in providers:
                    template_yaml += """  gcp:
    enabled: true
    project_id: your-gcp-project-id
    regions:
      - us-central1
      - us-west1
    credentials:
      # Path to service account key JSON file
      service_account_key: /path/to/service-account-key.json
      # Or use application default credentials:
      # application_default: true
    discover_all: true  # Discover all Cloud SQL/AlloyDB instances
    # Or specify specific resources:
    # resources:
    #   cloud_sql_instances:
    #     - my-cloudsql-instance
    #   alloydb_clusters:
    #     - my-alloydb-cluster

"""

                if "supabase" in providers:
                    template_yaml += """  supabase:
    enabled: true
    # Get your access token from: https://app.supabase.com/account/tokens
    access_token: your_supabase_access_token
    # Optional: specify organization ID to filter projects
    # organization_id: your-org-id

"""

                if "heroku" in providers:
                    template_yaml += """  heroku:
    enabled: true
    # Get your API key from: https://dashboard.heroku.com/account
    # Or set HEROKU_API_KEY environment variable
    api_key: your_heroku_api_key
    # Optional: analyze a specific app only
    # target_app: my-app-name
    discover_all: true  # Discover all apps with Heroku Postgres add-ons

"""

            # Add output and logging settings
            template_yaml += """# Output settings
output:
  output_dir: ./discovery_output

# Logging settings
log_level: INFO  # Options: DEBUG, INFO, WARNING, ERROR
# log_file: discovery.log  # Uncomment to write logs to file
"""

            with open(output_path, "w") as f:
                f.write(template_yaml)
        else:
            # JSON template
            template = {
                "database": {
                    "host": "localhost",
                    "port": 5432,
                    "database": "your_database_name",
                    "username": "your_username",
                    "password": "your_password",
                    "ssl_mode": "prefer",
                },
                "output": {
                    "output_dir": "./discovery_output",
                },
                "log_level": "INFO",
            }

            # Add cloud provider sections if any were specified
            if providers:
                template["providers"] = {}

                if "aws" in providers:
                    template["providers"]["aws"] = {
                        "enabled": True,
                        "regions": ["us-west-2", "us-east-1"],
                        "credentials": {"profile": "default"},
                        "discover_all": True,
                    }

                if "gcp" in providers:
                    template["providers"]["gcp"] = {
                        "enabled": True,
                        "project_id": "your-gcp-project-id",
                        "regions": ["us-central1", "us-west1"],
                        "credentials": {
                            "service_account_key": "/path/to/service-account-key.json"
                        },
                        "discover_all": True,
                    }

                if "supabase" in providers:
                    template["providers"]["supabase"] = {
                        "enabled": True,
                        "credentials": {"access_token": "your_supabase_access_token"},
                    }

                if "heroku" in providers:
                    template["providers"]["heroku"] = {
                        "enabled": True,
                        "api_key": "your_heroku_api_key",
                        "discover_all": True,
                    }

            with open(output_path, "w") as f:
                json.dump(template, f, indent=2)

    def get_config(self) -> DiscoveryConfig:
        """Get current configuration."""
        if not self.config:
            self.load_config()
        return self.config

    def validate_config(self) -> None:
        """Public method to validate configuration."""
        self._validate_config()
