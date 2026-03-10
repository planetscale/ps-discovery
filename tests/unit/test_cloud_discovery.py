"""
Tests for Cloud Discovery Tool
"""

from unittest.mock import MagicMock, patch
from planetscale_discovery.cloud.discovery import CloudDiscoveryTool
from planetscale_discovery.config.config_manager import DiscoveryConfig


class TestCloudDiscoveryTool:
    """Tests for CloudDiscoveryTool"""

    def test_instantiation(self):
        """Test tool can be instantiated"""
        config = DiscoveryConfig()
        tool = CloudDiscoveryTool(config)

        assert tool is not None
        assert tool.config == config
        assert "discovery_version" in tool.results
        assert "providers" in tool.results
        assert "summary" in tool.results

    def test_discover_with_no_providers(self):
        """Test discover with no providers enabled"""
        config = DiscoveryConfig()
        config.aws.enabled = False
        config.gcp.enabled = False

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        assert "providers" in results
        assert len(results["providers"]) == 0

    @patch("planetscale_discovery.cloud.analyzers.aws_analyzer.AWSAnalyzer")
    def test_discover_aws(self, mock_aws_analyzer_class):
        """Test AWS discovery"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.return_value = True
        mock_analyzer.analyze.return_value = {
            "resources": {"rds": [], "aurora": []},
            "metadata": {"region_count": 1},
        }
        mock_aws_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.aws.enabled = True
        config.aws.regions = ["us-east-1"]

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        assert "aws" in results["providers"]
        assert "resources" in results["providers"]["aws"]
        mock_analyzer.authenticate.assert_called_once()
        mock_analyzer.analyze.assert_called_once()

    @patch("planetscale_discovery.cloud.analyzers.aws_analyzer.AWSAnalyzer")
    def test_discover_aws_authentication_failure(self, mock_aws_analyzer_class):
        """Test AWS discovery with authentication failure"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.return_value = False
        mock_aws_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.aws.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        # AWS results should not be in providers if auth failed
        assert "aws" not in results["providers"]
        mock_analyzer.analyze.assert_not_called()

    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.GCPAnalyzer")
    def test_discover_gcp(self, mock_gcp_analyzer_class):
        """Test GCP discovery"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.return_value = True
        mock_analyzer.analyze.return_value = {
            "resources": {"cloud_sql": [], "alloydb": []},
            "metadata": {"project_id": "test-project"},
        }
        mock_gcp_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.gcp.enabled = True
        config.gcp.project_id = "test-project"

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        assert "gcp" in results["providers"]
        assert "resources" in results["providers"]["gcp"]
        mock_analyzer.authenticate.assert_called_once()
        mock_analyzer.analyze.assert_called_once()

    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.GCPAnalyzer")
    def test_discover_gcp_authentication_failure(self, mock_gcp_analyzer_class):
        """Test GCP discovery with authentication failure"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.return_value = False
        mock_gcp_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.gcp.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        # GCP results should not be in providers if auth failed
        assert "gcp" not in results["providers"]
        mock_analyzer.analyze.assert_not_called()

    @patch("planetscale_discovery.cloud.analyzers.aws_analyzer.AWSAnalyzer")
    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.GCPAnalyzer")
    def test_discover_both_providers(
        self, mock_gcp_analyzer_class, mock_aws_analyzer_class
    ):
        """Test discovery with both AWS and GCP enabled"""
        mock_aws = MagicMock()
        mock_aws.authenticate.return_value = True
        mock_aws.analyze.return_value = {"resources": {}}
        mock_aws_analyzer_class.return_value = mock_aws

        mock_gcp = MagicMock()
        mock_gcp.authenticate.return_value = True
        mock_gcp.analyze.return_value = {"resources": {}}
        mock_gcp_analyzer_class.return_value = mock_gcp

        config = DiscoveryConfig()
        config.aws.enabled = True
        config.gcp.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        assert "aws" in results["providers"]
        assert "gcp" in results["providers"]

    @patch("planetscale_discovery.cloud.analyzers.aws_analyzer.AWSAnalyzer")
    def test_discover_with_target_database(self, mock_aws_analyzer_class):
        """Test discovery with target_database filtering"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.return_value = True
        mock_analyzer.analyze.return_value = {"resources": {}}
        mock_aws_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.aws.enabled = True
        config.target_database = "specific_db"

        tool = CloudDiscoveryTool(config)
        tool.discover()

        # Verify target_database was passed to AWS config
        assert mock_aws_analyzer_class.called
        call_args = mock_aws_analyzer_class.call_args[0][0]
        assert hasattr(call_args, "target_database")

    @patch("planetscale_discovery.cloud.analyzers.aws_analyzer.AWSAnalyzer")
    def test_discover_handles_exceptions(self, mock_aws_analyzer_class):
        """Test discover handles exceptions gracefully"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.side_effect = Exception("Test exception")
        mock_aws_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.aws.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        # Should have error in results
        assert len(results["errors"]) > 0
        assert "Test exception" in results["errors"][0]["message"]

    def test_run_alias(self):
        """Test run() is an alias for discover()"""
        config = DiscoveryConfig()
        tool = CloudDiscoveryTool(config)

        discover_result = tool.discover()
        tool2 = CloudDiscoveryTool(config)
        run_result = tool2.run()

        # Both should have the same structure
        assert "providers" in discover_result
        assert "providers" in run_result

    @patch("planetscale_discovery.cloud.analyzers.aws_analyzer.AWSAnalyzer")
    def test_summary_generation(self, mock_aws_analyzer_class):
        """Test summary is generated after discovery"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.return_value = True
        mock_analyzer.analyze.return_value = {
            "resources": {"rds": [{"id": "db1"}], "aurora": []},
        }
        mock_aws_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.aws.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        # Summary should be populated
        assert "summary" in results

    @patch("planetscale_discovery.cloud.analyzers.aws_analyzer.AWSAnalyzer")
    def test_discover_counts_resources(self, mock_aws_analyzer_class):
        """Test discovery counts resources correctly"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.return_value = True
        mock_analyzer.analyze.return_value = {
            "resources": {
                "rds": [{"id": "db1"}, {"id": "db2"}],
                "aurora": [{"id": "cluster1"}],
            },
        }
        mock_aws_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.aws.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        # Should have AWS results with resources
        assert "aws" in results["providers"]
        assert len(results["providers"]["aws"]["resources"]["rds"]) == 2
        assert len(results["providers"]["aws"]["resources"]["aurora"]) == 1

    @patch("planetscale_discovery.cloud.analyzers.heroku_analyzer.HerokuAnalyzer")
    def test_discover_heroku(self, mock_heroku_analyzer_class):
        """Test Heroku discovery"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.return_value = True
        mock_analyzer.analyze.return_value = {
            "apps": [{"name": "my-app", "databases": [{"plan_name": "standard-0"}]}],
            "summary": {"total_apps": 1, "total_databases": 1},
        }
        mock_heroku_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.heroku.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        assert "heroku" in results["providers"]
        assert len(results["providers"]["heroku"]["apps"]) == 1
        mock_analyzer.authenticate.assert_called_once()
        mock_analyzer.analyze.assert_called_once()

    @patch("planetscale_discovery.cloud.analyzers.heroku_analyzer.HerokuAnalyzer")
    def test_discover_heroku_authentication_failure(self, mock_heroku_analyzer_class):
        """Test Heroku discovery with authentication failure"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.return_value = False
        mock_heroku_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.heroku.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        assert "heroku" not in results["providers"]
        mock_analyzer.analyze.assert_not_called()

    @patch("planetscale_discovery.cloud.analyzers.heroku_analyzer.HerokuAnalyzer")
    def test_discover_heroku_exception(self, mock_heroku_analyzer_class):
        """Test Heroku discovery handles exceptions gracefully"""
        mock_analyzer = MagicMock()
        mock_analyzer.authenticate.side_effect = Exception("Heroku test exception")
        mock_heroku_analyzer_class.return_value = mock_analyzer

        config = DiscoveryConfig()
        config.heroku.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        assert len(results["errors"]) > 0
        assert "Heroku" in results["errors"][0]["message"]

    @patch("planetscale_discovery.cloud.analyzers.aws_analyzer.AWSAnalyzer")
    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.GCPAnalyzer")
    def test_discover_mixed_success_and_failure(
        self, mock_gcp_analyzer_class, mock_aws_analyzer_class
    ):
        """Test discovery when one provider succeeds and one fails"""
        # AWS succeeds
        mock_aws = MagicMock()
        mock_aws.authenticate.return_value = True
        mock_aws.analyze.return_value = {"resources": {}}
        mock_aws_analyzer_class.return_value = mock_aws

        # GCP fails
        mock_gcp = MagicMock()
        mock_gcp.authenticate.return_value = False
        mock_gcp_analyzer_class.return_value = mock_gcp

        config = DiscoveryConfig()
        config.aws.enabled = True
        config.gcp.enabled = True

        tool = CloudDiscoveryTool(config)
        results = tool.discover()

        # AWS should be in results, GCP should not
        assert "aws" in results["providers"]
        assert "gcp" not in results["providers"]
