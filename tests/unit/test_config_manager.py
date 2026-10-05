"""
Tests for Configuration Manager
"""

import pytest
import tempfile
import yaml
import os
from pathlib import Path
from unittest.mock import patch
from planetscale_discovery.config.config_manager import (
    ConfigManager,
    DatabaseConfig,
    DataSizeConfig,
    AWSConfig,
    GCPConfig,
    AzureConfig,
    HerokuConfig,
    OutputConfig,
    DiscoveryConfig,
    apply_providers_override,
    resolve_modules,
)


class TestDatabaseConfig:
    """Tests for DatabaseConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = DatabaseConfig()
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.database == ""
        assert config.ssl_mode == "prefer"
        assert isinstance(config.data_size, DataSizeConfig)


class TestDataSizeConfig:
    """Tests for DataSizeConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = DataSizeConfig()
        assert config.enabled is False
        assert config.sample_percent == 10
        assert config.max_table_size_gb == 10
        assert config.target_tables == []
        assert config.target_schemas == ["public"]
        assert "text" in config.check_column_types
        assert "bytea" in config.check_column_types
        assert "json" in config.check_column_types
        assert "jsonb" in config.check_column_types
        assert config.size_thresholds["1kb"] == 1024
        assert config.size_thresholds["64kb"] == 65536

    def test_custom_values(self):
        """Test custom configuration values"""
        config = DataSizeConfig(
            enabled=True,
            sample_percent=25,
            max_table_size_gb=50,
            target_tables=["public.users"],
            target_schemas=["public", "app"],
            check_column_types=["text", "json"],
            size_thresholds={"1kb": 1024, "1mb": 1048576},
        )
        assert config.enabled is True
        assert config.sample_percent == 25
        assert config.max_table_size_gb == 50
        assert config.target_tables == ["public.users"]
        assert config.target_schemas == ["public", "app"]
        assert config.check_column_types == ["text", "json"]
        assert config.size_thresholds["1mb"] == 1048576


class TestAWSConfig:
    """Tests for AWSConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = AWSConfig()
        assert config.enabled is False
        assert config.regions == []
        assert config.discover_all is True


class TestGCPConfig:
    """Tests for GCPConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = GCPConfig()
        assert config.enabled is False
        assert config.project_id == ""
        assert config.discover_all is True


class TestAzureConfig:
    """Tests for AzureConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = AzureConfig()
        assert config.enabled is False
        assert config.subscription_id == ""
        assert config.resource_groups == []
        # Empty means every region: Azure lists per subscription.
        assert config.regions == []
        assert config.tenant_id is None
        assert config.client_id is None
        assert config.client_secret is None
        assert config.resources == {}
        assert config.discover_all is True

    def test_custom_values(self):
        """Test explicit configuration values"""
        config = AzureConfig(
            enabled=True,
            subscription_id="sub-1",
            resource_groups=["rg-a"],
            regions=["eastus"],
            tenant_id="t",
            client_id="c",
            client_secret="s",
            discover_all=False,
        )
        assert config.enabled is True
        assert config.subscription_id == "sub-1"
        assert config.resource_groups == ["rg-a"]
        assert config.regions == ["eastus"]
        assert config.discover_all is False


class TestHerokuConfig:
    """Tests for HerokuConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = HerokuConfig()
        assert config.enabled is False
        assert config.api_key is None
        assert config.target_app is None
        assert config.discover_all is True

    def test_custom_values(self):
        """Test custom configuration values"""
        config = HerokuConfig(
            enabled=True,
            api_key="test-key",
            target_app="my-app",
            discover_all=False,
        )
        assert config.enabled is True
        assert config.api_key == "test-key"
        assert config.target_app == "my-app"
        assert config.discover_all is False


class TestOutputConfig:
    """Tests for OutputConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = OutputConfig()
        assert config.output_dir == "./discovery_output"


class TestDiscoveryConfig:
    """Tests for DiscoveryConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = DiscoveryConfig()
        assert isinstance(config.database, DatabaseConfig)
        assert isinstance(config.aws, AWSConfig)
        assert isinstance(config.gcp, GCPConfig)
        assert isinstance(config.output, OutputConfig)
        # modules defaults to None ("unspecified"); resolve_modules() infers
        # what to run when the config file does not declare modules.
        assert config.modules is None
        assert config.log_level == "INFO"


class TestConfigManager:
    """Tests for ConfigManager"""

    def test_instantiation(self):
        """Test config manager can be created"""
        manager = ConfigManager()
        assert manager is not None
        assert manager.config is None

    def test_instantiation_with_path(self):
        """Test config manager with path"""
        manager = ConfigManager(config_path="/tmp/test.yaml")
        assert manager.config_path == "/tmp/test.yaml"

    def test_load_from_yaml_file(self):
        """Test loading configuration from a YAML file"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {
                    "host": "testhost",
                    "port": 5433,
                    "database": "testdb",
                    "username": "testuser",
                    "password": "testpass",
                },
                "modules": ["database"],
                "log_level": "DEBUG",
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.load_config(validate=False)

            assert config.database.host == "testhost"
            assert config.database.port == 5433
            assert config.database.database == "testdb"
            assert config.log_level == "DEBUG"
        finally:
            os.unlink(config_file)

    def test_load_planetscale_from_yaml_file(self):
        """The PlanetScale provider block loads from a config file"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "providers": {
                    "planetscale": {
                        "enabled": True,
                        "service_token_id": "id-abc",
                        "service_token": "token-xyz",
                        "organization": "my-org",
                        "target_database": "my-database",
                    }
                }
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            config = ConfigManager(config_file).load_config(validate=False)

            assert config.planetscale.enabled is True
            assert config.planetscale.service_token_id == "id-abc"
            assert config.planetscale.service_token == "token-xyz"
            assert config.planetscale.organization == "my-org"
            assert config.planetscale.target_database == "my-database"
        finally:
            os.unlink(config_file)

    def test_load_snowflake_from_yaml_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "providers": {
                    "snowflake": {
                        "enabled": True,
                        "account": "xy12345",
                        "user": "planetscale_discovery",
                        "role": "PS_DISCOVERY",
                        "authentication": "key_pair",
                        "private_key_path": "/tmp/key.p8",
                        "discover_all": True,
                        "use_secondary_roles": True,
                        "password": "do-not-load",
                        "private_key_passphrase": "do-not-load",
                    }
                }
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            config = ConfigManager(config_file).load_config(validate=False)

            assert config.snowflake.enabled is True
            assert config.snowflake.account == "xy12345"
            assert config.snowflake.user == "planetscale_discovery"
            assert config.snowflake.role == "PS_DISCOVERY"
            assert config.snowflake.authentication == "key_pair"
            assert config.snowflake.private_key_path == "/tmp/key.p8"
            assert config.snowflake.use_secondary_roles is True
            assert config.snowflake.password is None
            assert config.snowflake.private_key_passphrase is None
        finally:
            os.unlink(config_file)

    def test_snowflake_yaml_secrets_warn(self, caplog):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            yaml.dump(
                {
                    "providers": {
                        "snowflake": {
                            "enabled": True,
                            "password": "do-not-load",
                            "private_key_passphrase": "do-not-load",
                        }
                    }
                },
                f,
            )
            config_file = f.name

        try:
            ConfigManager(config_file).load_config(validate=False)
        finally:
            os.unlink(config_file)

        messages = [r.getMessage() for r in caplog.records]
        assert any("SNOWFLAKE_PASSWORD" in m for m in messages)
        assert any("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE" in m for m in messages)
        assert not any("do-not-load" in m for m in messages)

    def test_snowflake_secondary_roles_default_off(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            yaml.dump({"providers": {"snowflake": {"enabled": True}}}, f)
            config_file = f.name

        try:
            config = ConfigManager(config_file).load_config(validate=False)
            assert config.snowflake.use_secondary_roles is False
        finally:
            os.unlink(config_file)

    def test_load_snowflake_from_environment(self):
        env = {
            "SNOWFLAKE_ENABLED": "true",
            "SNOWFLAKE_ACCOUNT": "env-acct",
            "SNOWFLAKE_USER": "env-user",
            "SNOWFLAKE_ROLE": "DISCOVERY_READONLY",
            "SNOWFLAKE_PRIVATE_KEY_PATH": "/tmp/env.p8",
        }
        with patch.dict(os.environ, env):
            config = ConfigManager().load_config(validate=False)

        assert config.snowflake.enabled is True
        assert config.snowflake.account == "env-acct"
        assert config.snowflake.user == "env-user"
        assert config.snowflake.role == "DISCOVERY_READONLY"
        assert config.snowflake.private_key_path == "/tmp/env.p8"

    def test_load_snowflake_authenticator_alias_from_environment(self):
        env = {
            "SNOWFLAKE_ENABLED": "true",
            "SNOWFLAKE_ACCOUNT": "env-acct",
            "SNOWFLAKE_USER": "env-user",
            "SNOWFLAKE_AUTHENTICATOR": "password",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SNOWFLAKE_AUTHENTICATION", None)
            config = ConfigManager().load_config(validate=False)

        assert config.snowflake.authentication == "password"

    def test_load_planetscale_from_environment(self):
        """The PlanetScale provider block loads from the environment"""
        env = {
            "PLANETSCALE_SERVICE_TOKEN_ID": "env-id",
            "PLANETSCALE_SERVICE_TOKEN": "env-token",
            "PLANETSCALE_ORGANIZATION": "env-org",
            "PLANETSCALE_TARGET_DATABASE": "env-database",
        }
        with patch.dict(os.environ, env):
            config = ConfigManager().load_config(validate=False)

        assert config.planetscale.service_token_id == "env-id"
        assert config.planetscale.service_token == "env-token"
        assert config.planetscale.organization == "env-org"
        assert config.planetscale.target_database == "env-database"

    def test_load_from_environment(self):
        """Test loading configuration from environment"""
        os.environ["PGHOST"] = "envhost"
        os.environ["PGPORT"] = "5434"
        os.environ["PGDATABASE"] = "envdb"
        os.environ["AWS_ENABLED"] = "true"
        os.environ["AWS_REGIONS"] = "us-east-1,us-west-2"

        try:
            manager = ConfigManager()
            config = manager.load_config(validate=False)

            assert config.database.host == "envhost"
            assert config.database.port == 5434
            assert config.database.database == "envdb"
            assert config.aws.enabled is True
            assert "us-east-1" in config.aws.regions
        finally:
            del os.environ["PGHOST"]
            del os.environ["PGPORT"]
            del os.environ["PGDATABASE"]
            del os.environ["AWS_ENABLED"]
            del os.environ["AWS_REGIONS"]

    def test_load_aws_config(self):
        """Test loading AWS configuration"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "providers": {
                    "aws": {
                        "enabled": True,
                        "regions": ["us-west-1"],
                        "discover_all": False,
                        "credentials": {
                            "profile": "testprofile",
                            "access_key_id": "testkey",
                        },
                    }
                },
                "modules": ["database", "cloud"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.load_config(validate=False)

            assert config.aws.enabled is True
            assert "us-west-1" in config.aws.regions
            assert config.aws.profile == "testprofile"
            assert config.aws.discover_all is False
        finally:
            os.unlink(config_file)

    def test_load_gcp_config(self):
        """Test loading GCP configuration"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "providers": {
                    "gcp": {
                        "enabled": True,
                        "project_id": "test-project",
                        "regions": ["us-central1"],
                        "credentials": {
                            "service_account_key": "/path/to/key.json",
                            "application_default": True,
                        },
                    }
                },
                "modules": ["database", "cloud"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.load_config(validate=False)

            assert config.gcp.enabled is True
            assert config.gcp.project_id == "test-project"
            assert "us-central1" in config.gcp.regions
        finally:
            os.unlink(config_file)

    def test_load_output_config(self):
        """Test loading output configuration"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "output": {
                    "output_dir": "/tmp/output",
                },
                "modules": ["database"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.load_config(validate=False)

            assert config.output.output_dir == "/tmp/output"
        finally:
            os.unlink(config_file)

    def test_validation_invalid_module(self):
        """Test validation catches invalid modules"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "modules": ["invalid_module"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            with pytest.raises(ValueError, match="Invalid module"):
                manager.load_config(validate=True)
        finally:
            os.unlink(config_file)

    def test_validation_missing_database_name(self):
        """Test validation catches missing database name"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"host": "localhost"},
                "modules": ["database"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            with pytest.raises(ValueError, match="Database name is required"):
                manager.load_config(validate=True)
        finally:
            os.unlink(config_file)

    def test_validation_cloud_no_provider(self):
        """Test validation catches cloud module without providers"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "modules": ["cloud"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            with pytest.raises(ValueError, match="cloud provider must be enabled"):
                manager.load_config(validate=True)
        finally:
            os.unlink(config_file)

    def test_get_config(self):
        """Test getting configuration"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "modules": ["database"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.get_config()
            assert isinstance(config, DiscoveryConfig)
        finally:
            os.unlink(config_file)

    def test_validate_config_without_loading(self):
        """Test validation fails without loading"""
        manager = ConfigManager()
        with pytest.raises(ValueError, match="Configuration not loaded"):
            manager.validate_config()

    def test_non_yaml_template_rejected(self):
        """A non-YAML output extension is rejected (config is YAML-only)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "config.json"
            manager = ConfigManager()
            with pytest.raises(
                ValueError, match="Unsupported config template extension"
            ):
                manager.save_config_template(str(output_file))

    def test_save_yaml_template(self):
        """Test saving YAML configuration template"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "config.yaml"
            manager = ConfigManager()
            manager.save_config_template(str(output_file))

            assert output_file.exists()
            content = output_file.read_text()
            assert "database:" in content
            assert "output:" in content

    def test_load_with_target_database(self):
        """Test loading configuration with target_database"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "target_database": "specific_db",
                "modules": ["database"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.load_config(validate=False)
            assert config.target_database == "specific_db"
        finally:
            os.unlink(config_file)

    def test_gcp_environment_variables(self):
        """Test GCP configuration from environment"""
        os.environ["GCP_ENABLED"] = "true"
        os.environ["GCP_PROJECT_ID"] = "test-project-123"
        os.environ["GCP_REGIONS"] = "us-west1,us-east1"

        try:
            manager = ConfigManager()
            config = manager.load_config(validate=False)

            assert config.gcp.enabled is True
            assert config.gcp.project_id == "test-project-123"
            assert "us-west1" in config.gcp.regions
        finally:
            del os.environ["GCP_ENABLED"]
            del os.environ["GCP_PROJECT_ID"]
            del os.environ["GCP_REGIONS"]

    def test_load_azure_config(self):
        """Test loading Azure provider configuration from a file"""
        config_data = {
            "providers": {
                "azure": {
                    "enabled": True,
                    "subscription_id": "sub-1",
                    "resource_groups": ["rg-a", "rg-b"],
                    "regions": ["eastus", "westeurope"],
                    "discover_all": False,
                    "resources": {"postgresql_flexible_servers": ["pg1"]},
                    "credentials": {
                        "tenant_id": "tenant-1",
                        "client_id": "client-1",
                        "client_secret": "secret-1",
                    },
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            yaml.dump(config_data, f)
            temp_path = f.name

        try:
            manager = ConfigManager(temp_path)
            config = manager.load_config(validate=False)

            assert config.azure.enabled is True
            assert config.azure.subscription_id == "sub-1"
            assert config.azure.resource_groups == ["rg-a", "rg-b"]
            assert config.azure.regions == ["eastus", "westeurope"]
            assert config.azure.discover_all is False
            assert config.azure.resources == {"postgresql_flexible_servers": ["pg1"]}
            assert config.azure.tenant_id == "tenant-1"
            assert config.azure.client_id == "client-1"
            assert config.azure.client_secret == "secret-1"
        finally:
            Path(temp_path).unlink()

    def test_azure_environment_variables(self):
        """Test Azure configuration from environment"""
        os.environ["AZURE_ENABLED"] = "true"
        os.environ["AZURE_SUBSCRIPTION_ID"] = "sub-from-env"
        os.environ["AZURE_REGIONS"] = "eastus,westus2"
        os.environ["AZURE_RESOURCE_GROUPS"] = "rg-a,rg-b"
        os.environ["AZURE_TENANT_ID"] = "tenant-from-env"

        try:
            manager = ConfigManager()
            config = manager.load_config(validate=False)

            assert config.azure.enabled is True
            assert config.azure.subscription_id == "sub-from-env"
            assert "eastus" in config.azure.regions
            assert config.azure.resource_groups == ["rg-a", "rg-b"]
            assert config.azure.tenant_id == "tenant-from-env"
        finally:
            del os.environ["AZURE_ENABLED"]
            del os.environ["AZURE_SUBSCRIPTION_ID"]
            del os.environ["AZURE_REGIONS"]
            del os.environ["AZURE_RESOURCE_GROUPS"]
            del os.environ["AZURE_TENANT_ID"]

    def test_validation_cloud_azure_provider(self):
        """Azure alone satisfies the "one provider enabled" rule"""
        config = DiscoveryConfig()
        config.azure.enabled = True
        config.azure.subscription_id = "sub-1"
        config.modules = ["cloud"]

        manager = ConfigManager()
        manager.config = config

        manager._validate_config()  # must not raise

    def test_validation_azure_requires_subscription_id(self):
        """Azure has no ambient subscription default, so this must be caught"""
        config = DiscoveryConfig()
        config.azure.enabled = True
        config.modules = ["cloud"]

        manager = ConfigManager()
        manager.config = config

        with pytest.raises(ValueError, match="subscription ID"):
            manager._validate_config()

    def test_load_data_size_config(self):
        """Test loading data_size configuration"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {
                    "database": "testdb",
                    "data_size": {
                        "enabled": True,
                        "sample_percent": 25,
                        "max_table_size_gb": 50,
                        "target_tables": ["public.users", "public.posts"],
                        "target_schemas": ["public", "app_data"],
                        "check_column_types": ["text", "json"],
                        "size_thresholds": {"1kb": 1024, "64kb": 65536, "1mb": 1048576},
                    },
                },
                "modules": ["database"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.load_config(validate=False)

            assert config.database.data_size.enabled is True
            assert config.database.data_size.sample_percent == 25
            assert config.database.data_size.max_table_size_gb == 50
            assert "public.users" in config.database.data_size.target_tables
            assert "app_data" in config.database.data_size.target_schemas
            assert "text" in config.database.data_size.check_column_types
            assert config.database.data_size.size_thresholds["1mb"] == 1048576
        finally:
            os.unlink(config_file)

    def test_data_size_config_defaults_when_not_specified(self):
        """Test data_size configuration uses defaults when not specified"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "modules": ["database"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.load_config(validate=False)

            # Should have defaults
            assert config.database.data_size.enabled is False
            assert config.database.data_size.sample_percent == 10
            assert config.database.data_size.max_table_size_gb == 10
            assert config.database.data_size.target_schemas == ["public"]
        finally:
            os.unlink(config_file)

    def test_load_heroku_config(self):
        """Test loading Heroku configuration from file"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "providers": {
                    "heroku": {
                        "enabled": True,
                        "api_key": "test-heroku-key",
                        "target_app": "my-app",
                        "discover_all": False,
                    }
                },
                "modules": ["database", "cloud"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.load_config(validate=False)

            assert config.heroku.enabled is True
            assert config.heroku.api_key == "test-heroku-key"
            assert config.heroku.target_app == "my-app"
            assert config.heroku.discover_all is False
        finally:
            os.unlink(config_file)

    def test_heroku_environment_variables(self):
        """Test Heroku configuration from environment"""
        os.environ["HEROKU_ENABLED"] = "true"
        os.environ["HEROKU_API_KEY"] = "env-heroku-key"
        os.environ["HEROKU_TARGET_APP"] = "env-app"

        try:
            manager = ConfigManager()
            config = manager.load_config(validate=False)

            assert config.heroku.enabled is True
            assert config.heroku.api_key == "env-heroku-key"
            assert config.heroku.target_app == "env-app"
        finally:
            del os.environ["HEROKU_ENABLED"]
            del os.environ["HEROKU_API_KEY"]
            del os.environ["HEROKU_TARGET_APP"]

    def test_empty_config_sections_do_not_crash(self):
        """Test that empty/null config sections are handled gracefully.

        In YAML, a key with no value parses as None (e.g. 'credentials:').
        This should not cause 'NoneType has no attribute get' errors.
        """
        config_data = {
            "database": None,
            "providers": {
                "aws": {"enabled": True, "credentials": None},
                "gcp": None,
                "azure": None,
                "supabase": None,
                "heroku": None,
            },
            "output": None,
            "modules": ["cloud"],
        }

        manager = ConfigManager()
        config = manager._parse_config_dict(config_data)

        # Should parse without errors, using defaults
        assert config.database.host == "localhost"
        assert config.aws.enabled is True
        assert config.aws.profile is None
        assert config.gcp.enabled is False
        assert config.azure.enabled is False
        assert config.azure.subscription_id == ""
        assert config.heroku.enabled is False
        assert config.output.output_dir == "./discovery_output"

    def test_validation_cloud_heroku_provider(self):
        """Test validation passes with only heroku provider enabled"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            config_data = {
                "database": {"database": "testdb"},
                "providers": {
                    "heroku": {
                        "enabled": True,
                    }
                },
                "modules": ["cloud"],
            }
            yaml.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            # Should not raise - heroku is a valid provider
            config = manager.load_config(validate=True)
            assert config.heroku.enabled is True
        finally:
            os.unlink(config_file)


class TestResolveModules:
    """Tests for resolve_modules() — the config-as-source-of-truth logic."""

    def test_subcommand_overrides_everything(self):
        config = DiscoveryConfig(modules=["cloud"])  # config says cloud...
        # ...but the explicit subcommand wins.
        assert resolve_modules(config, "database") == ["database"]
        assert resolve_modules(config, "cloud") == ["cloud"]
        assert resolve_modules(config, "both") == ["database", "cloud"]

    def test_explicit_file_modules_honored(self):
        config = DiscoveryConfig(modules=["database"])
        assert resolve_modules(config, None) == ["database"]

    def test_explicit_file_modules_ordered_and_filtered(self):
        config = DiscoveryConfig(modules=["cloud", "database", "bogus"])
        assert resolve_modules(config, None) == ["database", "cloud"]

    def test_infer_database_only(self):
        config = DiscoveryConfig()
        config.database.database = "mydb"
        assert resolve_modules(config, None) == ["database"]

    def test_infer_cloud_only(self):
        config = DiscoveryConfig()
        config.aws.enabled = True
        assert resolve_modules(config, None) == ["cloud"]

    def test_infer_both(self):
        config = DiscoveryConfig()
        config.database.database = "mydb"
        config.neon.enabled = True
        assert resolve_modules(config, None) == ["database", "cloud"]

    def test_infer_cloud_snowflake(self):
        config = DiscoveryConfig()
        config.snowflake.enabled = True
        assert resolve_modules(config, None) == ["cloud"]

    def test_apply_providers_override_snowflake(self):
        config = DiscoveryConfig()
        config.aws.enabled = True
        apply_providers_override(config, ["snowflake"])
        assert config.snowflake.enabled is True
        assert config.aws.enabled is False
        assert config.gcp.enabled is False
        assert config.azure.enabled is False

    def test_infer_mysql_by_host(self):
        config = DiscoveryConfig(engine="mysql")
        config.mysql.host = "db.example.com"
        assert resolve_modules(config, None) == ["database"]

    def test_infer_empty_when_nothing_configured(self):
        config = DiscoveryConfig()
        assert resolve_modules(config, None) == []


class TestConfigRequired:
    """Tests for load_config(config_required=...)."""

    def test_missing_explicit_path_raises(self):
        manager = ConfigManager("/nonexistent/path/config.yaml")
        with pytest.raises(ValueError, match="Configuration file not found"):
            manager.load_config(config_required=True, validate=False)

    def test_missing_path_falls_back_when_not_required(self):
        # Without config_required, a missing path falls back to env/defaults.
        manager = ConfigManager("/nonexistent/path/config.yaml")
        config = manager.load_config(config_required=False, validate=False)
        assert isinstance(config, DiscoveryConfig)


class TestSelfDescribingTemplate:
    """Generated templates carry engine: and omit the optional modules: key."""

    def test_yaml_template_has_engine(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.yaml"
            ConfigManager().save_config_template(
                str(path), providers=["aws"], engines=["postgres"]
            )
            text = path.read_text()
            assert "engine: postgres" in text
            # modules: is an optional override, not part of the default config.
            assert "modules:" not in text

    def test_yaml_template_mysql_only_engine(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.yaml"
            ConfigManager().save_config_template(
                str(path), providers=[], engines=["mysql"]
            )
            text = path.read_text()
            assert "engine: mysql" in text

    def test_yaml_template_azure_block(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.yaml"
            ConfigManager().save_config_template(
                str(path), providers=["azure"], engines=["postgres"]
            )
            data = yaml.safe_load(path.read_text())
            azure = data["providers"]["azure"]
            assert azure["enabled"] is True
            assert "subscription_id" in azure
            # Service principal fields live under credentials:, like AWS/GCP.
            assert "tenant_id" in azure["credentials"]
            assert "client_secret" in azure["credentials"]

    def test_yaml_template_omits_unrequested_providers(self):
        """Asking for azure must not emit any other provider's block."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.yaml"
            ConfigManager().save_config_template(
                str(path), providers=["azure"], engines=["postgres"]
            )
            data = yaml.safe_load(path.read_text())
            assert set(data["providers"]) == {"azure"}

    def test_yaml_template_supabase_token_at_top_level(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.yaml"
            ConfigManager().save_config_template(
                str(path), providers=["supabase"], engines=["postgres"]
            )
            data = yaml.safe_load(path.read_text())
            # Supabase access_token lives at the top level of the block.
            assert "access_token" in data["providers"]["supabase"]

    def test_yaml_template_snowflake_block(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.yaml"
            ConfigManager().save_config_template(
                str(path), providers=["snowflake"], engines=["postgres"]
            )
            data = yaml.safe_load(path.read_text())
            assert data["providers"]["snowflake"]["enabled"] is True
            assert data["providers"]["snowflake"]["authentication"] == "key_pair"
            assert data["providers"]["snowflake"]["account"] == ""
            text = path.read_text()
            assert "${" not in text
            assert "SNOWFLAKE_ACCOUNT" in text
            assert "network_inventory" not in text
            assert "usage_history_days" not in text
