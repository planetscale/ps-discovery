"""Unit tests for the PlanetScale Postgres cloud analyzer."""

import json
from unittest.mock import MagicMock, patch

from planetscale_discovery.cloud.analyzers.planetscale_analyzer import (
    PlanetScaleAnalyzer,
)


class TestPlanetScaleAnalyzer:
    """Tests for PlanetScaleAnalyzer."""

    @staticmethod
    def _mock_response(status_code, json_body=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body if json_body is not None else {}
        return resp

    @staticmethod
    def _config(**overrides):
        config = MagicMock()
        config.service_token_id = overrides.get("service_token_id", "token-id-abc")
        config.service_token = overrides.get("service_token", "token-secret-xyz")
        config.organization = overrides.get("organization", "test-org")
        config.target_database = overrides.get("target_database", None)
        return config

    @patch("requests.Session")
    def test_authenticate_sets_scheme_less_header(self, mock_session_class):
        """Service tokens send '<id>:<token>' with no Bearer scheme."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value = self._mock_response(200, {"data": []})

        analyzer = PlanetScaleAnalyzer(self._config())

        assert analyzer.authenticate() is True

        headers = mock_session.headers.update.call_args[0][0]
        assert headers["Authorization"] == "token-id-abc:token-secret-xyz"
        assert not headers["Authorization"].startswith("Bearer")

    @patch("requests.Session")
    def test_authenticate_from_env_vars(self, mock_session_class):
        """Credentials fall back to the environment when config is empty."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value = self._mock_response(200, {"data": []})

        env = {
            "PLANETSCALE_SERVICE_TOKEN_ID": "env-id",
            "PLANETSCALE_SERVICE_TOKEN": "env-token",
        }
        with patch.dict("os.environ", env):
            analyzer = PlanetScaleAnalyzer(
                self._config(service_token_id=None, service_token=None)
            )
            assert analyzer.authenticate() is True
            assert analyzer.service_token_id == "env-id"

        with patch.dict("os.environ", {}, clear=True):
            analyzer = PlanetScaleAnalyzer(
                self._config(service_token_id=None, service_token=None)
            )
            assert analyzer.authenticate() is False
            assert len(analyzer.errors) > 0

    @patch("requests.Session")
    def test_authenticate_failure(self, mock_session_class):
        """HTTP 401 fails authentication and records an error."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value = self._mock_response(401)

        analyzer = PlanetScaleAnalyzer(self._config())

        assert analyzer.authenticate() is False
        assert any("Invalid or expired" in e["message"] for e in analyzer.errors)

    @patch("requests.Session")
    def test_authenticate_probes_the_configured_organization(self, mock_session_class):
        """A token scoped to one organization cannot list organizations."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value = self._mock_response(200, {"name": "test-org"})

        analyzer = PlanetScaleAnalyzer(self._config(organization="test-org"))

        assert analyzer.authenticate() is True
        assert mock_session.get.call_args[0][0].endswith("/organizations/test-org")

    @patch("requests.Session")
    def test_authenticate_lists_when_no_organization_is_set(self, mock_session_class):
        """Without a configured organization the probe lists organizations."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value = self._mock_response(200, {"data": []})

        analyzer = PlanetScaleAnalyzer(self._config(organization=None))

        assert analyzer.authenticate() is True
        assert mock_session.get.call_args[0][0].endswith("/organizations")

    @patch("requests.Session")
    def test_target_database_narrows_the_run(self, mock_session_class):
        """target_database is documented as a filter, so it must filter."""
        from tests.fixtures.planetscale_responses import (
            PLANETSCALE_ORGANIZATION_DETAIL_RESPONSE,
            PLANETSCALE_CLUSTER_SIZE_SKUS_RESPONSE,
            PLANETSCALE_DATABASES_TWO_POSTGRES_RESPONSE,
            PLANETSCALE_BRANCHES_RESPONSE,
        )

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = [
            self._mock_response(200, {"name": "test-org"}),  # auth probe
            self._mock_response(200, PLANETSCALE_ORGANIZATION_DETAIL_RESPONSE),
            self._mock_response(200, PLANETSCALE_CLUSTER_SIZE_SKUS_RESPONSE),
            self._mock_response(200, PLANETSCALE_DATABASES_TWO_POSTGRES_RESPONSE),
            self._mock_response(200, PLANETSCALE_BRANCHES_RESPONSE),
        ]

        analyzer = PlanetScaleAnalyzer(self._config(target_database="billing-pg"))
        results = analyzer.analyze()

        databases = results["organizations"][0]["databases"]
        assert [db["name"] for db in databases] == ["billing-pg"]

    @patch("requests.Session")
    def test_rate_limit_warns_and_stops_paginating(self, mock_session_class):
        """HTTP 429 stops the page loop instead of spinning on it."""
        mock_session_class.return_value = MagicMock()
        analyzer = PlanetScaleAnalyzer(self._config())
        analyzer.session = MagicMock()
        analyzer.session.get.return_value = self._mock_response(429)

        items = analyzer._paginated_get("https://api.planetscale.com/v1/x")

        assert items == []
        assert analyzer.session.get.call_count == 1
        assert any("rate limit" in w["message"] for w in analyzer.warnings)

    @patch("requests.Session")
    def test_mysql_databases_are_skipped(self, mock_session_class):
        """Only postgresql databases are analyzed."""
        from tests.fixtures.planetscale_responses import (
            PLANETSCALE_ORGANIZATION_DETAIL_RESPONSE,
            PLANETSCALE_CLUSTER_SIZE_SKUS_RESPONSE,
            PLANETSCALE_DATABASES_MIXED_RESPONSE,
            PLANETSCALE_BRANCHES_RESPONSE,
        )

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = [
            self._mock_response(200, {"data": []}),  # auth probe
            self._mock_response(200, PLANETSCALE_ORGANIZATION_DETAIL_RESPONSE),
            self._mock_response(200, PLANETSCALE_CLUSTER_SIZE_SKUS_RESPONSE),
            self._mock_response(200, PLANETSCALE_DATABASES_MIXED_RESPONSE),
            self._mock_response(200, PLANETSCALE_BRANCHES_RESPONSE),
        ]

        analyzer = PlanetScaleAnalyzer(self._config())
        results = analyzer.analyze()

        databases = results["organizations"][0]["databases"]
        assert [db["name"] for db in databases] == ["orders-pg"]
        assert results["summary"]["total_databases"] == 1
        assert results["summary"]["total_branches"] == 2
        assert results["summary"]["production_branches"] == 1
        # Cluster size joins the SKU list by cluster_name.
        assert databases[0]["branches"][0]["cluster_size"]["ram"] == 8

    @patch("requests.Session")
    def test_paginated_get_follows_next_page(self, mock_session_class):
        """Pagination follows next_page and stops when it is null."""
        from tests.fixtures.planetscale_responses import (
            PLANETSCALE_DATABASES_PAGE_1,
            PLANETSCALE_DATABASES_PAGE_2,
        )

        mock_session_class.return_value = MagicMock()
        analyzer = PlanetScaleAnalyzer(self._config())
        analyzer.session = MagicMock()
        analyzer.session.get.side_effect = [
            self._mock_response(200, PLANETSCALE_DATABASES_PAGE_1),
            self._mock_response(200, PLANETSCALE_DATABASES_PAGE_2),
        ]

        items = analyzer._paginated_get("https://api.planetscale.com/v1/x")

        assert [i["name"] for i in items] == ["db-one", "db-two"]
        assert analyzer.session.get.call_count == 2

    @patch("requests.Session")
    def test_cluster_skus_request_filters_by_engine(self, mock_session_class):
        """Without engine=postgresql the endpoint returns the MySQL catalog."""
        mock_session_class.return_value = MagicMock()
        analyzer = PlanetScaleAnalyzer(self._config())
        analyzer.session = MagicMock()
        analyzer.session.get.return_value = self._mock_response(200, [])

        analyzer._get_cluster_skus("test-org")

        params = analyzer.session.get.call_args[1]["params"]
        assert params["engine"] == "postgresql"

    @patch("requests.Session")
    def test_paginated_get_accepts_bare_array(self, mock_session_class):
        """cluster-size-skus returns a bare array, not a paged envelope."""
        mock_session_class.return_value = MagicMock()
        analyzer = PlanetScaleAnalyzer(self._config())
        analyzer.session = MagicMock()
        analyzer.session.get.return_value = self._mock_response(
            200, [{"name": "PS_10"}, {"name": "PS_20"}]
        )

        items = analyzer._paginated_get("https://api.planetscale.com/v1/x")

        assert [i["name"] for i in items] == ["PS_10", "PS_20"]
        assert analyzer.warnings == []

    @patch("requests.Session")
    def test_discover_resources_returns_database_names(self, mock_session_class):
        """discover_resources lists database names, not dict keys."""
        from tests.fixtures.planetscale_responses import (
            PLANETSCALE_DATABASES_MIXED_RESPONSE,
        )

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = [
            self._mock_response(200, {"data": []}),  # auth probe
            self._mock_response(200, PLANETSCALE_DATABASES_MIXED_RESPONSE),
        ]

        analyzer = PlanetScaleAnalyzer(self._config())

        assert analyzer.discover_resources() == ["orders-pg"]

    @patch("requests.Session")
    def test_branch_storage_reads_the_primary_pod(self, mock_session_class):
        """A non-Metal SKU reports no storage, so usage must come from metrics."""
        from tests.fixtures.planetscale_responses import (
            PLANETSCALE_INSTANT_STORAGE_METRICS_RESPONSE,
        )

        mock_session_class.return_value = MagicMock()
        analyzer = PlanetScaleAnalyzer(self._config())
        analyzer.session = MagicMock()
        analyzer.session.get.return_value = self._mock_response(
            200, PLANETSCALE_INSTANT_STORAGE_METRICS_RESPONSE
        )

        storage = analyzer._get_branch_storage(
            "test-org", "orders-pg", {"name": "main", "ready": True}
        )

        assert storage["bytes_used"] == 129285554176
        assert storage["bytes_capacity"] == 406998380544
        assert storage["usage_percentage"] == 31.77
        assert len(storage["pods"]) == 2

    @patch("requests.Session")
    def test_branch_storage_error_is_a_warning(self, mock_session_class):
        """A missing metrics privilege must not stop the estate report."""
        mock_session_class.return_value = MagicMock()
        analyzer = PlanetScaleAnalyzer(self._config())
        analyzer.session = MagicMock()
        analyzer.session.get.return_value = self._mock_response(403)

        storage = analyzer._get_branch_storage(
            "test-org", "orders-pg", {"name": "main", "ready": True}
        )

        assert storage["bytes_used"] is None
        assert analyzer.errors == []
        assert any("storage metrics" in w["message"] for w in analyzer.warnings)

    @patch("requests.Session")
    def test_branch_storage_skips_a_branch_that_is_not_ready(self, mock_session_class):
        """A branch with no volume yet must not produce a warning per run."""
        mock_session_class.return_value = MagicMock()
        analyzer = PlanetScaleAnalyzer(self._config())
        analyzer.session = MagicMock()

        storage = analyzer._get_branch_storage(
            "test-org", "orders-pg", {"name": "main", "ready": False}
        )

        assert storage["bytes_used"] is None
        assert analyzer.session.get.call_count == 0
        assert analyzer.warnings == []

    @patch("requests.Session")
    def test_analyze_records_storage_and_totals(self, mock_session_class):
        """Liftoff read the SKU default because the branch carried no usage."""
        from tests.fixtures.planetscale_responses import (
            PLANETSCALE_ORGANIZATION_DETAIL_RESPONSE,
            PLANETSCALE_CLUSTER_SIZE_SKUS_RESPONSE,
            PLANETSCALE_DATABASES_MIXED_RESPONSE,
            PLANETSCALE_BRANCHES_RESPONSE,
            PLANETSCALE_INSTANT_STORAGE_METRICS_RESPONSE,
        )

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = [
            self._mock_response(200, {"data": []}),  # auth probe
            self._mock_response(200, PLANETSCALE_ORGANIZATION_DETAIL_RESPONSE),
            self._mock_response(200, PLANETSCALE_CLUSTER_SIZE_SKUS_RESPONSE),
            self._mock_response(200, PLANETSCALE_DATABASES_MIXED_RESPONSE),
            self._mock_response(200, PLANETSCALE_BRANCHES_RESPONSE),
            self._mock_response(200, PLANETSCALE_INSTANT_STORAGE_METRICS_RESPONSE),
            self._mock_response(200, PLANETSCALE_INSTANT_STORAGE_METRICS_RESPONSE),
        ]

        analyzer = PlanetScaleAnalyzer(self._config())
        results = analyzer.analyze()

        main = results["organizations"][0]["databases"][0]["branches"][0]
        assert main["storage"]["bytes_used"] == 129285554176
        assert main["storage_config"]["autoscaling"] is True
        assert main["storage_config"]["maximum_bytes"] == 4398046511104
        assert results["summary"]["total_storage_bytes_used"] == 2 * 129285554176

    @patch("requests.Session")
    def test_analyze_does_not_leak_token(self, mock_session_class):
        """The service token never reaches the results payload."""
        from tests.fixtures.planetscale_responses import (
            PLANETSCALE_ORGANIZATION_DETAIL_RESPONSE,
            PLANETSCALE_CLUSTER_SIZE_SKUS_RESPONSE,
            PLANETSCALE_DATABASES_MIXED_RESPONSE,
            PLANETSCALE_BRANCHES_RESPONSE,
        )

        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.side_effect = [
            self._mock_response(200, {"data": []}),
            self._mock_response(200, PLANETSCALE_ORGANIZATION_DETAIL_RESPONSE),
            self._mock_response(200, PLANETSCALE_CLUSTER_SIZE_SKUS_RESPONSE),
            self._mock_response(200, PLANETSCALE_DATABASES_MIXED_RESPONSE),
            self._mock_response(200, PLANETSCALE_BRANCHES_RESPONSE),
        ]

        analyzer = PlanetScaleAnalyzer(self._config())
        payload = json.dumps(analyzer.analyze(), default=str)

        assert "token-secret-xyz" not in payload
        assert "token-id-abc" not in payload
