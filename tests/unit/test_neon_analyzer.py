"""
Tests for Neon Analyzer
"""

from unittest.mock import MagicMock, patch
from planetscale_discovery.cloud.analyzers.neon_analyzer import (
    NeonAnalyzer,
    NEON_COMPUTE_SPECS,
)


class TestNeonAnalyzer:
    """Neon Analyzer tests"""

    def test_instantiation(self):
        """Test analyzer can be instantiated"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = NeonAnalyzer(config)
        assert analyzer is not None
        assert analyzer.provider == "neon"

    def test_analyze_returns_dict(self):
        """Test that analyze returns a dictionary"""
        config = MagicMock()
        config.api_key = "test_key"
        config.target_project = None
        analyzer = NeonAnalyzer(config)

        with patch.object(analyzer, "authenticate", return_value=False):
            result = analyzer.analyze()
            assert isinstance(result, dict)
            assert "error" in result or "projects" in result

    def test_add_error_tracking(self):
        """Test error tracking"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = NeonAnalyzer(config)

        analyzer.add_error("Test error", Exception("test"))
        assert len(analyzer.errors) > 0

    def test_add_warning_tracking(self):
        """Test warning tracking"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = NeonAnalyzer(config)

        analyzer.add_warning("Test warning")
        assert len(analyzer.warnings) > 0

    def test_get_analysis_metadata(self):
        """Test metadata generation"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = NeonAnalyzer(config)

        metadata = analyzer.get_analysis_metadata()
        assert isinstance(metadata, dict)

    @patch("requests.Session")
    def test_authenticate_with_api_key(self, mock_session_class):
        """Test authentication with API key from config"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"projects": [], "pagination": {}}
        mock_session.get.return_value = mock_response

        config = MagicMock()
        config.api_key = "neon_test_api_key_123"
        analyzer = NeonAnalyzer(config)

        result = analyzer.authenticate()
        assert result is True
        assert analyzer.session is not None

    @patch("requests.Session")
    def test_authenticate_with_env_var(self, mock_session_class):
        """Test authentication with NEON_API_KEY env var"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"projects": [], "pagination": {}}
        mock_session.get.return_value = mock_response

        config = MagicMock()
        config.api_key = None

        with patch.dict("os.environ", {"NEON_API_KEY": "env_key_123"}):
            analyzer = NeonAnalyzer(config)
            result = analyzer.authenticate()
            assert result is True

    @patch("requests.Session")
    def test_authenticate_invalid_key(self, mock_session_class):
        """Test authentication with invalid key"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_session.get.return_value = mock_response

        config = MagicMock()
        config.api_key = "invalid_key"
        analyzer = NeonAnalyzer(config)

        result = analyzer.authenticate()
        assert result is False
        assert len(analyzer.errors) > 0

    @patch("requests.Session")
    def test_authenticate_no_key(self, mock_session_class):
        """Test authentication without any key"""
        config = MagicMock()
        config.api_key = None

        with patch.dict("os.environ", {}, clear=True):
            analyzer = NeonAnalyzer(config)
            result = analyzer.authenticate()
            assert result is False
            assert len(analyzer.errors) > 0

    @patch("requests.Session")
    def test_paginated_get(self, mock_session_class):
        """Test cursor-based pagination handling"""
        from tests.fixtures.neon_responses import (
            NEON_PROJECTS_PAGE_1,
            NEON_PROJECTS_PAGE_2,
        )

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        response1 = MagicMock()
        response1.status_code = 200
        response1.json.return_value = NEON_PROJECTS_PAGE_1

        response2 = MagicMock()
        response2.status_code = 200
        response2.json.return_value = NEON_PROJECTS_PAGE_2

        mock_session.get.side_effect = [response1, response2]

        config = MagicMock()
        config.api_key = "test_key"
        analyzer = NeonAnalyzer(config)
        analyzer.session = mock_session

        items = analyzer._paginated_get("https://console.neon.tech/api/v2/projects")
        assert len(items) == 2
        assert items[0]["id"] == "project-page1-001"
        assert items[1]["id"] == "project-page2-002"

    @patch("requests.Session")
    def test_paginated_get_rate_limited(self, mock_session_class):
        """Test pagination stops on rate limit"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        response = MagicMock()
        response.status_code = 429
        mock_session.get.return_value = response

        config = MagicMock()
        config.api_key = "test_key"
        analyzer = NeonAnalyzer(config)
        analyzer.session = mock_session

        items = analyzer._paginated_get("https://console.neon.tech/api/v2/projects")
        assert len(items) == 0
        assert len(analyzer.warnings) > 0

    @patch("requests.Session")
    def test_analyze_target_project(self, mock_session_class):
        """Test analysis targeting a specific project"""
        from tests.fixtures.neon_responses import (
            NEON_SINGLE_PROJECT_RESPONSE,
            NEON_BRANCHES_RESPONSE,
            NEON_ENDPOINTS_RESPONSE,
            NEON_DATABASES_RESPONSE,
        )

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Auth response
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = {"projects": [], "pagination": {}}

        # Project details response
        project_response = MagicMock()
        project_response.status_code = 200
        project_response.json.return_value = NEON_SINGLE_PROJECT_RESPONSE

        # Branches response
        branches_response = MagicMock()
        branches_response.status_code = 200
        branches_response.json.return_value = NEON_BRANCHES_RESPONSE

        # Endpoints response
        endpoints_response = MagicMock()
        endpoints_response.status_code = 200
        endpoints_response.json.return_value = NEON_ENDPOINTS_RESPONSE

        # Databases response
        databases_response = MagicMock()
        databases_response.status_code = 200
        databases_response.json.return_value = NEON_DATABASES_RESPONSE

        mock_session.get.side_effect = [
            auth_response,
            project_response,
            branches_response,
            endpoints_response,
            databases_response,
        ]

        config = MagicMock()
        config.api_key = "test_key"
        config.target_project = "project-abc-123"
        config.org_id = None
        analyzer = NeonAnalyzer(config)

        result = analyzer.analyze()
        assert result["provider"] == "neon"
        assert len(result["projects"]) == 1

        project = result["projects"][0]
        assert project["project_id"] == "project-abc-123"
        assert project["name"] == "production-app"
        assert project["region_id"] == "aws-us-east-2"
        assert project["pg_version"] == 16
        assert project["plan"] == "scale"
        assert len(project["branches"]) == 2
        assert len(project["endpoints"]) == 2
        assert len(project["databases"]) == 2
        assert project["default_branch_id"] == "br-main-abc123"

    def test_compute_specs_mapping(self):
        """Test CU to vCPU/RAM mapping"""
        assert NEON_COMPUTE_SPECS[0.25]["vcpu"] == 0.25
        assert NEON_COMPUTE_SPECS[0.25]["ram_gb"] == 1

        assert NEON_COMPUTE_SPECS[1]["vcpu"] == 1
        assert NEON_COMPUTE_SPECS[1]["ram_gb"] == 4

        assert NEON_COMPUTE_SPECS[4]["vcpu"] == 4
        assert NEON_COMPUTE_SPECS[4]["ram_gb"] == 16

        assert NEON_COMPUTE_SPECS[8]["vcpu"] == 8
        assert NEON_COMPUTE_SPECS[8]["ram_gb"] == 32

        # All CU sizes should have vcpu, ram_gb, and max_connections
        for cu, specs in NEON_COMPUTE_SPECS.items():
            assert "vcpu" in specs
            assert "ram_gb" in specs
            assert "max_connections" in specs

    def test_compute_specs_completeness(self):
        """Test that all expected CU sizes are mapped"""
        expected_sizes = {0.25, 0.5, 1, 2, 3, 4, 5, 6, 7, 8}
        assert set(NEON_COMPUTE_SPECS.keys()) == expected_sizes

    def test_generate_summary(self):
        """Test summary generation"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = NeonAnalyzer(config)

        projects = [
            {
                "name": "prod-app",
                "region_id": "aws-us-east-2",
                "pg_version": 16,
                "plan": "scale",
                "branches": [
                    {"name": "main", "default": True},
                    {"name": "dev", "default": False},
                ],
                "endpoints": [
                    {
                        "type": "read_write",
                        "pooler_enabled": True,
                    },
                    {
                        "type": "read_only",
                        "pooler_enabled": False,
                    },
                ],
            },
            {
                "name": "staging-app",
                "region_id": "aws-eu-central-1",
                "pg_version": 17,
                "plan": "free",
                "branches": [
                    {"name": "main", "default": True},
                ],
                "endpoints": [
                    {
                        "type": "read_write",
                        "pooler_enabled": True,
                    },
                ],
            },
        ]

        summary = analyzer._generate_summary(projects)
        assert summary["total_projects"] == 2
        assert summary["total_branches"] == 3
        assert summary["total_endpoints"] == 3
        assert summary["endpoints_with_pooling"] == 2
        assert summary["read_replicas"] == 1
        assert len(summary["regions"]) == 2
        assert len(summary["pg_versions"]) == 2
        assert summary["plan_tiers"]["scale"] == 1
        assert summary["plan_tiers"]["free"] == 1

    @patch("requests.Session")
    def test_analyze_with_authentication_failure(self, mock_session_class):
        """Test analyze when authentication fails"""
        config = MagicMock()
        config.api_key = "invalid"
        analyzer = NeonAnalyzer(config)

        with patch.object(analyzer, "authenticate", return_value=False):
            result = analyzer.analyze()
            assert isinstance(result, dict)
            assert "error" in result

    def test_endpoint_enrichment_with_compute_specs(self):
        """Test that endpoints are enriched with compute specs"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = NeonAnalyzer(config)
        analyzer.session = MagicMock()

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "endpoints": [
                {
                    "id": "ep-test",
                    "type": "read_write",
                    "current_state": "active",
                    "branch_id": "br-123",
                    "autoscaling_limit_min_cu": 0.5,
                    "autoscaling_limit_max_cu": 4,
                    "pooler_enabled": True,
                    "pooler_mode": "transaction",
                    "suspend_timeout_seconds": 300,
                    "created_at": "2024-01-01T00:00:00Z",
                }
            ]
        }
        analyzer.session.get.return_value = response

        endpoints = analyzer._get_endpoints("project-123")
        assert len(endpoints) == 1
        assert "compute_specs" in endpoints[0]
        assert endpoints[0]["compute_specs"]["vcpu"] == 4
        assert endpoints[0]["compute_specs"]["ram_gb"] == 16
