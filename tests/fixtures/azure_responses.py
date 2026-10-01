"""
Mock Azure SDK responses for testing.

The azure-mgmt-* libraries return typed model objects rather than dicts, so
these builders produce attribute-bearing stand-ins. SimpleNamespace, not
MagicMock, so a misspelled attribute raises instead of passing silently.

Server models are flat (server.storage); azure-mgmt-network nests everything
under properties (vnet.properties.address_space).
"""

from types import SimpleNamespace


def _ns(**kwargs):
    """A stand-in for an Azure SDK model object."""
    return SimpleNamespace(**kwargs)


def _enum(value):
    """A stand-in for an Azure str-subclass enum.

    The wire string is on ``.value``; ``str()`` returns ``"Class.MEMBER"``.
    """

    class _FakeEnum(str):
        def __new__(cls, wire):
            obj = super().__new__(cls, wire)
            obj.value = wire
            return obj

        def __str__(self):
            return f"FakeEnum.{self.value.upper()}"

    return _FakeEnum(value)


def server_id(subscription="sub-1", resource_group="rg-db", provider=None, name="srv"):
    """Build an ARM resource ID, the only place a resource group appears."""
    provider = provider or "Microsoft.DBforPostgreSQL/flexibleServers"
    return (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
        f"/providers/{provider}/{name}"
    )


def make_pg_server(
    name="pg-prod",
    location="eastus",
    resource_group="rg-db",
    version="16",
    state="Ready",
    sku_name="Standard_D4ds_v5",
    sku_tier="GeneralPurpose",
    storage_size_gb=512,
    backup_retention_days=14,
    geo_redundant_backup="Enabled",
    ha_mode="ZoneRedundant",
    public_network_access="Disabled",
    delegated_subnet_resource_id="",
    replication_role="Primary",
    source_server_resource_id="",
    data_encryption_type="SystemManaged",
    include_sku=True,
    include_ha=True,
):
    """A PostgreSQL Flexible Server.

    ``include_sku`` / ``include_ha`` drop those sub-objects to None.
    """
    return _ns(
        id=server_id(resource_group=resource_group, name=name),
        name=name,
        location=location,
        version=_enum(version),
        minor_version="16.3",
        state=_enum(state),
        fully_qualified_domain_name=f"{name}.postgres.database.azure.com",
        availability_zone="1",
        replication_role=_enum(replication_role),
        replica_capacity=5,
        source_server_resource_id=source_server_resource_id,
        # On the real model but never populated by the API.
        administrator_login="pgadmin",
        administrator_login_password=None,
        tags={"env": "prod"},
        sku=(_ns(name=sku_name, tier=_enum(sku_tier)) if include_sku else None),
        storage=_ns(
            storage_size_gb=storage_size_gb,
            tier=_enum("P20"),
            iops=2300,
            auto_grow=_enum("Enabled"),
            throughput=None,
            type=None,
        ),
        backup=_ns(
            backup_retention_days=backup_retention_days,
            geo_redundant_backup=_enum(geo_redundant_backup),
            earliest_restore_date=None,
        ),
        high_availability=(
            _ns(
                mode=_enum(ha_mode),
                state=_enum("Healthy"),
                standby_availability_zone="2",
            )
            if include_ha
            else None
        ),
        network=_ns(
            public_network_access=_enum(public_network_access),
            delegated_subnet_resource_id=delegated_subnet_resource_id,
            # PostgreSQL spells this differently from MySQL.
            private_dns_zone_arm_resource_id="/subscriptions/sub-1/zone/pg",
        ),
        data_encryption=_ns(type=_enum(data_encryption_type)),
    )


def make_mysql_server(
    name="mysql-prod",
    location="eastus",
    resource_group="rg-db",
    version="8.0.21",
    state="Ready",
    sku_name="Standard_D2ds_v4",
    sku_tier="GeneralPurpose",
    storage_size_gb=128,
    backup_retention_days=7,
    geo_redundant_backup="Disabled",
    ha_mode="Disabled",
    public_network_access="Enabled",
    replication_role="Primary",
    source_server_resource_id="",
    data_encryption_type="",
):
    """A MySQL Flexible Server.

    Spells several fields differently from PostgreSQL: ``full_version``,
    ``auto_io_scaling``, ``storage_redundancy``, no ``arm`` DNS-zone infix.
    """
    return _ns(
        id=server_id(
            resource_group=resource_group,
            provider="Microsoft.DBforMySQL/flexibleServers",
            name=name,
        ),
        name=name,
        location=location,
        version=_enum(version),
        full_version="8.0.21",
        state=_enum(state),
        fully_qualified_domain_name=f"{name}.mysql.database.azure.com",
        availability_zone="1",
        replication_role=_enum(replication_role),
        replica_capacity=10,
        source_server_resource_id=source_server_resource_id,
        administrator_login="mysqladmin",
        administrator_login_password=None,
        tags={},
        sku=_ns(name=sku_name, tier=_enum(sku_tier)),
        storage=_ns(
            storage_size_gb=storage_size_gb,
            iops=360,
            auto_grow=_enum("Enabled"),
            auto_io_scaling=_enum("Disabled"),
            storage_redundancy=_enum("LocalRedundancy"),
        ),
        backup=_ns(
            backup_retention_days=backup_retention_days,
            geo_redundant_backup=_enum(geo_redundant_backup),
        ),
        high_availability=_ns(
            mode=_enum(ha_mode), state=_enum("NotEnabled"), standby_availability_zone=""
        ),
        network=_ns(
            public_network_access=_enum(public_network_access),
            delegated_subnet_resource_id="",
            private_dns_zone_resource_id="",
        ),
        # MySQL reports data_encryption only for a customer-managed key, so
        # empty means service-managed, not unencrypted.
        data_encryption=_ns(type=_enum(data_encryption_type)),
    )


def make_configuration(
    name="max_connections",
    value="500",
    source="user-override",
    is_read_only=False,
):
    """A server parameter. ``source`` of system-default means untouched.

    ``is_read_only`` passes through verbatim, so a test can supply a real bool
    (PostgreSQL) or the string "True"/"False" (MySQL).
    """
    return _ns(
        name=name,
        value=value,
        current_value=value,
        default_value="100",
        source=_enum(source),
        is_read_only=is_read_only,
        is_config_pending_restart=True,
    )


def make_firewall_rule(name="office", start="203.0.113.0", end="203.0.113.255"):
    """A server-level firewall rule."""
    return _ns(name=name, start_ip_address=start, end_ip_address=end)


def make_vnet(
    name="vnet-prod",
    location="eastus",
    resource_group="rg-net",
    address_prefixes=("10.0.0.0/16",),
    subnet_name="snet-db",
    subnet_prefix="10.0.1.0/24",
    delegation="Microsoft.DBforPostgreSQL/flexibleServers",
    nsg_id="/subscriptions/sub-1/nsg/nsg-db",
):
    """A virtual network, with fields nested under ``properties``."""
    subnet = _ns(
        name=subnet_name,
        properties=_ns(
            address_prefix=subnet_prefix,
            address_prefixes=[],
            delegations=(
                [_ns(properties=_ns(service_name=_enum(delegation)))]
                if delegation
                else []
            ),
            service_endpoints=[_ns(service=_enum("Microsoft.Storage"))],
            network_security_group=_ns(id=nsg_id) if nsg_id else None,
        ),
    )
    return _ns(
        id=f"/subscriptions/sub-1/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Network/virtualNetworks/{name}",
        name=name,
        location=location,
        tags={},
        properties=_ns(
            address_space=_ns(address_prefixes=list(address_prefixes)),
            subnets=[subnet],
            virtual_network_peerings=[
                _ns(
                    name="peer-to-hub",
                    properties=_ns(
                        peering_state=_enum("Connected"),
                        remote_virtual_network=_ns(id="/subscriptions/sub-1/vnet/hub"),
                        allow_virtual_network_access=True,
                        allow_forwarded_traffic=False,
                        allow_gateway_transit=False,
                    ),
                )
            ],
        ),
    )


def make_nsg(
    name="nsg-db",
    location="eastus",
    resource_group="rg-net",
    rule_source="Internet",
    include_default_rules=True,
):
    """A network security group.

    ``default_security_rules`` holds Azure's built-ins, which are not counted.
    """
    custom_rule = _ns(
        name="allow-postgres",
        properties=_ns(
            direction=_enum("Inbound"),
            access=_enum("Allow"),
            protocol=_enum("Tcp"),
            priority=100,
            source_address_prefix=rule_source,
            source_address_prefixes=[],
            destination_port_range="5432",
            destination_port_ranges=[],
        ),
    )
    default_rule = _ns(
        name="AllowVnetInBound",
        properties=_ns(
            direction=_enum("Inbound"),
            access=_enum("Allow"),
            protocol=_enum("*"),
            priority=65000,
            source_address_prefix="VirtualNetwork",
            source_address_prefixes=[],
            destination_port_range="*",
            destination_port_ranges=[],
        ),
    )
    return _ns(
        id=f"/subscriptions/sub-1/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Network/networkSecurityGroups/{name}",
        name=name,
        location=location,
        tags={},
        properties=_ns(
            security_rules=[custom_rule],
            default_security_rules=[default_rule] if include_default_rules else [],
        ),
    )
