"""
Tests for CLI argument parsing and command handling.
"""

import pytest
import argparse
from unittest.mock import Mock, patch, mock_open

import tempfile
from pathlib import Path

from planetscale_discovery.cli import (
    create_main_parser,
    generate_combined_summary,
    generate_summary_markdown,
    _format_bytes,
    main,
)


class TestArgumentParsing:
    """Test CLI argument parsing."""

    def test_parser_creation(self):
        """Test that main parser can be created."""
        parser = create_main_parser()
        assert isinstance(parser, argparse.ArgumentParser)
        assert "PlanetScale Discovery Tools" in parser.description

    def test_database_subcommand_exists(self):
        """Test database subcommand is available."""
        parser = create_main_parser()
        args = parser.parse_args(["database", "--host", "localhost", "-d", "testdb"])
        assert args.command == "database"
        assert args.host == "localhost"
        assert args.database == "testdb"

    def test_cloud_subcommand_exists(self):
        """Test cloud subcommand is available."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--providers", "aws"])
        assert args.command == "cloud"
        assert args.providers == "aws"

    def test_both_subcommand_exists(self):
        """Test both subcommand is available."""
        parser = create_main_parser()
        args = parser.parse_args(
            ["both", "--host", "localhost", "-d", "testdb", "--providers", "aws,gcp"]
        )
        assert args.command == "both"
        assert args.host == "localhost"
        assert args.providers == "aws,gcp"

    def test_config_template_subcommand(self):
        """Test config-template subcommand."""
        parser = create_main_parser()
        args = parser.parse_args(["config-template", "--output", "test.yaml"])
        assert args.command == "config-template"
        assert args.output == "test.yaml"
        assert args.format == "yaml"

    def test_config_template_json_format(self):
        """Test config-template with json format."""
        parser = create_main_parser()
        args = parser.parse_args(
            ["config-template", "--output", "test.json", "--format", "json"]
        )
        assert args.format == "json"


class TestCommonArguments:
    """Test common CLI arguments across subcommands."""

    def test_config_argument(self):
        """Test --config argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--config", "myconfig.yaml"])
        assert args.config == "myconfig.yaml"

    def test_output_dir_argument(self):
        """Test --output-dir argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--output-dir", "/tmp/results"])
        assert args.output_dir == "/tmp/results"

    def test_output_dir_default(self):
        """Test default output directory."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud"])
        assert args.output_dir == "./discovery_output"

    def test_log_level_argument(self):
        """Test --log-level argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"

    def test_log_level_default(self):
        """Test default log level."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud"])
        assert args.log_level == "INFO"

    def test_log_file_argument(self):
        """Test --log-file argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--log-file", "discovery.log"])
        assert args.log_file == "discovery.log"


class TestDatabaseArguments:
    """Test database-specific CLI arguments."""

    def test_host_argument(self):
        """Test --host argument."""
        parser = create_main_parser()
        args = parser.parse_args(["database", "--host", "db.example.com"])
        assert args.host == "db.example.com"

    def test_port_argument(self):
        """Test --port argument."""
        parser = create_main_parser()
        args = parser.parse_args(["database", "-p", "5433"])
        assert args.port == 5433

    def test_database_argument(self):
        """Test --database argument."""
        parser = create_main_parser()
        args = parser.parse_args(["database", "-d", "mydb"])
        assert args.database == "mydb"

    def test_username_argument(self):
        """Test --username argument."""
        parser = create_main_parser()
        args = parser.parse_args(["database", "-u", "postgres"])
        assert args.username == "postgres"

    def test_password_prompt_flag(self):
        """Test --password flag."""
        parser = create_main_parser()
        args = parser.parse_args(["database", "-W"])
        assert args.password is True

    def test_analyzers_argument(self):
        """Test --analyzers argument."""
        parser = create_main_parser()
        args = parser.parse_args(["database", "--analyzers", "config,schema"])
        assert args.analyzers == "config,schema"

    def test_analyzers_default(self):
        """Test default analyzers."""
        parser = create_main_parser()
        args = parser.parse_args(["database"])
        assert args.analyzers == "config,schema,performance,security,features"


class TestCloudArguments:
    """Test cloud-specific CLI arguments."""

    def test_providers_argument(self):
        """Test --providers argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--providers", "aws,gcp"])
        assert args.providers == "aws,gcp"

    def test_regions_argument(self):
        """Test --regions argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--regions", "us-east-1,us-west-2"])
        assert args.regions == "us-east-1,us-west-2"

    def test_aws_profile_argument(self):
        """Test --aws-profile argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--aws-profile", "production"])
        assert args.aws_profile == "production"

    def test_gcp_project_argument(self):
        """Test --gcp-project argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--gcp-project", "my-project-123"])
        assert args.gcp_project == "my-project-123"

    def test_gcp_key_argument(self):
        """Test --gcp-key argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--gcp-key", "/path/to/key.json"])
        assert args.gcp_key == "/path/to/key.json"

    def test_target_database_argument(self):
        """Test --target-database argument."""
        parser = create_main_parser()
        args = parser.parse_args(["cloud", "--target-database", "my-db-instance"])
        assert args.target_database == "my-db-instance"


class TestConfigFileHandling:
    """Test configuration file loading and processing."""

    @patch("planetscale_discovery.cli.ConfigManager")
    def test_config_template_generation(self, mock_config_manager):
        """Test config template generation."""
        mock_manager = Mock()
        mock_config_manager.return_value = mock_manager

        with patch("sys.argv", ["cli", "config-template", "--output", "test.yaml"]):
            with patch("builtins.print"):
                try:
                    main()
                except SystemExit:
                    pass

        mock_manager.save_config_template.assert_called_once_with(
            "test.yaml", providers=[]
        )

    @patch("planetscale_discovery.cli.ConfigManager")
    @patch("planetscale_discovery.cli.CloudDiscoveryTool")
    @patch("planetscale_discovery.cli.setup_logging")
    def test_config_file_loaded(self, mock_logging, mock_tool, mock_config_manager):
        """Test that config file is loaded when provided."""
        mock_config = Mock()
        mock_config.output.output_dir = "./output"

        mock_config.log_level = "INFO"
        mock_config.log_file = None
        mock_config.modules = ["cloud"]
        mock_config.aws.enabled = True
        mock_config.gcp.enabled = False

        mock_manager = Mock()
        mock_manager.load_config.return_value = mock_config
        mock_config_manager.return_value = mock_manager

        mock_tool_instance = Mock()
        mock_tool_instance.discover.return_value = {
            "summary": {"providers_discovered": [], "total_databases": 0}
        }
        mock_tool.return_value = mock_tool_instance

        with patch("sys.argv", ["cli", "cloud", "--config", "myconfig.yaml"]):
            with patch("builtins.open", mock_open()):
                with patch("builtins.print"):
                    try:
                        main()
                    except SystemExit:
                        pass

        mock_config_manager.assert_called_once_with("myconfig.yaml")

    @patch("planetscale_discovery.cli.ConfigManager")
    @patch("planetscale_discovery.cli.CloudDiscoveryTool")
    @patch("planetscale_discovery.cli.setup_logging")
    def test_cli_args_override_config(
        self, mock_logging, mock_tool, mock_config_manager
    ):
        """Test that CLI arguments override config file values."""
        mock_config = Mock()
        mock_config.output.output_dir = "./default_output"

        mock_config.log_level = "INFO"
        mock_config.log_file = None
        mock_config.modules = ["cloud"]
        mock_config.aws = Mock()
        mock_config.aws.enabled = False
        mock_config.gcp = Mock()
        mock_config.gcp.enabled = False

        mock_manager = Mock()
        mock_manager.load_config.return_value = mock_config
        mock_config_manager.return_value = mock_manager

        mock_tool_instance = Mock()
        mock_tool_instance.discover.return_value = {
            "summary": {"providers_discovered": [], "total_databases": 0}
        }
        mock_tool.return_value = mock_tool_instance

        with patch(
            "sys.argv",
            [
                "cli",
                "cloud",
                "--config",
                "myconfig.yaml",
                "--output-dir",
                "/custom/output",
                "--log-level",
                "DEBUG",
            ],
        ):
            with patch("builtins.open", mock_open()):
                with patch("builtins.print"):
                    try:
                        main()
                    except SystemExit:
                        pass

        # Verify overrides were applied
        assert mock_config.output.output_dir == "/custom/output"
        assert mock_config.log_level == "DEBUG"


class TestDiscoveryExecution:
    """Test discovery execution with different commands."""

    @patch("planetscale_discovery.cli.ConfigManager")
    @patch("planetscale_discovery.cli.CloudDiscoveryTool")
    @patch("planetscale_discovery.cli.setup_logging")
    def test_cloud_discovery_execution(
        self, mock_logging, mock_tool, mock_config_manager
    ):
        """Test cloud discovery command execution."""
        mock_config = Mock()
        mock_config.output.output_dir = "./output"

        mock_config.log_level = "INFO"
        mock_config.log_file = None
        mock_config.modules = ["cloud"]
        mock_config.aws = Mock()
        mock_config.aws.enabled = True
        mock_config.aws.regions = ["us-east-1"]
        mock_config.gcp = Mock()
        mock_config.gcp.enabled = False

        mock_manager = Mock()
        mock_manager.load_config.return_value = mock_config
        mock_config_manager.return_value = mock_manager

        mock_tool_instance = Mock()
        mock_tool_instance.discover.return_value = {
            "summary": {
                "providers_discovered": ["aws"],
                "total_databases": 2,
                "total_clusters": 1,
                "total_regions": 1,
            }
        }
        mock_tool.return_value = mock_tool_instance

        with patch("sys.argv", ["cli", "cloud", "--providers", "aws"]):
            with patch("builtins.open", mock_open()):
                with patch("builtins.print"):
                    try:
                        main()
                    except SystemExit:
                        pass

        # Verify cloud discovery was called
        mock_tool_instance.discover.assert_called_once()


class TestCombinedSummary:
    """Test combined summary generation."""

    def test_generate_combined_summary_database_only(self):
        """Test summary generation with database results only."""
        results = {
            "database_results": {
                "analysis_results": {
                    "schema": {"table_analysis": [{"name": "users"}]},
                    "features": {
                        "extensions_analysis": {"installed_extensions": ["pg_stat"]}
                    },
                    "security": {"user_role_analysis": {"users": ["postgres"]}},
                },
                "analysis_gaps": [],
            },
            "cloud_results": None,
        }

        summary = generate_combined_summary(results)

        assert summary["database_summary"]["tables_analyzed"] == 1
        assert summary["database_summary"]["extensions_found"] == 1
        assert summary["database_summary"]["users_analyzed"] == 1

    def test_generate_combined_summary_cloud_only(self):
        """Test summary generation with cloud results only."""
        results = {
            "database_results": None,
            "cloud_results": {
                "summary": {
                    "providers_discovered": ["aws"],
                    "total_databases": 3,
                    "total_clusters": 2,
                    "total_regions": 2,
                }
            },
        }

        summary = generate_combined_summary(results)

        assert summary["cloud_summary"]["providers_analyzed"] == 1
        assert summary["cloud_summary"]["total_databases"] == 3
        assert summary["cloud_summary"]["total_clusters"] == 2
        assert summary["cloud_summary"]["regions_analyzed"] == 2

    def test_generate_combined_summary_both(self):
        """Test summary generation with both database and cloud results."""
        results = {
            "database_results": {
                "analysis_results": {
                    "schema": {"table_analysis": [{"name": "users"}]},
                    "features": {"extensions_analysis": {"installed_extensions": []}},
                    "security": {"user_role_analysis": {"users": []}},
                },
                "analysis_gaps": [],
            },
            "cloud_results": {
                "summary": {
                    "providers_discovered": ["aws", "gcp"],
                    "total_databases": 5,
                    "total_clusters": 3,
                    "total_regions": 3,
                }
            },
        }

        summary = generate_combined_summary(results)

        assert summary["database_summary"]["tables_analyzed"] == 1
        assert summary["cloud_summary"]["providers_analyzed"] == 2
        assert summary["cloud_summary"]["total_databases"] == 5


class TestErrorHandling:
    """Test CLI error handling."""

    def test_no_command_shows_help(self, capsys):
        """Test that no command shows help and exits."""
        with patch("sys.argv", ["cli"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    @patch("planetscale_discovery.cli.ConfigManager")
    def test_keyboard_interrupt_handling(self, mock_config_manager, capsys):
        """Test graceful handling of keyboard interrupt."""
        mock_manager = Mock()
        mock_manager.load_config.side_effect = KeyboardInterrupt()
        mock_config_manager.return_value = mock_manager

        with patch("sys.argv", ["cli", "cloud"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "interrupted" in captured.out.lower()

    @patch("planetscale_discovery.cli.ConfigManager")
    def test_general_exception_handling(self, mock_config_manager, capsys):
        """Test handling of general exceptions."""
        mock_manager = Mock()
        mock_manager.load_config.side_effect = Exception("Test error")
        mock_config_manager.return_value = mock_manager

        with patch("sys.argv", ["cli", "cloud"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Error:" in captured.err


class TestFormatBytes:
    """Test _format_bytes helper."""

    def test_zero_bytes(self):
        assert _format_bytes(0) == "0 B"

    def test_bytes(self):
        result = _format_bytes(500)
        assert "B" in result

    def test_kilobytes(self):
        result = _format_bytes(1024)
        assert "KB" in result

    def test_megabytes(self):
        result = _format_bytes(1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = _format_bytes(1024 * 1024 * 1024)
        assert "GB" in result

    def test_terabytes(self):
        result = _format_bytes(1024 * 1024 * 1024 * 1024)
        assert "TB" in result


class TestGenerateSummaryMarkdown:
    """Test generate_summary_markdown function."""

    def _write_and_read(self, combined_results):
        """Helper to generate markdown and return its content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "summary.md"
            generate_summary_markdown(combined_results, output_path)
            assert output_path.exists()
            return output_path.read_text()

    def test_header_present(self):
        """Test that header with timestamp and version is generated."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": None,
            "cloud_results": None,
        }
        content = self._write_and_read(results)
        assert "# Discovery Summary" in content
        assert "1.1.0" in content
        assert "2025-01-15" in content

    def test_database_section_with_full_data(self):
        """Test database section with realistic analysis data."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": {
                "connection_info": {
                    "current_database": "production_db",
                },
                "analysis_results": {
                    "config": {
                        "version_info": {"major_version": "14.5"},
                    },
                    "schema": {
                        "database_catalog": [{"name": "db1"}, {"name": "db2"}],
                        "table_analysis": [
                            {"name": "users", "total_size_bytes": 104857600},
                            {"name": "orders", "total_size_bytes": 524288000},
                        ],
                    },
                    "features": {
                        "extensions_analysis": {
                            "installed_extensions": [
                                {"extname": "pg_stat_statements"},
                                {"extname": "postgis"},
                            ]
                        }
                    },
                    "security": {
                        "user_role_analysis": {
                            "users": [{"username": "admin"}, {"username": "app"}],
                            "summary": {"user_count": 2, "role_count": 3},
                        }
                    },
                    "performance": {
                        "connection_analysis": {
                            "connection_summary": {
                                "total_connections": 50,
                                "active_connections": 10,
                            }
                        },
                    },
                },
                "analysis_gaps": [],
            },
            "cloud_results": None,
        }
        content = self._write_and_read(results)
        assert "## Database" in content
        assert "production_db" in content
        assert "14.5" in content
        assert "Tables:" in content
        assert "Extensions (2):" in content
        assert "pg_stat_statements" in content
        assert "Users:" in content
        assert "Connections:" in content

    def test_cloud_section_with_aws(self):
        """Test cloud section with AWS provider data."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": None,
            "cloud_results": {
                "providers": {
                    "aws": {
                        "summary": {
                            "database_instances": 2,
                            "database_clusters": 1,
                        },
                        "resources": {
                            "us-east-1": {
                                "rds_instances": [
                                    {
                                        "db_instance_identifier": "prod-db",
                                        "engine": "postgres",
                                        "engine_version": "14.5",
                                        "db_instance_class": "db.r5.large",
                                        "allocated_storage": 100,
                                        "storage_type": "gp3",
                                        "multi_az": True,
                                        "status": "available",
                                    },
                                    {
                                        "db_instance_identifier": "dev-db",
                                        "engine": "mysql",
                                        "engine_version": "8.0",
                                        "db_instance_class": "db.t3.micro",
                                        "allocated_storage": 20,
                                        "storage_type": "gp2",
                                        "multi_az": False,
                                        "status": "available",
                                    },
                                ],
                                "aurora_clusters": [
                                    {
                                        "identifier": "my-cluster",
                                        "engine": "aurora-mysql",
                                        "engine_version": "8.0",
                                        "multi_az": True,
                                        "allocated_storage": "",
                                        "cluster_members": [
                                            {"is_cluster_writer": True},
                                            {"is_cluster_writer": False},
                                        ],
                                        "status": "available",
                                    }
                                ],
                            }
                        },
                    }
                },
                "summary": {
                    "providers_discovered": ["aws"],
                    "total_databases": 2,
                    "total_clusters": 1,
                    "total_regions": 1,
                },
                "errors": [],
            },
        }
        content = self._write_and_read(results)
        assert "## Cloud Infrastructure" in content
        assert "AWS" in content
        assert "#### RDS Instances" in content
        assert "prod-db" in content
        assert "dev-db" in content
        assert "#### Aurora Clusters" in content
        assert "my-cluster" in content

    def test_cloud_section_with_gcp(self):
        """Test cloud section with GCP provider data."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": None,
            "cloud_results": {
                "providers": {
                    "gcp": {
                        "summary": {
                            "cloud_sql_instances": 1,
                            "alloydb_clusters": 1,
                        },
                        "resources": {
                            "us-central1": {
                                "cloud_sql_instances": [
                                    {
                                        "name": "prod-sql",
                                        "database_version": "POSTGRES_14",
                                        "settings": {
                                            "tier": "db-custom-4-16384",
                                            "availability_type": "REGIONAL",
                                        },
                                        "region": "us-central1",
                                        "state": "RUNNABLE",
                                    }
                                ],
                                "alloydb_clusters": [
                                    {
                                        "name": "my-alloydb",
                                        "state": "READY",
                                        "region": "us-central1",
                                    }
                                ],
                            }
                        },
                    }
                },
                "summary": {
                    "providers_discovered": ["gcp"],
                    "total_databases": 1,
                    "total_clusters": 1,
                    "total_regions": 1,
                },
                "errors": [],
            },
        }
        content = self._write_and_read(results)
        assert "GCP" in content
        assert "#### Cloud SQL Instances" in content
        assert "prod-sql" in content
        assert "#### AlloyDB Clusters" in content
        assert "my-alloydb" in content

    def test_cloud_section_with_heroku(self):
        """Test cloud section with Heroku provider data."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": None,
            "cloud_results": {
                "providers": {
                    "heroku": {
                        "summary": {
                            "total_apps": 5,
                            "total_databases": 7,
                            "plans": {"standard-0": 4, "premium-0": 3},
                        },
                    }
                },
                "summary": {
                    "providers_discovered": ["heroku"],
                    "total_databases": 7,
                    "total_clusters": 0,
                    "total_regions": 0,
                },
                "errors": [],
            },
        }
        content = self._write_and_read(results)
        assert "HEROKU" in content
        assert "Apps:" in content
        assert "Databases:" in content
        assert "Plans:" in content

    def test_cloud_section_with_supabase(self):
        """Test cloud section with Supabase provider data."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": None,
            "cloud_results": {
                "providers": {
                    "supabase": {
                        "summary": {"total_projects": 3},
                    }
                },
                "summary": {
                    "providers_discovered": ["supabase"],
                    "total_databases": 3,
                    "total_clusters": 0,
                    "total_regions": 0,
                },
                "errors": [],
            },
        }
        content = self._write_and_read(results)
        assert "SUPABASE" in content
        assert "Projects:" in content

    def test_gaps_section(self):
        """Test that analysis gaps are included in the output."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": {
                "connection_info": {},
                "analysis_results": {},
                "analysis_gaps": [
                    {
                        "module": "security",
                        "description": "Failed to analyze user_mappings in security",
                        "error_message": "Database user lacks permissions to view foreign server user mappings",
                        "severity": "medium",
                    },
                    {
                        "module": "performance",
                        "description": "Failed to analyze query_stats in performance",
                        "error_message": "pg_stat_statements extension is not installed",
                        "severity": "low",
                    },
                ],
            },
            "cloud_results": None,
        }
        content = self._write_and_read(results)
        assert "## Information Not Collected" in content
        assert "user_mappings" in content
        assert "permissions" in content
        assert "pg_stat_statements" in content

    def test_errors_section(self):
        """Test that cloud errors are included in the output."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": None,
            "cloud_results": {
                "providers": {},
                "summary": {"providers_discovered": []},
                "errors": [
                    {"message": "AWS authentication failed: Invalid credentials"},
                    {"message": "GCP discovery failed: API not enabled"},
                ],
            },
        }
        content = self._write_and_read(results)
        assert "## Errors" in content
        assert "AWS authentication failed" in content
        assert "GCP discovery failed" in content

    def test_combined_database_and_cloud(self):
        """Test report with both database and cloud results."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": {
                "connection_info": {"current_database": "mydb"},
                "analysis_results": {
                    "schema": {
                        "table_analysis": [{"name": "t1", "total_size_bytes": 1024}]
                    },
                },
                "analysis_gaps": [],
            },
            "cloud_results": {
                "providers": {
                    "aws": {
                        "summary": {"database_instances": 2, "database_clusters": 0}
                    }
                },
                "summary": {
                    "providers_discovered": ["aws"],
                    "total_databases": 2,
                    "total_clusters": 0,
                    "total_regions": 1,
                },
                "errors": [],
            },
        }
        content = self._write_and_read(results)
        assert "## Database" in content
        assert "## Cloud Infrastructure" in content
        assert "mydb" in content
        assert "AWS" in content

    def test_no_results(self):
        """Test report generation with no database or cloud results."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": None,
            "cloud_results": None,
        }
        content = self._write_and_read(results)
        assert "# Discovery Summary" in content
        # Should not have database or cloud sections
        assert "## Database" not in content
        assert "## Cloud Infrastructure" not in content

    def test_no_migration_language(self):
        """Verify no migration-specific language in output."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": {
                "connection_info": {"current_database": "mydb"},
                "analysis_results": {
                    "config": {"version_info": {"major_version": "16"}},
                    "schema": {
                        "table_analysis": [{"name": "t1", "total_size_bytes": 0}]
                    },
                    "features": {"extensions_analysis": {"installed_extensions": []}},
                    "security": {"user_role_analysis": {"users": [], "summary": {}}},
                },
                "analysis_gaps": [
                    {
                        "module": "security",
                        "description": "Gap",
                        "error_message": "Permission denied",
                        "severity": "medium",
                    }
                ],
            },
            "cloud_results": {
                "providers": {},
                "summary": {"providers_discovered": []},
                "errors": [],
            },
        }
        content = self._write_and_read(results)
        content_lower = content.lower()
        assert "migration" not in content_lower
        assert "complexity" not in content_lower
        assert "recommendation" not in content_lower
        assert "confidential" not in content_lower
        assert "[high]" not in content_lower
        assert "[medium]" not in content_lower
        assert "planetscale" not in content_lower

    def test_replication_primary_with_replicas(self):
        """Test replication status for primary with replicas."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": {
                "connection_info": {},
                "analysis_results": {
                    "performance": {
                        "replication_lag": {
                            "is_standby": False,
                            "replication_summary": {"total_replicas": 2},
                        }
                    },
                },
                "analysis_gaps": [],
            },
            "cloud_results": None,
        }
        content = self._write_and_read(results)
        assert "Primary with 2 replica(s)" in content

    def test_replication_standby(self):
        """Test replication status for standby."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": {
                "connection_info": {},
                "analysis_results": {
                    "performance": {
                        "replication_lag": {
                            "is_standby": True,
                        }
                    },
                },
                "analysis_gaps": [],
            },
            "cloud_results": None,
        }
        content = self._write_and_read(results)
        assert "Standby (replica)" in content

    def test_overview_table(self):
        """Test the overview module status table."""
        results = {
            "timestamp": "2025-01-15T10:00:00Z",
            "discovery_version": "1.1.0",
            "database_results": {
                "connection_info": {},
                "analysis_results": {},
                "analysis_gaps": [],
            },
            "cloud_results": {
                "providers": {},
                "summary": {"providers_discovered": []},
                "errors": [],
            },
        }
        content = self._write_and_read(results)
        assert "| Database | Completed |" in content
        assert "| Cloud | Completed |" in content


class TestCombinedSummaryGaps:
    """Test that combined summary properly handles analysis gaps."""

    def test_gap_count_reported(self):
        """Test that analysis gaps are counted in combined summary."""
        results = {
            "database_results": {
                "analysis_results": {
                    "schema": {"table_analysis": []},
                    "features": {"extensions_analysis": {"installed_extensions": []}},
                    "security": {"user_role_analysis": {"users": []}},
                },
                "analysis_gaps": [
                    {"module": "security", "type": "permission_denied"},
                    {"module": "performance", "type": "missing_extension"},
                    {"module": "security", "type": "permission_denied"},
                ],
            },
            "cloud_results": None,
        }
        summary = generate_combined_summary(results)
        assert summary["database_summary"]["analysis_gaps"] == 3

    def test_zero_gaps(self):
        """Test summary with no analysis gaps."""
        results = {
            "database_results": {
                "analysis_results": {
                    "schema": {"table_analysis": []},
                    "features": {"extensions_analysis": {"installed_extensions": []}},
                    "security": {"user_role_analysis": {"users": []}},
                },
                "analysis_gaps": [],
            },
            "cloud_results": None,
        }
        summary = generate_combined_summary(results)
        assert summary["database_summary"]["analysis_gaps"] == 0
