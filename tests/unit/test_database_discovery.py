"""
Tests for Database Discovery Tool
"""

import pytest
from unittest.mock import MagicMock, patch, Mock
from planetscale_discovery.database.discovery import (
    DatabaseDiscoveryTool,
    PostgreSQLDiscovery,
)
from planetscale_discovery.config.config_manager import DiscoveryConfig, DatabaseConfig


class TestDatabaseDiscoveryTool:
    """Tests for DatabaseDiscoveryTool wrapper"""

    def test_instantiation(self):
        """Test tool can be instantiated"""
        config = DiscoveryConfig()
        tool = DatabaseDiscoveryTool(config)
        assert tool is not None
        assert tool.config == config

    @patch("planetscale_discovery.database.discovery.PostgreSQLDiscovery")
    def test_discover_creates_postgresql_discovery(self, mock_discovery_class):
        """Test discover creates PostgreSQLDiscovery instance"""
        config = DiscoveryConfig()
        config.database = DatabaseConfig(
            host="testhost",
            port=5433,
            database="testdb",
            username="testuser",
            password="testpass",
        )

        mock_instance = MagicMock()
        mock_instance.connect.return_value = True
        mock_instance.run_analysis.return_value = {"test": "results"}
        mock_discovery_class.return_value = mock_instance

        tool = DatabaseDiscoveryTool(config)
        result = tool.discover()

        # Verify PostgreSQLDiscovery was created with correct config
        mock_discovery_class.assert_called_once()
        call_args = mock_discovery_class.call_args[0][0]
        assert call_args["host"] == "testhost"
        assert call_args["port"] == 5433
        assert call_args["database"] == "testdb"

        # Verify methods were called
        mock_instance.connect.assert_called_once()
        mock_instance.run_analysis.assert_called_once()
        assert result == {"test": "results"}

    @patch("planetscale_discovery.database.discovery.PostgreSQLDiscovery")
    def test_discover_raises_on_connection_failure(self, mock_discovery_class):
        """Test discover raises error when connection fails"""
        config = DiscoveryConfig()
        config.database = DatabaseConfig(database="testdb")

        mock_instance = MagicMock()
        mock_instance.connect.return_value = False
        mock_discovery_class.return_value = mock_instance

        tool = DatabaseDiscoveryTool(config)

        with pytest.raises(ConnectionError, match="Failed to connect"):
            tool.discover()


class TestPostgreSQLDiscovery:
    """Tests for PostgreSQLDiscovery"""

    def test_instantiation(self):
        """Test PostgreSQLDiscovery can be created"""
        params = {"host": "localhost", "port": 5432, "database": "testdb"}
        discovery = PostgreSQLDiscovery(params)

        assert discovery is not None
        assert discovery.connection_params == params
        assert discovery.connection is None
        assert "timestamp" in discovery.results
        assert "connection_info" in discovery.results

    @patch("psycopg2.connect")
    def test_connect_success(self, mock_connect):
        """Test successful database connection"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_cursor.fetchone.return_value = {
            "version": "PostgreSQL 14.0",
            "current_database": "testdb",
            "current_user": "testuser",
        }
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        params = {"host": "localhost", "database": "testdb"}
        discovery = PostgreSQLDiscovery(params)

        result = discovery.connect()

        assert result is True
        assert discovery.connection is not None
        assert (
            discovery.results["connection_info"]["version_string"] == "PostgreSQL 14.0"
        )
        assert discovery.results["connection_info"]["current_database"] == "testdb"

    @patch("psycopg2.connect")
    def test_connect_failure(self, mock_connect):
        """Test failed database connection"""
        mock_connect.side_effect = Exception("Connection refused")

        params = {"host": "localhost", "database": "testdb"}
        discovery = PostgreSQLDiscovery(params)

        result = discovery.connect()

        assert result is False
        assert discovery.connection is None

    def test_run_analysis_without_connection(self):
        """Test run_analysis fails without connection"""
        params = {"host": "localhost", "database": "testdb"}
        discovery = PostgreSQLDiscovery(params)

        with pytest.raises(RuntimeError, match="No database connection"):
            discovery.run_analysis()

    @patch("psycopg2.connect")
    def test_run_analysis_with_modules(self, mock_connect):
        """Test run_analysis with specific modules"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor

        # Mock analyzer
        with patch(
            "planetscale_discovery.database.discovery.ConfigAnalyzer"
        ) as mock_analyzer_class:
            mock_analyzer = MagicMock()
            mock_analyzer.analyze.return_value = {"test": "config_results"}
            mock_analyzer_class.return_value = mock_analyzer

            mock_connect.return_value = mock_conn

            params = {"host": "localhost", "database": "testdb"}
            discovery = PostgreSQLDiscovery(params)
            discovery.connection = mock_conn

            results = discovery.run_analysis(modules=["config"])

            assert "config" in results["analysis_results"]
            assert results["analysis_results"]["config"]["test"] == "config_results"

    @patch("psycopg2.connect")
    def test_run_analysis_handles_module_failure(self, mock_connect):
        """Test run_analysis handles module failures gracefully"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor

        # Mock analyzer that raises exception
        with patch(
            "planetscale_discovery.database.discovery.SchemaAnalyzer"
        ) as mock_analyzer_class:
            mock_analyzer = MagicMock()
            mock_analyzer.analyze.side_effect = Exception("Analysis failed")
            mock_analyzer_class.return_value = mock_analyzer

            mock_connect.return_value = mock_conn

            params = {"host": "localhost", "database": "testdb"}
            discovery = PostgreSQLDiscovery(params)
            discovery.connection = mock_conn

            results = discovery.run_analysis(modules=["schema"])

            # Should have error in results
            assert "schema" in results["analysis_results"]
            assert "error" in results["analysis_results"]["schema"]
            assert len(results["analysis_gaps"]) > 0

    @patch("psycopg2.connect")
    def test_run_analysis_warns_unknown_module(self, mock_connect):
        """Test run_analysis warns about unknown modules"""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        params = {"host": "localhost", "database": "testdb"}
        discovery = PostgreSQLDiscovery(params)
        discovery.connection = mock_conn

        results = discovery.run_analysis(modules=["nonexistent_module"])

        # Should complete without error, but module not in results
        assert "nonexistent_module" not in results["analysis_results"]

    def test_add_analysis_gap(self):
        """Test adding analysis gaps"""
        params = {"host": "localhost", "database": "testdb"}
        discovery = PostgreSQLDiscovery(params)

        discovery._add_analysis_gap(
            module="test_module",
            gap_type="test_gap",
            description="Test description",
            error_msg="Test error",
            severity="high",
        )

        assert len(discovery.results["analysis_gaps"]) == 1
        gap = discovery.results["analysis_gaps"][0]
        assert gap["module"] == "test_module"
        assert gap["type"] == "test_gap"
        assert gap["severity"] == "high"

    def test_extract_analysis_gaps_non_dict(self):
        """Test extract gaps handles non-dict results"""
        params = {"host": "localhost", "database": "testdb"}
        discovery = PostgreSQLDiscovery(params)

        # Should not raise error
        discovery._extract_analysis_gaps("test", "not a dict")
        discovery._extract_analysis_gaps("test", None)
        discovery._extract_analysis_gaps("test", [1, 2, 3])

    @patch("psycopg2.connect")
    def test_run_analysis_all_modules(self, mock_connect):
        """Test run_analysis with all default modules"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=None)
        mock_conn.cursor.return_value = mock_cursor

        # Mock all analyzers
        with (
            patch(
                "planetscale_discovery.database.discovery.ConfigAnalyzer"
            ) as mock_config,
            patch(
                "planetscale_discovery.database.discovery.SchemaAnalyzer"
            ) as mock_schema,
            patch(
                "planetscale_discovery.database.discovery.PerformanceAnalyzer"
            ) as mock_perf,
            patch(
                "planetscale_discovery.database.discovery.SecurityAnalyzer"
            ) as mock_security,
            patch(
                "planetscale_discovery.database.discovery.FeatureAnalyzer"
            ) as mock_features,
        ):

            for mock_analyzer_class in [
                mock_config,
                mock_schema,
                mock_perf,
                mock_security,
                mock_features,
            ]:
                mock_analyzer = MagicMock()
                mock_analyzer.analyze.return_value = {"test": "data"}
                mock_analyzer_class.return_value = mock_analyzer

            mock_connect.return_value = mock_conn

            params = {"host": "localhost", "database": "testdb"}
            discovery = PostgreSQLDiscovery(params)
            discovery.connection = mock_conn

            results = discovery.run_analysis()

            # All modules should be in results
            assert "config" in results["analysis_results"]
            assert "schema" in results["analysis_results"]
            assert "performance" in results["analysis_results"]
            assert "security" in results["analysis_results"]
            assert "features" in results["analysis_results"]
