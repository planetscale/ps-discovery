"""
Snowflake Postgres Cloud Infrastructure Analyzer

Inventories Snowflake Postgres instances via account SQL
(SHOW / DESCRIBE POSTGRES INSTANCE). Each SHOW/DESCRIBE row is one instance.
The analyzer does not invent Sharp Pulse / cross-instance metrics, shared HA
topology, or a replica list on the primary. It does not call AWS or GCP APIs,
even when the hostname contains an aws/azure/gcp location token.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple
import logging

try:
    import snowflake.connector

    HAS_SNOWFLAKE_LIBS = True
except ImportError:
    snowflake = None  # type: ignore[assignment]
    HAS_SNOWFLAKE_LIBS = False

from ...common.base_analyzer import CloudAnalyzer
from ...common.utils import generate_timestamp

# Hostname shape documented by Snowflake Postgres:
#   {id}.{account}.{region}.{aws|azure|gcp}.postgres.snowflake.app
_SNOWFLAKE_PG_HOST_RE = re.compile(
    r"^(?P<id>[^.]+)\.(?P<account>[^.]+)\.(?P<region>[^.]+)\."
    r"(?P<csp>aws|azure|gcp)\.postgres\.snowflake\.app(?:[:/].*)?$",
    re.IGNORECASE,
)

_TYPE_MAP = {
    "PRIMARY": "PRIMARY",
    "REPLICA": "READ_REPLICA",
    "READ_REPLICA": "READ_REPLICA",
}

# SHOW / DESCRIBE columns we treat as the same logical field.
_FIELD_ALIASES = {
    "name": ("name", "instance_name", "postgres_instance_name"),
    "type": ("type", "instance_type"),
    "host": ("host", "hostname", "endpoint"),
    "compute_family": ("compute_family", "instance_family", "sku"),
    "storage_size": ("storage_size", "storage_size_gb", "storage"),
    "postgres_version": ("postgres_version", "pg_version", "version"),
    "is_ha": ("is_ha", "ha", "high_availability"),
    "state": ("state", "status"),
    "origin": ("origin", "primary", "primary_name"),
    "csp": ("csp", "cloud", "cloud_provider", "provider"),
    "region": ("region", "cloud_region"),
}

# Fields we emit. Everything else from SHOW/DESCRIBE is dropped so we never
# invent metrics, SKUs, or multi-instance coupling.
INSTANCE_FIELDS = (
    "name",
    "type",
    "host",
    "compute_family",
    "storage_size_gb",
    "postgres_version",
    "is_ha",
    "state",
    "origin",
    "region",
    "csp",
)


def first_hostname_label(host: Optional[str]) -> Optional[str]:
    """Opaque id from `{id}.{account}.{region}.{csp}.postgres.snowflake.app`.

    This is not the customer-facing instance name. Liftoff Current hosting
    Name must use the SHOW ``name`` column, not this label.
    """
    if not host:
        return None
    label = str(host).strip().split(".")[0]
    return label or None


def is_hostname_id_label(name: Optional[str], host: Optional[str]) -> bool:
    """True when ``name`` is only the first label of a Snowflake PG hostname."""
    if not name or not host:
        return False
    if "postgres.snowflake.app" not in str(host).lower():
        return False
    label = first_hostname_label(host)
    return bool(label) and str(name).strip().lower() == label.lower()


def parse_snowflake_pg_hostname(
    host: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Return (csp, region) parsed from a Snowflake Postgres hostname.

    Returns (None, None) when the host is missing or does not match the
    documented shape. Never invents a CSP or region from a partial match.
    """
    if not host:
        return None, None
    match = _SNOWFLAKE_PG_HOST_RE.match(str(host).strip().lower())
    if not match:
        return None, None
    return match.group("csp").lower(), match.group("region")


def quote_snowflake_ident(name: str) -> str:
    """Double-quote a Snowflake identifier, preserving case."""
    return '"' + str(name).replace('"', '""') + '"'


def _lower_keys(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}


def _first_present(row: Dict[str, Any], aliases: Tuple[str, ...]) -> Any:
    for alias in aliases:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return None


def _as_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "t", "yes", "y", "1"}:
        return True
    if text in {"false", "f", "no", "n", "0"}:
        return False
    return None


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def map_instance_type(raw_type: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Map Snowflake SHOW type to the Liftoff inventory type.

    REPLICA becomes READ_REPLICA. PRIMARY stays PRIMARY. Unknown values are
    returned unchanged so we never invent PRIMARY / READ_REPLICA.
    """
    if raw_type is None or str(raw_type).strip() == "":
        return None, None
    raw = str(raw_type).strip()
    mapped = _TYPE_MAP.get(raw.upper())
    if mapped is None:
        return raw, raw
    return mapped, raw.upper()


def describe_rows_to_dict(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize DESCRIBE output into a single field dict.

    Snowflake may return either the same columns as SHOW (one row) or a
    property/value listing. Both shapes are accepted.
    """
    if not rows:
        return {}

    normalized = [_lower_keys(row) for row in rows]
    first = normalized[0]
    show_like = any(
        key in first for key in ("host", "compute_family", "postgres_version")
    )
    if show_like and ("name" in first or "type" in first):
        merged: Dict[str, Any] = {}
        for row in normalized:
            merged.update({k: v for k, v in row.items() if v not in (None, "")})
        return merged

    property_keys = ("property", "name", "key", "field")
    value_keys = ("value", "property_value", "propertyvalue")
    converted: Dict[str, Any] = {}
    for row in normalized:
        prop = _first_present(row, property_keys)
        if prop is None:
            continue
        value = None
        for key in value_keys:
            if key in row:
                value = row[key]
                break
        if value is None and "property_value" not in row:
            # Last resort: any leftover column that is not the property name.
            for key, candidate in row.items():
                if key not in property_keys and key not in ("property_type", "type"):
                    value = candidate
                    break
        converted[str(prop).lower()] = value
    return converted


def normalize_postgres_instance(
    row: Dict[str, Any], describe: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Turn one SHOW (+ optional DESCRIBE) row into one inventory instance.

    Each row stands alone. Missing compute_family stays None. This never
    invents a SKU, CPU/IOPS, Sharp Pulse metrics, or a replica list.
    """
    show = _lower_keys(row)
    # SHOW ``name`` is the human-readable instance name (your_primary_instance).
    # Liftoff Current hosting Name must use this field, not the hostname id.
    show_name = _as_text(show.get("name")) or _as_text(
        _first_present(show, ("instance_name", "postgres_instance_name"))
    )
    source = dict(show)
    if describe:
        for key, value in _lower_keys(describe).items():
            if value not in (None, ""):
                source[key] = value

    host = _as_text(_first_present(source, _FIELD_ALIASES["host"]))
    name = show_name
    if not name:
        name = _as_text(_first_present(source, _FIELD_ALIASES["name"]))
    # Never promote the opaque hostname label (first host token) to ``name``.
    if is_hostname_id_label(name, host):
        if show_name and not is_hostname_id_label(show_name, host):
            name = show_name
        else:
            name = None
    raw_type = _first_present(source, _FIELD_ALIASES["type"])
    mapped_type, _show_type_raw = map_instance_type(
        _as_text(raw_type) if raw_type is not None else None
    )
    compute_family = _as_text(_first_present(source, _FIELD_ALIASES["compute_family"]))
    storage_size_gb = _as_int(_first_present(source, _FIELD_ALIASES["storage_size"]))
    postgres_version = _as_text(
        _first_present(source, _FIELD_ALIASES["postgres_version"])
    )
    is_ha = _as_bool(_first_present(source, _FIELD_ALIASES["is_ha"]))
    state = _as_text(_first_present(source, _FIELD_ALIASES["state"]))
    origin = _as_text(_first_present(source, _FIELD_ALIASES["origin"]))
    csp = _as_text(_first_present(source, _FIELD_ALIASES["csp"]))
    region = _as_text(_first_present(source, _FIELD_ALIASES["region"]))

    parsed_csp, parsed_region = parse_snowflake_pg_hostname(host)
    if not csp:
        csp = parsed_csp
    if not region:
        region = parsed_region

    instance: Dict[str, Any] = {
        "name": name,
        "type": mapped_type,
        "host": host,
        "compute_family": compute_family,
        "storage_size_gb": storage_size_gb,
        "postgres_version": postgres_version,
        "state": state,
        "region": region,
        "csp": csp.lower() if isinstance(csp, str) else csp,
    }
    if is_ha is not None:
        instance["is_ha"] = is_ha
    # origin is a Snowflake column on the replica row. Do not invent it on
    # a primary, and do not attach a reverse replicas list.
    if origin:
        instance["origin"] = origin
    return {key: instance[key] for key in INSTANCE_FIELDS if key in instance}


class SnowflakeAnalyzer(CloudAnalyzer):
    """Analyzer for Snowflake Postgres account inventory."""

    def __init__(self, config: Any, logger: Optional[logging.Logger] = None):
        super().__init__(config, "snowflake", logger)
        self.connection = None

    def _config_value(self, name: str, *env_names: str) -> Optional[str]:
        value = getattr(self.config, name, None)
        if value not in (None, ""):
            text = str(value).strip()
            # Sample configs use ${ENV} as a reminder to inject secrets. If the
            # YAML still has the placeholder, fall through to the environment.
            if text and not (text.startswith("${") and text.endswith("}")):
                return text
        for env_name in env_names:
            env_value = os.environ.get(env_name)
            if env_value:
                return env_value
        return None

    def discover_resources(self) -> List[str]:
        """Return Snowflake Postgres instance names visible to the role."""
        try:
            if not self.authenticate():
                return []
            rows = self._show_postgres_instances()
            names = []
            for row in rows:
                name = _as_text(
                    _first_present(_lower_keys(row), _FIELD_ALIASES["name"])
                )
                if name:
                    names.append(name)
            return names
        except Exception as e:
            self.add_error("Failed to discover Snowflake Postgres instances", e)
            return []

    def authenticate(self) -> bool:
        """Authenticate to the Snowflake account (not the Postgres instance)."""
        if not HAS_SNOWFLAKE_LIBS:
            self.add_error(
                "Snowflake libraries not installed. Install with: "
                'pip install "ps-discovery[snowflake]"'
            )
            return False

        account = self._config_value("account", "SNOWFLAKE_ACCOUNT")
        user = self._config_value("user", "SNOWFLAKE_USER")
        if not account or not user:
            self.add_error(
                "Snowflake account and user are required. "
                "Set them in providers.snowflake or via SNOWFLAKE_ACCOUNT / "
                "SNOWFLAKE_USER.\n"
                "  Setup guide: https://github.com/planetscale/ps-discovery/"
                "blob/main/docs/providers/snowflake.md"
            )
            return False

        connect_kwargs: Dict[str, Any] = {
            "account": account,
            "user": user,
        }
        role = self._config_value("role", "SNOWFLAKE_ROLE")
        if role:
            connect_kwargs["role"] = role
        warehouse = self._config_value("warehouse", "SNOWFLAKE_WAREHOUSE")
        if warehouse:
            connect_kwargs["warehouse"] = warehouse

        authentication = (
            self._config_value("authentication", "SNOWFLAKE_AUTHENTICATOR")
            or "key_pair"
        ).lower()

        try:
            if authentication in {"key_pair", "keypair", "private_key"}:
                private_key = self._load_private_key()
                if private_key is None:
                    return False
                connect_kwargs["private_key"] = private_key
            elif authentication == "password":
                password = self._config_value("password", "SNOWFLAKE_PASSWORD")
                if not password:
                    self.add_error(
                        "Snowflake password authentication selected but no "
                        "password was provided. Set SNOWFLAKE_PASSWORD (do not "
                        "commit it to the config file)."
                    )
                    return False
                connect_kwargs["password"] = password
            elif authentication == "sso":
                # Interactive browser SSO. Works for an operator session; not
                # a fit for unattended discovery.
                connect_kwargs["authenticator"] = "externalbrowser"
            else:
                self.add_error(
                    f"Unsupported Snowflake authentication method: {authentication}. "
                    "Use key_pair (recommended), password, or sso."
                )
                return False

            self.connection = snowflake.connector.connect(**connect_kwargs)
            self.logger.info("Successfully authenticated with Snowflake")
            return True
        except Exception as e:
            self.add_error(
                "Snowflake authentication failed. Verify account locator, user, "
                "role, and key-pair or password.\n"
                "  Setup guide: https://github.com/planetscale/ps-discovery/"
                "blob/main/docs/providers/snowflake.md",
                e,
            )
            return False

    def _load_private_key(self) -> Optional[bytes]:
        path = self._config_value("private_key_path", "SNOWFLAKE_PRIVATE_KEY_PATH")
        if not path:
            self.add_error(
                "Snowflake key-pair authentication requires private_key_path "
                "in providers.snowflake or SNOWFLAKE_PRIVATE_KEY_PATH."
            )
            return None
        passphrase = self._config_value(
            "private_key_passphrase", "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"
        )
        try:
            from cryptography.hazmat.primitives import serialization

            with open(path, "rb") as key_file:
                private_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=passphrase.encode() if passphrase else None,
                )
            return private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        except Exception as e:
            self.add_error(
                f"Failed to load Snowflake private key from {path}. "
                "Check the path and optional passphrase.",
                e,
            )
            return None

    def analyze(self) -> Dict[str, Any]:
        """Run SHOW + DESCRIBE inventory and return provider results."""
        if not self.authenticate():
            return {
                "error": "Authentication failed",
                "timestamp": generate_timestamp(),
                "metadata": self.get_analysis_metadata(),
            }

        results: Dict[str, Any] = {
            "provider": "snowflake",
            "timestamp": generate_timestamp(),
            "resources": {},
            "summary": {},
            "metadata": self.get_analysis_metadata(),
        }

        try:
            if getattr(self.config, "account_inventory", True) is False:
                results["summary"] = {
                    "instance_count": 0,
                    "database_instances": 0,
                    "regions": [],
                    "note": "account_inventory is false; SHOW/DESCRIBE was skipped.",
                }
                return results

            instances = self._collect_instances()
            results["resources"] = self._group_by_region(instances)
            results["summary"] = self._generate_summary(instances)
            results["regions_analyzed"] = sorted(
                {
                    region
                    for region in (inst.get("region") for inst in instances)
                    if region
                }
            )
            self.logger.info(
                "Snowflake analysis completed. Found %s Postgres instance(s)",
                len(instances),
            )
        except Exception as e:
            self.add_error("Snowflake analysis failed", e)

        results["metadata"] = self.get_analysis_metadata()
        return results

    def _collect_instances(self) -> List[Dict[str, Any]]:
        rows = self._show_postgres_instances()
        wanted = self._requested_instance_names()
        instances: List[Dict[str, Any]] = []
        for row in rows:
            lowered = _lower_keys(row)
            name = _as_text(_first_present(lowered, _FIELD_ALIASES["name"]))
            if wanted is not None and name not in wanted:
                continue
            describe = self._describe_postgres_instance(name) if name else {}
            instances.append(normalize_postgres_instance(row, describe))
        return instances

    def _requested_instance_names(self) -> Optional[set]:
        if getattr(self.config, "discover_all", True):
            return None
        resources = getattr(self.config, "resources", None) or {}
        names = resources.get("postgres_instances") or []
        return {str(name) for name in names}

    def _show_postgres_instances(self) -> List[Dict[str, Any]]:
        return self._execute("SHOW POSTGRES INSTANCES")

    def _describe_postgres_instance(self, name: str) -> Dict[str, Any]:
        sql = f"DESCRIBE POSTGRES INSTANCE {quote_snowflake_ident(name)}"
        try:
            rows = self._execute(sql)
            return describe_rows_to_dict(rows)
        except Exception as e:
            self.add_warning(f"DESCRIBE POSTGRES INSTANCE failed for {name}: {e}")
            return {}

    def _execute(self, sql: str) -> List[Dict[str, Any]]:
        if self.connection is None:
            raise RuntimeError("Snowflake connection is not open")
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql)
            columns = (
                [col[0] for col in cursor.description] if cursor.description else []
            )
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _group_by_region(self, instances: List[Dict[str, Any]]) -> Dict[str, Any]:
        grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for instance in instances:
            region = instance.get("region") or "unknown"
            grouped.setdefault(region, {"instances": []})
            grouped[region]["instances"].append(instance)
        return grouped

    def _generate_summary(self, instances: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Counts only. These are tallies of independent rows, not a topology.
        primaries = [i for i in instances if i.get("type") == "PRIMARY"]
        replicas = [i for i in instances if i.get("type") == "READ_REPLICA"]
        regions = sorted({i.get("region") for i in instances if i.get("region")})
        return {
            "instance_count": len(instances),
            "database_instances": len(instances),
            "primary_count": len(primaries),
            "read_replica_count": len(replicas),
            "regions": regions,
        }
