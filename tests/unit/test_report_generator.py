"""
Tests for Report Generators
"""

import tempfile
from pathlib import Path
from planetscale_discovery.database.report_generator import ReportGenerator
from planetscale_discovery.cloud.report_generator import CloudReportGenerator


class TestDatabaseReportGenerator:
    """Tests for database report generator"""

    def test_instantiation(self):
        """Test report generator can be created"""
        results = {
            "connection_info": {
                "host": "localhost",
                "database": "testdb",
                "version": "17.4",
            },
            "analysis_results": {
                "config": {},
                "schema": {},
                "performance": {},
            },
        }
        generator = ReportGenerator(results)
        assert generator is not None
        assert generator.results == results

    def test_generate_json_report(self):
        """Test JSON report generation"""
        results = {
            "connection_info": {"host": "localhost"},
            "analysis_results": {"config": {"version": "14.0"}},
        }
        generator = ReportGenerator(results)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "report.json"
            generator.generate_json_report(str(output_file))

            assert output_file.exists()
            content = output_file.read_text()
            assert len(content) > 0
            assert "localhost" in content


class TestCloudReportGenerator:
    """Tests for cloud report generator"""

    def test_instantiation(self):
        """Test cloud report generator can be created"""
        results = {
            "providers": {"aws": {}},
            "summary": {},
        }
        generator = CloudReportGenerator(results)
        assert generator is not None
        assert generator.results == results
