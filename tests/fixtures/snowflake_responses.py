from typing import Any, Dict, List

PRIMARY_HOST = "your-instance.your-org.us-east-1.aws.postgres.snowflake.app"
REPLICA_HOST = "your-replica.your-org.us-east-1.aws.postgres.snowflake.app"

SHOW_POSTGRES_INSTANCES_LIVE: List[Dict[str, Any]] = [
    {
        "name": "primary_instance",
        "owner": "ACCOUNTADMIN",
        "owner_role_type": "ROLE",
        "created_on": "2026-09-25 10:51:30.269 -0700",
        "updated_on": "2026-09-26 13:08:09.308 -0700",
        "type": "PRIMARY",
        "origin": "",
        "host": PRIMARY_HOST,
        "privatelink_service_identifier": "",
        "compute_family": "STANDARD_M",
        "authentication_authority": "POSTGRES",
        "storage_size": 100,
        "postgres_version": "18",
        "postgres_settings": "{}",
        "is_ha": False,
        "retention_time": 0,
        "state": "READY",
        "comment": "",
        "instance_protection": False,
    },
    {
        "name": "replica_1",
        "owner": "ACCOUNTADMIN",
        "owner_role_type": "ROLE",
        "created_on": "2026-09-26 13:05:33.219 -0700",
        "updated_on": "2026-09-26 13:08:09.310 -0700",
        "type": "REPLICA",
        "origin": "primary_instance",
        "host": REPLICA_HOST,
        "privatelink_service_identifier": "",
        "compute_family": "STANDARD_M",
        "authentication_authority": "POSTGRES",
        "storage_size": 100,
        "postgres_version": "18",
        "postgres_settings": "{}",
        "is_ha": False,
        "retention_time": 0,
        "state": "READY",
        "comment": "",
        "instance_protection": False,
    },
]

DESCRIBE_PRIMARY_ROW: List[Dict[str, Any]] = [SHOW_POSTGRES_INSTANCES_LIVE[0]]
DESCRIBE_REPLICA_ROW: List[Dict[str, Any]] = [SHOW_POSTGRES_INSTANCES_LIVE[1]]

_DESCRIBE_SETTINGS = '{"work_mem": "64MB", "api_token": "sekrit"}'


def _property_rows(fields: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"property": key, "value": value} for key, value in fields.items()]


DESCRIBE_PROPERTY_VALUE_PRIMARY: List[Dict[str, Any]] = _property_rows(
    {
        "name": "primary_instance",
        "owner": "ACCOUNTADMIN",
        "owner_role_type": "ROLE",
        "created_on": "2026-09-25 10:51:30.269 -0700",
        "updated_on": "2026-09-26 13:08:09.308 -0700",
        "type": "PRIMARY",
        "host": PRIMARY_HOST,
        "privatelink_service_identifier": "",
        "compute_family": "STANDARD_M",
        "storage_size_gb": 250,
        "postgres_version": "18",
        "postgres_settings": _DESCRIBE_SETTINGS,
        "high_availability": True,
        "authentication_authority": "POSTGRES",
        "maintenance_window_start": 4,
        "state": "READY",
        "comment": "",
        "origin": "",
        "replicas": '"replica_1"',
        "operations": "{ }",
        "network_policy": "PG_POLICY",
        "storage_integration": "",
        "certificate": "-----BEGIN CERTIFICATE-----",
        "instance_protection": False,
    }
)

DESCRIBE_PROPERTY_VALUE_REPLICA: List[Dict[str, Any]] = _property_rows(
    {
        "name": "replica_1",
        "owner": "ACCOUNTADMIN",
        "owner_role_type": "ROLE",
        "created_on": "2026-09-26 13:05:33.219 -0700",
        "updated_on": "2026-09-26 13:08:09.310 -0700",
        "type": "REPLICA",
        "host": REPLICA_HOST,
        "privatelink_service_identifier": "",
        "compute_family": "STANDARD_M",
        "storage_size_gb": 250,
        "postgres_version": "18",
        "postgres_settings": _DESCRIBE_SETTINGS,
        "high_availability": False,
        "authentication_authority": "POSTGRES",
        "maintenance_window_start": 4,
        "state": "READY",
        "comment": "",
        "origin": "primary_instance",
        "replicas": "",
        "operations": "{ }",
        "network_policy": "",
        "storage_integration": "",
        "certificate": "-----BEGIN CERTIFICATE-----",
        "instance_protection": False,
    }
)
