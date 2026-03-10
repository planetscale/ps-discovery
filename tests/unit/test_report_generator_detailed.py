"""
Detailed tests for Report Generators with various data structures
"""

import tempfile
import json
from pathlib import Path
from planetscale_discovery.database.report_generator import ReportGenerator
from planetscale_discovery.cloud.report_generator import CloudReportGenerator


class TestDatabaseReportGeneratorDetailed:
    """Detailed tests for database report generator with realistic data"""

    def test_full_json_report_with_comprehensive_data(self):
        """Test JSON report generation with comprehensive data"""
        results = {
            "connection_info": {
                "version_string": "PostgreSQL 14.5 on x86_64-pc-linux-gnu",
                "current_database": "production_db",
                "current_user": "admin",
                "host": "db.example.com",
                "port": 5432,
            },
            "analysis_results": {
                "config": {
                    "version": {"major": 14, "minor": 5, "patch": 0},
                    "parameters": [
                        {"name": "max_connections", "value": "200", "unit": None},
                        {"name": "shared_buffers", "value": "8GB", "unit": "GB"},
                    ],
                },
                "schema": {
                    "tables": [
                        {
                            "name": "users",
                            "schema": "public",
                            "row_count": 1000000,
                            "size_bytes": 104857600,
                            "columns": 15,
                        },
                    ],
                    "views": [{"name": "active_users", "definition": "SELECT..."}],
                    "total_size_bytes": 1073741824,
                },
                "performance": {
                    "cache_hit_ratio": 0.95,
                },
                "security": {
                    "users": [
                        {"username": "admin", "roles": ["superuser"]},
                    ],
                    "ssl_enabled": True,
                },
                "features": {
                    "extensions": [
                        {"name": "pg_stat_statements", "version": "1.9"},
                    ],
                },
            },
            "analysis_gaps": [
                {
                    "module": "security",
                    "type": "permission_check",
                    "description": "Could not check all permissions",
                    "error_message": "Access denied",
                    "severity": "medium",
                    "impact": "Some security checks incomplete",
                }
            ],
        }

        generator = ReportGenerator(results)

        # Test JSON generation
        with tempfile.TemporaryDirectory() as tmpdir:
            json_file = Path(tmpdir) / "report.json"
            generator.generate_json_report(str(json_file))
            assert json_file.exists()
            data = json.loads(json_file.read_text())
            assert data["connection_info"]["current_database"] == "production_db"
            assert "analysis_results" in data
            assert "analysis_gaps" in data

    def test_json_report_preserves_all_data(self):
        """Test that JSON report preserves all input data faithfully"""
        results = {
            "connection_info": {"current_database": "testdb"},
            "analysis_results": {
                "config": {"version": "14.5"},
                "schema": {"tables_count": 42},
            },
        }
        generator = ReportGenerator(results)

        with tempfile.TemporaryDirectory() as tmpdir:
            json_file = Path(tmpdir) / "report.json"
            generator.generate_json_report(str(json_file))
            data = json.loads(json_file.read_text())
            assert data["analysis_results"]["schema"]["tables_count"] == 42


class TestCloudReportGeneratorDetailed:
    """Detailed tests for cloud report generator"""

    def test_instantiation_with_aws_data(self):
        """Test cloud report generator with comprehensive AWS data"""
        results = {
            "providers": {
                "aws": {
                    "metadata": {
                        "account_id": "123456789012",
                        "regions": ["us-east-1", "us-west-2"],
                    },
                    "resources": {
                        "us-east-1": {
                            "rds_instances": [
                                {
                                    "id": "db-prod-1",
                                    "engine": "postgres",
                                    "engine_version": "14.5",
                                }
                            ],
                        }
                    },
                }
            },
            "summary": {"total_databases": 1},
        }

        generator = CloudReportGenerator(results)
        assert generator.results == results
        assert "aws" in generator.results["providers"]

    def test_instantiation_with_mixed_providers(self):
        """Test cloud report generator with both AWS and GCP"""
        results = {
            "providers": {
                "aws": {"resources": {}},
                "gcp": {"resources": {}},
            },
            "summary": {"total_databases": 2},
        }

        generator = CloudReportGenerator(results)
        assert "aws" in generator.results["providers"]
        assert "gcp" in generator.results["providers"]
