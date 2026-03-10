"""
Basic smoke tests for core functionality
"""

from unittest.mock import MagicMock, patch


class TestConfigAnalyzer:
    """Basic tests for ConfigAnalyzer"""

    def test_analyzer_can_be_instantiated(self):
        """Test that ConfigAnalyzer can be created"""
        from planetscale_discovery.database.analyzers.config_analyzer import (
            ConfigAnalyzer,
        )

        mock_conn = MagicMock()
        analyzer = ConfigAnalyzer(mock_conn)
        assert analyzer is not None
        assert analyzer.connection == mock_conn


class TestAWSAnalyzer:
    """Basic tests for AWSAnalyzer"""

    def test_analyzer_can_be_instantiated(self):
        """Test that AWSAnalyzer can be created"""
        from planetscale_discovery.cloud.analyzers.aws_analyzer import AWSAnalyzer

        config = {"aws": {"regions": ["us-east-1"], "target_database": None}}
        analyzer = AWSAnalyzer(config)
        assert analyzer is not None
        assert analyzer.regions == ["us-east-1"]

    @patch("boto3.Session")
    def test_authentication_works(self, mock_session):
        """Test AWS authentication"""
        from planetscale_discovery.cloud.analyzers.aws_analyzer import AWSAnalyzer

        mock_session_instance = MagicMock()
        mock_session.return_value = mock_session_instance

        config = {"profile": "test", "regions": ["us-east-1"]}
        analyzer = AWSAnalyzer(config)

        # authenticate() should return True on success
        result = analyzer.authenticate()
        assert result is True


class TestReportGenerators:
    """Basic tests for report generators"""

    def test_database_report_generator_instantiates(self):
        """Test DatabaseReportGenerator can be created"""
        from planetscale_discovery.database.report_generator import ReportGenerator

        results = {
            "connection_info": {"host": "localhost"},
            "analysis_results": {},
        }
        generator = ReportGenerator(results)
        assert generator is not None

    def test_cloud_report_generator_instantiates(self):
        """Test CloudReportGenerator can be created"""
        from planetscale_discovery.cloud.report_generator import (
            CloudReportGenerator,
        )

        results = {"providers": {}, "summary": {}}
        generator = CloudReportGenerator(results)
        assert generator is not None


class TestDiscoveryTools:
    """Basic tests for discovery orchestration"""

    def test_database_discovery_tool_exists(self):
        """Test DatabaseDiscoveryTool can be imported"""
        from planetscale_discovery.database.discovery import DatabaseDiscoveryTool

        assert DatabaseDiscoveryTool is not None

    def test_cloud_discovery_tool_exists(self):
        """Test CloudDiscoveryTool can be imported"""
        from planetscale_discovery.cloud.discovery import CloudDiscoveryTool

        assert CloudDiscoveryTool is not None
