"""
Tests for Supabase Analyzer
"""

from unittest.mock import MagicMock, patch
from planetscale_discovery.cloud.analyzers.supabase_analyzer import SupabaseAnalyzer


class TestSupabaseAnalyzer:
    """Supabase Analyzer tests"""

    def test_instantiation(self):
        """Test analyzer can be instantiated"""
        config = MagicMock()
        config.access_token = "test_token"
        analyzer = SupabaseAnalyzer(config)
        assert analyzer is not None
        assert analyzer.provider == "supabase"

    def test_analyze_returns_dict(self):
        """Test that analyze returns a dictionary"""
        config = MagicMock()
        config.access_token = "test_token"
        config.project_ref = None
        analyzer = SupabaseAnalyzer(config)

        with patch.object(analyzer, "authenticate", return_value=False):
            result = analyzer.analyze()
            assert isinstance(result, dict)
            assert "error" in result or "projects" in result

    def test_add_error_tracking(self):
        """Test error tracking"""
        config = MagicMock()
        config.access_token = "test_token"
        analyzer = SupabaseAnalyzer(config)

        analyzer.add_error("Test error", Exception("test"))
        assert len(analyzer.errors) > 0

    def test_add_warning_tracking(self):
        """Test warning tracking"""
        config = MagicMock()
        config.access_token = "test_token"
        analyzer = SupabaseAnalyzer(config)

        analyzer.add_warning("Test warning")
        assert len(analyzer.warnings) > 0

    def test_get_analysis_metadata(self):
        """Test metadata generation"""
        config = MagicMock()
        config.access_token = "test_token"
        analyzer = SupabaseAnalyzer(config)

        metadata = analyzer.get_analysis_metadata()
        assert isinstance(metadata, dict)

    @patch("requests.Session")
    def test_authenticate_with_token(self, mock_session_class):
        """Test authentication with access token"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_session.get.return_value = mock_response

        config = MagicMock()
        config.access_token = "test_token_123"
        analyzer = SupabaseAnalyzer(config)

        result = analyzer.authenticate()
        assert result is True
        assert analyzer.session is not None

    @patch("requests.Session")
    def test_authenticate_invalid_token(self, mock_session_class):
        """Test authentication with invalid token"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_session.get.return_value = mock_response

        config = MagicMock()
        config.access_token = "invalid_token"
        analyzer = SupabaseAnalyzer(config)

        result = analyzer.authenticate()
        assert result is False
        assert len(analyzer.errors) > 0

    @patch("requests.Session")
    def test_authenticate_no_token(self, mock_session_class):
        """Test authentication without token"""
        config = MagicMock()
        config.access_token = None

        with patch.dict("os.environ", {}, clear=True):
            analyzer = SupabaseAnalyzer(config)
            result = analyzer.authenticate()
            assert result is False
            assert len(analyzer.errors) > 0

    @patch("requests.Session")
    def test_discover_projects_success(self, mock_session_class):
        """Test successful project discovery"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Mock authentication
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = [{"id": "project1"}]

        # Mock project details
        details_response = MagicMock()
        details_response.status_code = 200
        details_response.json.return_value = {
            "id": "project1",
            "name": "Test Project",
            "region": "us-east-1",
            "status": "ACTIVE_HEALTHY",
        }

        mock_session.get.side_effect = [auth_response, details_response]

        config = MagicMock()
        config.access_token = "test_token"
        config.project_ref = None
        analyzer = SupabaseAnalyzer(config)

        assert analyzer.authenticate()
        projects = analyzer._discover_projects()
        assert isinstance(projects, list)

    @patch("requests.Session")
    def test_analyze_with_authentication_failure(self, mock_session_class):
        """Test analyze when authentication fails"""
        config = MagicMock()
        config.access_token = "invalid"
        analyzer = SupabaseAnalyzer(config)

        with patch.object(analyzer, "authenticate", return_value=False):
            result = analyzer.analyze()
            assert isinstance(result, dict)
            assert "error" in result

    def test_generate_summary(self):
        """Test summary generation"""
        config = MagicMock()
        config.access_token = "test_token"
        analyzer = SupabaseAnalyzer(config)

        projects = [
            {
                "project_ref": "project1",
                "region": "us-east-1",
                "database": {"version": "PostgreSQL 15.1"},
            },
            {
                "project_ref": "project2",
                "region": "us-west-2",
                "database": {"version": "PostgreSQL 14.6"},
            },
        ]

        summary = analyzer._generate_summary(projects)
        assert summary["total_projects"] == 2
        assert len(summary["regions"]) == 2
        assert len(summary["database_versions"]) == 2

    @patch("requests.Session")
    def test_get_project_details_failure(self, mock_session_class):
        """Test handling of project details fetch failure"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_session.get.return_value = mock_response

        config = MagicMock()
        config.access_token = "test_token"
        analyzer = SupabaseAnalyzer(config)
        analyzer.session = mock_session

        result = analyzer._get_project_details("nonexistent")
        assert result is None
        assert len(analyzer.warnings) > 0
