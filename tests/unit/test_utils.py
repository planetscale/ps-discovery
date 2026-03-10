"""
Tests for common utility functions
"""

import tempfile
import json
from pathlib import Path
from datetime import datetime
from planetscale_discovery.common.utils import (
    setup_logging,
    load_config_file,
    save_results,
    generate_timestamp,
)


class TestLogging:
    """Tests for logging setup"""

    def test_setup_logging_default(self):
        """Test default logging setup"""
        logger = setup_logging()
        assert logger is not None
        assert logger.level == 20  # INFO level

    def test_setup_logging_with_level(self):
        """Test logging with custom level"""
        logger = setup_logging(level="DEBUG")
        assert logger.level == 10  # DEBUG level

    def test_setup_logging_with_file(self):
        """Test logging with file output"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            log_file = f.name

        logger = setup_logging(level="INFO", log_file=log_file)
        assert logger is not None
        assert len(logger.handlers) == 2  # Console + file handler


class TestConfigLoading:
    """Tests for configuration loading"""

    def test_load_json_config(self):
        """Test loading JSON config file"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config = {"database": {"host": "localhost", "port": 5432}}
            json.dump(config, f)
            config_file = f.name

        loaded = load_config_file(config_file)
        assert loaded["database"]["host"] == "localhost"
        assert loaded["database"]["port"] == 5432

    def test_load_yaml_config(self):
        """Test loading YAML config file"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as f:
            f.write("database:\n  host: localhost\n  port: 5432\n")
            config_file = f.name

        loaded = load_config_file(config_file)
        assert loaded["database"]["host"] == "localhost"
        assert loaded["database"]["port"] == 5432


class TestResultsSaving:
    """Tests for saving results"""

    def test_save_results_json(self):
        """Test saving results as JSON"""
        with tempfile.TemporaryDirectory() as tmpdir:
            results = {"test": "data", "number": 42}
            output_file = Path(tmpdir) / "results.json"

            save_results(results, str(output_file), "json")

            # Verify file was created and contains correct data
            with open(output_file) as f:
                loaded = json.load(f)
            assert loaded["test"] == "data"
            assert loaded["number"] == 42


class TestTimestamp:
    """Tests for timestamp generation"""

    def test_generate_timestamp(self):
        """Test timestamp generation"""
        timestamp = generate_timestamp()
        assert isinstance(timestamp, str)
        assert len(timestamp) > 10
        # Should be parseable as ISO format
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
