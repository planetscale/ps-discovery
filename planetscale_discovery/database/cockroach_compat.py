"""COMPAT.md catalog and detector for CockroachDB → PlanetScale Postgres.

The COMPAT.md catalog is the only source of truth.
This module does not invent outcomes. Every row keeps the COMPAT severity,
doc URL, and detection query so UIs can group findings as:

- blocker
- schema_rewrite (rewrites a migration tool can automate)
- app_work
- info (carries over cleanly)
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, NoReturn, Optional

from planetscale_discovery.common.sanitize import redact_sql
from planetscale_discovery.database.cockroach import (
    SYSTEM_SCHEMAS,
    is_system_database,
    is_user_zone_target,
    quote_ident,
    user_database_sql,
)
from planetscale_discovery.database.cockroach_privileges import (
    looks_unsupported,
    probe_discovery_privileges,
)

BLOCKER = "blocker"
REWRITE = "schema_rewrite"
APP_WORK = "app_work"
INFO = "info"

BUCKET_ORDER = ("blocker", "rewrite", "app_work", "carries")

SEVERITY_TO_BUCKET = {
    BLOCKER: "blocker",
    REWRITE: "rewrite",
    APP_WORK: "app_work",
    INFO: "carries",
}

D = "https://www.cockroachlabs.com/docs/v25.2"
PS_EXT = "https://planetscale.com/docs/postgres/extensions"

# Verified 2026-11-03 against the PS extensions page (PG 17.11 / 18.6).
PS_EXTENSIONS: Dict[str, Dict[str, Any]] = {
    "vector": {
        "name": "vector",
        "display": "pgvector 0.8.5",
        "status": "supported",
        "note": None,
        "doc_url": PS_EXT,
    },
    "postgis": {
        "name": "postgis",
        "display": "postgis 3.6.4",
        "status": "supported",
        "note": None,
        "doc_url": PS_EXT,
    },
    "pg_trgm": {
        "name": "pg_trgm",
        "display": "pg_trgm",
        "status": "supported",
        "note": None,
        "doc_url": PS_EXT,
    },
    "btree_gin": {
        "name": "btree_gin",
        "display": "btree_gin",
        "status": "supported",
        "note": "native extension; no superuser or restart needed",
        "doc_url": PS_EXT,
    },
    "pg_hint_plan": {
        "name": "pg_hint_plan",
        "display": "pg_hint_plan",
        "status": "supported",
        "note": "needs superuser and a restart; enable in the dashboard",
        "doc_url": PS_EXT,
    },
    "pg_cron": {
        "name": "pg_cron",
        "display": "pg_cron",
        "status": "supported",
        "note": (
            "Enable pg_cron in the PlanetScale dashboard. The database needs a "
            "restart, so plan that into the cutover."
        ),
        "doc_url": PS_EXT,
    },
    "pg_partman": {
        "name": "pg_partman",
        "display": "pg_partman",
        "status": "supported",
        "note": None,
        "doc_url": PS_EXT,
    },
    "icu": {
        "name": "ICU collations",
        "display": "ICU collations",
        "status": "unverified",
        "note": "not listed on planetscale.com/docs/postgres/extensions",
        "doc_url": PS_EXT,
    },
}

# COMPAT.md section 1. `detect` names a CompatDetector method.
# emit: always | if_found | if_found_or_unread
COMPAT_ROWS: List[Dict[str, Any]] = [
    {
        "id": 1,
        "key": "multi_region_db",
        "feature": "Multi-region DB (PRIMARY REGION / REGIONS / SURVIVE)",
        "severity": BLOCKER,
        "ps_status": "not supported",
        "action": "Choose one PS primary region. Re-architect multi-region writes (app routing, or separate DBs per region). Strip region DDL.",
        "doc_url": f"{D}/multiregion-overview",
        "detection_sql": "SHOW DATABASES; — primary_region / secondary_region / regions / survival_goal / super regions not NULL",
        "detect": "multi_region_db",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 2,
        "key": "regional_by_row",
        "feature": "REGIONAL BY ROW table",
        "severity": BLOCKER,
        "ps_status": "not supported",
        "action": "Drop LOCALITY. Decide whether to keep crdb_region as a plain enum/text column for app routing. Drop the crdb_internal_region enum. Multi-region active-active writes need a write-placement redesign (per-workload split or Neki).",
        "doc_url": f"{D}/table-localities",
        "detection_sql": "SELECT name, locality FROM crdb_internal.tables WHERE database_name=current_database() AND locality IS NOT NULL",
        "detect": "regional_by_row",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 3,
        "key": "global_table",
        "feature": "GLOBAL table",
        "severity": APP_WORK,
        "ps_status": "supported-with-work (unverified)",
        "action": "Strip LOCALITY GLOBAL. Serve remote reads from replicas, with staleness the app accepts.",
        "doc_url": f"{D}/table-localities",
        "detection_sql": "crdb_internal.tables.locality = 'GLOBAL'",
        "detect": "global_table",
        "emit": "if_found",
        "unverified": True,
        "ps_status_note": "Read replicas in other regions, if PS offers them, is unverified.",
    },
    {
        "id": 4,
        "key": "regional_by_table",
        "feature": "REGIONAL BY TABLE",
        "severity": REWRITE,
        "ps_status": "supported",
        "action": "Strip LOCALITY REGIONAL BY TABLE. The table becomes a regular PostgreSQL table.",
        "doc_url": f"{D}/table-localities",
        "detection_sql": "locality LIKE 'REGIONAL BY TABLE%'",
        "detect": "regional_by_table",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 5,
        "key": "logical_data_replication",
        "feature": "Multi-region / active-active writes (Logical Data Replication)",
        "severity": BLOCKER,
        "ps_status": "not supported",
        "action": "App or architecture redesign. A per-workload split or Neki; PS is a single-primary cluster.",
        "doc_url": "https://www.cockroachlabs.com/docs/v24.3/manage-logical-data-replication",
        "detection_sql": "SELECT job_type, status FROM [SHOW JOBS] WHERE job_type ILIKE '%LOGICAL%'",
        "detect": "logical_data_replication",
        "emit": "cluster",
        "unverified": True,
    },
    {
        "id": 6,
        "key": "hash_sharded",
        "feature": "Hash-sharded index / PK",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": (
            "Drop the hidden crdb_internal_*_shard_N column and the "
            "check_crdb_internal_*_shard_N CHECK constraint. Recreate as a plain btree "
            "on the user columns in the same order."
        ),
        "doc_url": f"{D}/hash-sharded-indexes",
        "detection_sql": (
            "crdb_internal.table_indexes.is_sharded; pg_indexes USING HASH; "
            "SELECT conrelid::regclass, conname FROM pg_constraint "
            "WHERE conname ~ '^check_crdb_internal_.*_shard_[0-9]+$'; "
            "hidden crdb_internal_*_shard_N columns"
        ),
        "detect": "hash_sharded",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 7,
        "key": "column_families",
        "feature": "Column families (FAMILY f (cols))",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": "Strip FAMILY clauses. Postgres uses TOAST automatically.",
        "doc_url": f"{D}/column-families",
        "detection_sql": "SHOW CREATE / create_statements matching FAMILY",
        "detect": "column_families",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 8,
        "key": "row_level_ttl",
        "feature": "Row-level TTL",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": (
            "Drop crdb_internal_expiration when ttl_expire_after was used. Enable "
            "pg_cron in the PlanetScale dashboard (the database needs a restart, so "
            "plan that into the cutover) and create a batched DELETE job per table, "
            "with an index on the expiry column."
        ),
        "doc_url": f"{D}/row-level-ttl",
        "detection_sql": "SELECT relname, reloptions FROM pg_class WHERE reloptions::text LIKE '%ttl%'",
        "detect": "row_level_ttl",
        "emit": "if_found_or_unread",
        "extension": "pg_cron",
        "unverified": False,
    },
    {
        "id": 9,
        "key": "vector_type",
        "feature": "VECTOR(n) type",
        "severity": INFO,
        "ps_status": "supported",
        "action": "CREATE EXTENSION vector; same type name and text format.",
        "doc_url": f"{D}/vector",
        "detection_sql": "information_schema.columns.crdb_sql_type LIKE 'VECTOR%'",
        "detect": "vector_type",
        "emit": "if_found",
        "extension": "vector",
        "unverified": False,
    },
    {
        "id": 10,
        "key": "vector_index",
        "feature": "Vector index (C-SPANN / USING cspann)",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": (
            "Rewrite as CREATE INDEX ... USING hnsw (embedding vector_l2_ops). "
            "Prefix columns become a partial index, a partitioned table, or a post-filter. "
            "CockroachDB accepts USING hnsw but stores it as C-SPANN (USING cspann). Only L2 works; "
            "vector_cosine_ops fails. Cosine on PlanetScale is fine (pgvector)."
        ),
        "doc_url": "https://www.cockroachlabs.com/docs/v25.2/vector-indexes",
        "detection_sql": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE indexdef LIKE '%USING cspann%'",
        "detect": "vector_index",
        "emit": "if_found",
        "extension": "vector",
        "unverified": False,
    },
    {
        "id": 11,
        "key": "spatial_types",
        "feature": "GEOMETRY / GEOGRAPHY",
        "severity": INFO,
        "ps_status": "supported",
        "action": "CREATE EXTENSION postgis; before loading. Types and SRIDs map directly.",
        "doc_url": f"{D}/spatial-data-overview",
        "detection_sql": "crdb_sql_type ~ '^(GEOMETRY|GEOGRAPHY)'",
        "detect": "spatial_types",
        "emit": "if_found",
        "extension": "postgis",
        "unverified": False,
    },
    {
        "id": 12,
        "key": "spatial_index",
        "feature": "Spatial inverted index",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": "Rewrite GIN on geometry → GiST. Drop S2 config options.",
        "doc_url": f"{D}/spatial-indexes",
        "detection_sql": "pg_indexes.indexdef ~ 'USING gin' joined to a geometry column",
        "detect": "spatial_index",
        "emit": "if_found",
        "extension": "postgis",
        "unverified": False,
    },
    {
        "id": 13,
        "key": "trigram_index",
        "feature": "Trigram index (gin_trgm_ops / gist_trgm_ops)",
        "severity": INFO,
        "ps_status": "supported",
        "action": "CREATE EXTENSION pg_trgm; then USING GIN (col gin_trgm_ops).",
        "doc_url": f"{D}/trigram-indexes",
        "detection_sql": "pg_indexes.indexdef LIKE '%trgm_ops%'",
        "detect": "trigram_index",
        "emit": "if_found",
        "extension": "pg_trgm",
        "unverified": False,
    },
    {
        "id": 14,
        "key": "trigram_ops_gap",
        "feature": "Trigram operators CockroachDB does not support (word_similarity, %>, <->, ...)",
        "severity": INFO,
        "ps_status": "supported (superset)",
        "action": "None for migration. The app can start using them after cutover.",
        "doc_url": f"{D}/trigram-indexes",
        "detection_sql": "n/a",
        "detect": None,
        "emit": "always",
        "unverified": False,
    },
    {
        "id": 15,
        "key": "jsonb_inverted",
        "feature": "JSONB inverted index",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": "INVERTED INDEX → USING GIN. Multi-column inverted needs btree_gin or a GIN on jsonb alone.",
        "doc_url": f"{D}/inverted-indexes",
        "detection_sql": "pg_indexes.indexdef ~ 'USING gin' on jsonb",
        "detect": "jsonb_inverted",
        "emit": "if_found",
        "extension": "btree_gin",
        "unverified": False,
    },
    {
        "id": 16,
        "key": "array_inverted",
        "feature": "ARRAY inverted index",
        "severity": REWRITE,
        "ps_status": "supported",
        "action": "Syntax rewrite to USING GIN (arr).",
        "doc_url": f"{D}/inverted-indexes",
        "detection_sql": "pg_indexes.indexdef ~ 'USING gin' on an array column",
        "detect": "array_inverted",
        "emit": "if_found",
        "extension": "btree_gin",
        "unverified": False,
    },
    {
        "id": 17,
        "key": "tsvector",
        "feature": "TSVECTOR / full-text",
        "severity": INFO,
        "ps_status": "supported",
        "action": "Clean. Native PG FTS.",
        "doc_url": f"{D}/full-text-search",
        "detection_sql": "crdb_sql_type IN ('TSVECTOR','TSQUERY')",
        "detect": "tsvector",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 18,
        "key": "stored_computed",
        "feature": "STORED computed column",
        "severity": INFO,
        "ps_status": "supported",
        "action": (
            "Normalize to GENERATED ALWAYS AS (expr) STORED. CRDB-only functions in the "
            "expression need rewriting. STORED and VIRTUAL computed columns appear in COPY "
            "output; the loader must use an explicit column list that leaves them out."
        ),
        "doc_url": f"{D}/computed-columns",
        "detection_sql": "information_schema.columns WHERE is_generated='ALWAYS'",
        "detect": "stored_computed",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 19,
        "key": "virtual_computed",
        "feature": "VIRTUAL computed column",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": (
            "Target PG18: keep VIRTUAL. Target PG17: convert to STORED or a view. Skip hidden "
            "shard columns. COPY loads must exclude it (computed columns appear in COPY output)."
        ),
        "doc_url": f"{D}/computed-columns",
        "detection_sql": "AS (...) VIRTUAL excluding crdb_internal_*_shard_ and NOT VISIBLE system cols",
        "detect": "virtual_computed",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 20,
        "key": "on_update",
        "feature": "ON UPDATE <expr> column clause",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": "Generate a BEFORE UPDATE FOR EACH ROW trigger function that sets the column.",
        "doc_url": f"{D}/create-table",
        "detection_sql": "SHOW CREATE matching ON UPDATE that is not an FK action",
        "detect": "on_update",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 21,
        "key": "not_visible",
        "feature": "NOT VISIBLE (hidden) columns",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": "System hidden cols: drop them (or keep rowid as a real PK). User-declared NOT VISIBLE: make visible and fix SELECT * consumers.",
        "doc_url": f"{D}/create-table",
        "detection_sql": "information_schema.columns WHERE is_hidden='YES'",
        "detect": "not_visible",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 22,
        "key": "hidden_rowid",
        "feature": "Table without explicit PK (hidden rowid)",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": "Add an explicit primary key.",
        "doc_url": f"{D}/primary-key",
        "detection_sql": "information_schema.columns WHERE column_name='rowid' AND is_hidden='YES'",
        "detect": "hidden_rowid",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 23,
        "key": "serial_unique_rowid",
        "feature": "SERIAL → unique_rowid()",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": "Keep as bigint identity. Set the identity/sequence start above max(id). Existing values are ~1.2e18 and may be timestamp-ordered.",
        "doc_url": f"{D}/serial",
        "detection_sql": "information_schema.columns WHERE column_default LIKE '%unique_rowid()%'",
        "detect": "serial_unique_rowid",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 24,
        "key": "sequences",
        "feature": "Sequences / IDENTITY",
        "severity": INFO,
        "ps_status": "supported",
        "action": "setval() to the source's current value at cutover.",
        "doc_url": f"{D}/create-sequence",
        "detection_sql": "SELECT * FROM information_schema.sequences",
        "detect": "sequences",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 25,
        "key": "enums",
        "feature": "ENUM types",
        "severity": INFO,
        "ps_status": "supported",
        "action": "Clean. Skip the system crdb_internal_region enum.",
        "doc_url": f"{D}/enum",
        "detection_sql": "SELECT typname FROM pg_type WHERE typtype='e'",
        "detect": "enums",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 26,
        "key": "partial_expression_indexes",
        "feature": "Partial / expression indexes",
        "severity": INFO,
        "ps_status": "supported",
        "action": "Clean. Strip crdb_internal casts like ::STRING → ::text.",
        "doc_url": f"{D}/partial-indexes",
        "detection_sql": "pg_indexes.indexdef ~ ' WHERE ' or expression indexes",
        "detect": "partial_expression_indexes",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 27,
        "key": "storing",
        "feature": "STORING / covering index",
        "severity": REWRITE,
        "ps_status": "supported",
        "action": "Rename STORING (cols) to INCLUDE (cols).",
        "doc_url": f"{D}/create-index",
        "detection_sql": "indexdef / SHOW CREATE matching STORING (",
        "detect": "storing",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 28,
        "key": "unique_without_index",
        "feature": "UNIQUE WITHOUT INDEX / NOT VISIBLE indexes / index hints",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": "Remove from DDL; strip @idx / FORCE_INDEX hints from app SQL. pg_hint_plan can replace hints.",
        "doc_url": f"{D}/create-index",
        "detection_sql": "SHOW CREATE matching UNIQUE WITHOUT INDEX or NOT VISIBLE on index lines",
        "detect": "unique_without_index",
        "emit": "if_found",
        "extension": "pg_hint_plan",
        "unverified": False,
        "also_app_work": True,
    },
    {
        "id": 29,
        "key": "udfs",
        "feature": "UDFs",
        "severity": INFO,
        "ps_status": "supported",
        "action": "Mostly clean. STRING → text, INT → bigint in bodies.",
        "doc_url": f"{D}/user-defined-functions",
        "detection_sql": "information_schema.routines WHERE routine_schema not system",
        "detect": "udfs",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 30,
        "key": "procedures",
        "feature": "Stored procedures",
        "severity": INFO,
        "ps_status": "supported",
        "action": "Clean, with the same type rewrites as UDFs.",
        "doc_url": f"{D}/stored-procedures",
        "detection_sql": "crdb_internal.create_procedure_statements",
        "detect": "procedures",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 31,
        "key": "triggers",
        "feature": "Triggers",
        "severity": INFO,
        "ps_status": "supported",
        "action": "Clean: the function and CREATE TRIGGER port. pg_trigger and information_schema.triggers are empty — detection uses SHOW TRIGGERS.",
        "doc_url": f"{D}/triggers",
        "detection_sql": "SHOW TRIGGERS FROM <table>",
        "detect": "triggers",
        "emit": "if_found_or_unread",
        "unverified": False,
    },
    {
        "id": 32,
        "key": "rls",
        "feature": "Row-level security",
        "severity": INFO,
        "ps_status": "supported",
        "action": "Clean. Re-create roles first. Policies are not in SHOW CREATE TABLE.",
        "doc_url": "https://www.cockroachlabs.com/docs/v25.2/row-level-security",
        "detection_sql": "SELECT relname, relrowsecurity FROM pg_class WHERE relrowsecurity",
        "detect": "rls",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 33,
        "key": "views",
        "feature": "Views / materialized views",
        "severity": INFO,
        "ps_status": "supported",
        "action": "Clean, except views using CockroachDB-only syntax.",
        "doc_url": f"{D}/views",
        "detection_sql": "SELECT * FROM information_schema.views",
        "detect": "views",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 34,
        "key": "collated_strings",
        "feature": "Collated strings",
        "severity": REWRITE,
        "ps_status": "unverified",
        "action": (
            "Map each locale to an ICU collation, or use citext for case-insensitive equality. "
            "ICU availability on PS is unverified. COPY writes collated values as "
            "'x' COLLATE ..., which corrupts a PG load — export with "
            "COPY (SELECT col::STRING, ...) TO STDOUT."
        ),
        "doc_url": f"{D}/collate",
        "detection_sql": "crdb_sql_type LIKE '%COLLATE%'",
        "detect": "collated_strings",
        "emit": "if_found",
        "extension": "icu",
        "unverified": True,
    },
    {
        "id": 35,
        "key": "string_bytes",
        "feature": "STRING / STRING(n) / BYTES",
        "severity": REWRITE,
        "ps_status": "supported",
        "action": "Type rename: STRING → text / varchar(n), BYTES → bytea.",
        "doc_url": f"{D}/string",
        "detection_sql": "crdb_sql_type matching STRING or BYTES",
        "detect": "string_bytes",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 36,
        "key": "int_is_int8",
        "feature": "INT = INT8",
        "severity": REWRITE,
        "ps_status": "supported",
        "action": "Map INT → bigint. CockroachDB INT is 64-bit; PostgreSQL integer is 32-bit.",
        "doc_url": f"{D}/int",
        "detection_sql": "SHOW default_int_size",
        "detect": "int_is_int8",
        "emit": "always",
        "unverified": False,
    },
    {
        "id": 37,
        "key": "upsert",
        "feature": "UPSERT statement",
        "severity": APP_WORK,
        "ps_status": "supported-with-work",
        "action": "Rewrite UPSERT INTO to INSERT ... ON CONFLICT (pk) DO UPDATE.",
        "doc_url": f"{D}/upsert",
        "detection_sql": "app grep UPSERT INTO; crdb_internal.statement_statistics",
        "detect": "upsert",
        "emit": "always",
        "unverified": False,
    },
    {
        "id": 38,
        "key": "as_of_system_time",
        "feature": "AS OF SYSTEM TIME / follower reads",
        "severity": APP_WORK,
        "ps_status": "not supported",
        "action": "App rewrite: send stale reads to a replica, or remove them. No time travel on Postgres.",
        "doc_url": f"{D}/as-of-system-time",
        "detection_sql": "app grep AS OF SYSTEM TIME",
        "detect": None,
        "emit": "always",
        "unverified": False,
    },
    {
        "id": 39,
        "key": "serializable_retries",
        "feature": "SERIALIZABLE default + 40001 retry loops",
        "severity": APP_WORK,
        "ps_status": "supported",
        "action": "Keep retry logic, or drop it if moving to READ COMMITTED. Remove SAVEPOINT cockroach_restart.",
        "doc_url": f"{D}/read-committed",
        "detection_sql": "SHOW default_transaction_isolation; app grep cockroach_restart|40001",
        "detect": "serializable_retries",
        "emit": "always",
        "unverified": False,
    },
    {
        "id": 40,
        "key": "integer_division",
        "feature": "Integer / returns DECIMAL; // is floor div",
        "severity": APP_WORK,
        "ps_status": "supported-with-work",
        "action": "Audit app/query arithmetic. Rewrite // → div() or a cast. INT4 overflow will start raising on PG.",
        "doc_url": f"{D}/functions-and-operators",
        "detection_sql": "app grep // ; review integer division sites",
        "detect": None,
        "emit": "always",
        "unverified": False,
    },
    {
        "id": 41,
        "key": "crdb_builtins",
        "feature": "CockroachDB-only built-in functions",
        "severity": APP_WORK,
        "ps_status": "not supported",
        "action": "App rewrite of crdb_internal.*, unique_rowid(), gateway_region(), fnv32, experimental_*, SHOW RANGES, SHOW JOBS.",
        "doc_url": f"{D}/crdb-internal",
        "detection_sql": "create_statements matching crdb_internal.|unique_rowid|gateway_region|fnv32",
        "detect": "crdb_builtins",
        "emit": "if_found",
        "unverified": False,
    },
    {
        "id": 42,
        "key": "changefeeds",
        "feature": "Changefeeds",
        "severity": APP_WORK,
        "ps_status": "supported-with-work",
        "action": (
            "Downstream consumers must re-point to PG logical decoding. Message format differs. "
            "CREATE CHANGEFEED ... WITH initial_scan='only' works even with kv.rangefeed.enabled=false "
            "and is a bulk-export alternative to COPY/EXPORT. Continuous changefeeds need rangefeed on."
        ),
        "doc_url": f"{D}/change-data-capture-overview",
        "detection_sql": "SELECT job_id, description, status FROM [SHOW CHANGEFEED JOBS]",
        "detect": "changefeeds",
        "emit": "cluster",
        "unverified": False,
    },
    {
        "id": 43,
        "key": "export_import",
        "feature": "EXPORT / IMPORT / BACKUP schedules",
        "severity": APP_WORK,
        "ps_status": "supported-with-work",
        "action": (
            "Replace ops jobs with COPY and PS-managed backups. pg_dump does not work "
            "against CockroachDB. SHOW SCHEDULES is cluster-wide; attribute backup / "
            "export / changefeed schedules to databases via the table id in the label."
        ),
        "doc_url": f"{D}/export",
        "detection_sql": "SHOW SCHEDULES; app grep EXPORT INTO",
        "detect": "export_import",
        "emit": "cluster",
        "unverified": False,
    },
    {
        "id": 44,
        "key": "zone_partition",
        "feature": "Zone configs / index partitioning",
        "severity": REWRITE,
        "ps_status": "supported-with-work",
        "action": (
            "Drop zone configs. CockroachDB index partitioning → drop, or redo as "
            "PostgreSQL table partitioning if the user table is actually partitioned. "
            "Flag any zone whose num_replicas exceeds the cluster node count."
        ),
        "doc_url": f"{D}/partitioning",
        "detection_sql": (
            "SHOW ZONE CONFIGURATIONS; PARTITION BY / CONFIGURE ZONE; "
            "flag num_replicas > node count. Skip RANGE meta / liveness / system."
        ),
        "detect": "zone_partition",
        "emit": "if_found_or_unread",
        "unverified": False,
    },
    {
        "id": 45,
        "key": "jsonpath",
        "feature": "JSONPath (v25.2 preview)",
        "severity": INFO,
        "ps_status": "supported",
        "action": "Clean. Native PG jsonpath.",
        "doc_url": "https://www.cockroachlabs.com/docs/releases/v25.2",
        "detection_sql": "app grep jsonb_path_",
        "detect": None,
        "emit": "always",
        "unverified": False,
    },
    {
        "id": 46,
        "key": "create_extension_noop",
        "feature": "CREATE EXTENSION is a no-op; pg_extension is empty",
        "severity": INFO,
        "ps_status": "n/a",
        "action": "Infer extensions from column and index types (VECTOR → vector, GEOMETRY → postgis, trgm_ops → pg_trgm).",
        "doc_url": f"{D}/pg-extension",
        "detection_sql": "SELECT count(*) FROM pg_extension; infer from type usage",
        "detect": "create_extension_noop",
        "emit": "cluster",
        "unverified": True,
    },
    {
        "id": 47,
        "key": "high_oids",
        "feature": "Non-PG type OIDs (geometry 90000, geography 90002, vector 90006)",
        "severity": INFO,
        "ps_status": "info",
        "action": "Tools that map types by OID must map by name. Text COPY is unaffected.",
        "doc_url": f"{D}/data-types",
        "detection_sql": "SELECT oid, typname FROM pg_type WHERE oid >= 90000",
        "detect": "high_oids",
        "emit": "if_found",
        "unverified": False,
    },
]

_SCHEMA_SQL = "(" + ", ".join(f"'{s}'" for s in SYSTEM_SCHEMAS) + ")"


def rows_by_id() -> Dict[int, Dict[str, Any]]:
    return {row["id"]: row for row in COMPAT_ROWS}


def rows_by_key() -> Dict[str, Dict[str, Any]]:
    return {row["key"]: row for row in COMPAT_ROWS}


def bucket_for(severity: str) -> str:
    return SEVERITY_TO_BUCKET.get(severity, "carries")


def extension_payload(name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    spec = PS_EXTENSIONS.get(name)
    if not spec:
        return {
            "name": name,
            "status": "unverified",
            "note": "not on the PS extensions page",
            "doc_url": PS_EXT,
        }
    return dict(spec)


def validate_catalog() -> Optional[str]:
    seen_ids = set()
    seen_keys = set()
    for row in COMPAT_ROWS:
        if row["id"] in seen_ids:
            return f"duplicate id {row['id']}"
        if row["key"] in seen_keys:
            return f"duplicate key {row['key']}"
        seen_ids.add(row["id"])
        seen_keys.add(row["key"])
        if not row.get("doc_url"):
            return f"row {row['id']} missing doc_url"
        if row["severity"] not in SEVERITY_TO_BUCKET:
            return f"row {row['id']} bad severity"
        # Unverified rows must not display as supported. _finding rewrites
        # ps_status at emit time; do not duplicate that check here.
    if set(range(1, 48)) - seen_ids:
        return f"missing ids: {sorted(set(range(1, 48)) - seen_ids)}"
    return None


def _qual(row: Dict[str, Any], *parts: str) -> str:
    bits = [str(p) for p in parts if p]
    return ".".join(bits) if bits else str(row)


def _obj(**kwargs: Any) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


class CompatDetector:
    """Run COMPAT.md detection queries on a live CockroachDB connection."""

    def __init__(
        self,
        connection: Any,
        add_error: Optional[Callable[[str, Any], None]] = None,
        add_warning: Optional[Callable[[str], None]] = None,
    ):
        self.connection = connection
        self.add_error = add_error or (lambda *_args, **_kw: None)
        self.add_warning = add_warning or (lambda *_args, **_kw: None)
        self._create_statements: Optional[List[Dict[str, Any]]] = None
        self._pg_indexes: Optional[List[Dict[str, Any]]] = None
        self._columns: Optional[List[Dict[str, Any]]] = None
        self._user_tables: Optional[List[Dict[str, Any]]] = None
        self._privilege_probe: Optional[Dict[str, Any]] = None
        self._current_db_cache: Optional[str] = None

    def detect(self) -> Dict[str, Any]:
        findings: List[Dict[str, Any]] = []
        cluster_findings: List[Dict[str, Any]] = []
        for spec in COMPAT_ROWS:
            read_status = "ok"
            read_error = None
            objects: List[Dict[str, Any]] = []
            method_name = spec.get("detect")
            if method_name:
                try:
                    objects = getattr(self, f"detect_{method_name}")()
                except Exception as exc:
                    try:
                        self.connection.rollback()
                    except Exception:
                        pass
                    # v24.3 has no vector index / RLS / CREATE POLICY.
                    if spec.get("key") in ("vector_index", "rls") and looks_unsupported(
                        exc
                    ):
                        objects = []
                    else:
                        read_status = "couldn't read"
                        read_error = str(exc)
                        self.add_error(
                            f"Couldn't read COMPAT row {spec['id']} ({spec['key']}): {exc}",
                            exc,
                        )
            finding = self._finding(spec, objects, read_status, read_error)
            if spec.get("emit") == "cluster":
                cluster_findings.append(finding)
            elif self._should_emit(spec, finding):
                findings.append(finding)
        try:
            inferred = self._infer_extensions(findings)
        except Exception as exc:
            try:
                self.connection.rollback()
            except Exception:
                pass
            self.add_error(f"Couldn't infer extensions from catalogs: {exc}", exc)
            inferred = []
        return {
            "compat_findings": findings,
            "inferred_extensions": inferred,
            "compat_source": "COMPAT.md",
            "cluster_context": self._cluster_context(cluster_findings),
        }

    def _should_emit(self, spec: Dict[str, Any], finding: Dict[str, Any]) -> bool:
        emit = spec.get("emit") or "if_found"
        if emit == "cluster":
            return False
        if emit == "always":
            return True
        if emit == "if_found_or_unread":
            return bool(finding["objects"]) or finding["read_status"] != "ok"
        return bool(finding["objects"])

    def _finding(
        self,
        spec: Dict[str, Any],
        objects: List[Dict[str, Any]],
        read_status: str,
        read_error: Optional[str],
    ) -> Dict[str, Any]:
        ext = extension_payload(spec.get("extension"))
        ps_status = spec["ps_status"]
        if spec.get("unverified") and "unverified" not in ps_status:
            # Never display unverified as supported.
            if ps_status == "supported":
                ps_status = "unverified"
        return {
            "row": spec["id"],
            "key": spec["key"],
            "feature": spec["feature"],
            "severity": spec["severity"],
            "bucket": bucket_for(spec["severity"]),
            "ps_status": ps_status,
            "action": spec["action"],
            "doc_url": spec["doc_url"],
            "detection_sql": spec["detection_sql"],
            "objects": objects,
            "object_count": len(objects),
            "object_names": [
                o.get("name") or o.get("table") or str(o) for o in objects
            ],
            "extension": ext,
            "unverified": bool(spec.get("unverified")),
            "read_status": read_status,
            "read_error": read_error,
            "also_app_work": bool(spec.get("also_app_work")),
        }

    # Infer PlanetScale extensions only from the user's own column/index types
    # (and TTL → pg_cron). Zone configs, system ranges, and catalog no-ops do
    # not count — RANGE meta / liveness must never become pg_partman.
    _USER_TYPE_EXTENSION_KEYS = frozenset(
        {
            "vector_type",
            "vector_index",
            "spatial_types",
            "spatial_index",
            "trigram_index",
            "row_level_ttl",
            "jsonb_inverted",
            "array_inverted",
            "scalar_inverted",
        }
    )

    def _infer_extensions(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        wanted: Dict[str, Dict[str, Any]] = {}
        for finding in findings:
            if finding.get("key") not in self._USER_TYPE_EXTENSION_KEYS:
                continue
            ext = finding.get("extension")
            if not ext:
                continue
            if finding["read_status"] != "ok" or finding["object_count"] == 0:
                continue
            key = ext["name"]
            entry = wanted.setdefault(key, {**ext, "inferred_from": []})
            if finding["object_count"]:
                entry["inferred_from"].append(finding["key"])
        try:
            scalar = self.detect_scalar_inverted()
        except Exception as exc:
            try:
                self.connection.rollback()
            except Exception:
                pass
            if looks_unsupported(exc):
                scalar = []
            else:
                raise RuntimeError(
                    f"Couldn't read scalar inverted indexes: {exc}"
                ) from exc
        if scalar:
            entry = wanted.setdefault(
                "btree_gin", {**PS_EXTENSIONS["btree_gin"], "inferred_from": []}
            )
            if "scalar_inverted" not in entry["inferred_from"]:
                entry["inferred_from"].append("scalar_inverted")
        return list(wanted.values())

    def _cluster_context(
        self, cluster_findings: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        by_key = {finding["key"]: finding for finding in cluster_findings}
        settings: Dict[str, Any] = {}
        try:
            settings["kv.rangefeed.enabled"] = self._scalar(
                "SHOW CLUSTER SETTING kv.rangefeed.enabled"
            )
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                pass
            settings["kv.rangefeed.enabled"] = None
        regions: List[str] = []
        try:
            for row in self._fetch("SHOW REGIONS FROM CLUSTER"):
                name = row.get("region") or row.get("region_name") or row.get("name")
                if name:
                    regions.append(str(name))
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                pass
        license: Dict[str, Any] = {}
        try:
            license["organization"] = self._scalar(
                "SHOW CLUSTER SETTING cluster.organization"
            )
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                pass
            license["organization"] = None
        return {
            "cluster_settings": settings,
            "jobs": (by_key.get("logical_data_replication") or {}).get("objects") or [],
            "schedules": (by_key.get("export_import") or {}).get("objects") or [],
            "license": license,
            "node_count": self._node_count(),
            "cluster_regions": regions,
            "changefeeds": (by_key.get("changefeeds") or {}).get("objects") or [],
            "pg_extension": (by_key.get("create_extension_noop") or {}).get("objects")
            or [],
            "unread": [
                {
                    "key": finding["key"],
                    "feature": finding["feature"],
                    "read_status": finding["read_status"],
                    "read_error": finding["read_error"],
                }
                for finding in cluster_findings
                if finding.get("read_status") != "ok"
            ],
        }

    def _fetch(self, sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def _scalar(self, sql: str):
        rows = self._fetch(sql)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    def _privilege_status(self) -> Dict[str, Any]:
        if self._privilege_probe is None:
            self._privilege_probe = probe_discovery_privileges(
                self.connection, self.add_error, self.add_warning
            )
        return self._privilege_probe

    def _require_viewjob(self, label: str) -> None:
        probe = self._privilege_status()
        if any(g.get("privilege") == "VIEWJOB" for g in probe.get("gaps") or []):
            raise RuntimeError(
                probe.get("jobs_error")
                or f"VIEWJOB is missing. SHOW JOBS for {label} silently returns 0 rows."
            )

    def _fail_read(self, label: str, exc: BaseException) -> NoReturn:
        """Turn a failed catalog read into a visible couldn't-read error."""
        try:
            self.connection.rollback()
        except Exception:
            pass
        raise RuntimeError(f"Couldn't read {label}: {exc}") from exc

    def _node_count(self) -> Optional[int]:
        try:
            value = self._scalar("SELECT count(*) FROM crdb_internal.gossip_nodes")
            return int(value) if value is not None else None
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                pass
            return None

    def _current_database(self) -> Optional[str]:
        if self._current_db_cache is not None:
            return self._current_db_cache
        try:
            value = self._scalar("SELECT current_database()")
            self._current_db_cache = str(value) if value else None
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                pass
            self._current_db_cache = None
        return self._current_db_cache

    def _in_current_database(
        self, rows: List[Dict[str, Any]], key: str = "database_name"
    ) -> List[Dict[str, Any]]:
        current = self._current_database()
        if not current:
            return rows
        return [row for row in rows if not row.get(key) or row.get(key) == current]

    def user_tables(self) -> List[Dict[str, Any]]:
        if self._user_tables is None:
            self._user_tables = self._fetch(f"""
                SELECT table_catalog AS database_name, table_schema AS schema_name,
                       table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema NOT IN {_SCHEMA_SQL}
                  AND {user_database_sql('table_catalog')}
                  AND table_catalog = current_database()
                ORDER BY 1, 2, 3
                """)
        return self._user_tables

    def create_statements(self) -> List[Dict[str, Any]]:
        if self._create_statements is None:
            rows = self._fetch(f"""
                SELECT database_name, schema_name, descriptor_name, descriptor_type,
                       create_statement
                FROM crdb_internal.create_statements
                WHERE {user_database_sql('database_name')}
                  AND schema_name NOT IN {_SCHEMA_SQL}
                  AND database_name = current_database()
                """)
            self._create_statements = self._in_current_database(rows)
        return self._create_statements

    def pg_indexes(self) -> List[Dict[str, Any]]:
        if self._pg_indexes is None:
            self._pg_indexes = self._fetch(f"""
                SELECT schemaname, tablename, indexname, indexdef
                FROM pg_indexes
                WHERE schemaname NOT IN {_SCHEMA_SQL}
                """)
        return self._pg_indexes

    def columns(self) -> List[Dict[str, Any]]:
        if self._columns is None:
            self._columns = self._fetch(f"""
                SELECT table_catalog AS database_name, table_schema AS schema_name,
                       table_name, column_name, crdb_sql_type, column_default,
                       is_generated, generation_expression, is_hidden, data_type
                FROM information_schema.columns
                WHERE table_schema NOT IN {_SCHEMA_SQL}
                  AND {user_database_sql('table_catalog')}
                  AND table_catalog = current_database()
                """)
            self._columns = self._in_current_database(self._columns)
        return self._columns

    def detect_multi_region_db(self) -> List[Dict[str, Any]]:
        rows = self._fetch("SHOW DATABASES")
        current = self._current_database()
        out = []
        for row in rows:
            name = row.get("database_name")
            if is_system_database(name, current):
                continue
            if current and name != current:
                continue
            primary = row.get("primary_region")
            regions = row.get("regions")
            secondary = row.get("secondary_region")
            survival = row.get("survival_goal")
            has_regions = regions not in (None, [], "{}", "")
            if not (primary or secondary or has_regions or survival):
                continue
            super_regions = []
            try:
                super_regions = [
                    str(
                        sr.get("super_region_name")
                        or sr.get("super_region")
                        or sr.get("name")
                        or ""
                    )
                    for sr in self._fetch(
                        f"SHOW SUPER REGIONS FROM DATABASE {quote_ident(name)}"
                    )
                    if sr
                ]
                super_regions = [s for s in super_regions if s]
            except Exception:
                try:
                    self.connection.rollback()
                except Exception:
                    pass
            out.append(
                _obj(
                    name=name,
                    primary_region=primary,
                    secondary_region=secondary,
                    regions=str(regions),
                    survival_goal=survival,
                    super_regions=super_regions,
                )
            )
        return out

    def detect_regional_by_row(self) -> List[Dict[str, Any]]:
        rows = self._fetch(f"""
            SELECT database_name, schema_name, name AS table_name, locality
            FROM crdb_internal.tables
            WHERE {user_database_sql('database_name')}
              AND schema_name NOT IN {_SCHEMA_SQL}
              AND drop_time IS NULL
              AND database_name = current_database()
              AND locality ILIKE '%REGIONAL BY ROW%'
            """)
        rows = [
            r
            for r in self._in_current_database(rows)
            if "REGIONAL BY ROW" in str(r.get("locality") or "").upper()
        ]
        return [
            _obj(
                name=_qual(
                    r, r.get("database_name"), r.get("schema_name"), r.get("table_name")
                ),
                locality=r.get("locality"),
            )
            for r in rows
        ]

    def detect_global_table(self) -> List[Dict[str, Any]]:
        rows = self._fetch(f"""
            SELECT database_name, schema_name, name AS table_name, locality
            FROM crdb_internal.tables
            WHERE {user_database_sql('database_name')}
              AND schema_name NOT IN {_SCHEMA_SQL}
              AND drop_time IS NULL
              AND database_name = current_database()
              AND locality = 'GLOBAL'
            """)
        rows = [
            r
            for r in self._in_current_database(rows)
            if str(r.get("locality") or "").upper() == "GLOBAL"
        ]
        return [
            _obj(
                name=_qual(
                    r, r.get("database_name"), r.get("schema_name"), r.get("table_name")
                )
            )
            for r in rows
        ]

    def detect_regional_by_table(self) -> List[Dict[str, Any]]:
        rows = self._fetch(f"""
            SELECT database_name, schema_name, name AS table_name, locality
            FROM crdb_internal.tables
            WHERE {user_database_sql('database_name')}
              AND schema_name NOT IN {_SCHEMA_SQL}
              AND drop_time IS NULL
              AND database_name = current_database()
              AND locality ILIKE 'REGIONAL BY TABLE%'
            """)
        rows = [
            r
            for r in self._in_current_database(rows)
            if str(r.get("locality") or "").upper().startswith("REGIONAL BY TABLE")
        ]
        return [
            _obj(
                name=_qual(
                    r, r.get("database_name"), r.get("schema_name"), r.get("table_name")
                ),
                locality=r.get("locality"),
            )
            for r in rows
        ]

    def detect_logical_data_replication(self) -> List[Dict[str, Any]]:
        self._require_viewjob("logical replication jobs")
        try:
            rows = self._fetch(
                "SELECT job_type, status, description FROM [SHOW JOBS] WHERE job_type ILIKE '%LOGICAL%'"
            )
        except Exception as exc:
            try:
                self.connection.rollback()
            except Exception:
                pass
            raise RuntimeError(f"Couldn't read SHOW JOBS: {exc}") from exc
        return [
            _obj(
                name=r.get("job_type"),
                description_redacted=redact_sql(r.get("description")),
                status=r.get("status"),
            )
            for r in rows
        ]

    def detect_hash_sharded(self) -> List[Dict[str, Any]]:
        seen = set()
        out = []
        rows = self._fetch(f"""
            SELECT t.database_name, t.schema_name, ti.descriptor_name AS table_name,
                   ti.index_name, ti.shard_bucket_count, ti.is_sharded
            FROM crdb_internal.table_indexes AS ti
            JOIN crdb_internal.tables AS t ON t.table_id = ti.descriptor_id
            WHERE {user_database_sql('t.database_name')}
              AND t.schema_name NOT IN {_SCHEMA_SQL}
              AND t.database_name = current_database()
              AND ti.is_sharded
            """)
        rows = self._in_current_database(rows)
        for r in rows:
            name = f"{r.get('schema_name')}.{r.get('table_name')}.{r.get('index_name')}"
            seen.add(name)
            out.append(
                _obj(
                    name=name,
                    table=_qual(
                        r,
                        r.get("database_name"),
                        r.get("schema_name"),
                        r.get("table_name"),
                    ),
                    index_name=r.get("index_name"),
                )
            )
        for idx in self.pg_indexes():
            definition = str(idx.get("indexdef") or "")
            if "USING HASH" not in definition.upper():
                continue
            name = (
                f"{idx.get('schemaname')}.{idx.get('tablename')}.{idx.get('indexname')}"
            )
            if name in seen:
                continue
            seen.add(name)
            out.append(_obj(name=name, indexdef=definition, kind="indexdef"))
        # pg-style catalogs: hash sharding is visible only as CHECK constraints
        # plus hidden crdb_internal_*_shard_N columns.
        try:
            checks = self._fetch(f"""
                SELECT n.nspname AS schema_name, c.relname AS table_name, con.conname
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT IN {_SCHEMA_SQL}
                  AND con.conname ~ '^check_crdb_internal_.*_shard_[0-9]+$'
                """)
        except Exception:
            checks = []
        for r in checks:
            name = f"{r.get('schema_name')}.{r.get('table_name')}.{r.get('conname')}"
            if name in seen:
                continue
            seen.add(name)
            out.append(
                _obj(
                    name=name,
                    table=f"{r.get('schema_name')}.{r.get('table_name')}",
                    kind="check_constraint",
                )
            )
        for c in self.columns():
            col = str(c.get("column_name") or "")
            if not (col.startswith("crdb_internal_") and "_shard_" in col):
                continue
            name = f"{c.get('schema_name')}.{c.get('table_name')}.{col}"
            if name in seen:
                continue
            seen.add(name)
            out.append(
                _obj(
                    name=name,
                    table=f"{c.get('schema_name')}.{c.get('table_name')}",
                    kind="hidden_shard_column",
                )
            )
        return out

    def detect_column_families(self) -> List[Dict[str, Any]]:
        out = []
        for row in self.create_statements():
            sql = row.get("create_statement") or ""
            if "FAMILY " in sql.upper() and row.get("descriptor_type") == "table":
                out.append(
                    _obj(
                        name=_qual(
                            row,
                            row.get("database_name"),
                            row.get("schema_name"),
                            row.get("descriptor_name"),
                        )
                    )
                )
        return out

    def detect_row_level_ttl(self) -> List[Dict[str, Any]]:
        rows = self._fetch(f"""
            SELECT n.nspname AS schema_name, c.relname AS table_name, c.reloptions
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname NOT IN {_SCHEMA_SQL}
              AND c.relkind = 'r'
              AND c.reloptions::text LIKE '%ttl%'
            """)
        out = [
            _obj(
                name=f"{r.get('schema_name')}.{r.get('table_name')}",
                reloptions=str(r.get("reloptions")),
            )
            for r in rows
        ]
        # SHOW SCHEDULES is cluster-wide. Map row-level-ttl: <table> [id]
        # back to this database via the table id in the label.
        try:
            schedules = self._fetch("SHOW SCHEDULES")
        except Exception as exc:
            self._fail_read("SHOW SCHEDULES", exc)
        table_by_id: Dict[str, Dict[str, Any]] = {}
        try:
            table_sql = f"""
                SELECT table_id, database_name, schema_name, name
                FROM crdb_internal.tables
                WHERE drop_time IS NULL
                  AND {user_database_sql('database_name')}
                  AND schema_name NOT IN {_SCHEMA_SQL}
                """
            for t in self._fetch(table_sql):
                table_by_id[str(t["table_id"])] = t
        except Exception as exc:
            self._fail_read("crdb_internal.tables", exc)
        current = None
        try:
            current = self._scalar("SELECT current_database()")
        except Exception:
            current = None
        seen = {o.get("name") for o in out}
        for sched in schedules:
            label = str(sched.get("label") or sched.get("schedule_name") or "")
            if "row-level-ttl" not in label.lower():
                continue
            match = re.search(r"\[(\d+)\]", label)
            db = None
            table = None
            if match and match.group(1) in table_by_id:
                t = table_by_id[match.group(1)]
                db = t.get("database_name")
                table = f"{t.get('schema_name')}.{t.get('name')}"
            if current and db and db != current:
                continue
            name = table or label
            if name in seen:
                continue
            seen.add(name)
            out.append(_obj(name=name, schedule=label, database=db))
        return out

    def detect_vector_type(self) -> List[Dict[str, Any]]:
        return [
            _obj(
                name=f"{c.get('schema_name')}.{c.get('table_name')}.{c.get('column_name')}",
                crdb_sql_type=c.get("crdb_sql_type"),
            )
            for c in self.columns()
            if str(c.get("crdb_sql_type") or "").upper().startswith("VECTOR")
        ]

    def detect_vector_index(self) -> List[Dict[str, Any]]:
        try:
            indexes = self.pg_indexes()
        except Exception as exc:
            if looks_unsupported(exc):
                return []
            raise
        return [
            _obj(
                name=f"{r.get('schemaname')}.{r.get('tablename')}.{r.get('indexname')}",
                indexdef=r.get("indexdef"),
            )
            for r in indexes
            if any(
                token in str(r.get("indexdef") or "").lower()
                for token in ("using cspann", "using hnsw")
            )
        ]

    def detect_spatial_types(self) -> List[Dict[str, Any]]:
        out = []
        for c in self.columns():
            dtype = str(c.get("crdb_sql_type") or "")
            if dtype.startswith("GEOMETRY") or dtype.startswith("GEOGRAPHY"):
                out.append(
                    _obj(
                        name=f"{c.get('schema_name')}.{c.get('table_name')}.{c.get('column_name')}",
                        crdb_sql_type=dtype,
                    )
                )
        return out

    def detect_spatial_index(self) -> List[Dict[str, Any]]:
        geom_cols = {
            (c.get("schema_name"), c.get("table_name"), c.get("column_name"))
            for c in self.columns()
            if str(c.get("crdb_sql_type") or "").startswith(("GEOMETRY", "GEOGRAPHY"))
        }
        out = []
        for idx in self.pg_indexes():
            definition = str(idx.get("indexdef") or "")
            if "using gin" not in definition.lower():
                continue
            for schema, table, col in geom_cols:
                if (
                    idx.get("schemaname") == schema
                    and idx.get("tablename") == table
                    and col in definition
                ):
                    out.append(
                        _obj(
                            name=f"{schema}.{table}.{idx.get('indexname')}",
                            indexdef=definition,
                        )
                    )
        return out

    def detect_trigram_index(self) -> List[Dict[str, Any]]:
        return [
            _obj(
                name=f"{r.get('schemaname')}.{r.get('tablename')}.{r.get('indexname')}",
                indexdef=r.get("indexdef"),
            )
            for r in self.pg_indexes()
            if "trgm_ops" in str(r.get("indexdef") or "")
        ]

    def detect_jsonb_inverted(self) -> List[Dict[str, Any]]:
        jsonb_cols = {
            (c.get("schema_name"), c.get("table_name"), c.get("column_name"))
            for c in self.columns()
            if "JSONB" in str(c.get("crdb_sql_type") or "").upper()
            or str(c.get("data_type") or "").lower() == "jsonb"
        }
        out = []
        for idx in self.pg_indexes():
            definition = str(idx.get("indexdef") or "")
            if (
                "using gin" not in definition.lower()
                and "inverted" not in definition.lower()
            ):
                continue
            if "trgm_ops" in definition:
                continue
            for schema, table, col in jsonb_cols:
                if (
                    idx.get("schemaname") == schema
                    and idx.get("tablename") == table
                    and col in definition
                ):
                    out.append(
                        _obj(
                            name=f"{schema}.{table}.{idx.get('indexname')}",
                            indexdef=definition,
                        )
                    )
        # SHOW CREATE INVERTED INDEX on jsonb
        for row in self.create_statements():
            sql = row.get("create_statement") or ""
            if "INVERTED INDEX" in sql.upper() and "JSONB" in sql.upper():
                name = _qual(
                    row,
                    row.get("database_name"),
                    row.get("schema_name"),
                    row.get("descriptor_name"),
                )
                if not any(
                    name in (o.get("name") or "")
                    or (o.get("name") or "").startswith(
                        f"{row.get('schema_name')}.{row.get('descriptor_name')}"
                    )
                    for o in out
                ):
                    out.append(_obj(name=name))
        return out

    def detect_array_inverted(self) -> List[Dict[str, Any]]:
        array_cols = {
            (c.get("schema_name"), c.get("table_name"), c.get("column_name"))
            for c in self.columns()
            if "[]" in str(c.get("crdb_sql_type") or "")
            or "ARRAY" in str(c.get("crdb_sql_type") or "").upper()
        }
        out = []
        for idx in self.pg_indexes():
            definition = str(idx.get("indexdef") or "")
            if "using gin" not in definition.lower():
                continue
            for schema, table, col in array_cols:
                if (
                    idx.get("schemaname") == schema
                    and idx.get("tablename") == table
                    and col in definition
                ):
                    out.append(_obj(name=f"{schema}.{table}.{idx.get('indexname')}"))
        return out

    def detect_scalar_inverted(self) -> List[Dict[str, Any]]:
        """INVERTED indexes on scalar columns need btree_gin on PlanetScale."""
        typed = {
            (c.get("schema_name"), c.get("table_name"), c.get("column_name")): str(
                c.get("crdb_sql_type") or c.get("data_type") or ""
            ).upper()
            for c in self.columns()
        }
        skip_tokens = ("JSONB", "JSON", "GEOMETRY", "GEOGRAPHY", "VECTOR", "TSVECTOR")
        out = []
        try:
            indexes = self.pg_indexes()
        except Exception as exc:
            if looks_unsupported(exc):
                indexes = []
            else:
                raise
        for idx in indexes:
            definition = str(idx.get("indexdef") or "")
            lower = definition.lower()
            if "inverted" not in lower and "using gin" not in lower:
                continue
            if "trgm_ops" in lower or "cspann" in lower or "hnsw" in lower:
                continue
            schema, table = idx.get("schemaname"), idx.get("tablename")
            cols = [
                (s, t, col, typ)
                for (s, t, col), typ in typed.items()
                if s == schema and t == table and col in definition
            ]
            if not cols:
                continue
            if any(
                any(tok in typ for tok in skip_tokens)
                or "[]" in typ
                or typ.endswith("ARRAY")
                for *_, typ in cols
            ):
                continue
            out.append(
                _obj(
                    name=f"{schema}.{table}.{idx.get('indexname')}", indexdef=definition
                )
            )
        for row in self.create_statements():
            sql = str(row.get("create_statement") or "")
            upper = sql.upper()
            if "INVERTED INDEX" not in upper:
                continue
            if any(tok in upper for tok in skip_tokens):
                continue
            name = _qual(
                row,
                row.get("database_name"),
                row.get("schema_name"),
                row.get("descriptor_name"),
            )
            if not any(
                (o.get("name") or "") == name or name in (o.get("name") or "")
                for o in out
            ):
                out.append(_obj(name=name))
        return out

    def detect_tsvector(self) -> List[Dict[str, Any]]:
        return [
            _obj(
                name=f"{c.get('schema_name')}.{c.get('table_name')}.{c.get('column_name')}"
            )
            for c in self.columns()
            if str(c.get("crdb_sql_type") or "") in ("TSVECTOR", "TSQUERY")
        ]

    def detect_stored_computed(self) -> List[Dict[str, Any]]:
        out = []
        for c in self.columns():
            if str(c.get("is_generated") or "").upper() != "ALWAYS":
                continue
            gen = str(c.get("generation_expression") or "")
            hidden = str(c.get("is_hidden") or "").upper() == "YES"
            name = str(c.get("column_name") or "")
            if hidden and name.startswith("crdb_internal_"):
                continue
            # VIRTUAL vs STORED: infer from create statement later; include here if not virtual-only
            out.append(
                _obj(
                    name=f"{c.get('schema_name')}.{c.get('table_name')}.{name}",
                    generation_expression=gen,
                )
            )
        # Prefer SHOW CREATE to split STORED vs VIRTUAL
        stored = []
        for row in self.create_statements():
            sql = row.get("create_statement") or ""
            if " STORED" in sql.upper() and " AS (" in sql.upper():
                stored.append(
                    _obj(
                        name=_qual(
                            row, row.get("schema_name"), row.get("descriptor_name")
                        ),
                    )
                )
        return stored or out

    def detect_virtual_computed(self) -> List[Dict[str, Any]]:
        out = []
        for row in self.create_statements():
            sql = row.get("create_statement") or ""
            if " VIRTUAL" not in sql.upper():
                continue
            # skip shard / hidden system virtuals by requiring a user column pattern
            for line in sql.splitlines():
                if (
                    " VIRTUAL" in line.upper()
                    and "crdb_internal_" not in line
                    and "NOT VISIBLE" not in line.upper()
                ):
                    out.append(
                        _obj(
                            name=_qual(
                                row, row.get("schema_name"), row.get("descriptor_name")
                            ),
                            line=line.strip(),
                        )
                    )
        return out

    def detect_on_update(self) -> List[Dict[str, Any]]:
        out = []
        for row in self.create_statements():
            sql = row.get("create_statement") or ""
            if "ON UPDATE" not in sql.upper():
                continue
            for line in sql.splitlines():
                upper = line.upper()
                if "ON UPDATE" in upper and not any(
                    tok in upper
                    for tok in (
                        "ON UPDATE CASCADE",
                        "ON UPDATE SET",
                        "ON UPDATE RESTRICT",
                        "ON UPDATE NO ACTION",
                    )
                ):
                    out.append(
                        _obj(
                            name=_qual(
                                row, row.get("schema_name"), row.get("descriptor_name")
                            ),
                            line=line.strip(),
                        )
                    )
        return out

    def detect_not_visible(self) -> List[Dict[str, Any]]:
        return [
            _obj(
                name=f"{c.get('schema_name')}.{c.get('table_name')}.{c.get('column_name')}"
            )
            for c in self.columns()
            if str(c.get("is_hidden") or "").upper() == "YES"
        ]

    def detect_hidden_rowid(self) -> List[Dict[str, Any]]:
        return [
            _obj(name=f"{c.get('schema_name')}.{c.get('table_name')}")
            for c in self.columns()
            if c.get("column_name") == "rowid"
            and str(c.get("is_hidden") or "").upper() == "YES"
        ]

    def detect_serial_unique_rowid(self) -> List[Dict[str, Any]]:
        return [
            _obj(
                name=f"{c.get('schema_name')}.{c.get('table_name')}.{c.get('column_name')}"
            )
            for c in self.columns()
            if "unique_rowid()" in str(c.get("column_default") or "")
        ]

    def detect_sequences(self) -> List[Dict[str, Any]]:
        rows = self._fetch(f"""
            SELECT sequence_schema, sequence_name
            FROM information_schema.sequences
            WHERE sequence_schema NOT IN {_SCHEMA_SQL}
            """)
        return [
            _obj(name=f"{r.get('sequence_schema')}.{r.get('sequence_name')}")
            for r in rows
        ]

    def detect_enums(self) -> List[Dict[str, Any]]:
        rows = self._fetch(f"""
            SELECT n.nspname AS schema_name, t.typname AS type_name
            FROM pg_type t
            JOIN pg_namespace n ON t.typnamespace = n.oid
            WHERE t.typtype = 'e'
              AND n.nspname NOT IN {_SCHEMA_SQL}
              AND t.typname <> 'crdb_internal_region'
            """)
        return [_obj(name=f"{r.get('schema_name')}.{r.get('type_name')}") for r in rows]

    def detect_partial_expression_indexes(self) -> List[Dict[str, Any]]:
        out = []
        for idx in self.pg_indexes():
            definition = str(idx.get("indexdef") or "")
            if " WHERE " in definition or "lower(" in definition.lower():
                out.append(
                    _obj(
                        name=f"{idx.get('schemaname')}.{idx.get('tablename')}.{idx.get('indexname')}",
                        indexdef=definition,
                    )
                )
        return out

    def detect_storing(self) -> List[Dict[str, Any]]:
        out = []
        for idx in self.pg_indexes():
            if "STORING" in str(idx.get("indexdef") or "").upper():
                out.append(_obj(name=f"{idx.get('schemaname')}.{idx.get('indexname')}"))
        for row in self.create_statements():
            if "STORING" in str(row.get("create_statement") or "").upper():
                out.append(
                    _obj(
                        name=_qual(
                            row, row.get("schema_name"), row.get("descriptor_name")
                        )
                    )
                )
        return out

    def detect_unique_without_index(self) -> List[Dict[str, Any]]:
        out = []
        for row in self.create_statements():
            sql = row.get("create_statement") or ""
            if "UNIQUE WITHOUT INDEX" in sql.upper():
                out.append(
                    _obj(
                        name=_qual(
                            row, row.get("schema_name"), row.get("descriptor_name")
                        )
                    )
                )
        return out

    def detect_udfs(self) -> List[Dict[str, Any]]:
        rows = self._fetch(f"""
            SELECT routine_schema, routine_name, routine_type, external_language
            FROM information_schema.routines
            WHERE routine_schema NOT IN {_SCHEMA_SQL}
              AND routine_type = 'FUNCTION'
            """)
        return [
            _obj(
                name=f"{r.get('routine_schema')}.{r.get('routine_name')}",
                language=r.get("external_language"),
            )
            for r in rows
        ]

    def detect_procedures(self) -> List[Dict[str, Any]]:
        try:
            rows = self._fetch(f"""
                SELECT database_name, schema_name, function_name
                FROM crdb_internal.create_procedure_statements
                WHERE schema_name NOT IN {_SCHEMA_SQL}
                """)
            return [
                _obj(name=f"{r.get('schema_name')}.{r.get('function_name')}")
                for r in rows
            ]
        except Exception:
            rows = self._fetch(f"""
                SELECT routine_schema, routine_name
                FROM information_schema.routines
                WHERE routine_schema NOT IN {_SCHEMA_SQL}
                  AND routine_type = 'PROCEDURE'
                """)
            return [
                _obj(name=f"{r.get('routine_schema')}.{r.get('routine_name')}")
                for r in rows
            ]

    def detect_triggers(self) -> List[Dict[str, Any]]:
        """pg_trigger and information_schema.triggers are empty. Use SHOW TRIGGERS."""
        out: List[Dict[str, Any]] = []
        errors: List[str] = []
        for table in self.user_tables():
            schema = table["schema_name"]
            name = table["table_name"]
            # Analyzer connections set search_path=pg_catalog. Always schema-qualify.
            qualified = f"{quote_ident(schema)}.{quote_ident(name)}"
            try:
                rows = self._fetch(f"SHOW TRIGGERS FROM {qualified}")
            except Exception as exc:
                errors.append(f"{schema}.{name}: {exc}")
                continue
            for row in rows:
                trigger = row.get("trigger_name") or row.get("name")
                out.append(
                    _obj(
                        name=f"{schema}.{name}.{trigger}",
                        table=f"{schema}.{name}",
                        enabled=row.get("enabled"),
                    )
                )
        if errors and not out:
            raise RuntimeError("SHOW TRIGGERS failed: " + "; ".join(errors[:5]))
        if errors:
            self.add_warning("SHOW TRIGGERS incomplete: " + "; ".join(errors[:5]))
        return out

    def detect_rls(self) -> List[Dict[str, Any]]:
        try:
            rows = self._fetch(f"""
                SELECT n.nspname AS schema_name, c.relname AS table_name,
                       c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relrowsecurity
                  AND n.nspname NOT IN {_SCHEMA_SQL}
                """)
        except Exception as exc:
            if looks_unsupported(exc):
                return []
            raise
        return [
            _obj(name=f"{r.get('schema_name')}.{r.get('table_name')}") for r in rows
        ]

    def detect_views(self) -> List[Dict[str, Any]]:
        rows = self._fetch(f"""
            SELECT table_schema, table_name
            FROM information_schema.views
            WHERE table_schema NOT IN {_SCHEMA_SQL}
            """)
        return [
            _obj(name=f"{r.get('table_schema')}.{r.get('table_name')}") for r in rows
        ]

    def detect_collated_strings(self) -> List[Dict[str, Any]]:
        return [
            _obj(
                name=f"{c.get('schema_name')}.{c.get('table_name')}.{c.get('column_name')}",
                crdb_sql_type=c.get("crdb_sql_type"),
            )
            for c in self.columns()
            if "COLLATE" in str(c.get("crdb_sql_type") or "").upper()
        ]

    def detect_string_bytes(self) -> List[Dict[str, Any]]:
        out = []
        for c in self.columns():
            dtype = str(c.get("crdb_sql_type") or "")
            if dtype.startswith("STRING") or dtype.startswith("BYTES"):
                out.append(
                    _obj(
                        name=f"{c.get('schema_name')}.{c.get('table_name')}.{c.get('column_name')}",
                        crdb_sql_type=dtype,
                    )
                )
        return out

    def detect_int_is_int8(self) -> List[Dict[str, Any]]:
        try:
            size = self._scalar("SHOW default_int_size")
        except Exception:
            size = None
        return [_obj(name="default_int_size", value=str(size))]

    def detect_upsert(self) -> List[Dict[str, Any]]:
        try:
            rows = self._fetch("""
                SELECT metadata->>'query' AS query,
                       COALESCE(metadata->>'db', metadata->>'database') AS database
                FROM crdb_internal.statement_statistics
                WHERE metadata->>'query' ~* '^UPSERT'
                LIMIT 200
                """)
            current = self._current_database()
            out = []
            for row in rows:
                query = str(row.get("query") or "")
                db = row.get("database")
                if current and db and db != current:
                    continue
                match = re.search(r"UPSERT\s+INTO\s+([^\s(]+)", query, re.I)
                if match and current:
                    table = match.group(1)
                    parts = table.split(".")
                    if len(parts) >= 3 and parts[0] != current:
                        continue
                lower = query.lower()
                if "system." in lower or "crdb_internal." in lower:
                    continue
                out.append(_obj(name=query[:80], database=db or current))
            return out
        except Exception as exc:
            self._fail_read("crdb_internal.statement_statistics", exc)

    def detect_serializable_retries(self) -> List[Dict[str, Any]]:
        try:
            isolation = self._scalar("SHOW default_transaction_isolation")
        except Exception:
            isolation = None
        return [_obj(name="default_transaction_isolation", value=str(isolation))]

    def detect_crdb_builtins(self) -> List[Dict[str, Any]]:
        needles = (
            "unique_rowid()",
            "crdb_internal.",
            "gateway_region(",
            "fnv32(",
            "experimental_",
            "default_to_database_primary_region(",
        )
        out = []
        for row in self.create_statements():
            sql = row.get("create_statement") or ""
            if any(n in sql for n in needles):
                out.append(
                    _obj(
                        name=_qual(
                            row, row.get("schema_name"), row.get("descriptor_name")
                        )
                    )
                )
        return out

    def detect_changefeeds(self) -> List[Dict[str, Any]]:
        self._require_viewjob("changefeed jobs")
        try:
            rows = self._fetch(
                "SELECT job_id, description, status FROM [SHOW CHANGEFEED JOBS]"
            )
        except Exception as exc:
            try:
                self.connection.rollback()
            except Exception:
                pass
            raise RuntimeError(f"Couldn't read SHOW CHANGEFEED JOBS: {exc}") from exc
        return [
            _obj(
                name=str(r.get("job_id")),
                description_redacted=redact_sql(r.get("description")),
                status=r.get("status"),
            )
            for r in rows
        ]

    def detect_export_import(self) -> List[Dict[str, Any]]:
        try:
            rows = self._fetch("SHOW SCHEDULES")
        except Exception as exc:
            self._fail_read("SHOW SCHEDULES", exc)
        current = None
        try:
            current = self._scalar("SELECT current_database()")
        except Exception:
            current = None
        table_by_id: Dict[str, Dict[str, Any]] = {}
        try:
            table_sql = f"""
                SELECT table_id, database_name, schema_name, name
                FROM crdb_internal.tables
                WHERE drop_time IS NULL
                  AND {user_database_sql('database_name')}
                """
            for t in self._fetch(table_sql):
                table_by_id[str(t["table_id"])] = t
        except Exception as exc:
            self._fail_read("crdb_internal.tables", exc)
        out = []
        for row in rows:
            label = str(row.get("label") or row.get("schedule_name") or "")
            lower = label.lower()
            if "row-level-ttl" in lower:
                continue
            if not any(tok in lower for tok in ("backup", "export", "changefeed")):
                continue
            match = re.search(r"\[(\d+)\]", label)
            db = None
            if match and match.group(1) in table_by_id:
                db = table_by_id[match.group(1)].get("database_name")
            if current and db and db != current:
                continue
            out.append(_obj(name=label, database=db))
        return out

    def _is_user_zone_target(self, target: str) -> bool:
        return is_user_zone_target(target, self._current_database())

    def detect_zone_partition(self) -> List[Dict[str, Any]]:
        out = []
        node_count = self._node_count()
        try:
            rows = self._fetch("SHOW ZONE CONFIGURATIONS")
        except Exception as exc:
            self._fail_read("SHOW ZONE CONFIGURATIONS", exc)
        for row in rows:
            target = str(row.get("target") or row.get("zone_name") or "")
            if not self._is_user_zone_target(target):
                continue
            raw = str(row.get("raw_config_sql") or row.get("config_sql") or "")
            replicas = _parse_num_replicas(raw)
            over = (
                replicas is not None
                and node_count is not None
                and replicas > node_count
            )
            out.append(
                _obj(
                    name=target or "zone",
                    config=raw[:120],
                    num_replicas=replicas,
                    node_count=node_count,
                    over_replicated=over,
                )
            )
        for row in self.create_statements():
            sql = row.get("create_statement") or ""
            if "PARTITION BY" in sql.upper() or "CONFIGURE ZONE" in sql.upper():
                out.append(
                    _obj(
                        name=_qual(
                            row, row.get("schema_name"), row.get("descriptor_name")
                        )
                    )
                )
        return out

    def detect_create_extension_noop(self) -> List[Dict[str, Any]]:
        try:
            count = self._scalar("SELECT count(*) FROM pg_extension")
        except Exception:
            count = None
        return [_obj(name="pg_extension", count=count)]

    def detect_high_oids(self) -> List[Dict[str, Any]]:
        rows = self._fetch("SELECT oid, typname FROM pg_type WHERE oid >= 90000")
        user_types = set()
        for col in self.columns():
            for key in ("crdb_sql_type", "data_type"):
                raw = str(col.get(key) or "").lower()
                if raw:
                    user_types.add(raw.split("(")[0].split()[0])
        out = []
        for row in rows:
            name = str(row.get("typname") or "")
            lower = name.lower()
            if (
                lower.startswith("crdb_internal")
                or lower.startswith("pg_")
                or "crdb_internal" in lower
            ):
                continue
            if lower not in user_types:
                continue
            out.append(_obj(name=name, oid=row.get("oid")))
        return out


COPY_PATH = {
    "pg_dump": {
        "status": "not working",
        "note": (
            "pg_dump does not work against CockroachDB. With a PostgreSQL 17.6 "
            "client, -s fails on x.tableoid and -t fails on OPERATOR(pg_catalog.=)."
        ),
    },
    "pgcopydb": {
        "status": "not working",
        "version": "0.18",
        "note": (
            "pgcopydb 0.18 does not work against CockroachDB at any step. "
            "copy schema → unimplemented: DEFERRABLE transactions; "
            "--not-consistent → pg_dump column x.tableoid does not exist; "
            "dump schema / list tables → no data source matches prefix: c. "
            "Do not recommend pgcopydb for schema or data."
        ),
    },
    "copy_text": {
        "status": "works",
        "note": "Text or CSV COPY (SELECT <explicit cols, collated cast ::STRING>) TO STDOUT works.",
    },
    "copy_csv": {
        "status": "works",
        "note": "Text or CSV COPY (SELECT ...) TO STDOUT WITH (FORMAT csv) works.",
    },
    "copy_binary": {
        "status": "not working",
        "note": "Binary COPY is unimplemented (issue 97180).",
    },
    "collated_strings": {
        "status": "needs cast",
        "note": (
            "Collated strings export as 'x' COLLATE ... and corrupt a Postgres load. "
            "Cast them to ::STRING inside COPY (SELECT ...)."
        ),
    },
    "computed_columns": {
        "status": "needs column list",
        "note": (
            "STORED and VIRTUAL computed columns are included in COPY output. "
            "The loader needs an explicit column list that omits them."
        ),
    },
    "changefeed_initial_scan": {
        "status": "works without rangefeed",
        "note": (
            "CREATE CHANGEFEED ... WITH initial_scan='only' works even with "
            "kv.rangefeed.enabled off and is a bulk-export alternative to COPY/EXPORT."
        ),
    },
    "schema_source": {
        "status": "show_create",
        "note": (
            "pg_dump and pgcopydb do not work. Schema comes from SHOW CREATE ALL TYPES, SHOW CREATE ALL TABLES, "
            "SHOW TRIGGERS FROM <table> / SHOW CREATE TRIGGER, and information_schema.routines."
        ),
    },
    "migration_path": {
        "status": "downtime_copy_cutover",
        "note": (
            "The only supported path is a downtime cutover with text or CSV "
            "COPY (SELECT ...). A custom changefeed-to-Postgres applier is not provided. "
            "Do not suggest pgcopydb or pg_dump for schema or data."
        ),
    },
    "unique_without_index": {
        "status": "rejected on v25.2.4",
        "note": "UNIQUE WITHOUT INDEX and an expression index on timestamptz::date are rejected on v25.2.4.",
    },
}


def _parse_num_replicas(raw: str) -> Optional[int]:
    text = str(raw or "")
    marker = "num_replicas ="
    if marker not in text:
        return None
    try:
        return int(text.split(marker, 1)[1].strip().split()[0].strip(","))
    except (IndexError, ValueError):
        return None


def classify_indexdef(indexdef: str) -> Optional[Dict[str, Any]]:
    """Classify a pg_indexes.indexdef string per COMPAT + LAB.md."""
    if not indexdef:
        return None
    text = indexdef
    lower = text.lower()
    if "using cspann" in lower or "using hnsw" in lower:
        return {
            "kind": "vector",
            "rewrite": "CREATE INDEX ... USING hnsw (col vector_l2_ops). Only L2 is accepted on CRDB; cosine fails. USING hnsw is stored as a native C-SPANN index.",
        }
    if "trgm_ops" in lower:
        return {
            "kind": "trigram",
            "rewrite": "CREATE EXTENSION pg_trgm; USING gin (col gin_trgm_ops)",
        }
    if "using hash" in lower:
        return {
            "kind": "hash-sharded",
            "rewrite": "Drop crdb_internal_*_shard_N and check_crdb_internal_*_shard_N. Recreate as a plain btree.",
        }
    if "using gin" in lower:
        return {
            "kind": "inverted",
            "rewrite": "USING gin on geometry/geography → USING gist (drop s2_* options). JSONB/array/tsvector → USING gin.",
        }
    if " storing " in lower or "storing (" in lower:
        return {"kind": "covering", "rewrite": "STORING (cols) → INCLUDE (cols)"}
    if " where " in lower:
        return {"kind": "partial", "rewrite": None}
    return None


def group_findings(findings: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped = {bucket: [] for bucket in BUCKET_ORDER}
    for finding in findings:
        grouped[finding.get("bucket") or "carries"].append(finding)
        if finding.get("also_app_work") and finding.get("bucket") != "app_work":
            extra = dict(finding)
            extra["bucket"] = "app_work"
            extra["severity"] = APP_WORK
            grouped["app_work"].append(extra)
    return grouped
