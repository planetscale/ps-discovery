"""
Unit tests for MySQLDiscovery orchestrator — SSL handling and
analysis-gap extraction.
"""

import pytest
from unittest.mock import MagicMock, patch

from planetscale_discovery.database.mysql_discovery import MySQLDiscovery


@pytest.fixture
def base_params():
    return {
        "host": "db.example.com",
        "port": 3306,
        "user": "ps",
        "password": "secret",
        "database": "app",
    }


class TestSSLHandling:
    """Verify ssl_mode is mapped to PyMySQL params correctly."""

    def _capture_connect_params(self, params):
        captured = {}

        def fake_connect(**kwargs):
            captured.update(kwargs)
            conn = MagicMock()
            cursor = MagicMock()
            cursor.fetchone.return_value = ("8.0.34", "ps@%", "app")
            conn.cursor.return_value = cursor
            return conn

        fake_pymysql = MagicMock()
        fake_pymysql.connect.side_effect = fake_connect

        with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
            discovery = MySQLDiscovery(params)
            assert discovery.connect() is True
        return captured

    def test_ssl_disabled_sets_ssl_disabled(self, base_params):
        base_params["ssl_mode"] = "disabled"
        captured = self._capture_connect_params(base_params)
        assert captured.get("ssl_disabled") is True
        assert "ssl" not in captured

    def test_ssl_empty_defaults_to_disabled(self, base_params):
        # No ssl_mode at all
        captured = self._capture_connect_params(base_params)
        assert captured.get("ssl_disabled") is True

    def test_ssl_required_enables_tls_without_verification(self, base_params):
        base_params["ssl_mode"] = "required"
        captured = self._capture_connect_params(base_params)
        assert captured.get("ssl") == {}
        assert "ssl_disabled" not in captured

    def test_ssl_preferred_enables_tls(self, base_params):
        base_params["ssl_mode"] = "preferred"
        captured = self._capture_connect_params(base_params)
        assert captured.get("ssl") == {}

    def test_verify_ca_requires_ssl_ca(self, base_params):
        base_params["ssl_mode"] = "verify-ca"
        # No ssl_ca provided — connection should fail
        fake_pymysql = MagicMock()
        with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
            discovery = MySQLDiscovery(base_params)
            assert discovery.connect() is False
        # And pymysql.connect should not have been called
        fake_pymysql.connect.assert_not_called()

    def test_verify_identity_requires_ssl_ca(self, base_params):
        base_params["ssl_mode"] = "verify-identity"
        fake_pymysql = MagicMock()
        with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
            discovery = MySQLDiscovery(base_params)
            assert discovery.connect() is False
        fake_pymysql.connect.assert_not_called()

    def test_verify_ca_with_ssl_ca_sets_ssl_dict(self, base_params):
        base_params["ssl_mode"] = "verify-ca"
        base_params["ssl_ca"] = "/etc/ssl/ca.pem"
        captured = self._capture_connect_params(base_params)
        ssl = captured.get("ssl")
        assert ssl["ca"] == "/etc/ssl/ca.pem"
        assert ssl["check_hostname"] is False
        assert "cert" not in ssl
        assert "key" not in ssl

    def test_verify_identity_sets_check_hostname(self, base_params):
        base_params["ssl_mode"] = "verify-identity"
        base_params["ssl_ca"] = "/etc/ssl/ca.pem"
        captured = self._capture_connect_params(base_params)
        ssl = captured.get("ssl")
        assert ssl["ca"] == "/etc/ssl/ca.pem"
        assert ssl["check_hostname"] is True

    def test_mutual_tls_includes_cert_and_key(self, base_params):
        base_params["ssl_mode"] = "verify-identity"
        base_params["ssl_ca"] = "/etc/ssl/ca.pem"
        base_params["ssl_cert"] = "/etc/ssl/client.pem"
        base_params["ssl_key"] = "/etc/ssl/client.key"
        captured = self._capture_connect_params(base_params)
        ssl = captured.get("ssl")
        assert ssl["cert"] == "/etc/ssl/client.pem"
        assert ssl["key"] == "/etc/ssl/client.key"

    def test_unknown_ssl_mode_falls_back_to_disabled(self, base_params):
        base_params["ssl_mode"] = "garbage"
        captured = self._capture_connect_params(base_params)
        assert captured.get("ssl_disabled") is True


class TestScanForErrors:
    """Verify _scan_for_errors catches partial failures."""

    @pytest.fixture
    def discovery(self, base_params):
        return MySQLDiscovery(base_params)

    def test_catches_error_without_status_field(self, discovery):
        discovery._extract_analysis_gaps(
            "security", {"ssl_status": {"error": "permission denied"}}
        )
        assert len(discovery.results["analysis_gaps"]) == 1
        gap = discovery.results["analysis_gaps"][0]
        assert gap["module"] == "security"
        assert gap["type"] == "analysis_error"
        assert "ssl_status" in gap["description"]
        assert gap["error_message"] == "permission denied"

    def test_catches_error_with_note(self, discovery):
        discovery._extract_analysis_gaps(
            "security",
            {
                "user_summary": {
                    "error": "denied",
                    "note": "mysql.user may not be accessible",
                }
            },
        )
        assert len(discovery.results["analysis_gaps"]) == 1
        assert discovery.results["analysis_gaps"][0]["error_message"] == "denied"

    def test_recurses_into_lists(self, discovery):
        discovery._extract_analysis_gaps(
            "performance",
            {
                "snapshots": [
                    {"value": 1},
                    {"error": "second snapshot failed"},
                ]
            },
        )
        assert len(discovery.results["analysis_gaps"]) == 1
        gap = discovery.results["analysis_gaps"][0]
        assert "snapshots[1]" in gap["description"]

    def test_does_not_recurse_into_error_subtree(self, discovery):
        # A nested error inside an error subtree would have produced two
        # gaps under the old recurse-everywhere behavior; we want just one.
        discovery._extract_analysis_gaps(
            "config",
            {
                "settings": {
                    "error": "outer",
                    "inner": {"error": "should not show up"},
                }
            },
        )
        assert len(discovery.results["analysis_gaps"]) == 1
        assert discovery.results["analysis_gaps"][0]["error_message"] == "outer"

    def test_no_gaps_when_no_errors(self, discovery):
        discovery._extract_analysis_gaps(
            "schema", {"tables": [{"name": "users"}, {"name": "orders"}]}
        )
        assert discovery.results["analysis_gaps"] == []
