"""
Tests for Heroku Analyzer
"""

from unittest.mock import MagicMock, patch
from planetscale_discovery.cloud.analyzers.heroku_analyzer import (
    HerokuAnalyzer,
    HEROKU_PLAN_SPECS,
)


class TestHerokuAnalyzer:
    """Heroku Analyzer tests"""

    def test_instantiation(self):
        """Test analyzer can be instantiated"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)
        assert analyzer is not None
        assert analyzer.provider == "heroku"

    def test_analyze_returns_dict(self):
        """Test that analyze returns a dictionary"""
        config = MagicMock()
        config.api_key = "test_key"
        config.target_app = None
        analyzer = HerokuAnalyzer(config)

        with patch.object(analyzer, "authenticate", return_value=False):
            result = analyzer.analyze()
            assert isinstance(result, dict)
            assert "error" in result or "apps" in result

    def test_add_error_tracking(self):
        """Test error tracking"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        analyzer.add_error("Test error", Exception("test"))
        assert len(analyzer.errors) > 0

    def test_add_warning_tracking(self):
        """Test warning tracking"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        analyzer.add_warning("Test warning")
        assert len(analyzer.warnings) > 0

    def test_get_analysis_metadata(self):
        """Test metadata generation"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        metadata = analyzer.get_analysis_metadata()
        assert isinstance(metadata, dict)

    @patch("requests.Session")
    def test_authenticate_with_api_key(self, mock_session_class):
        """Test authentication with API key from config"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "account-uuid",
            "email": "test@test.com",
        }
        mock_session.get.return_value = mock_response

        config = MagicMock()
        config.api_key = "test_api_key_123"
        analyzer = HerokuAnalyzer(config)

        result = analyzer.authenticate()
        assert result is True
        assert analyzer.session is not None

    @patch("requests.Session")
    def test_authenticate_with_env_var(self, mock_session_class):
        """Test authentication with HEROKU_API_KEY env var"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "account-uuid"}
        mock_session.get.return_value = mock_response

        config = MagicMock()
        config.api_key = None

        with patch.dict("os.environ", {"HEROKU_API_KEY": "env_key_123"}):
            analyzer = HerokuAnalyzer(config)
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
        analyzer = HerokuAnalyzer(config)

        result = analyzer.authenticate()
        assert result is False
        assert len(analyzer.errors) > 0

    @patch("requests.Session")
    def test_authenticate_no_key(self, mock_session_class):
        """Test authentication without any key"""
        config = MagicMock()
        config.api_key = None

        with patch.dict("os.environ", {}, clear=True):
            analyzer = HerokuAnalyzer(config)
            result = analyzer.authenticate()
            assert result is False
            assert len(analyzer.errors) > 0

    @patch("requests.Session")
    def test_paginated_get(self, mock_session_class):
        """Test pagination handling"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # First page - 206 with Next-Range
        response1 = MagicMock()
        response1.status_code = 206
        response1.json.return_value = [{"id": "1"}, {"id": "2"}]
        response1.headers = {"Next-Range": "id 2..; max=200"}

        # Second page - 200 (final)
        response2 = MagicMock()
        response2.status_code = 200
        response2.json.return_value = [{"id": "3"}]
        response2.headers = {}

        mock_session.get.side_effect = [response1, response2]

        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)
        analyzer.session = mock_session

        items = analyzer._paginated_get("https://api.heroku.com/apps")
        assert len(items) == 3

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
        analyzer = HerokuAnalyzer(config)
        analyzer.session = mock_session

        items = analyzer._paginated_get("https://api.heroku.com/apps")
        assert len(items) == 0
        assert len(analyzer.warnings) > 0

    def test_detect_connection_pooling_present(self):
        """Test detection of PgBouncer connection pooling"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        config_vars = [
            "DATABASE_URL",
            "HEROKU_POSTGRESQL_COPPER_URL",
            "DATABASE_CONNECTION_POOL_URL",
        ]
        result = analyzer._detect_connection_pooling(config_vars)
        assert result["detected"] is True
        assert result["mode"] == "transaction"
        assert "DATABASE_CONNECTION_POOL_URL" in result["pool_config_vars"]

    def test_detect_connection_pooling_absent(self):
        """Test no pooling detected when no pool vars exist"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        config_vars = ["DATABASE_URL", "HEROKU_POSTGRESQL_COPPER_URL"]
        result = analyzer._detect_connection_pooling(config_vars)
        assert result["detected"] is False

    def test_detect_followers(self):
        """Test follower database detection"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        config_vars = [
            "DATABASE_URL",
            "HEROKU_POSTGRESQL_COPPER_URL",
            "HEROKU_POSTGRESQL_AMBER_URL",
            "HEROKU_POSTGRESQL_GREEN_URL",
        ]
        followers = analyzer._detect_followers(config_vars)
        assert len(followers) == 3
        colors = [f["color"] for f in followers]
        assert "COPPER" in colors
        assert "AMBER" in colors
        assert "GREEN" in colors

    def test_detect_no_followers(self):
        """Test no followers detected with single database"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        config_vars = ["DATABASE_URL", "SECRET_KEY"]
        followers = analyzer._detect_followers(config_vars)
        assert len(followers) == 0

    def test_enrich_with_known_plan_specs(self):
        """Test plan spec enrichment for known plan"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        specs = analyzer._enrich_with_plan_specs("standard-0")
        assert specs["tier"] == "standard"
        assert specs["vcpu"] == 2
        assert specs["ram_gb"] == 4
        assert specs["storage_gb"] == 64
        assert specs["piops"] == 3000
        assert specs["ha"] is False  # HA only on premium/private/shield

    def test_enrich_with_unknown_plan_specs(self):
        """Test plan spec enrichment for unknown plan"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        specs = analyzer._enrich_with_plan_specs("future-99")
        assert specs["tier"] == "future"
        assert specs["vcpu"] == "unknown"
        assert specs["ram_gb"] == "unknown"
        assert specs["piops"] == "unknown"

    def test_enrich_essential_plan(self):
        """Test Essential plan has no HA/fork/follow"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        specs = analyzer._enrich_with_plan_specs("essential-0")
        assert specs["tier"] == "essential"
        assert specs["vcpu"] is None
        assert specs["piops"] is None
        assert specs["ha"] is False
        assert specs["fork"] is False
        assert specs["follow"] is False

    def test_plan_ha_only_on_premium_private_shield(self):
        """Test HA is only on premium/private/shield, not essential or standard"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        assert analyzer._enrich_with_plan_specs("essential-0")["ha"] is False
        assert analyzer._enrich_with_plan_specs("standard-3")["ha"] is False
        assert analyzer._enrich_with_plan_specs("premium-3")["ha"] is True
        assert analyzer._enrich_with_plan_specs("private-3")["ha"] is True
        assert analyzer._enrich_with_plan_specs("shield-3")["ha"] is True

    def test_plan_specs_mapping_completeness(self):
        """Test that plan specs mapping covers expected tiers"""
        tiers = set()
        for specs in HEROKU_PLAN_SPECS.values():
            tiers.add(specs["tier"])

        assert "essential" in tiers
        assert "standard" in tiers
        assert "premium" in tiers
        assert "private" in tiers
        assert "shield" in tiers

    @patch("requests.Session")
    def test_analyze_target_app(self, mock_session_class):
        """Test analysis targeting a specific app"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Auth response
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = {"id": "account-uuid"}

        # Addons response
        addons_response = MagicMock()
        addons_response.status_code = 200
        addons_response.json.return_value = [
            {
                "id": "addon-1",
                "name": "pg-123",
                "addon_service": {"name": "heroku-postgresql"},
                "plan": {"name": "heroku-postgresql:standard-0"},
                "state": "provisioned",
                "config_vars": ["DATABASE_URL"],
            }
        ]

        # Data API database details response
        data_api_response = MagicMock()
        data_api_response.status_code = 200
        data_api_response.json.return_value = {
            "num_bytes": 1000000,
            "num_tables": 10,
            "num_connections": 5,
            "num_connections_waiting": 0,
            "postgres_version": "17.5",
            "info": [
                {"name": "Data Size", "values": ["1 MB / 64 GB (0.00%)"]},
                {"name": "Status", "values": ["Available"]},
            ],
        }

        # Config vars response
        config_response = MagicMock()
        config_response.status_code = 200
        config_response.json.return_value = {"DATABASE_URL": "postgres://..."}

        # Attachments response
        attachments_response = MagicMock()
        attachments_response.status_code = 200
        attachments_response.json.return_value = []

        mock_session.get.side_effect = [
            auth_response,
            addons_response,
            data_api_response,
            config_response,
            attachments_response,
        ]

        config = MagicMock()
        config.api_key = "test_key"
        config.target_app = "my-app"
        analyzer = HerokuAnalyzer(config)

        result = analyzer.analyze()
        assert result["provider"] == "heroku"
        assert len(result["apps"]) == 1
        assert result["apps"][0]["databases"][0]["plan_name"] == "standard-0"
        # Verify Data API details are included
        db_details = result["apps"][0]["databases"][0].get("database_details")
        assert db_details is not None
        assert db_details["num_bytes"] == 1000000
        assert db_details["postgres_version"] == "17.5"
        assert db_details["data_size"] == "1 MB / 64 GB (0.00%)"

    def test_get_database_details_success(self):
        """Test fetching live database details from the Data API"""
        from tests.fixtures.heroku_responses import HEROKU_DATA_API_DATABASE_DETAILS

        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)
        analyzer.session = MagicMock()

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = HEROKU_DATA_API_DATABASE_DETAILS
        analyzer.session.get.return_value = response

        details = analyzer._get_database_details("addon-uuid-1")

        assert details is not None
        assert details["num_bytes"] == 71015181459
        assert details["num_tables"] == 45
        assert details["num_connections"] == 12
        assert details["postgres_version"] == "17.5"
        assert details["data_size"] == "66.1 GB / 64 GB (103.28%)"
        assert details["status"] == "Available"
        assert details["continuous_protection"] == "On"
        assert details["data_encryption"] == "In Use"
        assert details["maintenance_window"] == "Thursdays 21:30 to Fridays 01:30 UTC"

    def test_get_database_details_no_credentials_stored(self):
        """Test that credentials are never stored in database details"""
        from tests.fixtures.heroku_responses import HEROKU_DATA_API_DATABASE_DETAILS

        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)
        analyzer.session = MagicMock()

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = HEROKU_DATA_API_DATABASE_DETAILS
        analyzer.session.get.return_value = response

        details = analyzer._get_database_details("addon-uuid-1")

        assert details is not None
        # Ensure no credential fields are present
        assert "resource_url" not in details
        assert "database_password" not in details
        assert "database_user" not in details
        assert "database_name" not in details
        # Verify stored values don't contain credentials
        all_values = str(details)
        assert "secret_password_redacted" not in all_values
        assert "postgres://user:pass" not in all_values

    def test_get_database_details_api_failure(self):
        """Test graceful handling when Data API returns an error"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)
        analyzer.session = MagicMock()

        response = MagicMock()
        response.status_code = 403
        analyzer.session.get.return_value = response

        details = analyzer._get_database_details("addon-uuid-1")
        assert details is None

    @patch("requests.Session")
    def test_analyze_with_authentication_failure(self, mock_session_class):
        """Test analyze when authentication fails"""
        config = MagicMock()
        config.api_key = "invalid"
        analyzer = HerokuAnalyzer(config)

        with patch.object(analyzer, "authenticate", return_value=False):
            result = analyzer.analyze()
            assert isinstance(result, dict)
            assert "error" in result

    def test_generate_summary(self):
        """Test summary generation"""
        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)

        apps = [
            {
                "name": "app1",
                "region": "us",
                "databases": [
                    {"plan_name": "standard-0", "plan_specs": {"tier": "standard"}},
                ],
                "connection_pooling": {"detected": True},
                "followers": [{"color": "AMBER"}],
            },
            {
                "name": "app2",
                "region": "eu",
                "databases": [
                    {"plan_name": "premium-2", "plan_specs": {"tier": "premium"}},
                ],
                "connection_pooling": {"detected": False},
                "followers": [],
            },
        ]

        summary = analyzer._generate_summary(apps)
        assert summary["total_apps"] == 2
        assert summary["total_databases"] == 2
        assert len(summary["regions"]) == 2
        assert summary["apps_with_pooling"] == 1
        assert summary["total_followers"] == 1
        assert summary["plans"]["standard-0"] == 1
        assert summary["plans"]["premium-2"] == 1

    @patch("requests.Session")
    def test_get_config_var_names_masks_values(self, mock_session_class):
        """Test that config var values are never stored - only key names"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "DATABASE_URL": "postgres://secret:password@host/db",
            "SECRET_KEY": "supersecret123",
        }
        mock_session.get.return_value = response

        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)
        analyzer.session = mock_session

        names = analyzer._get_config_var_names("my-app")
        assert "DATABASE_URL" in names
        assert "SECRET_KEY" in names
        # Verify no values leaked
        assert "postgres://secret:password@host/db" not in names
        assert "supersecret123" not in names

    @patch("requests.Session")
    def test_get_addon_attachments_cross_app(self, mock_session_class):
        """Test cross-app attachment detection"""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [
            {
                "name": "DATABASE",
                "addon": {"name": "pg-123"},
                "app": {"name": "my-app"},
            },
            {
                "name": "SHARED_DB",
                "addon": {"name": "pg-123"},
                "app": {"name": "other-app"},
            },
        ]
        mock_session.get.return_value = response

        config = MagicMock()
        config.api_key = "test_key"
        analyzer = HerokuAnalyzer(config)
        analyzer.session = mock_session

        attachments = analyzer._get_addon_attachments("my-app")
        assert len(attachments) == 2
        assert attachments[0]["is_cross_app"] is False
        assert attachments[1]["is_cross_app"] is True
