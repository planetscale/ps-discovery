"""
Tests for GCP Analyzer - AlloyDB storage usage via Cloud Monitoring API
"""

from unittest.mock import MagicMock, patch
import pytest

from planetscale_discovery.cloud.analyzers.gcp_analyzer import GCPAnalyzer


@pytest.fixture
def mock_gcp_config():
    """Create a mock GCP config."""
    config = MagicMock()
    config.project_id = "test-project"
    config.regions = ["us-central1"]
    config.__dict__ = {
        "project_id": "test-project",
        "regions": ["us-central1"],
    }
    return config


@pytest.fixture
def mock_cluster():
    """Create a mock AlloyDB cluster object."""
    cluster = MagicMock()
    cluster.name = "projects/test-project/locations/us-central1/clusters/my-cluster"
    cluster.display_name = "My Cluster"
    cluster.state.name = "READY"
    cluster.cluster_type.name = "PRIMARY"
    cluster.database_type.name = "POSTGRES"
    cluster.network = "projects/test-project/global/networks/default"
    cluster.etag = "abc123"
    cluster.labels = {"env": "test"}
    cluster.encryption_config = None
    cluster.continuous_backup_config = None
    return cluster


class TestAlloyDBStorageUsage:
    """Tests for AlloyDB storage usage retrieval via Cloud Monitoring."""

    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.monitoring_v3")
    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.HAS_GCP_LIBS", True)
    def test_get_alloydb_storage_usage_success(
        self, mock_monitoring_v3, mock_gcp_config
    ):
        """Test successful retrieval of AlloyDB storage usage."""
        analyzer = GCPAnalyzer(mock_gcp_config)

        # Set up mock monitoring client
        mock_client = MagicMock()
        analyzer.monitoring_client = mock_client

        # Create mock time series response
        mock_point = MagicMock()
        mock_point.value.int64_value = 10737418240  # 10 GB in bytes

        mock_time_series = MagicMock()
        mock_time_series.points = [mock_point]

        mock_client.list_time_series.return_value = [mock_time_series]

        # Mock the monitoring_v3 classes used in the method
        mock_monitoring_v3.TimeInterval.return_value = MagicMock()
        mock_monitoring_v3.ListTimeSeriesRequest.return_value = MagicMock()
        mock_monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL = 0

        result = analyzer._get_alloydb_storage_usage("my-cluster", "us-central1")

        assert result is not None
        assert result["storage_usage_bytes"] == 10737418240
        assert result["storage_usage_gb"] == 10.0
        mock_client.list_time_series.assert_called_once()

    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.HAS_GCP_LIBS", True)
    def test_get_alloydb_storage_usage_no_monitoring_client(self, mock_gcp_config):
        """Test returns None when monitoring client is not initialized."""
        analyzer = GCPAnalyzer(mock_gcp_config)
        analyzer.monitoring_client = None

        result = analyzer._get_alloydb_storage_usage("my-cluster", "us-central1")
        assert result is None

    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.monitoring_v3")
    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.HAS_GCP_LIBS", True)
    def test_get_alloydb_storage_usage_no_data(
        self, mock_monitoring_v3, mock_gcp_config
    ):
        """Test returns None when no metric data is available."""
        analyzer = GCPAnalyzer(mock_gcp_config)

        mock_client = MagicMock()
        analyzer.monitoring_client = mock_client

        # Return empty results
        mock_client.list_time_series.return_value = []

        mock_monitoring_v3.TimeInterval.return_value = MagicMock()
        mock_monitoring_v3.ListTimeSeriesRequest.return_value = MagicMock()
        mock_monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL = 0

        result = analyzer._get_alloydb_storage_usage("my-cluster", "us-central1")
        assert result is None

    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.monitoring_v3")
    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.HAS_GCP_LIBS", True)
    def test_get_alloydb_storage_usage_api_error(
        self, mock_monitoring_v3, mock_gcp_config
    ):
        """Test graceful degradation when monitoring API fails."""
        analyzer = GCPAnalyzer(mock_gcp_config)

        mock_client = MagicMock()
        analyzer.monitoring_client = mock_client

        # Simulate API error
        mock_client.list_time_series.side_effect = Exception("Permission denied")

        mock_monitoring_v3.TimeInterval.return_value = MagicMock()
        mock_monitoring_v3.ListTimeSeriesRequest.return_value = MagicMock()
        mock_monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL = 0

        result = analyzer._get_alloydb_storage_usage("my-cluster", "us-central1")

        assert result is None
        # Should have added a warning, not raised
        assert len(analyzer.warnings) > 0
        assert "my-cluster" in analyzer.warnings[0]["message"]

    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.monitoring_v3")
    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.alloydb_v1")
    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.HAS_GCP_LIBS", True)
    def test_analyze_alloydb_clusters_includes_storage(
        self, mock_alloydb_v1, mock_monitoring_v3, mock_gcp_config, mock_cluster
    ):
        """Test that _analyze_alloydb_clusters includes storage data."""
        analyzer = GCPAnalyzer(mock_gcp_config)

        # Mock AlloyDB client
        mock_alloydb_client = MagicMock()
        mock_alloydb_v1.AlloyDBAdminClient.return_value = mock_alloydb_client
        mock_alloydb_v1.ListClustersRequest.return_value = MagicMock()
        mock_alloydb_v1.ListInstancesRequest.return_value = MagicMock()

        mock_alloydb_client.list_clusters.return_value = [mock_cluster]
        mock_alloydb_client.list_instances.return_value = []

        # Mock monitoring client with storage data
        mock_monitoring_client = MagicMock()
        analyzer.monitoring_client = mock_monitoring_client

        mock_point = MagicMock()
        mock_point.value.int64_value = 5368709120  # 5 GB

        mock_time_series = MagicMock()
        mock_time_series.points = [mock_point]
        mock_monitoring_client.list_time_series.return_value = [mock_time_series]

        mock_monitoring_v3.TimeInterval.return_value = MagicMock()
        mock_monitoring_v3.ListTimeSeriesRequest.return_value = MagicMock()
        mock_monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL = 0

        clusters = analyzer._analyze_alloydb_clusters("us-central1")

        assert len(clusters) == 1
        assert "storage" in clusters[0]
        assert clusters[0]["storage"]["storage_usage_bytes"] == 5368709120
        assert clusters[0]["storage"]["storage_usage_gb"] == 5.0

    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.alloydb_v1")
    @patch("planetscale_discovery.cloud.analyzers.gcp_analyzer.HAS_GCP_LIBS", True)
    def test_analyze_alloydb_clusters_no_storage_on_failure(
        self, mock_alloydb_v1, mock_gcp_config, mock_cluster
    ):
        """Test that clusters still work when storage monitoring fails."""
        analyzer = GCPAnalyzer(mock_gcp_config)

        # Mock AlloyDB client
        mock_alloydb_client = MagicMock()
        mock_alloydb_v1.AlloyDBAdminClient.return_value = mock_alloydb_client
        mock_alloydb_v1.ListClustersRequest.return_value = MagicMock()
        mock_alloydb_v1.ListInstancesRequest.return_value = MagicMock()

        mock_alloydb_client.list_clusters.return_value = [mock_cluster]
        mock_alloydb_client.list_instances.return_value = []

        # No monitoring client - simulates initialization failure
        analyzer.monitoring_client = None

        clusters = analyzer._analyze_alloydb_clusters("us-central1")

        assert len(clusters) == 1
        assert "storage" not in clusters[0]
        assert clusters[0]["name"] == "my-cluster"
