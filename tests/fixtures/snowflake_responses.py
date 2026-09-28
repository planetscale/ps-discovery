"""
Mock Snowflake SHOW / DESCRIBE POSTGRES INSTANCE rows.

SHOW columns follow the documented inventory shape: name, owner, type, origin,
host, compute_family, authentication_authority, storage_size, postgres_version,
is_ha, state. Hosts are placeholders, not live endpoints.
"""

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

# DESCRIBE can come back as the same columns as SHOW (one row).
DESCRIBE_PRIMARY_ROW: List[Dict[str, Any]] = [SHOW_POSTGRES_INSTANCES_LIVE[0]]
DESCRIBE_REPLICA_ROW: List[Dict[str, Any]] = [SHOW_POSTGRES_INSTANCES_LIVE[1]]

# Alternate DESCRIBE shape: property / value listing.
DESCRIBE_PRIMARY_PROPERTIES: List[Dict[str, Any]] = [
    {"property": "name", "value": "primary_instance"},
    {"property": "type", "value": "PRIMARY"},
    {"property": "host", "value": PRIMARY_HOST},
    {"property": "compute_family", "value": "STANDARD_M"},
    {"property": "storage_size", "value": 100},
    {"property": "postgres_version", "value": "18"},
    {"property": "is_ha", "value": False},
    {"property": "state", "value": "READY"},
    {"property": "origin", "value": ""},
    {"property": "authentication_authority", "value": "POSTGRES"},
    {"property": "owner", "value": "ACCOUNTADMIN"},
]
