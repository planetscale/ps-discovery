"""
Tests for Configuration Manager
"""

import pytest
import tempfile
import json
import os
from pathlib import Path
from planetscale_discovery.config.config_manager import (
    ConfigManager,
    DatabaseConfig,
    DataSizeConfig,
    AWSConfig,
    GCPConfig,
    HerokuConfig,
    OutputConfig,
    DiscoveryConfig,
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
        assert "database" in config.modules
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

    def test_load_from_json_file(self):
        """Test loading configuration from JSON file"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
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
            json.dump(config_data, f)
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
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
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
            json.dump(config_data, f)
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
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
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
            json.dump(config_data, f)
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
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {
                "database": {"database": "testdb"},
                "output": {
                    "output_dir": "/tmp/output",
                },
                "modules": ["database"],
            }
            json.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            config = manager.load_config(validate=False)

            assert config.output.output_dir == "/tmp/output"
        finally:
            os.unlink(config_file)

    def test_validation_invalid_module(self):
        """Test validation catches invalid modules"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {
                "database": {"database": "testdb"},
                "modules": ["invalid_module"],
            }
            json.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            with pytest.raises(ValueError, match="Invalid module"):
                manager.load_config(validate=True)
        finally:
            os.unlink(config_file)

    def test_validation_missing_database_name(self):
        """Test validation catches missing database name"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {
                "database": {"host": "localhost"},
                "modules": ["database"],
            }
            json.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            with pytest.raises(ValueError, match="Database name is required"):
                manager.load_config(validate=True)
        finally:
            os.unlink(config_file)

    def test_validation_cloud_no_provider(self):
        """Test validation catches cloud module without providers"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {
                "database": {"database": "testdb"},
                "modules": ["cloud"],
            }
            json.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            with pytest.raises(ValueError, match="cloud provider must be enabled"):
                manager.load_config(validate=True)
        finally:
            os.unlink(config_file)

    def test_get_config(self):
        """Test getting configuration"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {
                "database": {"database": "testdb"},
                "modules": ["database"],
            }
            json.dump(config_data, f)
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

    def test_save_json_template(self):
        """Test saving JSON configuration template"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "config.json"
            manager = ConfigManager()
            manager.save_config_template(str(output_file))

            assert output_file.exists()
            with open(output_file) as f:
                template = json.load(f)
            assert "database" in template
            assert "output" in template

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
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {
                "database": {"database": "testdb"},
                "target_database": "specific_db",
                "modules": ["database"],
            }
            json.dump(config_data, f)
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

    def test_load_data_size_config(self):
        """Test loading data_size configuration"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
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
            json.dump(config_data, f)
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
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {
                "database": {"database": "testdb"},
                "modules": ["database"],
            }
            json.dump(config_data, f)
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
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
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
            json.dump(config_data, f)
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
        assert config.heroku.enabled is False
        assert config.output.output_dir == "./discovery_output"

    def test_validation_cloud_heroku_provider(self):
        """Test validation passes with only heroku provider enabled"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {
                "database": {"database": "testdb"},
                "providers": {
                    "heroku": {
                        "enabled": True,
                    }
                },
                "modules": ["cloud"],
            }
            json.dump(config_data, f)
            config_file = f.name

        try:
            manager = ConfigManager(config_file)
            # Should not raise - heroku is a valid provider
            config = manager.load_config(validate=True)
            assert config.heroku.enabled is True
        finally:
            os.unlink(config_file)
