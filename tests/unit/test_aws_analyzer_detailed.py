"""
Detailed tests for AWS Analyzer
"""

from unittest.mock import MagicMock, patch
from planetscale_discovery.cloud.analyzers.aws_analyzer import AWSAnalyzer


class TestAWSAnalyzerDetailed:
    """Detailed AWS Analyzer tests"""

    def test_analyze_returns_dict(self):
        """Test that analyze returns a dictionary"""
        config = {"aws": {"regions": ["us-east-1"], "target_database": None}}
        analyzer = AWSAnalyzer(config)

        with patch.object(analyzer, "authenticate", return_value=False):
            result = analyzer.analyze()
            assert isinstance(result, dict)

    def test_add_error_tracking(self):
        """Test error tracking"""
        config = {"aws": {"regions": ["us-east-1"]}}
        analyzer = AWSAnalyzer(config)

        analyzer.add_error("Test error", Exception("test"))
        assert len(analyzer.errors) > 0

    def test_add_warning_tracking(self):
        """Test warning tracking"""
        config = {"aws": {"regions": ["us-east-1"]}}
        analyzer = AWSAnalyzer(config)

        analyzer.add_warning("Test warning")
        assert len(analyzer.warnings) > 0

    @patch("boto3.Session")
    def test_authenticate_with_profile(self, mock_session):
        """Test authentication with AWS profile"""
        mock_session_instance = MagicMock()
        mock_session.return_value = mock_session_instance
        mock_sts = MagicMock()
        mock_session_instance.client.return_value = mock_sts
        mock_sts.get_caller_identity.return_value = {"Account": "123456789012"}

        config = {"profile": "test", "regions": ["us-east-1"]}
        analyzer = AWSAnalyzer(config)
        result = analyzer.authenticate()

        assert result is True
        assert analyzer.session is not None

    def test_get_analysis_metadata(self):
        """Test metadata generation"""
        config = {"aws": {"regions": ["us-east-1"]}}
        analyzer = AWSAnalyzer(config)

        metadata = analyzer.get_analysis_metadata()
        assert isinstance(metadata, dict)
        assert "timestamp" in metadata or "analyzer" in metadata

    @patch("boto3.Session")
    def test_analyze_with_authentication_failure(self, mock_session):
        """Test analyze when authentication fails"""
        config = {"profile": "invalid", "regions": ["us-east-1"]}
        analyzer = AWSAnalyzer(config)

        with patch.object(analyzer, "authenticate", return_value=False):
            result = analyzer.analyze()
            assert isinstance(result, dict)

    def test_filtered_analysis_attribute(self):
        """Test filtered analysis tracking"""
        config = {"aws": {"regions": ["us-east-1"], "target_database": "mydb"}}
        analyzer = AWSAnalyzer(config)

        assert analyzer.target_database == "mydb"

    @patch("boto3.Session")
    def test_discover_resources(self, mock_session):
        """Test resource discovery"""
        mock_session_instance = MagicMock()
        mock_session.return_value = mock_session_instance

        config = {"aws": {"regions": ["us-east-1"]}}
        analyzer = AWSAnalyzer(config)
        analyzer.session = mock_session_instance

        # Mock clients
        analyzer.rds_clients = {"us-east-1": MagicMock()}
        analyzer.ec2_clients = {"us-east-1": MagicMock()}

        result = analyzer.discover_resources()
        assert isinstance(result, list)
