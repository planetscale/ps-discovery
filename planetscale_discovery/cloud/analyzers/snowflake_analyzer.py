from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
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

_DOCUMENTED_COLUMNS = {
    "name",
    "owner",
    "owner_role_type",
    "created_on",
    "updated_on",
    "type",
    "origin",
    "host",
    "privatelink_service_identifier",
    "compute_family",
    "authentication_authority",
    "storage_size",
    "storage_size_gb",
    "postgres_version",
    "postgres_settings",
    "is_ha",
    "high_availability",
    "retention_time",
    "state",
    "comment",
    "instance_protection",
    "network_policy",
    "replicas",
    "operations",
    "maintenance_window_start",
    "certificate",
}

_SECRET_KEY_RE = re.compile(r"password|secret|key|token", re.IGNORECASE)

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
    "network_policy",
    "network_policy_redacted",
    "privatelink_service_identifier",
    "postgres_settings",
    "maintenance_window_start",
    "instance_protection",
    "retention_time",
    "pending_operations",
    "created_on",
    "comment",
)

_REDACTED = "<redacted>"


def is_redacted(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == _REDACTED


def first_hostname_label(host: Optional[str]) -> Optional[str]:
    if not host:
        return None
    label = str(host).strip().split(".")[0]
    return label or None


def is_hostname_id_label(name: Optional[str], host: Optional[str]) -> bool:
    if not name or not host:
        return False
    if "postgres.snowflake.app" not in str(host).lower():
        return False
    label = first_hostname_label(host)
    if not label:
        return False
    return str(name).strip().lower() == label.lower()


def parse_snowflake_pg_hostname(
    host: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    if not host:
        return None, None
    match = _SNOWFLAKE_PG_HOST_RE.match(str(host).strip().lower())
    if not match:
        return None, None
    return match.group("csp").lower(), match.group("region")


def quote_snowflake_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _lower_keys(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}


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


def parse_replica_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    quoted = re.findall(r'"((?:[^"]|"")*)"', text)
    if quoted:
        return [q.replace('""', '"') for q in quoted if q.strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def parse_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _as_timestamp(value: Any) -> Optional[str]:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return _as_text(value)


def parse_account_region(value: Optional[str]) -> Dict[str, Optional[str]]:
    raw = _as_text(value)
    parsed: Dict[str, Optional[str]] = {"raw": raw, "csp": None, "region": None}
    if not raw:
        return parsed
    token = raw.split(".")[-1]
    match = re.match(r"^(AWS|AZURE|GCP)_(.+)$", token, re.IGNORECASE)
    if match:
        parsed["csp"] = match.group(1).lower()
        parsed["region"] = match.group(2).lower().replace("_", "-")
    return parsed


def _drop_secret_keys(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                continue
            cleaned[key] = _drop_secret_keys(item)
        return cleaned
    if isinstance(value, list):
        return [_drop_secret_keys(item) for item in value]
    return value


def _settings_for_report(value: Any) -> Any:
    parsed = _drop_secret_keys(parse_json_value(value))
    if parsed in (None, "", {}, []):
        return None
    return parsed


def map_instance_type(raw_type: Optional[str]) -> Optional[str]:
    if raw_type is None or str(raw_type).strip() == "":
        return None
    raw = str(raw_type).strip()
    return _TYPE_MAP.get(raw.upper(), raw)


def describe_rows_to_dict(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}

    normalized = [_lower_keys(row) for row in rows]
    first = normalized[0]
    if "property" in first and "value" in first:
        converted: Dict[str, Any] = {}
        for row in normalized:
            prop = row.get("property")
            if prop is None or str(prop).strip() == "":
                continue
            converted[str(prop).strip().lower()] = row.get("value")
        return converted

    if not _DOCUMENTED_COLUMNS.intersection(first):
        return {}

    merged: Dict[str, Any] = {}
    for row in normalized:
        merged.update({k: v for k, v in row.items() if v not in (None, "")})
    return merged


def normalize_postgres_instance(
    row: Dict[str, Any], describe: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    show = _lower_keys(row)
    show_name = _as_text(show.get("name"))
    source = dict(show)
    if describe:
        for key, value in _lower_keys(describe).items():
            if value not in (None, ""):
                source[key] = value

    host = _as_text(source.get("host"))
    describe_name = None
    if describe:
        describe_name = _as_text(_lower_keys(describe).get("name"))
    name = show_name or _as_text(source.get("name"))
    if is_hostname_id_label(name, host):
        if describe_name and not is_hostname_id_label(describe_name, host):
            name = describe_name
        else:
            name = None
    raw_type = source.get("type")
    mapped_type = map_instance_type(
        _as_text(raw_type) if raw_type is not None else None
    )
    compute_family = _as_text(source.get("compute_family"))
    storage_raw = source.get("storage_size_gb")
    if storage_raw in (None, ""):
        storage_raw = source.get("storage_size")
    storage_size_gb = _as_int(storage_raw)
    postgres_version = _as_text(source.get("postgres_version"))
    ha_raw = source.get("high_availability")
    if ha_raw in (None, ""):
        ha_raw = source.get("is_ha")
    is_ha = _as_bool(ha_raw)
    state = _as_text(source.get("state"))
    origin = _as_text(source.get("origin"))
    csp = _as_text(source.get("csp"))
    region = _as_text(source.get("region"))

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
    if origin:
        instance["origin"] = origin

    created_on = _as_timestamp(show.get("created_on")) or _as_timestamp(
        source.get("created_on")
    )
    network_policy = source.get("network_policy")
    optional = {
        "network_policy": (
            None if is_redacted(network_policy) else _as_text(network_policy)
        ),
        "network_policy_redacted": True if is_redacted(network_policy) else None,
        "privatelink_service_identifier": _as_text(
            source.get("privatelink_service_identifier")
        ),
        "postgres_settings": _settings_for_report(source.get("postgres_settings")),
        "maintenance_window_start": _as_int(source.get("maintenance_window_start")),
        "instance_protection": _as_bool(source.get("instance_protection")),
        "retention_time": _as_int(source.get("retention_time")),
        "created_on": created_on,
        "comment": _as_text(source.get("comment")),
    }
    for key, value in optional.items():
        if value is not None:
            instance[key] = value
    operations = parse_json_value(source.get("operations"))
    if operations:
        instance["pending_operations"] = operations
    return {key: instance[key] for key in INSTANCE_FIELDS if key in instance}


class SnowflakeAnalyzer(CloudAnalyzer):
    def __init__(self, config: Any, logger: Optional[logging.Logger] = None):
        super().__init__(config, "snowflake", logger)
        self.connection = None
        self._declared_replicas: Dict[str, List[str]] = {}

    def _config_value(self, name: str, *env_names: str) -> Optional[str]:
        value = getattr(self.config, name, None)
        if value not in (None, ""):
            return str(value).strip()
        for env_name in env_names:
            env_value = os.environ.get(env_name)
            if env_value:
                return env_value
        return None

    def discover_resources(self) -> List[str]:
        try:
            if not self.authenticate():
                return []
            rows = self._show_postgres_instances()
            names = []
            for row in rows:
                name = _as_text(_lower_keys(row).get("name"))
                if name:
                    names.append(name)
            return names
        except Exception as e:
            self.add_error("Failed to discover Snowflake Postgres instances", e)
            return []

    def authenticate(self) -> bool:
        if self.connection is not None:
            return True

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
                "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/snowflake.md"
            )
            return False

        connect_kwargs: Dict[str, Any] = {
            "account": account,
            "user": user,
        }
        role = self._config_value("role", "SNOWFLAKE_ROLE")
        if role:
            connect_kwargs["role"] = role

        authentication = (
            self._config_value("authentication", "SNOWFLAKE_AUTHENTICATION")
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
                        "password was provided. Set SNOWFLAKE_PASSWORD. "
                        "Do not put the password in the config file."
                    )
                    return False
                connect_kwargs["password"] = password
            elif authentication == "sso":
                connect_kwargs["authenticator"] = "externalbrowser"
            else:
                self.add_error(
                    f"Unsupported Snowflake authentication method: {authentication}. "
                    "Use key_pair (recommended), password, or sso."
                )
                return False

            self.connection = snowflake.connector.connect(**connect_kwargs)
            self.logger.info("Successfully authenticated with Snowflake")
            if role and not getattr(self.config, "use_secondary_roles", False):
                self._disable_secondary_roles()
            return True
        except Exception as e:
            self.add_error(
                "Snowflake authentication failed. Verify account locator, user, "
                "role, and key-pair or password.\n"
                "  Setup guide: https://github.com/planetscale/ps-discovery/blob/main/docs/providers/snowflake.md",
                e,
            )
            return False

    def _disable_secondary_roles(self) -> None:
        try:
            self._execute("USE SECONDARY ROLES NONE")
        except Exception as e:
            self.add_warning(
                "Could not disable secondary roles; inventory may include "
                f"instances visible only through other roles granted to the user: {e}"
            )

    def _collect_account_context(self) -> Dict[str, Any]:
        try:
            rows = self._execute(
                "SELECT CURRENT_ACCOUNT() AS account_locator, "
                "CURRENT_ACCOUNT_NAME() AS account_name, "
                "CURRENT_ORGANIZATION_NAME() AS organization_name, "
                "CURRENT_REGION() AS account_region, "
                "CURRENT_ROLE() AS role, "
                "CURRENT_SECONDARY_ROLES() AS secondary_roles"
            )
        except Exception as e:
            self.add_warning(f"Could not read Snowflake account context: {e}")
            return {}
        if not rows:
            return {}
        row = _lower_keys(rows[0])
        region = parse_account_region(row.get("account_region"))
        secondary = parse_json_value(row.get("secondary_roles"))
        if isinstance(secondary, dict):
            secondary = secondary.get("roles") or None
        return {
            "account_locator": _as_text(row.get("account_locator")),
            "account_name": _as_text(row.get("account_name")),
            "organization_name": _as_text(row.get("organization_name")),
            "account_region": region["raw"],
            "account_csp": region["csp"],
            "account_cloud_region": region["region"],
            "role": _as_text(row.get("role")),
            "secondary_roles": secondary or None,
        }

    def close(self) -> None:
        connection = self.connection
        self.connection = None
        if connection is None:
            return
        try:
            connection.close()
        except Exception as e:
            self.add_warning(f"Failed to close Snowflake connection: {e}")

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
            results["account"] = self._collect_account_context()
            instances = self._collect_instances()
            self._check_visibility(instances)
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
        self._declared_replicas = {}
        for row in rows:
            lowered = _lower_keys(row)
            name = _as_text(lowered.get("name"))
            if wanted is not None and name not in wanted:
                continue
            describe = self._describe_postgres_instance(name) if name else {}
            if name:
                self._declared_replicas[name] = parse_replica_list(
                    _lower_keys(describe).get("replicas")
                )
            instances.append(normalize_postgres_instance(row, describe))
        return instances

    def _requested_instance_names(self) -> Optional[set]:
        if getattr(self.config, "discover_all", True):
            return None
        resources = getattr(self.config, "resources", None) or {}
        names = resources.get("postgres_instances") or []
        wanted = {str(name) for name in names if str(name).strip()}
        if not wanted:
            self.add_warning(
                "discover_all is false but resources.postgres_instances is "
                "empty. No Snowflake Postgres instances will be inventoried. "
                "List instance names or set discover_all: true."
            )
        return wanted

    def _show_postgres_instances(self) -> List[Dict[str, Any]]:
        rows = self._execute("SHOW POSTGRES INSTANCES")
        if not rows:
            self.add_warning(
                "SHOW POSTGRES INSTANCES returned no rows. The role only sees "
                "instances it has OPERATE or OWNERSHIP on. Grant OPERATE on "
                "every instance, including each read replica."
            )
        return rows

    def _check_visibility(self, instances: List[Dict[str, Any]]) -> None:
        if not getattr(self.config, "discover_all", True):
            return
        visible = {i.get("name") for i in instances if i.get("name")}
        for inst in instances:
            replicas = self._declared_replicas.get(inst.get("name") or "", [])
            hidden = [r for r in replicas if r not in visible]
            if hidden:
                self.add_warning(
                    f"{inst.get('name')} lists read replica(s) {', '.join(hidden)} "
                    "that the role cannot see. Grant OPERATE on each replica."
                )
            if inst.get("type") != "READ_REPLICA":
                continue
            origin = inst.get("origin")
            if not origin:
                self.add_warning(
                    f"{inst.get('name')} is a read replica but Snowflake hid its "
                    "primary from this role. Grant OPERATE on the primary."
                )
            elif origin not in visible:
                self.add_warning(
                    f"{inst.get('name')} replicates from {origin}, which the role "
                    "cannot see. Grant OPERATE on the primary."
                )

    def _describe_postgres_instance(self, name: str) -> Dict[str, Any]:
        sql = f"DESCRIBE POSTGRES INSTANCE {quote_snowflake_ident(name)}"
        try:
            rows = self._execute(sql)
            converted = describe_rows_to_dict(rows)
            if rows and not converted:
                self.add_warning(
                    f"DESCRIBE POSTGRES INSTANCE for {name} returned an "
                    "unrecognized shape; using the SHOW row only."
                )
            return converted
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
