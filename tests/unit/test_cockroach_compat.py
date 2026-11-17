"""Unit tests for the COMPAT.md catalog and detectors."""

from planetscale_discovery.database.cockroach_compat import (
    APP_WORK,
    BLOCKER,
    COMPAT_ROWS,
    COPY_PATH,
    INFO,
    REWRITE,
    CompatDetector,
    classify_indexdef,
    group_findings,
    validate_catalog,
)
from planetscale_discovery.database.cockroach_privileges import (
    looks_unsupported,
    probe_discovery_privileges,
)
from tests.fixtures.crdb_size_baselines import SIZE_ACCEPTANCE


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, params=None):
        self.rows = self.conn.resolve(sql)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, handlers):
        self.handlers = handlers

    def cursor(self):
        return FakeCursor(self)

    def resolve(self, sql):
        text = " ".join(sql.split())
        for needle, rows in self.handlers:
            if needle in text:
                return rows
        return []


def test_catalog_has_all_47_compat_rows():
    assert validate_catalog() is None
    assert [row["id"] for row in COMPAT_ROWS] == list(range(1, 48))
    for row in COMPAT_ROWS:
        assert row["doc_url"].startswith("https://")
        assert row["severity"] in (BLOCKER, REWRITE, APP_WORK, INFO)
        assert row["detection_sql"]
        assert row["action"]
        assert row["emit"] in ("always", "if_found", "if_found_or_unread", "cluster")
        if row["detect"]:
            assert hasattr(CompatDetector, f"detect_{row['detect']}"), row["key"]


def test_copy_path_matches_compat_lab_facts():
    assert COPY_PATH["pg_dump"]["status"] == "not working"
    assert "tableoid" in COPY_PATH["pg_dump"]["note"]
    assert COPY_PATH["pgcopydb"]["status"] == "not working"
    assert COPY_PATH["pgcopydb"]["version"] == "0.18"
    assert "DEFERRABLE" in COPY_PATH["pgcopydb"]["note"]
    assert "tableoid" in COPY_PATH["pgcopydb"]["note"]
    assert "prefix: c" in COPY_PATH["pgcopydb"]["note"]
    assert COPY_PATH["copy_binary"]["status"] == "not working"
    assert COPY_PATH["copy_text"]["status"] == "works"
    assert COPY_PATH["copy_csv"]["status"] == "works"
    assert "COPY (SELECT" in COPY_PATH["copy_text"]["note"]
    assert "COPY (SELECT" in COPY_PATH["copy_csv"]["note"]
    assert "initial_scan='only'" in COPY_PATH["changefeed_initial_scan"]["note"]
    assert "SHOW CREATE ALL TABLES" in COPY_PATH["schema_source"]["note"]
    assert "SHOW TRIGGERS" in COPY_PATH["schema_source"]["note"]
    assert "information_schema.routines" in COPY_PATH["schema_source"]["note"]
    assert "::STRING" in COPY_PATH["collated_strings"]["note"]
    assert "explicit column list" in COPY_PATH["computed_columns"]["note"]
    assert COPY_PATH["migration_path"]["status"] == "downtime_copy_cutover"
    assert "not provided" in COPY_PATH["migration_path"]["note"]
    assert SIZE_ACCEPTANCE["lab"]["table_count"] == 24
    assert SIZE_ACCEPTANCE["secure"]["table_count"] == 34


def test_changefeed_and_schema_actions_match_compat():
    by_id = {row["id"]: row for row in COMPAT_ROWS}
    assert "initial_scan='only'" in by_id[42]["action"]
    assert "pg_dump does not work" in by_id[43]["action"]
    assert "explicit column list" in by_id[18]["action"]
    assert "COPY" in by_id[19]["action"]
    assert "::STRING" in by_id[34]["action"]
    assert "check_crdb_internal" in by_id[6]["detection_sql"]
    assert "vector_cosine_ops" in by_id[10]["action"]


def test_classify_indexdef_vector_hash_spatial_trigram():
    assert (
        classify_indexdef("CREATE INDEX i ON t USING cspann (embedding)")["kind"]
        == "vector"
    )
    assert (
        classify_indexdef("CREATE INDEX i ON t USING hnsw (embedding vector_l2_ops)")[
            "kind"
        ]
        == "vector"
    )
    assert (
        classify_indexdef("CREATE INDEX i ON t USING HASH (id)")["kind"]
        == "hash-sharded"
    )
    assert (
        "check_crdb_internal"
        in classify_indexdef("CREATE INDEX i ON t USING HASH (id)")["rewrite"]
    )
    assert (
        classify_indexdef("CREATE INDEX i ON t USING gin (name gin_trgm_ops)")["kind"]
        == "trigram"
    )
    assert (
        classify_indexdef("CREATE INDEX i ON t USING gin (geom)")["kind"] == "inverted"
    )
    assert (
        classify_indexdef("CREATE INDEX i ON t (email) STORING (name)")["kind"]
        == "covering"
    )


def test_group_findings_also_app_work():
    findings = [
        {
            "bucket": "rewrite",
            "severity": REWRITE,
            "also_app_work": True,
            "key": "serial_unique_rowid",
        }
    ]
    grouped = group_findings(findings)
    assert len(grouped["rewrite"]) == 1
    assert len(grouped["app_work"]) == 1
    assert grouped["app_work"][0]["severity"] == APP_WORK


def test_detect_hash_sharded_from_check_constraint_only():
    conn = FakeConn(
        [
            (
                "FROM crdb_internal.table_indexes",
                [],
            ),
            (
                "FROM pg_indexes",
                [],
            ),
            (
                "FROM pg_constraint",
                [
                    {
                        "schema_name": "public",
                        "table_name": "c_events",
                        "conname": "check_crdb_internal_ts_shard_8",
                    }
                ],
            ),
            (
                "FROM information_schema.columns",
                [
                    {
                        "database_name": "compat_lab",
                        "schema_name": "public",
                        "table_name": "c_events",
                        "column_name": "crdb_internal_ts_shard_8",
                        "crdb_sql_type": "INT8",
                        "column_default": None,
                        "is_generated": "ALWAYS",
                        "generation_expression": "mod(...)",
                        "is_hidden": "YES",
                        "data_type": "bigint",
                    }
                ],
            ),
        ]
    )
    detector = CompatDetector(conn)
    objects = detector.detect_hash_sharded()
    names = [o["name"] for o in objects]
    assert "public.c_events.check_crdb_internal_ts_shard_8" in names
    assert "public.c_events.crdb_internal_ts_shard_8" in names


def test_detect_row_level_ttl_maps_cluster_wide_schedule():
    conn = FakeConn(
        [
            (
                "FROM pg_class",
                [],
            ),
            (
                "SHOW SCHEDULES",
                [{"label": "row-level-ttl: c_sessions [106]"}],
            ),
            (
                "FROM crdb_internal.tables",
                [
                    {
                        "table_id": 106,
                        "database_name": "compat_lab",
                        "schema_name": "public",
                        "name": "c_sessions",
                    }
                ],
            ),
            (
                "SELECT current_database()",
                [{"current_database": "compat_lab"}],
            ),
        ]
    )
    detector = CompatDetector(conn)
    objects = detector.detect_row_level_ttl()
    assert objects[0]["name"] == "public.c_sessions"
    assert objects[0]["database"] == "compat_lab"


def test_show_triggers_is_schema_qualified():
    seen = []

    class RecordingConn(FakeConn):
        def resolve(self, sql):
            seen.append(" ".join(sql.split()))
            return super().resolve(sql)

    conn = RecordingConn(
        [
            (
                "FROM information_schema.tables",
                [
                    {
                        "database_name": "compat_lab",
                        "schema_name": "public",
                        "table_name": "c_wide",
                    }
                ],
            ),
            (
                "SHOW TRIGGERS FROM",
                [{"trigger_name": "c_wide_touch", "enabled": True}],
            ),
        ]
    )
    objects = CompatDetector(conn).detect_triggers()
    assert any('SHOW TRIGGERS FROM "public"."c_wide"' in s for s in seen)
    assert objects[0]["name"] == "public.c_wide.c_wide_touch"


def test_always_emit_rows_appear_even_when_empty():
    conn = FakeConn([])
    detector = CompatDetector(conn)
    result = detector.detect()
    keys = {f["key"] for f in result["compat_findings"]}
    for key in (
        "trigram_ops_gap",
        "int_is_int8",
        "upsert",
        "as_of_system_time",
        "serializable_retries",
        "integer_division",
        "jsonpath",
    ):
        assert key in keys
    assert "create_extension_noop" not in keys
    assert "changefeeds" not in keys
    assert "logical_data_replication" not in keys
    assert result["compat_source"] == "COMPAT.md"
    assert "cluster_context" in result


def test_zone_over_replicas_flagged_against_node_count():
    conn = FakeConn(
        [
            (
                "SHOW ZONE CONFIGURATIONS",
                [
                    {
                        "target": "DATABASE mr_app",
                        "raw_config_sql": "ALTER DATABASE mr_app CONFIGURE ZONE USING num_replicas = 5",
                    }
                ],
            ),
            (
                "FROM crdb_internal.gossip_nodes",
                [{"count": 3}],
            ),
        ]
    )
    objects = CompatDetector(conn).detect_zone_partition()
    assert objects[0]["over_replicated"] is True
    assert objects[0]["num_replicas"] == 5
    assert objects[0]["node_count"] == 3


def test_v24_vector_index_and_rls_are_absent_not_unread():
    class BoomConn(FakeConn):
        def resolve(self, sql):
            text = " ".join(sql.split())
            if "FROM pg_indexes" in text or "relrowsecurity" in text:
                raise RuntimeError("unimplemented: vector indexes / RLS")
            return super().resolve(sql)

    detector = CompatDetector(BoomConn([]))
    result = detector.detect()
    by_key = {f["key"]: f for f in result["compat_findings"]}
    assert "vector_index" not in by_key or by_key["vector_index"]["read_status"] == "ok"
    assert "rls" not in by_key or by_key["rls"]["read_status"] == "ok"
    assert looks_unsupported(RuntimeError("unimplemented: RLS"))


def test_missing_viewjob_marks_jobs_unread():
    conn = FakeConn(
        [
            (
                "SHOW SYSTEM GRANTS",
                [{"privilege_type": "VIEWACTIVITYREDACTED"}],
            ),
            (
                "is_admin()",
                [{"is_admin": False}],
            ),
            (
                "SELECT job_id FROM [SHOW JOBS]",
                [],
            ),
            (
                "SHOW CLUSTER SETTING",
                [{"kv.rangefeed.enabled": False}],
            ),
            (
                "SHOW ROLES",
                [{"role_name": "discovery_ro", "options": ""}],
            ),
            (
                "SELECT current_user",
                [{"current_user": "discovery_ro"}],
            ),
            (
                "FROM crdb_internal.gossip_nodes",
                [{"n": 1}],
            ),
        ]
    )
    errors = []
    probe = probe_discovery_privileges(
        conn, add_error=lambda m, *a, **k: errors.append(m)
    )
    assert any(g["privilege"] == "VIEWJOB" for g in probe["gaps"])
    assert probe["jobs_status"] == "couldn't read"
    assert probe["is_admin"] is False
    assert errors

    detector = CompatDetector(conn)
    result = detector.detect()
    by_key = {f["key"]: f for f in result["compat_findings"]}
    assert "logical_data_replication" not in by_key
    assert "changefeeds" not in by_key
    unread = {row["key"]: row for row in result["cluster_context"]["unread"]}
    assert unread["logical_data_replication"]["read_status"] == "couldn't read"
    assert unread["changefeeds"]["read_status"] == "couldn't read"


def test_admin_root_does_not_get_false_privilege_gaps():
    conn = FakeConn(
        [
            ("SHOW SYSTEM GRANTS", []),
            ("is_admin()", [{"is_admin": True}]),
            ("SELECT job_id FROM [SHOW JOBS]", []),
            ("SHOW CLUSTER SETTING", [{"kv.rangefeed.enabled": False}]),
            (
                "SHOW ROLES",
                [{"username": "root", "options": "", "member_of": ["admin"]}],
            ),
            ("SELECT current_user", [{"current_user": "root"}]),
            ("FROM crdb_internal.gossip_nodes", [{"n": 3}]),
        ]
    )
    probe = probe_discovery_privileges(conn)
    assert probe["is_admin"] is True
    assert probe["gaps"] == []
    assert probe["jobs_status"] == "ok"


def test_system_zones_and_upserts_are_filtered():
    conn = FakeConn(
        [
            (
                "SHOW ZONE CONFIGURATIONS",
                [
                    {"target": "RANGE meta", "raw_config_sql": "num_replicas = 5"},
                    {"target": "RANGE liveness", "raw_config_sql": "num_replicas = 5"},
                    {
                        "target": "DATABASE tpcc",
                        "raw_config_sql": "ALTER DATABASE tpcc CONFIGURE ZONE USING num_replicas = 3",
                    },
                ],
            ),
            ("FROM crdb_internal.gossip_nodes", [{"count": 3}]),
            ("SELECT current_database()", [{"current_database": "tpcc"}]),
            (
                "FROM crdb_internal.statement_statistics",
                [
                    {
                        "query": "UPSERT INTO system.public.jobs VALUES (1)",
                        "database": "system",
                    },
                    {
                        "query": "UPSERT INTO public.warehouse VALUES (1)",
                        "database": "tpcc",
                    },
                    {
                        "query": "UPSERT INTO crdb_features.public.t VALUES (1)",
                        "database": "crdb_features",
                    },
                ],
            ),
            (
                "FROM information_schema.columns",
                [
                    {
                        "database_name": "tpcc",
                        "schema_name": "public",
                        "table_name": "warehouse",
                        "column_name": "geom",
                        "crdb_sql_type": "GEOMETRY",
                        "data_type": "USER-DEFINED",
                    }
                ],
            ),
            (
                "FROM pg_type",
                [
                    {"oid": 90000, "typname": "geometry"},
                    {"oid": 90001, "typname": "vector"},
                    {"oid": 90002, "typname": "geography"},
                    {"oid": 90010, "typname": "crdb_internal_region"},
                    {"oid": 90100, "typname": "crdb_internal_unique_id"},
                ],
            ),
        ]
    )
    detector = CompatDetector(conn)
    zones = detector.detect_zone_partition()
    assert all("RANGE meta" not in (z.get("name") or "") for z in zones)
    assert all("RANGE liveness" not in (z.get("name") or "") for z in zones)
    assert any(z.get("name") == "DATABASE tpcc" for z in zones)

    upserts = detector.detect_upsert()
    assert [u["name"] for u in upserts] == ["UPSERT INTO public.warehouse VALUES (1)"]

    oids = detector.detect_high_oids()
    names = {o.get("name") for o in oids}
    assert "geometry" in names
    assert "vector" not in names
    assert "geography" not in names
    assert "crdb_internal_region" not in names


def test_inferred_extensions_ignore_system_zones():
    finding = {
        "key": "zone_partition",
        "read_status": "ok",
        "object_count": 2,
        "extension": {"name": "pg_partman", "status": "supported"},
    }
    ttl = {
        "key": "row_level_ttl",
        "read_status": "ok",
        "object_count": 1,
        "extension": {"name": "pg_cron", "status": "supported"},
    }
    vector = {
        "key": "vector_type",
        "read_status": "ok",
        "object_count": 1,
        "extension": {"name": "vector", "status": "supported"},
    }
    inferred = CompatDetector(FakeConn([]))._infer_extensions([finding, ttl, vector])
    names = {ext["name"] for ext in inferred}
    assert "pg_partman" not in names
    assert "pg_cron" in names
    assert "vector" in names


def test_role_settings_suppress_read_only_warning():
    warnings = []
    conn = FakeConn(
        [
            ("SHOW SYSTEM GRANTS", [{"privilege_type": "VIEWJOB"}]),
            ("is_admin()", [{"is_admin": False}]),
            ("SELECT job_id FROM [SHOW JOBS]", [{"job_id": 1}]),
            ("SHOW CLUSTER SETTING", [{"kv.rangefeed.enabled": False}]),
            ("FROM crdb_internal.gossip_nodes", [{"n": 1}]),
            ("SHOW ROLES", [{"role_name": "discovery_ro", "options": ""}]),
            ("SELECT current_user", [{"current_user": "discovery_ro"}]),
            (
                "FROM system.database_role_settings",
                [{"settings": ["default_transaction_read_only=on"]}],
            ),
        ]
    )
    probe = probe_discovery_privileges(
        conn, add_warning=lambda m, *a, **k: warnings.append(m)
    )
    assert probe["default_transaction_read_only"] is True
    assert not any("default_transaction_read_only" in w for w in warnings)


def test_viewactivity_copy_does_not_claim_silent_zero_sizes():
    class BoomGossip(FakeConn):
        def resolve(self, sql):
            text = " ".join(sql.split())
            if "gossip_nodes" in text:
                raise RuntimeError("permission denied")
            return super().resolve(sql)

    conn = BoomGossip(
        [
            ("SHOW SYSTEM GRANTS", []),
            ("is_admin()", [{"is_admin": False}]),
            ("SELECT job_id FROM [SHOW JOBS]", []),
            ("SHOW ROLES", [{"role_name": "discovery_ro", "options": ""}]),
            ("SELECT current_user", [{"current_user": "discovery_ro"}]),
        ]
    )
    errors = []
    probe = probe_discovery_privileges(
        conn, add_error=lambda m, *a, **k: errors.append(m)
    )
    text = " ".join(errors)
    assert "VIEWACTIVITYREDACTED" in text or any(
        g["privilege"] == "VIEWACTIVITYREDACTED" for g in probe["gaps"]
    )
    assert "silent zero" not in text.lower()


def test_multi_region_includes_secondary_and_survival():
    conn = FakeConn(
        [
            (
                "SHOW DATABASES",
                [
                    {
                        "database_name": "mr_app",
                        "primary_region": "us-east-1",
                        "secondary_region": "us-west-2",
                        "regions": "{us-east-1,us-west-2,eu-west-1}",
                        "survival_goal": "REGION",
                    }
                ],
            ),
            (
                "SHOW SUPER REGIONS",
                [{"super_region_name": "usa"}],
            ),
        ]
    )
    objects = CompatDetector(conn).detect_multi_region_db()
    assert objects[0]["name"] == "mr_app"
    assert objects[0]["secondary_region"] == "us-west-2"
    assert objects[0]["survival_goal"] == "REGION"
    assert "usa" in objects[0]["super_regions"]


def test_multi_region_keeps_the_connected_defaultdb():
    conn = FakeConn(
        [
            ("SELECT current_database()", [{"current_database": "defaultdb"}]),
            (
                "SHOW DATABASES",
                [
                    {
                        "database_name": "defaultdb",
                        "primary_region": "us-east-1",
                        "secondary_region": None,
                        "regions": "{us-east-1,us-west-2}",
                        "survival_goal": "ZONE",
                    }
                ],
            ),
        ]
    )
    objects = CompatDetector(conn).detect_multi_region_db()
    assert [o["name"] for o in objects] == ["defaultdb"]


def _tpcc_mr_cluster(current):
    return FakeConn(
        [
            ("SELECT current_database()", [{"current_database": current}]),
            ("SHOW SYSTEM GRANTS", [{"privilege_type": "VIEWJOB"}]),
            ("SELECT job_id FROM [SHOW JOBS]", [{"job_id": 1}]),
            (
                "SHOW ROLES",
                [{"username": "root", "options": "", "member_of": ["admin"]}],
            ),
            ("SELECT current_user", [{"current_user": "root"}]),
            (
                "FROM crdb_internal.create_statements",
                [
                    {
                        "database_name": "tpcc",
                        "schema_name": "public",
                        "descriptor_name": "warehouse",
                        "descriptor_type": "table",
                        "create_statement": "CREATE TABLE warehouse (id INT)",
                    },
                    {
                        "database_name": "mr_app",
                        "schema_name": "public",
                        "descriptor_name": "regions",
                        "descriptor_type": "table",
                        "create_statement": "CREATE TABLE regions (id INT) LOCALITY REGIONAL BY ROW",
                    },
                ],
            ),
            (
                "SHOW DATABASES",
                [
                    {
                        "database_name": "tpcc",
                        "primary_region": None,
                        "regions": None,
                        "survival_goal": None,
                    },
                    {
                        "database_name": "mr_app",
                        "primary_region": "us-east-1",
                        "secondary_region": "us-west-2",
                        "regions": "{us-east-1,us-west-2}",
                        "survival_goal": "ZONE",
                    },
                ],
            ),
            (
                "FROM crdb_internal.tables",
                [
                    {
                        "database_name": "tpcc",
                        "schema_name": "public",
                        "name": "warehouse",
                        "locality": None,
                    },
                    {
                        "database_name": "mr_app",
                        "schema_name": "public",
                        "name": "regions",
                        "locality": "REGIONAL BY ROW",
                    },
                ],
            ),
            (
                "SHOW ZONE CONFIGURATIONS",
                [
                    {"target": "RANGE meta", "raw_config_sql": "num_replicas = 5"},
                    {"target": "RANGE liveness", "raw_config_sql": "num_replicas = 5"},
                    {"target": "RANGE default", "raw_config_sql": "num_replicas = 3"},
                    {
                        "target": "DATABASE tpcc",
                        "raw_config_sql": "ALTER DATABASE tpcc CONFIGURE ZONE USING num_replicas = 3",
                    },
                    {
                        "target": "DATABASE mr_app",
                        "raw_config_sql": "ALTER DATABASE mr_app CONFIGURE ZONE USING num_replicas = 5",
                    },
                    {
                        "target": "PARTITION p1 OF INDEX crdb_features.public.t_partitioned@t_partitioned_pkey",
                        "raw_config_sql": "num_replicas = 3",
                    },
                    {
                        "target": "PARTITION us_east OF INDEX mr_app.public.users_rbr@users_rbr_pkey",
                        "raw_config_sql": "num_replicas = 3",
                    },
                    {
                        "target": "TABLE mr_app.public.users_rbr",
                        "raw_config_sql": "num_replicas = 3",
                    },
                ],
            ),
            (
                "FROM crdb_internal.statement_statistics",
                [
                    {
                        "query": "UPSERT INTO public.warehouse VALUES (1)",
                        "database": "tpcc",
                    },
                    {
                        "query": "UPSERT INTO mr_app.public.regions VALUES (1)",
                        "database": "mr_app",
                    },
                ],
            ),
            (
                "FROM information_schema.columns",
                [
                    {
                        "database_name": "tpcc",
                        "schema_name": "public",
                        "table_name": "warehouse",
                        "column_name": "id",
                        "crdb_sql_type": "INT8",
                        "data_type": "bigint",
                    },
                    {
                        "database_name": "mr_app",
                        "schema_name": "public",
                        "table_name": "regions",
                        "column_name": "loc",
                        "crdb_sql_type": "GEOMETRY",
                        "data_type": "USER-DEFINED",
                    },
                ],
            ),
            (
                "FROM pg_type",
                [
                    {"oid": 90000, "typname": "geometry"},
                    {"oid": 90001, "typname": "vector"},
                    {"oid": 90002, "typname": "geography"},
                ],
            ),
            (
                "FROM crdb_internal.table_indexes",
                [
                    {
                        "database_name": "tpcc",
                        "schema_name": "public",
                        "table_name": "warehouse",
                        "index_name": "warehouse_hash",
                        "is_sharded": True,
                    },
                    {
                        "database_name": "mr_app",
                        "schema_name": "public",
                        "table_name": "regions",
                        "index_name": "regions_hash",
                        "is_sharded": True,
                    },
                ],
            ),
            (
                "SELECT job_id, description, status FROM [SHOW CHANGEFEED JOBS]",
                [
                    {
                        "job_id": 9,
                        "description": "CHANGEFEED FOR TABLE mr_app.public.regions",
                        "status": "running",
                    }
                ],
            ),
            (
                "SHOW CLUSTER SETTING kv.rangefeed.enabled",
                [{"kv.rangefeed.enabled": False}],
            ),
            ("FROM crdb_internal.gossip_nodes", [{"count": 3}]),
        ]
    )


def test_two_databases_from_same_cluster_are_scoped():
    tpcc = CompatDetector(_tpcc_mr_cluster("tpcc")).detect()
    mr = CompatDetector(_tpcc_mr_cluster("mr_app")).detect()

    tpcc_keys = {f["key"] for f in tpcc["compat_findings"]}
    mr_keys = {f["key"] for f in mr["compat_findings"]}
    assert "multi_region_db" not in tpcc_keys
    assert "regional_by_row" not in tpcc_keys
    assert "multi_region_db" in mr_keys
    assert "regional_by_row" in mr_keys
    assert "changefeeds" not in tpcc_keys
    assert "changefeeds" not in mr_keys
    assert "logical_data_replication" not in tpcc_keys
    assert "logical_data_replication" not in mr_keys

    tpcc_zones = next(
        f for f in tpcc["compat_findings"] if f["key"] == "zone_partition"
    )
    mr_zones = next(f for f in mr["compat_findings"] if f["key"] == "zone_partition")
    assert "DATABASE tpcc" in tpcc_zones["object_names"]
    assert "DATABASE mr_app" in mr_zones["object_names"]
    assert all("crdb_features" not in n for n in tpcc_zones["object_names"])
    assert all("crdb_features" not in n for n in mr_zones["object_names"])
    assert any("users_rbr" in n for n in mr_zones["object_names"])
    assert all("users_rbr" not in n for n in tpcc_zones["object_names"])
    assert all(
        "RANGE" not in n for n in tpcc_zones["object_names"] + mr_zones["object_names"]
    )

    tpcc_oids = {
        o["name"] for o in CompatDetector(_tpcc_mr_cluster("tpcc")).detect_high_oids()
    }
    mr_oids = {
        o["name"] for o in CompatDetector(_tpcc_mr_cluster("mr_app")).detect_high_oids()
    }
    assert tpcc_oids == set()
    assert mr_oids == {"geometry"}

    tpcc_upserts = [
        o["name"] for o in CompatDetector(_tpcc_mr_cluster("tpcc")).detect_upsert()
    ]
    mr_upserts = [
        o["name"] for o in CompatDetector(_tpcc_mr_cluster("mr_app")).detect_upsert()
    ]
    assert tpcc_upserts == ["UPSERT INTO public.warehouse VALUES (1)"]
    assert mr_upserts == ["UPSERT INTO mr_app.public.regions VALUES (1)"]

    tpcc_hash = CompatDetector(_tpcc_mr_cluster("tpcc")).detect_hash_sharded()
    mr_hash = CompatDetector(_tpcc_mr_cluster("mr_app")).detect_hash_sharded()
    assert [o["name"] for o in tpcc_hash] == ["public.warehouse.warehouse_hash"]
    assert [o["name"] for o in mr_hash] == ["public.regions.regions_hash"]

    assert tpcc["cluster_context"]["changefeeds"]
    assert mr["cluster_context"]["changefeeds"]
    assert tpcc["cluster_context"]["node_count"] == 3
    assert "kv.rangefeed.enabled" in tpcc["cluster_context"]["cluster_settings"]


def test_pg_cron_note_plans_dashboard_enable_and_restart():
    spec = next(row for row in COMPAT_ROWS if row["key"] == "row_level_ttl")
    note = spec["action"]
    assert "PlanetScale dashboard" in note
    assert "restart" in note
    assert "cutover" in note
    from planetscale_discovery.database.cockroach_compat import PS_EXTENSIONS

    cron = PS_EXTENSIONS["pg_cron"]["note"]
    assert "PlanetScale dashboard" in cron
    assert "restart" in cron
    assert "cutover" in cron


class BoomConn(FakeConn):
    """Raises on named catalog fragments; otherwise returns empty rows."""

    def __init__(self, boom_needles):
        super().__init__([])
        self.boom_needles = list(boom_needles)

    def rollback(self):
        return None

    def resolve(self, sql):
        text = " ".join(sql.split())
        for needle in self.boom_needles:
            if needle in text:
                raise RuntimeError(f"permission denied: {needle}")
        return super().resolve(sql)


def _detect_with_boom(needles):
    errors = []
    detector = CompatDetector(
        BoomConn(needles),
        add_error=lambda msg, exc=None: errors.append(str(msg)),
    )
    return detector.detect(), errors


def _finding(result, key):
    for finding in result.get("compat_findings") or []:
        if finding.get("key") == key:
            return finding
    return None


def test_looks_unsupported_is_limited_to_v24_signals():
    """Broad tokens must not turn a tool SQL bug into 'feature absent'."""
    assert looks_unsupported(RuntimeError("unimplemented: RLS"))
    assert looks_unsupported(RuntimeError("unknown function: foo"))
    assert looks_unsupported(RuntimeError("unknown column foo"))
    assert looks_unsupported(RuntimeError("42883"))
    assert looks_unsupported(RuntimeError("42703"))
    assert looks_unsupported(RuntimeError("0a000"))
    assert not looks_unsupported(RuntimeError("syntax error at or near SELECT"))
    assert not looks_unsupported(RuntimeError("column does not exist"))
    assert not looks_unsupported(RuntimeError("undefined table"))
    assert not looks_unsupported(RuntimeError("unsupported"))


def test_failed_statement_statistics_does_not_report_upsert_ok_zero():
    result, errors = _detect_with_boom(["statement_statistics"])
    finding = _finding(result, "upsert")
    assert finding is not None
    assert finding["read_status"] != "ok"
    assert finding["object_count"] == 0
    assert any("upsert" in e for e in errors)


def test_failed_zone_configurations_does_not_report_zone_ok_zero():
    result, errors = _detect_with_boom(["SHOW ZONE CONFIGURATIONS"])
    finding = _finding(result, "zone_partition")
    assert finding is not None
    assert finding["read_status"] != "ok"
    assert finding["object_count"] == 0
    assert any("zone_partition" in e for e in errors)


def test_failed_schedules_does_not_report_export_ok_zero():
    result, errors = _detect_with_boom(["SHOW SCHEDULES"])
    unread = {
        row["key"]: row
        for row in (result.get("cluster_context") or {}).get("unread") or []
    }
    assert "export_import" in unread
    assert unread["export_import"]["read_status"] != "ok"
    ttl = _finding(result, "row_level_ttl")
    assert ttl is not None
    assert ttl["read_status"] != "ok"
    assert any("export_import" in e or "row_level_ttl" in e for e in errors)
    assert "pg_cron" not in {
        ext.get("name") for ext in result.get("inferred_extensions") or []
    }


def test_failed_pg_indexes_does_not_silently_skip_extension_inference():
    result, errors = _detect_with_boom(["FROM pg_indexes"])
    assert any("infer extensions" in e or "scalar inverted" in e for e in errors)
    assert result["inferred_extensions"] == []


def test_failed_catalog_is_never_ok_with_zero_objects():
    """Each named swallow site must surface as unread, not ok/0."""
    cases = [
        (["statement_statistics"], "upsert", "compat"),
        (["SHOW ZONE CONFIGURATIONS"], "zone_partition", "compat"),
        (["SHOW SCHEDULES"], "export_import", "cluster"),
        (["SHOW SCHEDULES"], "row_level_ttl", "compat"),
    ]
    for needles, key, where in cases:
        result, _errors = _detect_with_boom(needles)
        if where == "compat":
            finding = _finding(result, key)
            assert finding is not None, key
            assert finding["read_status"] != "ok", key
            assert finding["object_count"] == 0, key
        else:
            unread = {
                row["key"]: row
                for row in (result.get("cluster_context") or {}).get("unread") or []
            }
            assert unread[key]["read_status"] != "ok", key


def test_job_descriptions_leave_no_literal_value():
    """Catches a changefeed or LDR job description stored with its WHERE values."""
    changefeed = (
        "CREATE CHANGEFEED INTO 'kafka://broker:9092?sasl_password=redacted' "
        'AS SELECT * FROM "public"."orders" WHERE (tier = \'vip\')'
    )
    ldr = (
        'CREATE LOGICAL REPLICATION STREAM FROM TABLE "public"."orders" '
        "ON 'postgresql://repl@source:26257/app?sslmode=verify-full' "
        'INTO TABLE "public"."orders"'
    )
    detector = CompatDetector(
        FakeConn(
            [
                (
                    "[SHOW CHANGEFEED JOBS]",
                    [{"job_id": 1101, "description": changefeed, "status": "running"}],
                ),
                (
                    "[SHOW JOBS] WHERE job_type ILIKE '%LOGICAL%'",
                    [
                        {
                            "job_type": "LOGICAL REPLICATION",
                            "description": ldr,
                            "status": "running",
                        }
                    ],
                ),
            ]
        )
    )
    detector._privilege_probe = {"gaps": []}

    assert detector.detect_changefeeds() == [
        {
            "name": "1101",
            "description_redacted": (
                'CREATE CHANGEFEED INTO $1 AS SELECT * FROM "public"."orders" '
                "WHERE (tier = $2)"
            ),
            "status": "running",
        }
    ]
    assert detector.detect_logical_data_replication() == [
        {
            "name": "LOGICAL REPLICATION",
            "description_redacted": (
                'CREATE LOGICAL REPLICATION STREAM FROM TABLE "public"."orders" '
                'ON $1 INTO TABLE "public"."orders"'
            ),
            "status": "running",
        }
    ]
