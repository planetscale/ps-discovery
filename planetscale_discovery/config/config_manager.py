"""
Configuration management for PlanetScale Discovery Tools.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

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
    excluded_databases: List[str] = field(default_factory=list)


@dataclass
class MySQLConfig:
    """MySQL connection configuration."""

    host: str = "localhost"
    port: int = 3306
    database: str = ""
    username: str = "root"
    password: str = ""
    ssl_mode: str = "disabled"
    ssl_ca: Optional[str] = None
    ssl_cert: Optional[str] = None
    ssl_key: Optional[str] = None
    connection_timeout: int = 30


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
class NeonConfig:
    """Neon provider configuration."""

    enabled: bool = False
    api_key: Optional[str] = None
    target_project: Optional[str] = None
    org_id: Optional[str] = None
    discover_all: bool = True


@dataclass
class OutputConfig:
    """Output configuration."""

    output_dir: str = "./discovery_output"


@dataclass
class DiscoveryConfig:
    """Main discovery configuration."""

    engine: str = "postgres"  # "postgres" or "mysql"
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    mysql: MySQLConfig = field(default_factory=MySQLConfig)
    aws: AWSConfig = field(default_factory=AWSConfig)
    gcp: GCPConfig = field(default_factory=GCPConfig)
    supabase: SupabaseConfig = field(default_factory=SupabaseConfig)
    heroku: HerokuConfig = field(default_factory=HerokuConfig)
    neon: NeonConfig = field(default_factory=NeonConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    # None means "unspecified" — the config file did not declare `modules:`.
    # resolve_modules() then infers what to run from the config contents.
    modules: Optional[List[str]] = None
    log_level: str = "INFO"
    log_file: Optional[str] = None
    target_database: Optional[str] = None


def resolve_modules(
    config: DiscoveryConfig, command: Optional[str] = None
) -> List[str]:
    """Resolve which discovery modules to run.

    The config file is the source of truth; the CLI subcommand is an optional
    override. Precedence (first match wins):

    1. Explicit subcommand: ``database`` -> ["database"], ``cloud`` -> ["cloud"],
       ``both`` -> ["database", "cloud"].
    2. Explicit ``modules:`` declared in the config file (``config.modules`` is
       not None).
    3. Inference from config contents: include "cloud" if any cloud provider is
       enabled; include "database" if the active engine's connection looks
       configured.

    Returns a list that is a subset of {"database", "cloud"}, ordered
    [database, cloud]. An empty result means nothing is configured to run and
    the caller should error politely.
    """
    # 1. Explicit subcommand wins.
    if command == "database":
        return ["database"]
    if command == "cloud":
        return ["cloud"]
    if command == "both":
        return ["database", "cloud"]

    # 2. Explicit modules from the config file.
    if config.modules is not None:
        order = {"database": 0, "cloud": 1}
        return sorted((m for m in config.modules if m in order), key=lambda m: order[m])

    # 3. Infer from config contents.
    modules: List[str] = []

    if _database_is_configured(config):
        modules.append("database")

    if any(
        provider.enabled
        for provider in (
            config.aws,
            config.gcp,
            config.supabase,
            config.heroku,
            config.neon,
        )
    ):
        modules.append("cloud")

    return modules


def _database_is_configured(config: DiscoveryConfig) -> bool:
    """Return True if the active engine's connection looks meaningfully set.

    PostgreSQL: a database name is required (matches the run requirement in
    cli.run_database_discovery). MySQL: an empty database means "all databases",
    so a non-default host is the reliable signal that a connection was set up.
    """
    if config.engine == "mysql":
        return bool(config.mysql.database) or config.mysql.host != "localhost"
    return bool(config.database.database)


class ConfigManager:
    """Configuration manager for discovery tools."""

    def __init__(self, config_path: Optional[str] = None):
        """Initialize configuration manager."""
        self.config_path = config_path
        self.config: Optional[DiscoveryConfig] = None

    def load_config(
        self,
        config_path: Optional[str] = None,
        validate: bool = True,
        config_required: bool = False,
    ) -> DiscoveryConfig:
        """Load configuration from file or environment.

        When ``config_required`` is True and a config path is set but the file
        does not exist, raise instead of silently falling back to environment
        variables/defaults. This is used when the user explicitly passed
        ``--config`` so a typo'd path fails loudly rather than running with an
        unexpected configuration.
        """
        if config_path:
            self.config_path = config_path

        if config_required and self.config_path and not Path(self.config_path).exists():
            raise ValueError(f"Configuration file not found: {self.config_path}")

        if self.config_path and Path(self.config_path).exists():
            # Load from file
            config_data = load_config_file(self.config_path)
            try:
                self.config = self._parse_config_dict(config_data)
            except Exception as e:
                docs_base = "https://github.com/planetscale/planetscale-discovery-cli-dev/blob/main"
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
                    f"  Heroku:   {docs_base}/docs/providers/heroku.md\n"
                    f"  Neon:     {docs_base}/docs/providers/neon.md"
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
            db_config.excluded_databases = db_data.get(
                "excluded_databases", db_config.excluded_databases
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

        # Parse MySQL config
        mysql_config = MySQLConfig()
        if "mysql" in config_data:
            my_data = config_data["mysql"] or {}
            mysql_config.host = my_data.get("host", mysql_config.host)
            mysql_config.port = my_data.get("port", mysql_config.port)
            mysql_config.database = my_data.get("database", mysql_config.database)
            mysql_config.username = my_data.get("username", mysql_config.username)
            mysql_config.password = my_data.get("password", mysql_config.password)
            mysql_config.ssl_mode = my_data.get("ssl_mode", mysql_config.ssl_mode)
            mysql_config.ssl_ca = my_data.get("ssl_ca", mysql_config.ssl_ca)
            mysql_config.ssl_cert = my_data.get("ssl_cert", mysql_config.ssl_cert)
            mysql_config.ssl_key = my_data.get("ssl_key", mysql_config.ssl_key)
            mysql_config.connection_timeout = my_data.get(
                "connection_timeout", mysql_config.connection_timeout
            )

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

        # Parse Neon config
        neon_config = NeonConfig()
        if "providers" in config_data and "neon" in (config_data["providers"] or {}):
            neon_data = config_data["providers"]["neon"] or {}
            neon_config.enabled = neon_data.get("enabled", neon_config.enabled)
            neon_config.api_key = neon_data.get("api_key")
            neon_config.target_project = neon_data.get("target_project")
            neon_config.org_id = neon_data.get("org_id")
            neon_config.discover_all = neon_data.get(
                "discover_all", neon_config.discover_all
            )

        # Parse output config
        output_config = OutputConfig()
        if "output" in config_data:
            output_data = config_data["output"] or {}
            output_config.output_dir = output_data.get(
                "output_dir", output_config.output_dir
            )

        return DiscoveryConfig(
            engine=config_data.get("engine", "postgres"),
            database=db_config,
            mysql=mysql_config,
            aws=aws_config,
            gcp=gcp_config,
            supabase=supabase_config,
            heroku=heroku_config,
            neon=neon_config,
            output=output_config,
            # Absent key -> None (unspecified); resolve_modules() will infer.
            modules=config_data.get("modules"),
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

        # MySQL configuration
        mysql_config = MySQLConfig(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            database=os.getenv("MYSQL_DATABASE", ""),
            username=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            ssl_mode=os.getenv("MYSQL_SSL_MODE", "disabled"),
            ssl_ca=os.getenv("MYSQL_SSL_CA"),
            ssl_cert=os.getenv("MYSQL_SSL_CERT"),
            ssl_key=os.getenv("MYSQL_SSL_KEY"),
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

        # Neon configuration
        neon_config = NeonConfig(
            enabled=os.getenv("NEON_ENABLED", "false").lower() == "true",
            api_key=os.getenv("NEON_API_KEY"),
            target_project=os.getenv("NEON_TARGET_PROJECT"),
            org_id=os.getenv("NEON_ORG_ID"),
        )

        # Output configuration
        output_config = OutputConfig(
            output_dir=os.getenv("DISCOVERY_OUTPUT_DIR", "./discovery_output"),
        )

        return DiscoveryConfig(
            engine=os.getenv("DISCOVERY_ENGINE", "postgres"),
            database=db_config,
            mysql=mysql_config,
            aws=aws_config,
            gcp=gcp_config,
            supabase=supabase_config,
            heroku=heroku_config,
            neon=neon_config,
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

        # Validate engine
        valid_engines = {"postgres", "mysql"}
        if self.config.engine not in valid_engines:
            errors.append(
                f"Invalid engine: {self.config.engine}. "
                f"Valid engines are: {', '.join(sorted(valid_engines))}"
            )

        # Validate modules. At runtime main() sets config.modules to the
        # resolved list before validating; `or []` only guards direct callers
        # that load with validate=True before modules have been resolved.
        modules = self.config.modules or []
        valid_modules = {"database", "cloud"}
        for module in modules:
            if module not in valid_modules:
                errors.append(f"Invalid module: {module}")

        # Validate database config if database module enabled
        if "database" in modules and self.config.engine == "postgres":
            if not self.config.database.database:
                errors.append("Database name is required for PostgreSQL discovery")

        # Validate cloud providers if cloud module enabled
        if "cloud" in modules:
            if not (
                self.config.aws.enabled
                or self.config.gcp.enabled
                or self.config.supabase.enabled
                or self.config.heroku.enabled
                or self.config.neon.enabled
            ):
                errors.append(
                    "At least one cloud provider must be enabled for cloud discovery.\n"
                    "  Enable a provider in your config file (e.g. providers.aws.enabled: true)\n"
                    "  or pass --providers on the command line (e.g. --providers heroku).\n"
                    "  Supported providers: aws, gcp, supabase, heroku, neon"
                )

        if errors:
            raise ValueError("Configuration validation failed:\n" + "\n".join(errors))

    def save_config_template(
        self, output_path: str, providers: List[str] = None, engines: List[str] = None
    ) -> None:
        """Save a configuration template file.

        Args:
            output_path: Path where the template should be saved
            providers: List of cloud providers to include (aws, gcp, supabase, heroku, neon).
                      If None or empty, no cloud provider sections are generated.
            engines: List of database engines to include (postgres, mysql).
                    Defaults to ["postgres"].
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if providers is None:
            providers = []
        if engines is None:
            engines = ["postgres"]

        # Normalize names
        providers = [p.strip().lower() for p in providers]
        engines = [e.strip().lower() for e in engines]

        # A run targets a single engine. Default to mysql only when it is the
        # sole selection; otherwise postgres.
        selected_engine = "mysql" if engines == ["mysql"] else "postgres"

        if output_path.suffix.lower() in [".yaml", ".yml"]:
            template_yaml = f"""# PlanetScale Discovery Configuration
# Generated for your selected providers
#
# Fill in the values below, then run:  ps-discovery

# Database engine to analyze: postgres or mysql
engine: {selected_engine}
"""

            # Add database engine sections
            if "postgres" in engines:
                template_yaml += """
# PostgreSQL connection settings
database:
  host: localhost
  port: 5432
  database: your_database_name
  username: your_username
  password: your_password
  ssl_mode: prefer  # Options: disable, allow, prefer, require, verify-ca, verify-full
  # excluded_databases:  # Additional databases to skip (rdsadmin is always excluded)
  #   - some_internal_db
"""

            if "mysql" in engines:
                template_yaml += """
# MySQL connection settings (set engine: mysql above to use this block)
mysql:
  host: localhost
  port: 3306
  database: ""  # Leave empty to discover all databases
  username: root
  password: your_password
  ssl_mode: disabled  # Options: disabled, preferred, required, verify-ca, verify-identity
  # For verify-ca / verify-identity, ssl_ca is required.
  # ssl_ca: /path/to/server-ca.pem
  # ssl_cert: /path/to/client-cert.pem   # optional, for mutual TLS
  # ssl_key: /path/to/client-key.pem     # optional, for mutual TLS
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

                if "neon" in providers:
                    template_yaml += """  neon:
    enabled: true
    # Get your API key from: https://console.neon.tech/app/settings/api-keys
    # Or set NEON_API_KEY environment variable
    api_key: your_neon_api_key
    # Optional: analyze a specific project only
    # target_project: project-id-here
    # Optional: filter to a specific organization
    # org_id: your-org-id
    discover_all: true  # Discover all Neon projects

"""

            # Add output and logging settings
            template_yaml += """
# Output settings
output:
  output_dir: ./discovery_output

# Logging settings
log_level: INFO  # Options: DEBUG, INFO, WARNING, ERROR
# log_file: discovery.log  # Uncomment to write logs to file
"""

            with open(output_path, "w") as f:
                f.write(template_yaml)
        else:
            raise ValueError(
                f"Unsupported config template extension: {output_path.suffix}. "
                "Use a .yaml or .yml output path."
            )

    def get_config(self) -> DiscoveryConfig:
        """Get current configuration."""
        if not self.config:
            self.load_config()
        return self.config

    def validate_config(self) -> None:
        """Public method to validate configuration."""
        self._validate_config()
