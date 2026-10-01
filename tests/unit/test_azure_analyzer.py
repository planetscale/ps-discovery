"""
Tests for Azure Analyzer

Run against the SimpleNamespace stand-ins in tests/fixtures/azure_responses.py
rather than the real SDK, so they need no azure-mgmt-* install. HAS_AZURE_LIBS
is patched True to get past the guarded-import check.
"""

from unittest.mock import MagicMock, patch

import pytest

from planetscale_discovery.cloud.analyzers.azure_analyzer import (
    AzureAnalyzer,
    _as_bool,
    _enum_str,
    _normalize_location,
    _resource_group_from_id,
    _server_version_full,
)
from planetscale_discovery.config.config_manager import AzureConfig

MODULE = "planetscale_discovery.cloud.analyzers.azure_analyzer"


@pytest.fixture
def azure_config():
    """A minimal enabled Azure config."""
    return AzureConfig(enabled=True, subscription_id="sub-1")


def _paged(items):
    """Stand in for an ItemPaged: a plain iterable is close enough."""
    return list(items)


def _wire(analyzer, pg_servers=(), mysql_servers=(), vnets=(), nsgs=()):
    """Attach mock management clients returning the given resources."""
    analyzer.postgresql_client = MagicMock()
    analyzer.postgresql_client.servers.list.return_value = _paged(pg_servers)
    analyzer.postgresql_client.configurations.list_by_server.return_value = []
    analyzer.postgresql_client.firewall_rules.list_by_server.return_value = []

    analyzer.mysql_client = MagicMock()
    analyzer.mysql_client.servers.list.return_value = _paged(mysql_servers)
    analyzer.mysql_client.configurations.list_by_server.return_value = []
    analyzer.mysql_client.firewall_rules.list_by_server.return_value = []

    analyzer.network_client = MagicMock()
    analyzer.network_client.virtual_networks.list_all.return_value = _paged(vnets)
    analyzer.network_client.network_security_groups.list_all.return_value = _paged(nsgs)
    return analyzer


class TestHelpers:
    """Tests for the module-level field helpers."""

    def test_normalize_location_handles_display_names(self):
        """A config written as "East US" must match a location of "eastus"."""
        assert _normalize_location("East US") == "eastus"
        assert _normalize_location("  westeurope  ") == "westeurope"
        assert _normalize_location("") == ""
        assert _normalize_location(None) == ""

    def test_enum_str_reads_value_not_str(self):
        """Azure enums stringify as "Class.MEMBER"; only .value is the wire name."""
        from tests.fixtures.azure_responses import _enum

        mode = _enum("ZoneRedundant")
        assert str(mode) != "ZoneRedundant"  # the trap
        assert _enum_str(mode) == "ZoneRedundant"  # the fix
        assert _enum_str(None) == ""
        assert _enum_str("Plain") == "Plain"

    def test_as_bool_handles_the_mysql_string_flag(self):
        """The two SDKs type is_read_only differently.

        PostgreSQL declares it bool, MySQL a str enum of "True"/"False", where
        a plain bool("False") would be True.
        """
        assert _as_bool(False) is False
        assert _as_bool(True) is True
        assert bool("False") is True
        assert _as_bool("False") is False
        assert _as_bool("True") is True
        assert _as_bool(None) is None
        assert _as_bool("") is None

    def test_server_version_full_across_engines(self):
        """PostgreSQL splits the version; MySQL does not."""
        from types import SimpleNamespace

        pg = SimpleNamespace(version="16", minor_version="15", full_version=None)
        assert _server_version_full(pg) == "16.15"

        mysql = SimpleNamespace(version="8.0.21", full_version="8.0.21")
        assert _server_version_full(mysql) == "8.0.21"

        bare = SimpleNamespace(version="17", minor_version="", full_version="")
        assert _server_version_full(bare) == "17"

    def test_resource_group_from_id_is_case_insensitive(self):
        """ARM returns both resourceGroups and resourcegroups."""
        assert (
            _resource_group_from_id(
                "/subscriptions/s/resourceGroups/rg-a/providers/x/y"
            )
            == "rg-a"
        )
        assert (
            _resource_group_from_id(
                "/subscriptions/s/resourcegroups/rg-b/providers/x/y"
            )
            == "rg-b"
        )
        assert _resource_group_from_id("") == ""
        assert _resource_group_from_id(None) == ""


class TestInstantiation:
    """Tests for construction and the base-class contract."""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_provider_name_is_azure(self, azure_config):
        """CloudAnalyzer takes (config, provider, logger).

        Two positional args would bind provider=logger and put a Logger repr
        in metadata.provider.
        """
        analyzer = AzureAnalyzer(azure_config)

        assert analyzer.provider == "azure"
        assert analyzer.get_analysis_metadata()["provider"] == "azure"

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_missing_subscription_id_is_an_error(self):
        """Azure has no ambient subscription default, so this must not proceed."""
        analyzer = AzureAnalyzer(AzureConfig(enabled=True))

        assert analyzer.authenticate() is False
        assert any("subscription ID" in e["message"] for e in analyzer.errors)

    @patch(f"{MODULE}.HAS_AZURE_LIBS", False)
    def test_missing_libraries_degrades(self, azure_config):
        """Without the extra installed, record an error and return an empty envelope."""
        analyzer = AzureAnalyzer(azure_config)

        assert analyzer.authenticate() is False
        assert any("not installed" in e["message"] for e in analyzer.errors)

        results = analyzer.analyze()
        assert results["provider"] == "azure"
        assert results["resources"] == {}

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_regions_normalized_at_construction(self):
        """Region filters are normalized once, up front."""
        analyzer = AzureAnalyzer(
            AzureConfig(
                enabled=True, subscription_id="sub-1", regions=["East US", "westus2"]
            )
        )

        assert analyzer.regions == ["eastus", "westus2"]


class TestAuthentication:
    """Tests for credential selection and tolerated API failures."""

    @patch(f"{MODULE}.NetworkManagementClient")
    @patch(f"{MODULE}.MySQLManagementClient")
    @patch(f"{MODULE}.PostgreSQLManagementClient")
    @patch(f"{MODULE}.ClientSecretCredential")
    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_service_principal_credential_used(self, mock_secret_cred, *_clients):
        """All three service-principal fields present selects ClientSecretCredential."""
        config = AzureConfig(
            enabled=True,
            subscription_id="sub-1",
            tenant_id="t",
            client_id="c",
            client_secret="s",
        )
        analyzer = AzureAnalyzer(config)

        assert analyzer.authenticate() is True
        mock_secret_cred.assert_called_once_with(
            tenant_id="t", client_id="c", client_secret="s"
        )

    @patch(f"{MODULE}.NetworkManagementClient")
    @patch(f"{MODULE}.MySQLManagementClient")
    @patch(f"{MODULE}.PostgreSQLManagementClient")
    @patch(f"{MODULE}.DefaultAzureCredential")
    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_default_credential_fallback(self, mock_default_cred, *_clients):
        """No service-principal fields falls back to ambient credentials."""
        analyzer = AzureAnalyzer(AzureConfig(enabled=True, subscription_id="sub-1"))

        assert analyzer.authenticate() is True
        mock_default_cred.assert_called_once()

    @patch(f"{MODULE}.NetworkManagementClient")
    @patch(f"{MODULE}.MySQLManagementClient")
    @patch(f"{MODULE}.PostgreSQLManagementClient")
    @patch(f"{MODULE}.DefaultAzureCredential")
    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_partial_service_principal_falls_back(self, mock_default_cred, *_clients):
        """A half-filled service principal must not be used as one."""
        config = AzureConfig(
            enabled=True, subscription_id="sub-1", tenant_id="t", client_id="c"
        )
        analyzer = AzureAnalyzer(config)

        assert analyzer.authenticate() is True
        mock_default_cred.assert_called_once()

    @patch(f"{MODULE}.DefaultAzureCredential")
    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_authentication_error_is_fatal(self, mock_default_cred):
        """A bad credential aborts this provider and names the setup guide."""
        from azure.core.exceptions import ClientAuthenticationError

        mock_default_cred.side_effect = ClientAuthenticationError("no token")
        analyzer = AzureAnalyzer(AzureConfig(enabled=True, subscription_id="sub-1"))

        assert analyzer.authenticate() is False
        assert len(analyzer.errors) == 1
        assert "docs/providers/azure.md" in analyzer.errors[0]["message"]

    @patch(f"{MODULE}.NetworkManagementClient")
    @patch(f"{MODULE}.MySQLManagementClient")
    @patch(f"{MODULE}.PostgreSQLManagementClient")
    @patch(f"{MODULE}.DefaultAzureCredential")
    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_permission_error_on_probe_is_a_warning(
        self, _cred, mock_pg_client, *_rest
    ):
        """A 403 means limited privilege, which is a warning, not a failed run."""
        from azure.core.exceptions import HttpResponseError

        error = HttpResponseError("forbidden")
        error.status_code = 403
        mock_pg_client.return_value.servers.list.side_effect = error

        analyzer = AzureAnalyzer(AzureConfig(enabled=True, subscription_id="sub-1"))

        assert analyzer.authenticate() is True
        assert analyzer.errors == []
        assert any(
            "Insufficient permissions" in w["message"] for w in analyzer.warnings
        )

    @patch(f"{MODULE}.NetworkManagementClient")
    @patch(f"{MODULE}.MySQLManagementClient")
    @patch(f"{MODULE}.PostgreSQLManagementClient")
    @patch(f"{MODULE}.DefaultAzureCredential")
    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_unregistered_provider_is_a_warning(self, _cred, mock_pg_client, *_rest):
        """A subscription with only MySQL servers has no PostgreSQL registration."""
        from azure.core.exceptions import HttpResponseError

        error = HttpResponseError("not registered")
        error.error = MagicMock()
        error.error.code = "MissingSubscriptionRegistration"
        mock_pg_client.return_value.servers.list.side_effect = error

        analyzer = AzureAnalyzer(AzureConfig(enabled=True, subscription_id="sub-1"))

        assert analyzer.authenticate() is True
        assert analyzer.errors == []
        assert any("az provider register" in w["message"] for w in analyzer.warnings)


class TestRegionBucketing:
    """Tests for the fetch-once, bucket-by-location design."""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_resources_bucketed_by_own_location(self, azure_config):
        """Each server lands under its own location, not a configured region."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="pg-east", location="eastus"),
                make_pg_server(name="pg-west", location="westus2"),
            ],
        )

        results = analyzer.analyze()

        assert sorted(results["resources"]) == ["eastus", "westus2"]
        assert [
            s["name"]
            for s in results["resources"]["eastus"]["postgresql_flexible_servers"]
        ] == ["pg-east"]
        assert [
            s["name"]
            for s in results["resources"]["westus2"]["postgresql_flexible_servers"]
        ] == ["pg-west"]

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_global_resources_not_multiplied_across_regions(self):
        """One VNet stays one VNet however many regions are configured.

        Subscription-global resources are fetched once and bucketed, so they
        cannot be multiplied by the region count.
        """
        from tests.fixtures.azure_responses import make_pg_server, make_vnet

        config = AzureConfig(
            enabled=True,
            subscription_id="sub-1",
            regions=["eastus", "westus2", "westeurope"],
        )
        analyzer = AzureAnalyzer(config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="pg-east", location="eastus"),
                make_pg_server(name="pg-west", location="westus2"),
            ],
            vnets=[make_vnet(name="vnet-a", location="eastus")],
        )

        results = analyzer.analyze()

        assert results["summary"]["virtual_networks"] == 1
        assert analyzer.network_client.virtual_networks.list_all.call_count == 1
        # The one VNet appears in its own region and nowhere else.
        assert len(results["resources"]["eastus"]["virtual_networks"]) == 1
        assert results["resources"]["westus2"]["virtual_networks"] == []
        assert results["resources"]["westeurope"]["virtual_networks"] == []

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_servers_fetched_once_not_once_per_region(self):
        """The subscription-wide list must not be re-issued per region."""
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(
            enabled=True, subscription_id="sub-1", regions=["eastus", "westus2"]
        )
        analyzer = AzureAnalyzer(config)
        _wire(analyzer, pg_servers=[make_pg_server(location="eastus")])

        analyzer.analyze()

        assert analyzer.postgresql_client.servers.list.call_count == 1

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_region_filter_excludes_other_locations(self):
        """A configured filter drops resources living elsewhere."""
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(enabled=True, subscription_id="sub-1", regions=["eastus"])
        analyzer = AzureAnalyzer(config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="pg-east", location="eastus"),
                make_pg_server(name="pg-west", location="westus2"),
            ],
        )

        results = analyzer.analyze()

        assert list(results["resources"]) == ["eastus"]
        assert results["summary"]["postgresql_flexible_servers"] == 1

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_filtered_out_servers_cost_no_follow_up_calls(self):
        """Out-of-filter servers must be dropped before their extra API calls.

        Mapping a server costs two follow-up calls: configurations and
        firewall rules.
        """
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(enabled=True, subscription_id="sub-1", regions=["eastus"])
        analyzer = AzureAnalyzer(config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="pg-east", location="eastus"),
                make_pg_server(name="pg-west", location="westus2"),
                make_pg_server(name="pg-europe", location="westeurope"),
            ],
        )

        analyzer.analyze()

        # Exactly one in-scope server, so exactly one pair of follow-up calls.
        assert analyzer.postgresql_client.configurations.list_by_server.call_count == 1
        assert analyzer.postgresql_client.firewall_rules.list_by_server.call_count == 1

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_region_filter_matches_display_names(self):
        """regions: ["East US"] has to match a location of "eastus"."""
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(enabled=True, subscription_id="sub-1", regions=["East US"])
        analyzer = AzureAnalyzer(config)
        _wire(analyzer, pg_servers=[make_pg_server(location="eastus")])

        results = analyzer.analyze()

        assert results["summary"]["postgresql_flexible_servers"] == 1

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_filter_matching_nothing_warns_with_the_real_regions(self):
        """A filter that misses every server must say so.

        `eastus` against servers in `eastus2` would otherwise give a clean
        report with zero instances and no explanation.
        """
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(
            enabled=True, subscription_id="sub-1", regions=["eastus", "westeurope"]
        )
        analyzer = AzureAnalyzer(config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="pg", location="eastus2"),
                make_pg_server(name="pg2", location="westus2"),
            ],
        )

        results = analyzer.analyze()

        assert results["summary"]["postgresql_flexible_servers"] == 0
        assert len(analyzer.warnings) == 1
        msg = analyzer.warnings[0]["message"]
        assert "matched no resources at all" in msg
        # It must name where the resources actually are.
        assert "eastus2" in msg and "westus2" in msg
        assert analyzer.errors == []

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_partial_filter_warns_without_claiming_total_miss(self):
        """A filter that matches some resources warns differently."""
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(enabled=True, subscription_id="sub-1", regions=["eastus"])
        analyzer = AzureAnalyzer(config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="kept", location="eastus"),
                make_pg_server(name="dropped", location="westus2"),
            ],
        )

        results = analyzer.analyze()

        assert results["summary"]["postgresql_flexible_servers"] == 1
        msg = analyzer.warnings[0]["message"]
        assert "outside the configured regions" in msg
        assert "matched no resources at all" not in msg

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_no_warning_when_filter_matches_everything(self):
        """No noise when the filter is correct."""
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(enabled=True, subscription_id="sub-1", regions=["eastus"])
        analyzer = AzureAnalyzer(config)
        _wire(analyzer, pg_servers=[make_pg_server(location="eastus")])

        analyzer.analyze()

        assert analyzer.warnings == []

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_configured_region_with_no_resources_is_still_reported(self):
        """ "We looked here and found nothing" must stay visible."""
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(
            enabled=True, subscription_id="sub-1", regions=["eastus", "westus2"]
        )
        analyzer = AzureAnalyzer(config)
        _wire(analyzer, pg_servers=[make_pg_server(location="eastus")])

        results = analyzer.analyze()

        assert results["regions_analyzed"] == ["eastus", "westus2"]
        assert results["resources"]["westus2"]["postgresql_flexible_servers"] == []
        assert results["summary"]["regions_analyzed"] == 2

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_no_filter_reports_only_regions_holding_resources(self, azure_config):
        """Without a filter, total_regions must not be padded with empty regions."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server(location="eastus")])

        results = analyzer.analyze()

        assert results["regions_analyzed"] == ["eastus"]

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_every_region_reports_every_resource_key(self, azure_config):
        """A region bucket always carries the full key set, so consumers can index it."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server(location="eastus")])

        region = analyzer.analyze()["resources"]["eastus"]

        for key in (
            "postgresql_flexible_servers",
            "mysql_flexible_servers",
            "virtual_networks",
            "network_security_groups",
            "networking_summary",
            "security_summary",
        ):
            assert key in region


class TestServerMapping:
    """Tests for turning SDK model objects into result dicts."""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_postgresql_fields_mapped(self, azure_config):
        """The fields a migration engineer scopes from."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])

        server = analyzer.analyze()["resources"]["eastus"][
            "postgresql_flexible_servers"
        ][0]

        assert server["name"] == "pg-prod"
        assert server["resource_group"] == "rg-db"
        assert server["version"] == "16"
        assert server["minor_version"] == "16.3"
        assert server["state"] == "Ready"
        assert server["sku"] == {"name": "Standard_D4ds_v5", "tier": "GeneralPurpose"}
        assert server["storage"]["storage_size_gb"] == 512
        assert server["backup"]["retention_days"] == 14
        assert server["backup"]["geo_redundant_backup"] == "Enabled"
        assert server["high_availability"]["mode"] == "ZoneRedundant"
        assert server["network"]["public_network_access"] == "Disabled"
        assert server["data_encryption"]["type"] == "SystemManaged"

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_mysql_field_divergences_absorbed(self, azure_config):
        """MySQL spells several fields differently from PostgreSQL."""
        from tests.fixtures.azure_responses import make_mysql_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, mysql_servers=[make_mysql_server()])

        server = analyzer.analyze()["resources"]["eastus"]["mysql_flexible_servers"][0]

        # full_version, not minor_version
        assert server["minor_version"] == "8.0.21"
        # MySQL-only storage fields
        assert server["storage"]["auto_io_scaling"] == "Disabled"
        assert server["storage"]["storage_redundancy"] == "LocalRedundancy"
        # private_dns_zone_resource_id, without the "arm" infix
        assert server["network"]["private_dns_zone_resource_id"] == ""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_credentials_never_collected(self, azure_config):
        """administrator_login/password are on the model and must not be copied.

        The API never returns the password, but this tool collects no
        credential or identity fields regardless.
        """
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])

        server = analyzer.analyze()["resources"]["eastus"][
            "postgresql_flexible_servers"
        ][0]

        assert "administrator_login" not in server
        assert "administrator_login_password" not in server
        assert "pgadmin" not in repr(server)

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_missing_sub_objects_do_not_raise(self, azure_config):
        """A server with no sku and no HA block still maps."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[make_pg_server(include_sku=False, include_ha=False)],
        )

        server = analyzer.analyze()["resources"]["eastus"][
            "postgresql_flexible_servers"
        ][0]

        assert server["sku"] == {"name": "", "tier": ""}
        assert server["high_availability"]["mode"] == ""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_only_non_default_server_parameters_collected(self, azure_config):
        """A customized parameter is signal; the default list is noise."""
        from tests.fixtures.azure_responses import make_configuration, make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])
        analyzer.postgresql_client.configurations.list_by_server.return_value = [
            make_configuration(name="max_connections", source="user-override"),
            make_configuration(name="work_mem", source="system-default"),
        ]

        server = analyzer.analyze()["resources"]["eastus"][
            "postgresql_flexible_servers"
        ][0]

        assert [p["name"] for p in server["server_parameters"]] == ["max_connections"]

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_read_only_platform_parameters_excluded_from_complexity(self, azure_config):
        """Azure's own platform settings must not inflate the complexity count.

        Azure marks ~36 of its own settings (config_file, ssl_cert_file, ...)
        user-override and read-only; only writable ones are a real decision.
        """
        from tests.fixtures.azure_responses import make_configuration, make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])
        analyzer.postgresql_client.configurations.list_by_server.return_value = [
            make_configuration(name="work_mem", is_read_only=False),
            make_configuration(name="config_file", is_read_only=True),
            make_configuration(name="server_version", is_read_only=True),
            # MySQL spells the same flag as a string.
            make_configuration(name="ssl_cert_file", is_read_only="True"),
            make_configuration(name="slow_query_log", is_read_only="False"),
        ]

        results = analyzer.analyze()
        server = results["resources"]["eastus"]["postgresql_flexible_servers"][0]

        # All five are still reported as raw data, correctly labelled...
        assert len(server["server_parameters"]) == 5
        flags = {p["name"]: p["is_read_only"] for p in server["server_parameters"]}
        assert flags == {
            "work_mem": False,
            "config_file": True,
            "server_version": True,
            "ssl_cert_file": True,
            "slow_query_log": False,
        }
        # ...but only the two writable ones count as customizations.
        assert results["complexity_factors"]["custom_server_parameters"] == 2

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_permissive_firewall_rules_flagged(self, azure_config):
        """The 0.0.0.0 sentinels reach beyond this subscription."""
        from tests.fixtures.azure_responses import make_firewall_rule, make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])
        analyzer.postgresql_client.firewall_rules.list_by_server.return_value = [
            make_firewall_rule("azure-services", "0.0.0.0", "0.0.0.0"),
            make_firewall_rule("wide-open", "0.0.0.0", "255.255.255.255"),
            make_firewall_rule("office", "203.0.113.0", "203.0.113.255"),
        ]

        rules = analyzer.analyze()["resources"]["eastus"][
            "postgresql_flexible_servers"
        ][0]["firewall_rules"]

        by_name = {r["name"]: r for r in rules}
        assert by_name["azure-services"]["allows_azure_services"] is True
        assert by_name["wide-open"]["allows_all_addresses"] is True
        assert by_name["office"]["allows_azure_services"] is False
        assert by_name["office"]["allows_all_addresses"] is False

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_compute_specs_reports_vcpu_and_ram(self, azure_config):
        """compute_specs carries vCPU, RAM, storage and architecture.

        Azure states compute only as a SKU name, so the numbers come from the
        generated SKU table.
        """
        from tests.fixtures.azure_responses import make_mysql_server, make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[make_pg_server()],
            mysql_servers=[make_mysql_server()],
        )
        region = analyzer.analyze()["resources"]["eastus"]

        pg = region["postgresql_flexible_servers"][0]["compute_specs"]
        # Standard_D4ds_v5
        assert pg["vcpu"] == 4
        assert pg["ram_gb"] == 16
        assert pg["storage_gb"] == 512
        assert pg["architecture"] == "x86_64"

        mysql = region["mysql_flexible_servers"][0]["compute_specs"]
        # Standard_D2ds_v4
        assert mysql["vcpu"] == 2
        assert mysql["ram_gb"] == 8
        assert mysql["storage_gb"] == 128

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_compute_specs_key_names_differ_from_the_neutral_view(self, azure_config):
        """compute_specs uses `vcpu`/`ram_gb`; the neutral instance does not.

        The neutral instance keeps the schema's `cpu_cores`/`memory_gb`.
        """
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])
        region = analyzer.analyze()["resources"]["eastus"]

        specs = region["postgresql_flexible_servers"][0]["compute_specs"]
        assert "cpu_cores" not in specs
        assert "memory_gb" not in specs

        # The canonical instance keeps the schema's names, unchanged.
        instance = region["instances"][0]
        assert instance["cpu_cores"] == 4
        assert instance["memory_gb"] == 16

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_compute_specs_omits_unknown_sku_and_warns_once(self, azure_config):
        """An unrecognized SKU omits vcpu/ram_gb and warns exactly once."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server(sku_name="Standard_Imaginary_v9")])
        region = analyzer.analyze()["resources"]["eastus"]

        specs = region["postgresql_flexible_servers"][0]["compute_specs"]
        assert "vcpu" not in specs
        assert "ram_gb" not in specs
        # Storage comes off the server itself, so it survives.
        assert specs["storage_gb"] == 512

        sku_warnings = [
            w for w in analyzer.warnings if "Standard_Imaginary_v9" in w["message"]
        ]
        assert len(sku_warnings) == 1

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_compute_specs_respects_the_per_engine_memory_override(self, azure_config):
        """Azure caps Standard_E64ds_v4 at 432 GB on PostgreSQL, 512 on MySQL."""
        from tests.fixtures.azure_responses import make_mysql_server, make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[make_pg_server(sku_name="Standard_E64ds_v4")],
            mysql_servers=[make_mysql_server(sku_name="Standard_E64ds_v4")],
        )
        region = analyzer.analyze()["resources"]["eastus"]

        assert (
            region["postgresql_flexible_servers"][0]["compute_specs"]["ram_gb"] == 432
        )
        assert region["mysql_flexible_servers"][0]["compute_specs"]["ram_gb"] == 512


class TestNetworking:
    """Tests for the nested azure-mgmt-network models."""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_vnet_nested_properties_read(self, azure_config):
        """azure-mgmt-network 32.x nests fields under .properties."""
        from tests.fixtures.azure_responses import make_vnet

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, vnets=[make_vnet()])

        vnet = analyzer.analyze()["resources"]["eastus"]["virtual_networks"][0]

        assert vnet["name"] == "vnet-prod"
        assert vnet["resource_group"] == "rg-net"
        assert vnet["address_prefixes"] == ["10.0.0.0/16"]
        assert vnet["subnets"][0]["name"] == "snet-db"
        assert vnet["subnets"][0]["address_prefix"] == "10.0.1.0/24"
        assert vnet["subnets"][0]["delegations"] == [
            "Microsoft.DBforPostgreSQL/flexibleServers"
        ]
        assert vnet["peerings"][0]["peering_state"] == "Connected"

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_nsg_default_rules_excluded(self, azure_config):
        """Azure separates built-in rules, so the custom count is exact."""
        from tests.fixtures.azure_responses import make_nsg

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, nsgs=[make_nsg()])

        nsg = analyzer.analyze()["resources"]["eastus"]["network_security_groups"][0]

        assert [r["name"] for r in nsg["security_rules"]] == ["allow-postgres"]
        assert "AllowVnetInBound" not in repr(nsg)

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_internet_exposed_nsg_rule_flagged(self, azure_config):
        """A rule sourced from Internet is a migration security signal."""
        from tests.fixtures.azure_responses import make_nsg

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, nsgs=[make_nsg(rule_source="Internet")])

        rule = analyzer.analyze()["resources"]["eastus"]["network_security_groups"][0][
            "security_rules"
        ][0]

        assert rule["open_to_internet"] is True
        assert rule["destination_port_ranges"] == ["5432"]

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_internal_nsg_rule_not_flagged(self, azure_config):
        """A VirtualNetwork-sourced rule is not internet-exposed."""
        from tests.fixtures.azure_responses import make_nsg

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, nsgs=[make_nsg(rule_source="VirtualNetwork")])

        rule = analyzer.analyze()["resources"]["eastus"]["network_security_groups"][0][
            "security_rules"
        ][0]

        assert rule["open_to_internet"] is False


class TestDegradation:
    """Tests that one failure never takes down the rest of the run."""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_network_failure_leaves_servers_intact(self, azure_config):
        """A networking 403 must not lose the database inventory."""
        from azure.core.exceptions import HttpResponseError
        from tests.fixtures.azure_responses import make_pg_server

        error = HttpResponseError("forbidden")
        error.status_code = 403

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])
        analyzer.network_client.virtual_networks.list_all.side_effect = error

        results = analyzer.analyze()

        assert results["summary"]["postgresql_flexible_servers"] == 1
        assert results["summary"]["virtual_networks"] == 0
        assert analyzer.errors == []
        assert analyzer.warnings

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_mysql_failure_leaves_postgresql_intact(self, azure_config):
        """One engine's provider being unregistered must not fail the other."""
        from azure.core.exceptions import HttpResponseError
        from tests.fixtures.azure_responses import make_pg_server

        error = HttpResponseError("not registered")
        error.error = MagicMock()
        error.error.code = "MissingSubscriptionRegistration"

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])
        analyzer.mysql_client.servers.list.side_effect = error

        results = analyzer.analyze()

        assert results["summary"]["postgresql_flexible_servers"] == 1
        assert results["summary"]["mysql_flexible_servers"] == 0
        assert analyzer.errors == []

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_server_parameter_failure_keeps_the_server(self, azure_config):
        """A failed follow-up call must not drop the server it belonged to."""
        from azure.core.exceptions import HttpResponseError
        from tests.fixtures.azure_responses import make_pg_server

        error = HttpResponseError("forbidden")
        error.status_code = 403

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])
        analyzer.postgresql_client.configurations.list_by_server.side_effect = error

        server = analyzer.analyze()["resources"]["eastus"][
            "postgresql_flexible_servers"
        ][0]

        assert server["name"] == "pg-prod"
        assert server["server_parameters"] == []
        assert analyzer.errors == []

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    @pytest.mark.parametrize(
        "config_kwargs",
        [
            {},
            {"resource_groups": ["rg-a"]},
            {"regions": ["eastus"]},
        ],
    )
    def test_analyze_never_raises_when_unauthenticated(self, config_kwargs):
        """analyze() must always return a well-formed envelope.

        Collection happens once, outside the per-region loop, so a missing
        client has to degrade to warnings rather than raise.
        """
        analyzer = AzureAnalyzer(
            AzureConfig(enabled=True, subscription_id="sub-1", **config_kwargs)
        )

        results = analyzer.analyze()

        assert results["provider"] == "azure"
        assert "resources" in results
        assert "summary" in results
        assert analyzer.warnings


class TestScopeNarrowing:
    """Tests for resource_groups and discover_all."""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_resource_groups_use_scoped_list(self):
        """With resource_groups set, the subscription-wide list is not used."""
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(
            enabled=True, subscription_id="sub-1", resource_groups=["rg-a", "rg-b"]
        )
        analyzer = AzureAnalyzer(config)
        _wire(analyzer)
        analyzer.postgresql_client.servers.list_by_resource_group.return_value = [
            make_pg_server()
        ]

        analyzer.analyze()

        analyzer.postgresql_client.servers.list.assert_not_called()
        assert analyzer.postgresql_client.servers.list_by_resource_group.call_count == 2

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_resource_groups_also_narrow_networking(self):
        """resource_groups must scope networking, not just servers.

        Otherwise a scoped scan still reports every other group's VNets and
        NSGs, and pulls their regions into regions_analyzed.
        """
        from tests.fixtures.azure_responses import make_nsg, make_vnet

        config = AzureConfig(
            enabled=True, subscription_id="sub-1", resource_groups=["rg-a"]
        )
        analyzer = AzureAnalyzer(config)
        _wire(analyzer)
        analyzer.postgresql_client.servers.list_by_resource_group.return_value = []
        analyzer.mysql_client.servers.list_by_resource_group.return_value = []
        analyzer.network_client.virtual_networks.list.return_value = [
            make_vnet(name="vnet-in-scope")
        ]
        analyzer.network_client.network_security_groups.list.return_value = [
            make_nsg(name="nsg-in-scope")
        ]

        results = analyzer.analyze()

        # The scoped call is used and the subscription-wide one is not.
        analyzer.network_client.virtual_networks.list.assert_called_once_with("rg-a")
        analyzer.network_client.virtual_networks.list_all.assert_not_called()
        analyzer.network_client.network_security_groups.list_all.assert_not_called()
        assert results["summary"]["virtual_networks"] == 1
        assert results["summary"]["network_security_groups"] == 1

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_named_resources_narrow_the_result(self):
        """resources.* keeps only the named servers."""
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(
            enabled=True,
            subscription_id="sub-1",
            resources={"postgresql_flexible_servers": ["pg-keep"]},
        )
        analyzer = AzureAnalyzer(config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="pg-keep"),
                make_pg_server(name="pg-drop"),
            ],
        )

        results = analyzer.analyze()

        assert [
            s["name"]
            for s in results["resources"]["eastus"]["postgresql_flexible_servers"]
        ] == ["pg-keep"]

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_discover_all_false_without_names_skips_and_warns(self):
        """discover_all false with no names listed: skip, and say so."""
        from tests.fixtures.azure_responses import make_pg_server

        config = AzureConfig(enabled=True, subscription_id="sub-1", discover_all=False)
        analyzer = AzureAnalyzer(config)
        _wire(analyzer, pg_servers=[make_pg_server()])

        results = analyzer.analyze()

        assert results["summary"]["postgresql_flexible_servers"] == 0
        assert any("discover_all=false" in w["message"] for w in analyzer.warnings)


class TestCanonicalArrays:
    """Tests for the provider-neutral views derived from the native arrays."""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_both_engines_land_in_instances(self, azure_config):
        """One entry per server, whichever engine, with the engine recorded."""
        from tests.fixtures.azure_responses import make_mysql_server, make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[make_pg_server(name="pg", location="eastus")],
            mysql_servers=[make_mysql_server(name="my", location="eastus")],
        )

        instances = analyzer.analyze()["resources"]["eastus"]["instances"]

        assert {i["db_instance_identifier"]: i["engine"] for i in instances} == {
            "pg": "postgres",
            "my": "mysql",
        }
        assert all(i["provider"] == "azure" for i in instances)

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_instances_is_the_only_instance_key(self, azure_config):
        """No second key carries the same list.

        Checked after serialisation too, since that is the form a consumer
        reads.
        """
        import json

        from tests.fixtures.azure_responses import make_mysql_server, make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[make_pg_server(name="pg", location="eastus")],
            mysql_servers=[make_mysql_server(name="my", location="eastus")],
        )

        written = json.loads(json.dumps(analyzer.analyze(), default=str))
        region = written["resources"]["eastus"]

        assert len(region["instances"]) == 2
        assert "rds_instances" not in region
        # Nothing else duplicates the list under another name either.
        duplicates = [
            key
            for key, value in region.items()
            if key != "instances" and value == region["instances"]
        ]
        assert duplicates == []

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_no_per_provider_instance_key(self, azure_config):
        """A per-provider key would recreate the problem one provider later."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])

        region = analyzer.analyze()["resources"]["eastus"]

        assert "azure_instances" not in region

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_required_fields_all_present(self, azure_config):
        """An instance missing any of these is treated as invalid downstream."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])

        instance = analyzer.analyze()["resources"]["eastus"]["instances"][0]

        for field in (
            "db_instance_identifier",
            "db_instance_class",
            "provider",
            "region",
            "engine",
            "cpu_cores",
            "memory_gb",
            "allocated_storage",
            "multi_az",
        ):
            assert field in instance, field
            assert instance[field] is not None, field

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_sku_resolved_to_cpu_and_memory(self, azure_config):
        """Standard_B1ms is 1 vCPU / 2 GB per Azure's own capability API."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server(sku_name="Standard_B1ms")])

        instance = analyzer.analyze()["resources"]["eastus"]["instances"][0]

        assert (instance["cpu_cores"], instance["memory_gb"]) == (1, 2)

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_unknown_sku_omits_rather_than_guesses(self, azure_config):
        """A wrong vCPU count is worse than an absent one, so warn and omit."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server(sku_name="Standard_Imaginary_v9")])

        instance = analyzer.analyze()["resources"]["eastus"]["instances"][0]

        assert "cpu_cores" not in instance
        assert "memory_gb" not in instance
        assert any(
            "Unknown Azure compute SKU" in w["message"] for w in analyzer.warnings
        )

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_arm_sku_reported_as_aarch64(self, azure_config):
        """ARM SKUs select a different, cheaper replacement family downstream."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="x86", sku_name="Standard_D4ds_v5"),
                make_pg_server(name="arm", sku_name="Standard_D4pls_v5"),
            ],
        )

        arch = {
            i["db_instance_identifier"]: i["architecture"]
            for i in analyzer.analyze()["resources"]["eastus"]["instances"]
        }
        assert arch == {"x86": "x86_64", "arm": "aarch64"}

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_ai_extracted_is_false(self, azure_config):
        """This data is read from the Azure API, not inferred by a model."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])

        instance = analyzer.analyze()["resources"]["eastus"]["instances"][0]

        assert instance["_ai_extracted"] is False

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_multi_az_true_for_both_ha_modes(self, azure_config):
        """SameZone and ZoneRedundant both provision a standby you pay for."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="zr", ha_mode="ZoneRedundant"),
                make_pg_server(name="sz", ha_mode="SameZone"),
                make_pg_server(name="off", ha_mode="Disabled"),
            ],
        )

        got = {
            i["db_instance_identifier"]: i["multi_az"]
            for i in analyzer.analyze()["resources"]["eastus"]["instances"]
        }
        assert got == {"zr": True, "sz": True, "off": False}

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_storage_encrypted_true_even_when_azure_omits_the_field(self, azure_config):
        """MySQL reports data_encryption only for customer-managed keys.

        An empty type does not mean unencrypted - Azure always encrypts
        Flexible Server storage at rest.
        """
        from tests.fixtures.azure_responses import make_mysql_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, mysql_servers=[make_mysql_server(location="eastus")])

        instance = analyzer.analyze()["resources"]["eastus"]["instances"][0]

        assert instance["_azure"]["data_encryption"]["type"] == ""
        assert instance["storage_encrypted"] is True

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_replica_count_counted_across_regions(self, azure_config):
        """A replica commonly sits in a different region from its primary."""
        from tests.fixtures.azure_responses import make_pg_server, server_id

        primary_id = server_id(name="primary")
        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="primary", location="eastus"),
                make_pg_server(
                    name="replica-a",
                    location="westus2",
                    replication_role="Replica",
                    source_server_resource_id=primary_id,
                ),
                make_pg_server(
                    name="replica-b",
                    location="westeurope",
                    replication_role="Replica",
                    source_server_resource_id=primary_id,
                ),
            ],
        )

        results = analyzer.analyze()
        counts = {
            i["db_instance_identifier"]: i["replica_count"]
            for region in results["resources"].values()
            for i in region["instances"]
        }
        assert counts["primary"] == 2
        assert counts["replica-a"] == 0

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_vpcs_and_security_groups_translated(self, azure_config):
        """VNet -> vpc, NSG -> security_group, with the counters consumers read."""
        from tests.fixtures.azure_responses import make_nsg, make_vnet

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, vnets=[make_vnet()], nsgs=[make_nsg()])

        region = analyzer.analyze()["resources"]["eastus"]
        vpc = region["vpcs"][0]
        group = region["security_groups"][0]

        assert vpc["vpc_id"] == "vnet-prod"
        assert vpc["cidr_block"] == "10.0.0.0/16"
        assert vpc["cidr_blocks"] == ["10.0.0.0/16"]
        assert vpc["state"] == "available"
        assert len(vpc["subnets"]) == 1
        assert vpc["subnets"][0]["cidr_block"] == "10.0.1.0/24"
        # Azure subnets are regional, so the region is the honest answer.
        assert vpc["subnets"][0]["availability_zone"] == "eastus"
        assert group["group_name"] == "nsg-db"

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_subnet_references_nsg_by_name_for_cross_reference(self, azure_config):
        """The canonical subnet's NSG must match security_groups[].group_id.

        Azure returns a full ARM id, which the native array keeps.
        """
        from tests.fixtures.azure_responses import make_nsg, make_vnet

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            vnets=[
                make_vnet(nsg_id="/subscriptions/s/rg/x/networkSecurityGroups/nsg-db")
            ],
            nsgs=[make_nsg()],
        )

        region = analyzer.analyze()["resources"]["eastus"]

        assert (
            region["vpcs"][0]["subnets"][0]["network_security_group"]
            == region["security_groups"][0]["group_id"]
        )
        # The native array is untouched.
        assert region["virtual_networks"][0]["subnets"][0][
            "network_security_group"
        ].startswith("/subscriptions/")

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_vnet_injected_server_builds_a_db_subnet_group(self, azure_config):
        """A delegated subnet groups an instance under its network."""
        from tests.fixtures.azure_responses import make_pg_server, make_vnet

        subnet_id = (
            "/subscriptions/sub-1/resourceGroups/rg-net/providers"
            "/Microsoft.Network/virtualNetworks/vnet-prod/subnets/snet-db"
        )
        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[make_pg_server(delegated_subnet_resource_id=subnet_id)],
            vnets=[make_vnet()],
        )

        region = analyzer.analyze()["resources"]["eastus"]
        groups = region["db_subnet_groups"]
        instance = region["instances"][0]

        assert len(groups) == 1
        assert groups[0]["name"] == "vnet-prod-snet-db"
        assert groups[0]["vpc_id"] == "vnet-prod"
        assert [s["name"] for s in groups[0]["subnets"]] == ["snet-db"]
        # And the instance points back at it.
        assert instance["db_subnet_group"] == "vnet-prod-snet-db"
        assert instance["vpc_id"] == "vnet-prod"

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_public_server_contributes_no_subnet_group(self, azure_config):
        """A server on a public endpoint has no delegated subnet."""
        from tests.fixtures.azure_responses import make_pg_server, make_vnet

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()], vnets=[make_vnet()])

        region = analyzer.analyze()["resources"]["eastus"]

        assert region["db_subnet_groups"] == []
        assert "db_subnet_group" not in region["instances"][0]

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_native_arrays_kept_and_not_double_counted(self, azure_config):
        """The canonical arrays are added alongside, not instead.

        The summary must keep counting the native arrays only.
        """
        from tests.fixtures.azure_responses import make_nsg, make_pg_server, make_vnet

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[make_pg_server()],
            vnets=[make_vnet()],
            nsgs=[make_nsg()],
        )

        results = analyzer.analyze()
        region = results["resources"]["eastus"]

        assert len(region["postgresql_flexible_servers"]) == 1
        assert len(region["instances"]) == 1
        assert results["summary"]["postgresql_flexible_servers"] == 1
        assert results["summary"]["virtual_networks"] == 1
        # Detail that only survives in the native array.
        assert "server_parameters" in region["postgresql_flexible_servers"][0]

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_azure_detail_namespaced_not_flattened(self, azure_config):
        """Consumers shallow-spread instances, so Azure keys stay under _azure."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()])

        instance = analyzer.analyze()["resources"]["eastus"]["instances"][0]

        assert "resource_group" not in instance
        assert "replication_role" not in instance
        assert instance["_azure"]["resource_group"] == "rg-db"
        assert instance["_azure"]["replication_role"] == "Primary"


class TestSummaryAndComplexity:
    """Tests for the counters the combined report aggregates."""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_summary_counts(self, azure_config):
        """Row counts and posture counters."""
        from tests.fixtures.azure_responses import (
            make_mysql_server,
            make_nsg,
            make_pg_server,
            make_vnet,
        )

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[make_pg_server()],
            mysql_servers=[make_mysql_server()],
            vnets=[make_vnet()],
            nsgs=[make_nsg()],
        )

        summary = analyzer.analyze()["summary"]

        assert summary["postgresql_flexible_servers"] == 1
        assert summary["mysql_flexible_servers"] == 1
        assert summary["virtual_networks"] == 1
        assert summary["network_security_groups"] == 1
        # PostgreSQL fixture is ZoneRedundant; MySQL fixture is Disabled.
        assert summary["high_availability_servers"] == 1
        # PostgreSQL fixture has public access Disabled; MySQL has it Enabled.
        assert summary["private_access_servers"] == 1
        # PostgreSQL fixture is geo-redundant; MySQL is not.
        assert summary["geo_redundant_backup_servers"] == 1
        # Azure always encrypts at rest, so both count.
        assert summary["encrypted_servers"] == 2

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_ha_read_from_enum_value(self, azure_config):
        """SameZone counts as HA; Disabled does not."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="a", ha_mode="SameZone"),
                make_pg_server(name="b", ha_mode="Disabled"),
            ],
        )

        assert analyzer.analyze()["summary"]["high_availability_servers"] == 1

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_vnet_injection_counts_as_private_access(self, azure_config):
        """A delegated subnet is private even with public access Enabled."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(
                    public_network_access="Enabled",
                    delegated_subnet_resource_id="/subscriptions/sub-1/subnet/db",
                )
            ],
        )

        results = analyzer.analyze()

        assert results["summary"]["private_access_servers"] == 1
        assert results["complexity_factors"]["custom_vnet_configuration"] is True

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_read_replicas_counted_for_both_engines(self, azure_config):
        """PostgreSQL and MySQL report different roles for the same thing.

        PostgreSQL never returns "Replica": its wire values are AsyncReplica
        and GeoAsyncReplica, while MySQL uses Source and Replica.
        """
        from tests.fixtures.azure_responses import (
            make_mysql_server,
            make_pg_server,
            server_id,
        )

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="pg-primary", replication_role="Primary"),
                make_pg_server(
                    name="pg-replica",
                    replication_role="AsyncReplica",
                    source_server_resource_id=server_id(name="pg-primary"),
                ),
                make_pg_server(
                    name="pg-geo-replica",
                    replication_role="GeoAsyncReplica",
                    source_server_resource_id=server_id(name="pg-primary"),
                ),
            ],
            mysql_servers=[
                make_mysql_server(name="my-primary", replication_role="Source"),
                make_mysql_server(
                    name="my-replica",
                    replication_role="Replica",
                    source_server_resource_id=server_id(
                        provider="Microsoft.DBforMySQL/flexibleServers",
                        name="my-primary",
                    ),
                ),
            ],
        )

        results = analyzer.analyze()

        assert results["summary"]["read_replicas"] == 3
        assert results["complexity_factors"]["read_replicas"] == 3

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_primary_with_no_source_is_not_a_replica(self, azure_config):
        """A primary names no source server, whatever its role string."""
        from tests.fixtures.azure_responses import make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server(name="solo")])

        results = analyzer.analyze()

        assert results["summary"]["read_replicas"] == 0
        assert results["complexity_factors"]["read_replicas"] == 0

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_cross_region_replica_detected(self, azure_config):
        """A replica whose source lives in another region raises complexity."""
        from tests.fixtures.azure_responses import make_pg_server, server_id

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(name="primary", location="eastus"),
                make_pg_server(
                    name="replica",
                    location="westus2",
                    replication_role="AsyncReplica",
                    source_server_resource_id=server_id(name="primary"),
                ),
            ],
        )

        assert analyzer.analyze()["complexity_factors"]["cross_region_replicas"] == 1

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_same_region_replica_is_not_cross_region(self, azure_config):
        """An ARM id carries no region, so the source's own location decides.

        Both servers sit in eastus under a resource group whose name holds no
        region string.
        """
        from tests.fixtures.azure_responses import make_pg_server, server_id

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(
                    name="primary", location="eastus", resource_group="rg-prod"
                ),
                make_pg_server(
                    name="replica",
                    location="eastus",
                    resource_group="rg-prod",
                    replication_role="AsyncReplica",
                    source_server_resource_id=server_id(
                        resource_group="rg-prod", name="primary"
                    ),
                ),
            ],
        )

        results = analyzer.analyze()

        assert results["complexity_factors"]["read_replicas"] == 1
        assert results["complexity_factors"]["cross_region_replicas"] == 0

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_unscanned_source_is_reported_not_guessed(self, azure_config):
        """A source outside the scan cannot be compared, so it is not counted."""
        from tests.fixtures.azure_responses import make_pg_server, server_id

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[
                make_pg_server(
                    name="replica",
                    location="westus2",
                    replication_role="AsyncReplica",
                    source_server_resource_id=server_id(name="elsewhere"),
                )
            ],
        )

        results = analyzer.analyze()

        assert results["complexity_factors"]["read_replicas"] == 1
        assert results["complexity_factors"]["cross_region_replicas"] == 0
        assert any("was not scanned" in w["message"] for w in analyzer.warnings)

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_complexity_counts_custom_parameters_and_rules(self, azure_config):
        """Customized parameters and rules are the migration-complexity signals."""
        from tests.fixtures.azure_responses import (
            make_configuration,
            make_nsg,
            make_pg_server,
        )

        analyzer = AzureAnalyzer(azure_config)
        _wire(analyzer, pg_servers=[make_pg_server()], nsgs=[make_nsg()])
        analyzer.postgresql_client.configurations.list_by_server.return_value = [
            make_configuration(name="max_connections"),
            make_configuration(name="shared_buffers"),
        ]

        complexity = analyzer.analyze()["complexity_factors"]

        assert complexity["custom_server_parameters"] == 2
        # One custom NSG rule; the default rule is not counted.
        assert complexity["custom_network_security_rules"] == 1
        assert complexity["backup_enabled"] is True
        assert complexity["geo_redundant_backup_enabled"] is True


class TestDiscoverResources:
    """Tests for the cheap inventory listing."""

    @patch(f"{MODULE}.HAS_AZURE_LIBS", True)
    def test_discover_resources_labels(self, azure_config):
        """Identifiers are <kind>:<region>:<name>."""
        from tests.fixtures.azure_responses import make_mysql_server, make_pg_server

        analyzer = AzureAnalyzer(azure_config)
        _wire(
            analyzer,
            pg_servers=[make_pg_server(name="pg1", location="eastus")],
            mysql_servers=[make_mysql_server(name="my1", location="westus2")],
        )

        assert analyzer.discover_resources() == [
            "postgresql-flexible-server:eastus:pg1",
            "mysql-flexible-server:westus2:my1",
        ]
