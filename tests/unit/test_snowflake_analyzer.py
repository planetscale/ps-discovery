from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from planetscale_discovery.cloud.analyzers.snowflake_analyzer import (
    SnowflakeAnalyzer,
    INSTANCE_FIELDS,
    describe_rows_to_dict,
    first_hostname_label,
    map_instance_type,
    normalize_postgres_instance,
    parse_account_region,
    parse_replica_list,
    parse_snowflake_pg_hostname,
    quote_snowflake_ident,
)
from tests.fixtures.snowflake_responses import (
    DESCRIBE_PRIMARY_ROW,
    DESCRIBE_PROPERTY_VALUE_PRIMARY,
    DESCRIBE_PROPERTY_VALUE_REPLICA,
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
    config.authentication = "password"
    config.password = "unused"
    config.private_key_path = None
    config.private_key_passphrase = None
    config.discover_all = True
    config.resources = {}
    config.use_secondary_roles = False
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
        assert map_instance_type("REPLICA") == "READ_REPLICA"

    def test_primary_stays_primary(self):
        assert map_instance_type("PRIMARY") == "PRIMARY"

    def test_unknown_type_is_not_invented(self):
        assert map_instance_type("SOMETHING_ELSE") == "SOMETHING_ELSE"


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

    def test_describe_name_used_when_show_name_is_hostname_id(self):
        show = {
            "name": "your-instance",
            "type": "PRIMARY",
            "host": PRIMARY_HOST,
        }
        describe = {"name": "primary_instance", "host": PRIMARY_HOST}
        instance = normalize_postgres_instance(show, describe)
        assert instance["name"] == "primary_instance"

    def test_missing_compute_family_is_not_invented(self):
        row = dict(SHOW_POSTGRES_INSTANCES_LIVE[0])
        row["compute_family"] = ""
        instance = normalize_postgres_instance(row)
        assert instance["compute_family"] is None

    def test_describe_show_shaped_row(self):
        describe = describe_rows_to_dict(DESCRIBE_PRIMARY_ROW)
        assert describe["name"] == "primary_instance"
        assert describe["compute_family"] == "STANDARD_M"

    def test_describe_show_shaped_without_name_or_type(self):
        rows = [{"host": PRIMARY_HOST, "compute_family": "STANDARD_M"}]
        describe = describe_rows_to_dict(rows)
        assert describe["host"] == PRIMARY_HOST
        assert describe["compute_family"] == "STANDARD_M"

    def test_describe_property_value_listing(self):
        describe = describe_rows_to_dict(DESCRIBE_PROPERTY_VALUE_PRIMARY)
        assert describe["compute_family"] == "STANDARD_M"
        assert describe["storage_size_gb"] == 250
        assert describe["high_availability"] is True
        assert describe["origin"] == ""
        assert describe["replicas"] == '"replica_1"'
        assert "work_mem" in describe["postgres_settings"]
        nulled = describe_rows_to_dict(
            [{"property": "compute_family", "value": None, "comment": "STANDARD_M"}]
        )
        assert nulled["compute_family"] is None

    def test_describe_unrecognized_shape_is_empty(self):
        rows = [{"foo": "bar", "baz": 1}]
        assert describe_rows_to_dict(rows) == {}

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

    def test_authenticate_is_idempotent(self):
        analyzer = SnowflakeAnalyzer(_config())
        mock_connect = MagicMock()
        with (
            patch(
                "planetscale_discovery.cloud.analyzers.snowflake_analyzer.HAS_SNOWFLAKE_LIBS",
                True,
            ),
            patch(
                "planetscale_discovery.cloud.analyzers.snowflake_analyzer.snowflake"
            ) as mock_sf,
        ):
            mock_sf.connector.connect.return_value = mock_connect
            assert analyzer.authenticate() is True
            assert analyzer.authenticate() is True
            assert mock_sf.connector.connect.call_count == 1

    def test_close_closes_connection(self):
        analyzer = SnowflakeAnalyzer(_config())
        connection = MagicMock()
        analyzer.connection = connection
        analyzer.close()
        connection.close.assert_called_once()
        assert analyzer.connection is None
        analyzer.close()

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

    def test_discover_all_false_without_names_warns(self):
        analyzer = SnowflakeAnalyzer(_config(discover_all=False, resources={}))
        analyzer.connection = MagicMock()

        def execute(sql: str):
            if sql.startswith("SHOW"):
                return list(SHOW_POSTGRES_INSTANCES_LIVE)
            return []

        with (
            patch.object(analyzer, "authenticate", return_value=True),
            patch.object(analyzer, "_execute", side_effect=execute),
        ):
            result = analyzer.analyze()

        assert result["resources"] == {}
        assert any("postgres_instances" in w["message"] for w in analyzer.warnings)

    def test_property_value_describe_merges_without_warning(self):
        analyzer = SnowflakeAnalyzer(_config())
        show_primary = dict(SHOW_POSTGRES_INSTANCES_LIVE[0])
        show_replica = dict(SHOW_POSTGRES_INSTANCES_LIVE[1], origin="")

        def execute(sql: str):
            if sql.startswith("SHOW"):
                return [show_primary, show_replica]
            if "DESCRIBE" in sql and "replica_1" in sql:
                return list(DESCRIBE_PROPERTY_VALUE_REPLICA)
            if "DESCRIBE" in sql:
                return list(DESCRIBE_PROPERTY_VALUE_PRIMARY)
            return []

        with (
            patch.object(analyzer, "authenticate", return_value=True),
            patch.object(analyzer, "_execute", side_effect=execute),
        ):
            result = analyzer.analyze()

        assert not any("unrecognized shape" in w["message"] for w in analyzer.warnings)
        instances = result["resources"]["us-east-1"]["instances"]
        primary = next(row for row in instances if row["name"] == "primary_instance")
        replica = next(row for row in instances if row["name"] == "replica_1")
        assert primary["compute_family"] == "STANDARD_M"
        assert primary["storage_size_gb"] == 250
        assert primary["is_ha"] is True
        assert primary["postgres_settings"] == {"work_mem": "64MB"}
        assert "certificate" not in primary
        assert "replicas" not in primary
        assert analyzer._declared_replicas["primary_instance"] == ["replica_1"]
        assert replica["origin"] == "primary_instance"
        assert replica["is_ha"] is False
        assert replica["storage_size_gb"] == 250

    def test_unrecognized_describe_shape_warns(self):
        analyzer = SnowflakeAnalyzer(_config())
        analyzer.connection = MagicMock()
        with patch.object(analyzer, "_execute", return_value=[{"foo": "bar"}]):
            converted = analyzer._describe_postgres_instance("primary_instance")
        assert converted == {}
        assert any("unrecognized shape" in w["message"] for w in analyzer.warnings)

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


class TestInstanceDetailFields:
    def test_replica_list_parsing(self):
        assert parse_replica_list('"replica_1"') == ["replica_1"]
        assert parse_replica_list('"a","B_REPLICA_2"') == ["a", "B_REPLICA_2"]
        assert parse_replica_list('"we""ird"') == ['we"ird']
        assert parse_replica_list("a, b") == ["a", "b"]
        assert parse_replica_list("") == []
        assert parse_replica_list(None) == []

    def test_account_region_parsing(self):
        assert parse_account_region("AWS_US_EAST_1") == {
            "raw": "AWS_US_EAST_1",
            "csp": "aws",
            "region": "us-east-1",
        }
        parsed = parse_account_region("PUBLIC.AZURE_EASTUS2")
        assert parsed["csp"] == "azure"
        assert parsed["region"] == "eastus2"
        assert parse_account_region("SOMETHING")["csp"] is None
        assert parse_account_region(None)["raw"] is None

    def test_describe_details_are_emitted(self):
        show = dict(SHOW_POSTGRES_INSTANCES_LIVE[0])
        show["created_on"] = datetime(2026, 9, 25, 17, 51, 30, tzinfo=timezone.utc)
        describe = describe_rows_to_dict(
            [
                {
                    "name": "primary_instance",
                    "network_policy": "PG_POLICY",
                    "postgres_settings": (
                        '{"work_mem": "64MB", "ssl_key": "secret-material", '
                        '"password": "x"}'
                    ),
                    "maintenance_window_start": "4",
                    "operations": "{ }",
                    "replicas": '"replica_1"',
                    "certificate": "-----BEGIN CERTIFICATE-----",
                }
            ]
        )
        inst = normalize_postgres_instance(show, describe)
        assert inst["network_policy"] == "PG_POLICY"
        assert inst["postgres_settings"] == {"work_mem": "64MB"}
        assert inst["maintenance_window_start"] == 4
        assert inst["created_on"] == "2026-09-25T17:51:30+00:00"
        assert inst["instance_protection"] is False
        assert inst["retention_time"] == 0
        assert "pending_operations" not in inst
        assert "certificate" not in inst
        assert set(inst) <= set(INSTANCE_FIELDS)
        assert _FORBIDDEN_INSTANCE_KEYS.isdisjoint(inst)

    def test_pending_operations_emitted_when_present(self):
        describe = {"operations": '{"upgrade": {"state": "UPGRADING"}}'}
        inst = normalize_postgres_instance(SHOW_POSTGRES_INSTANCES_LIVE[0], describe)
        assert inst["pending_operations"] == {"upgrade": {"state": "UPGRADING"}}


def _routing_execute(routes):
    calls = []

    def execute(sql, params=None):
        calls.append((sql, params))
        for needle, rows in routes:
            if needle in sql:
                if isinstance(rows, Exception):
                    raise rows
                return rows
        return []

    return execute, calls


_PRIMARY_DESCRIBE_WITH_POLICY = [
    {
        "name": "primary_instance",
        "host": PRIMARY_HOST,
        "network_policy": "PG_POLICY",
        "replicas": '"replica_1"',
    }
]


class TestSessionAndVisibility:
    def test_secondary_roles_disabled_when_role_configured(self):
        analyzer = SnowflakeAnalyzer(_config())
        connection = MagicMock()
        cursor = connection.cursor.return_value
        with (
            patch(
                "planetscale_discovery.cloud.analyzers.snowflake_analyzer.HAS_SNOWFLAKE_LIBS",
                True,
            ),
            patch(
                "planetscale_discovery.cloud.analyzers.snowflake_analyzer.snowflake"
            ) as mock_sf,
        ):
            mock_sf.connector.connect.return_value = connection
            assert analyzer.authenticate() is True
        cursor.execute.assert_any_call("USE SECONDARY ROLES NONE")

    def test_secondary_roles_kept_when_opted_in(self):
        analyzer = SnowflakeAnalyzer(_config(use_secondary_roles=True))
        connection = MagicMock()
        with (
            patch(
                "planetscale_discovery.cloud.analyzers.snowflake_analyzer.HAS_SNOWFLAKE_LIBS",
                True,
            ),
            patch(
                "planetscale_discovery.cloud.analyzers.snowflake_analyzer.snowflake"
            ) as mock_sf,
        ):
            mock_sf.connector.connect.return_value = connection
            assert analyzer.authenticate() is True
        connection.cursor.assert_not_called()

    def test_account_context_recorded(self):
        analyzer = SnowflakeAnalyzer(_config())
        execute, _ = _routing_execute(
            [
                (
                    "CURRENT_ACCOUNT()",
                    [
                        {
                            "ACCOUNT_LOCATOR": "ABC12345",
                            "ACCOUNT_NAME": "MYACCT",
                            "ORGANIZATION_NAME": "MYORG",
                            "ACCOUNT_REGION": "AWS_US_WEST_2",
                            "ROLE": "PS_DISCOVERY",
                            "SECONDARY_ROLES": '{"roles":"","value":""}',
                        }
                    ],
                ),
                ("SHOW", list(SHOW_POSTGRES_INSTANCES_LIVE)),
            ]
        )
        with (
            patch.object(analyzer, "authenticate", return_value=True),
            patch.object(analyzer, "_execute", side_effect=execute),
        ):
            result = analyzer.analyze()
        account = result["account"]
        assert account["account_csp"] == "aws"
        assert account["account_cloud_region"] == "us-west-2"
        assert account["role"] == "PS_DISCOVERY"
        assert account["secondary_roles"] is None

    def test_empty_show_warns_about_operate_grants(self):
        analyzer = SnowflakeAnalyzer(_config())
        with (
            patch.object(analyzer, "authenticate", return_value=True),
            patch.object(analyzer, "_execute", return_value=[]),
        ):
            result = analyzer.analyze()
        assert result["summary"]["instance_count"] == 0
        assert any("OPERATE" in w["message"] for w in analyzer.warnings)

    def test_hidden_replica_warns(self):
        analyzer = SnowflakeAnalyzer(_config())
        execute, _ = _routing_execute(
            [
                ("SHOW", [SHOW_POSTGRES_INSTANCES_LIVE[0]]),
                ('"primary_instance"', _PRIMARY_DESCRIBE_WITH_POLICY),
            ]
        )
        with (
            patch.object(analyzer, "authenticate", return_value=True),
            patch.object(analyzer, "_execute", side_effect=execute),
        ):
            result = analyzer.analyze()
        assert result["summary"]["instance_count"] == 1
        assert any(
            "replica_1" in w["message"] and "cannot see" in w["message"]
            for w in analyzer.warnings
        )
        primary = result["resources"]["us-east-1"]["instances"][0]
        assert "replicas" not in primary

    def test_hidden_primary_warns(self):
        analyzer = SnowflakeAnalyzer(_config())
        execute, _ = _routing_execute([("SHOW", [SHOW_POSTGRES_INSTANCES_LIVE[1]])])
        with (
            patch.object(analyzer, "authenticate", return_value=True),
            patch.object(analyzer, "_execute", side_effect=execute),
        ):
            analyzer.analyze()
        assert any(
            "primary_instance" in w["message"] and "cannot see" in w["message"]
            for w in analyzer.warnings
        )

    def test_replica_with_filtered_origin_warns(self):
        replica = dict(SHOW_POSTGRES_INSTANCES_LIVE[1], origin=None)
        analyzer = SnowflakeAnalyzer(_config())
        execute, _ = _routing_execute([("SHOW", [replica])])
        with (
            patch.object(analyzer, "authenticate", return_value=True),
            patch.object(analyzer, "_execute", side_effect=execute),
        ):
            analyzer.analyze()
        assert any("hid its primary" in w["message"] for w in analyzer.warnings)


class TestRedactedProperties:
    def test_redacted_network_policy_is_not_a_name(self):
        describe = {"network_policy": "<redacted>"}
        inst = normalize_postgres_instance(SHOW_POSTGRES_INSTANCES_LIVE[0], describe)
        assert "network_policy" not in inst
        assert inst["network_policy_redacted"] is True
