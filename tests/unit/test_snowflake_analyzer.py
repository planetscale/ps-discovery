"""
Tests for Snowflake Postgres cloud analyzer.

Uses the SHOW POSTGRES INSTANCES fixture. The Snowflake connector is
mocked; these tests do not need live credentials.
"""

from unittest.mock import MagicMock, patch

from planetscale_discovery.cloud.analyzers.snowflake_analyzer import (
    SnowflakeAnalyzer,
    INSTANCE_FIELDS,
    describe_rows_to_dict,
    first_hostname_label,
    map_instance_type,
    normalize_postgres_instance,
    parse_snowflake_pg_hostname,
    quote_snowflake_ident,
)
from tests.fixtures.snowflake_responses import (
    DESCRIBE_PRIMARY_PROPERTIES,
    DESCRIBE_PRIMARY_ROW,
    PRIMARY_HOST,
    SHOW_POSTGRES_INSTANCES_LIVE,
)

_FORBIDDEN_INSTANCE_KEYS = {
    "replicas",
    "cpu",
    "vcpu",
    "iops",
    "piops",
    "cpu_util",
    "cpu_utilization",
    "pulse",
    "sharp_pulse",
    "metrics",
    "authentication_authority",
    "owner",
    "show_type_raw",
}


def _config(**overrides):
    config = MagicMock()
    config.enabled = True
    config.account = "testacct"
    config.user = "planetscale_discovery"
    config.role = "DISCOVERY_READONLY"
    config.warehouse = None
    config.authentication = "password"
    config.password = "unused"
    config.private_key_path = None
    config.private_key_passphrase = None
    config.discover_all = True
    config.account_inventory = True
    config.resources = {}
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class TestHostnameAndTypeMapping:
    def test_parse_aws_hostname(self):
        csp, region = parse_snowflake_pg_hostname(
            "abc.myorg.us-east-1.aws.postgres.snowflake.app"
        )
        assert csp == "aws"
        assert region == "us-east-1"

    def test_parse_azure_hostname(self):
        csp, region = parse_snowflake_pg_hostname(
            "abc.myorg.eastus2.azure.postgres.snowflake.app"
        )
        assert csp == "azure"
        assert region == "eastus2"

    def test_parse_gcp_token_when_present(self):
        csp, region = parse_snowflake_pg_hostname(
            "abc.myorg.us-central1.gcp.postgres.snowflake.app"
        )
        assert csp == "gcp"
        assert region == "us-central1"

    def test_parse_rejects_non_snowflake_host(self):
        assert parse_snowflake_pg_hostname("db.example.com") == (None, None)
        assert parse_snowflake_pg_hostname(None) == (None, None)
        assert parse_snowflake_pg_hostname("") == (None, None)

    def test_replica_maps_to_read_replica(self):
        mapped, raw = map_instance_type("REPLICA")
        assert mapped == "READ_REPLICA"
        assert raw == "REPLICA"

    def test_primary_stays_primary(self):
        mapped, raw = map_instance_type("PRIMARY")
        assert mapped == "PRIMARY"
        assert raw == "PRIMARY"

    def test_unknown_type_is_not_invented(self):
        mapped, raw = map_instance_type("SOMETHING_ELSE")
        assert mapped == "SOMETHING_ELSE"
        assert raw == "SOMETHING_ELSE"


class TestNormalizeLiveShowRows:
    def test_ready_primary_and_replica(self):
        primary = normalize_postgres_instance(SHOW_POSTGRES_INSTANCES_LIVE[0])
        replica = normalize_postgres_instance(SHOW_POSTGRES_INSTANCES_LIVE[1])

        assert primary["name"] == "primary_instance"
        assert primary["type"] == "PRIMARY"
        assert primary["state"] == "READY"
        assert primary["compute_family"] == "STANDARD_M"
        assert primary["storage_size_gb"] == 100
        assert primary["postgres_version"] == "18"
        assert primary["is_ha"] is False
        assert primary["csp"] == "aws"
        assert primary["region"] == "us-east-1"
        assert primary["host"].endswith(".aws.postgres.snowflake.app")
        assert "origin" not in primary
        assert "replicas" not in primary
        assert set(primary) <= set(INSTANCE_FIELDS)
        assert _FORBIDDEN_INSTANCE_KEYS.isdisjoint(primary)

        assert replica["name"] == "replica_1"
        assert replica["type"] == "READ_REPLICA"
        assert replica["state"] == "READY"
        assert replica["compute_family"] == "STANDARD_M"
        assert replica["origin"] == "primary_instance"
        assert replica["csp"] == "aws"
        assert replica["region"] == "us-east-1"
        assert "replicas" not in replica
        assert "show_type_raw" not in replica
        assert set(replica) <= set(INSTANCE_FIELDS)
        assert _FORBIDDEN_INSTANCE_KEYS.isdisjoint(replica)

    def test_name_is_show_name_not_hostname_label(self):
        """Liftoff Current hosting Name uses SHOW name, not the hostname id."""
        primary = normalize_postgres_instance(SHOW_POSTGRES_INSTANCES_LIVE[0])
        replica = normalize_postgres_instance(SHOW_POSTGRES_INSTANCES_LIVE[1])
        assert primary["name"] == "primary_instance"
        assert replica["name"] == "replica_1"
        assert first_hostname_label(primary["host"]) == "your-instance"
        assert primary["name"] != first_hostname_label(primary["host"])
        assert replica["name"] != first_hostname_label(replica["host"])
        assert primary["host"] == PRIMARY_HOST

    def test_describe_cannot_replace_show_name_with_host_id(self):
        show = dict(SHOW_POSTGRES_INSTANCES_LIVE[0])
        describe = {"name": "your-instance", "host": PRIMARY_HOST}
        instance = normalize_postgres_instance(show, describe)
        assert instance["name"] == "primary_instance"
        assert instance["host"] == PRIMARY_HOST

    def test_missing_compute_family_is_not_invented(self):
        row = dict(SHOW_POSTGRES_INSTANCES_LIVE[0])
        row["compute_family"] = ""
        instance = normalize_postgres_instance(row)
        assert instance["compute_family"] is None

    def test_describe_property_rows_merge(self):
        show = {"name": "primary_instance", "type": "PRIMARY"}
        describe = describe_rows_to_dict(DESCRIBE_PRIMARY_PROPERTIES)
        instance = normalize_postgres_instance(show, describe)
        assert instance["host"].endswith(".postgres.snowflake.app")
        assert instance["compute_family"] == "STANDARD_M"
        assert instance["state"] == "READY"

    def test_describe_show_shaped_row(self):
        describe = describe_rows_to_dict(DESCRIBE_PRIMARY_ROW)
        assert describe["name"] == "primary_instance"
        assert describe["compute_family"] == "STANDARD_M"

    def test_quote_ident_preserves_case(self):
        assert quote_snowflake_ident("primary_instance") == '"primary_instance"'
        assert quote_snowflake_ident('weird"name') == '"weird""name"'


class TestSnowflakeAnalyzer:
    def test_instantiation(self):
        analyzer = SnowflakeAnalyzer(_config())
        assert analyzer.provider == "snowflake"

    def test_missing_libraries(self):
        analyzer = SnowflakeAnalyzer(_config())
        with patch(
            "planetscale_discovery.cloud.analyzers.snowflake_analyzer.HAS_SNOWFLAKE_LIBS",
            False,
        ):
            assert analyzer.authenticate() is False
            assert analyzer.errors

    def test_password_from_env(self):
        analyzer = SnowflakeAnalyzer(_config(password=None))
        mock_connect = MagicMock()
        with (
            patch(
                "planetscale_discovery.cloud.analyzers.snowflake_analyzer.HAS_SNOWFLAKE_LIBS",
                True,
            ),
            patch(
                "planetscale_discovery.cloud.analyzers.snowflake_analyzer.snowflake"
            ) as mock_sf,
            patch.dict("os.environ", {"SNOWFLAKE_PASSWORD": "from-env"}, clear=False),
        ):
            mock_sf.connector.connect.return_value = mock_connect
            assert analyzer.authenticate() is True
            kwargs = mock_sf.connector.connect.call_args.kwargs
            assert kwargs["password"] == "from-env"
            assert kwargs["account"] == "testacct"
            assert kwargs["user"] == "planetscale_discovery"
            assert kwargs["role"] == "DISCOVERY_READONLY"

    def test_analyze_live_show_describe(self):
        analyzer = SnowflakeAnalyzer(_config())
        analyzer.connection = MagicMock()

        def execute(sql: str):
            if sql.startswith("SHOW"):
                return list(SHOW_POSTGRES_INSTANCES_LIVE)
            if "replica_1" in sql:
                return [SHOW_POSTGRES_INSTANCES_LIVE[1]]
            if "primary_instance" in sql:
                return [SHOW_POSTGRES_INSTANCES_LIVE[0]]
            return []

        with (
            patch.object(analyzer, "authenticate", return_value=True),
            patch.object(analyzer, "_execute", side_effect=execute),
        ):
            result = analyzer.analyze()

        assert result["provider"] == "snowflake"
        assert result["summary"]["instance_count"] == 2
        assert result["summary"]["primary_count"] == 1
        assert result["summary"]["read_replica_count"] == 1
        instances = result["resources"]["us-east-1"]["instances"]
        assert len(instances) == 2
        types = {row["name"]: row["type"] for row in instances}
        states = {row["name"]: row["state"] for row in instances}
        assert types["primary_instance"] == "PRIMARY"
        assert types["replica_1"] == "READ_REPLICA"
        assert states["primary_instance"] == "READY"
        assert states["replica_1"] == "READY"
        replica = next(i for i in instances if i["type"] == "READ_REPLICA")
        primary = next(i for i in instances if i["type"] == "PRIMARY")
        assert replica["origin"] == "primary_instance"
        assert "replicas" not in primary
        assert "replicas" not in replica
        for row in instances:
            assert set(row) <= set(INSTANCE_FIELDS)
            assert _FORBIDDEN_INSTANCE_KEYS.isdisjoint(row)
            assert "cpu" not in row
            assert "iops" not in row
            assert "metrics" not in row

    def test_discover_all_false_filters_names(self):
        analyzer = SnowflakeAnalyzer(
            _config(
                discover_all=False,
                resources={"postgres_instances": ["primary_instance"]},
            )
        )
        analyzer.connection = MagicMock()

        def execute(sql: str):
            if sql.startswith("SHOW"):
                return list(SHOW_POSTGRES_INSTANCES_LIVE)
            return [SHOW_POSTGRES_INSTANCES_LIVE[0]]

        with (
            patch.object(analyzer, "authenticate", return_value=True),
            patch.object(analyzer, "_execute", side_effect=execute),
        ):
            result = analyzer.analyze()

        instances = result["resources"]["us-east-1"]["instances"]
        assert [row["name"] for row in instances] == ["primary_instance"]

    def test_account_inventory_disabled(self):
        analyzer = SnowflakeAnalyzer(_config(account_inventory=False))
        with patch.object(analyzer, "authenticate", return_value=True):
            result = analyzer.analyze()
        assert result["summary"]["instance_count"] == 0
        assert result["resources"] == {}

    def test_missing_password_fails_auth(self):
        analyzer = SnowflakeAnalyzer(_config(password=None))
        env = {
            "SNOWFLAKE_PASSWORD": "",
            "SNOWFLAKE_ACCOUNT": "testacct",
            "SNOWFLAKE_USER": "planetscale_discovery",
        }
        with (
            patch(
                "planetscale_discovery.cloud.analyzers.snowflake_analyzer.HAS_SNOWFLAKE_LIBS",
                True,
            ),
            patch.dict("os.environ", env, clear=True),
        ):
            assert analyzer.authenticate() is False
        assert any("password" in err["message"].lower() for err in analyzer.errors)
