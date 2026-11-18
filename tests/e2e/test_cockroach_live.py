"""Live CockroachDB discovery checks against COMPAT.md rows.

Set CRDB_E2E_HOST / CRDB_E2E_PORT (and TLS vars for the secure cluster).
Optional CRDB_E2E_FREEZE_JSON is a frozen live_bytes snapshot for
tpcc / phantom_app / movr. compat_lab and compat_mr stay out of that baseline.
"""

import json
import os
from pathlib import Path

import pytest

from planetscale_discovery.database.cockroach_compat import (
    COMPAT_ROWS,
    COPY_PATH,
)
from tests.fixtures.crdb_size_baselines import (
    SIZE_ACCEPTANCE,
    SIZE_BASELINE_DATABASES,
    SIZE_BASELINE_DATABASES_LAB,
    SIZE_BASELINE_DATABASES_SECURE,
)
from planetscale_discovery.database.cockroach import restricts_internals_by_default
from planetscale_discovery.database.discovery import PostgreSQLDiscovery


def _params():
    host = os.environ.get("CRDB_E2E_HOST")
    if not host:
        pytest.skip("CRDB_E2E_HOST not set")
    params = {
        "host": host,
        "port": int(os.environ.get("CRDB_E2E_PORT", "26400")),
        "database": os.environ.get("CRDB_E2E_DB", "compat_lab"),
        "user": os.environ.get("CRDB_E2E_USER", "root"),
        "sslmode": os.environ.get("CRDB_E2E_SSLMODE", "disable"),
        "sslrootcert": os.environ.get("CRDB_E2E_SSLROOTCERT"),
        "sslcert": os.environ.get("CRDB_E2E_SSLCERT"),
        "sslkey": os.environ.get("CRDB_E2E_SSLKEY"),
        "connect_timeout": 30,
    }
    return {k: v for k, v in params.items() if v}


def _run():
    discovery = PostgreSQLDiscovery(_params())
    assert discovery.connect(), "failed to connect"
    return discovery.run_analysis(["config", "schema", "features"])


def test_live_engine_is_cockroachdb_not_postgres_13():
    results = _run()
    assert results["engine"] == "cockroachdb"
    version = results["connection_info"]["version_string"]
    assert "CockroachDB" in version
    assert "PostgreSQL 13" not in (results.get("flavor") or {}).get("version", "")
    flavor = results["flavor"]
    assert flavor["major"] in (24, 25, 26)
    if restricts_internals_by_default(flavor):
        assert results["connection_info"].get("allow_unsafe_internals") == "on"


def test_schema_comes_from_show_create_not_pg_dump():
    results = _run()
    schema = results["analysis_results"]["schema"]
    source = schema["schema_source"]
    assert source["pg_dump"] == "not working"
    assert "SHOW CREATE ALL TABLES" in source["method"]
    assert "SHOW TRIGGERS" in source["method"]
    assert "information_schema.routines" in source["method"]
    statements = source["show_create_all_tables"]
    assert statements, "SHOW CREATE ALL TABLES returned no statements"
    joined = " ".join(s.get("create_statement") or "" for s in statements)
    assert "CREATE TABLE" in joined
    assert COPY_PATH["schema_source"]["status"] == "show_create"


def test_every_compat_row_has_a_finding_or_was_unread_or_absent():
    results = _run()
    findings = results["analysis_results"]["features"]["compat_findings"]
    by_key = {f["key"]: f for f in findings}
    always = {row["key"] for row in COMPAT_ROWS if row["emit"] == "always"}
    assert always <= set(by_key)
    for finding in findings:
        assert finding["doc_url"].startswith("https://")
        assert finding["feature"]
        assert finding["action"]
        if finding["read_status"] != "ok":
            assert finding["read_error"]
            assert "none found" not in (finding["read_error"] or "").lower()


def test_compat_lab_objects_are_detected():
    if os.environ.get("CRDB_E2E_DB", "compat_lab") not in ("compat_lab", "compat_mr"):
        pytest.skip("not pointed at a compat database")
    results = _run()
    findings = {
        f["key"]: f for f in results["analysis_results"]["features"]["compat_findings"]
    }
    schema = results["analysis_results"]["schema"]
    db = os.environ.get("CRDB_E2E_DB", "compat_lab")
    if db == "compat_lab":
        expected = (
            "hash_sharded",
            "column_families",
            "row_level_ttl",
            "vector_type",
            "spatial_types",
            "trigram_index",
            "stored_computed",
            "virtual_computed",
            "on_update",
            "hidden_rowid",
            "serial_unique_rowid",
            "sequences",
            "enums",
            "udfs",
            "procedures",
            "triggers",
            "collated_strings",
            "string_bytes",
        )
        for key in expected:
            finding = findings.get(key)
            assert finding, f"COMPAT row {key} missing from findings"
            if finding["read_status"] != "ok":
                pytest.xfail(f"{key} couldn't read: {finding['read_error']}")
            assert finding["object_count"] >= 1, f"{key} found 0 objects"
        assert schema["trigger_analysis"], "SHOW TRIGGERS returned no triggers"
        assert any(t.get("trigger_definition") for t in schema["trigger_analysis"])
        routines = schema["schema_source"]["routines"]
        types = {r.get("routine_type") for r in routines}
        assert "FUNCTION" in types
        assert "PROCEDURE" in types or schema["procedure_analysis"]
    else:
        assert findings.get("multi_region_db", {}).get("object_count", 0) >= 1
        assert findings.get("regional_by_row", {}).get("object_count", 0) >= 1


def test_report_is_scoped_to_current_database():
    results = _run()
    schema = results["analysis_results"]["schema"]
    assert "size_baseline" not in schema
    current = results["connection_info"]["current_database"]
    tables = schema["table_analysis"]
    assert tables
    assert all(t.get("database_name") in (None, current) for t in tables)
    indexes = schema["index_analysis"]
    assert all(i.get("database_name") in (None, current) for i in indexes)
    findings = {
        f["key"]: f
        for f in results["analysis_results"]["features"].get("compat_findings", [])
    }
    multi = findings.get("multi_region_db")
    if multi:
        assert all((o.get("name") == current) for o in multi.get("objects") or [])
    inferred = results["analysis_results"]["features"].get("inferred_extensions") or []
    assert all(ext.get("name") != "pg_partman" for ext in inferred)


def test_size_baseline_excludes_compat_databases():
    results = _run()
    current = results["connection_info"]["current_database"]
    tables = [
        t
        for t in results["analysis_results"]["schema"]["table_analysis"]
        if t.get("database_name") in (None, current)
        and t.get("database_name")
        in SIZE_BASELINE_DATABASES_LAB
        + SIZE_BASELINE_DATABASES_SECURE
        + SIZE_BASELINE_DATABASES
    ]
    # Lab constants stay in tests, never in the customer JSON.
    assert "size_baseline" not in results["analysis_results"]["schema"]
    assert SIZE_ACCEPTANCE["lab"]["table_count"] == 24
    assert SIZE_ACCEPTANCE["secure"]["table_count"] == 34
    freeze_path = os.environ.get("CRDB_E2E_FREEZE_JSON")
    if not freeze_path:
        pytest.skip("CRDB_E2E_FREEZE_JSON not set")
    freeze = json.loads(Path(freeze_path).read_text())
    allowed = (
        SIZE_BASELINE_DATABASES_LAB
        + SIZE_BASELINE_DATABASES_SECURE
        + SIZE_BASELINE_DATABASES
    )
    tables = [
        t
        for t in results["analysis_results"]["schema"]["table_analysis"]
        if t.get("database_name") in allowed
    ]
    by_key = {
        f"{t['database_name']}.{t['schema_name']}.{t['table_name']}": t["live_bytes"]
        for t in freeze["tables"]
    }
    assert len(tables) == freeze["table_count"]
    for table in tables:
        key = f"{table['database_name']}.{table['schema_name']}.{table['table_name']}"
        expected = by_key[key]
        got = table["total_size_bytes"]
        if expected == 0:
            assert got == 0
            continue
        delta = abs(got - expected) / expected
        assert delta <= 0.05, f"{key} {got} vs frozen {expected} ({delta:.3%})"
