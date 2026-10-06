"""The distribution tier: config, resolution, the pg_stats read, the CLI's
failure posture, and the bundle file's shape in both hash modes."""

import json
from unittest.mock import MagicMock, call

import pytest
from psycopg2.extras import RealDictCursor

from planetscale_discovery.config.config_manager import ConfigManager, WorkloadConfig
from planetscale_discovery.workload import cli_workload
from planetscale_discovery.workload import distributions as dist
from planetscale_discovery.workload.bundle import write_bundle

UUID = "c8e04639-e752-c11e-9f42-3a9018720192"
# UUID's join hash, from the golden vectors.
UUID_HASH = "880e09a2bf699bde4ac777360b204578553ecebe81c743cd111076b8f9fd260a"


def _table(schema, name, *columns):
    return {
        "schema_name": schema,
        "table_name": name,
        "columns": [{"column_name": c, "data_type": t} for c, t in columns],
    }


def _schema(*extra):
    return {
        "table_analysis": [
            _table("public", "issues", ("id", "bigint"), ("organization_id", "uuid")),
            _table("public", "events", ("organization_id", "uuid"), ("status", "text")),
            *extra,
        ]
    }


def _keys(targets):
    return [(t["schema"], t["table"], t["column"]) for t in targets]


class TestConfig:
    def _parse(self, workload):
        config = {"database": {"host": "h", "workload": workload}}
        return ConfigManager()._parse_config_dict(config).database.workload

    def test_the_nested_block_maps_flat_and_defaults_to_off_and_verbatim(self):
        w = self._parse({"distributions": {"by_column": ["a"], "hash_values": True}})
        assert (w.distributions_by_column, w.distributions_hash_values) == (
            ["a"],
            True,
        )
        w = self._parse({})
        assert (w.distributions_by_column, w.distributions_explicit) == ([], [])
        assert w.distributions_hash_values is False

    def test_only_the_nested_block_declares_a_flat_key_is_ignored(self):
        w = self._parse(
            {
                "distributions_by_column": ["a"],
                "distributions_hash_values": True,
                "distributions": {"explicit": ["t.d"]},
            }
        )
        assert (w.distributions_by_column, w.distributions_explicit) == ([], ["t.d"])
        assert w.distributions_hash_values is False

    def test_a_lone_entry_is_one_entry_never_its_characters_or_keys(self):
        spec = {"table": "issues", "column": "organization_id"}
        w = self._parse({"distributions": {"by_column": "a", "explicit": spec}})
        assert (w.distributions_by_column, w.distributions_explicit) == (["a"], [spec])

    def test_a_non_mapping_block_refuses_by_name(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            self._parse({"distributions": True})


class TestResolveTargets:
    def test_by_column_blankets_every_table_and_names_a_miss(self):
        targets, notes = dist.resolve_targets(
            _schema(), ["organization_id", "org_id"], [], None
        )
        assert _keys(targets) == [
            ("public", "events", "organization_id"),
            ("public", "issues", "organization_id"),
        ]
        assert len(notes) == 1 and "org_id" in notes[0]
        assert dist.resolve_targets(_schema(), ["organization_id"], [], ["x"])[0] == []

    @pytest.mark.parametrize(
        "spec",
        [
            "issues.organization_id",
            "public.issues.organization_id",
            {"table": "issues", "column": "organization_id"},
            {"schema": "public", "table": "ISSUES", "column": "Organization_Id"},
        ],
    )
    def test_explicit_resolves_to_the_inventory_names(self, spec):
        targets, notes = dist.resolve_targets(_schema(), [], [spec], None)
        assert _keys(targets) == [("public", "issues", "organization_id")]
        assert targets[0]["declared"] == "explicit" and notes == []

    def test_ambiguous_and_malformed_explicit_entries_are_named(self):
        schema = _schema(
            _table("audit", "issues", ("organization_id", "uuid")),
            _table("public", "Issues", ("organization_id", "uuid")),
        )
        explicit = [
            {"table": "issues", "column": "organization_id"},  # public and audit
            {"schema": "public", "table": "ISSUES", "column": "organization_id"},
            "too.many.dots",
            42,
            {},
        ]
        targets, notes = dist.resolve_targets(schema, [], explicit, None)
        assert targets == [] and len(notes) == 5
        # An exact match wins over a case fold.
        exact = {"schema": "public", "table": "Issues", "column": "organization_id"}
        targets, _ = dist.resolve_targets(schema, [], [exact], None)
        assert _keys(targets) == [("public", "Issues", "organization_id")]

    def test_a_blanket_and_explicit_overlap_reads_once(self):
        targets, _ = dist.resolve_targets(
            _schema(),
            ["organization_id"],
            ["issues.organization_id", "issues.id"],
            None,
        )
        assert [(t["table"], t["column"], t["declared"]) for t in targets] == [
            ("events", "organization_id", "by_column"),
            ("issues", "id", "explicit"),
            ("issues", "organization_id", "by_column"),
        ]


def _stats_row(freqs, vals, table="issues"):
    return {
        "schemaname": "public",
        "tablename": table,
        "attname": "organization_id",
        "n_distinct": 2740.0,
        "null_frac": 0.1,
        "most_common_freqs": freqs,
        "most_common_vals": vals,
        "last_analyze": "2026-09-03 08:00:00+00",
        "last_autoanalyze": None,
    }


KEY = ("public", "issues", "organization_id")


def _target(type_name="uuid"):
    return dict(zip(("schema", "table", "column"), KEY), type=type_name, declared="x")


class TestReadColumn:
    def test_the_picture_is_the_pg_stats_row_values_raw(self):
        out = dist.read_column({KEY: _stats_row([0.1, 0.4], ["a-a", "b-b"])}, _target())
        # Raw values, frequency descending; finalize hashes them by type.
        assert out["mcv"] == [
            {"value": "b-b", "frequency": 0.4},
            {"value": "a-a", "frequency": 0.1},
        ]
        assert out["mcv_coverage"] == pytest.approx(0.5)
        assert (out["table"], out["type"], out["n_distinct"]) == (
            "public.issues",
            "uuid",
            2740.0,
        )

    def test_coverage_never_exceeds_one(self):
        # Live PG 17 stores a three-value column's freqs as float4 thirds,
        # which sum to 1.00000002; the importer rejects coverage above 1.
        row = _stats_row([0.33333334] * 3, ["a", "b", "c"])
        assert dist.read_column({KEY: row}, _target("text"))["mcv_coverage"] == 1.0

    @pytest.mark.parametrize(
        "rows, type_name, error",
        [
            ({}, "uuid", "never ANALYZEd"),
            ({}, "time without time zone", "outside the distribution tier"),
            ({KEY: _stats_row([0.4], ["a", "b"])}, "uuid", "disagree in length"),
        ],
    )
    def test_an_unreadable_column_raises_by_name(self, rows, type_name, error):
        with pytest.raises((LookupError, ValueError), match=error):
            dist.read_column(rows, _target(type_name))

    def test_the_query_casts_and_prefers_the_whole_tree(self):
        # anyarray refuses a direct ::text[] cast, and a parent's two rows would
        # race without the order; only a live server checks either.
        assert "most_common_vals::text::text[]" in dist._PG_STATS_SQL
        assert "s.inherited DESC" in dist._PG_STATS_SQL


class TestCollectWiring:
    """A failed read never fails the collect, nor replaces an earlier reading."""

    def _run(self, conn, earlier=None, **workload):
        config = MagicMock()
        config.database.workload = WorkloadConfig(**workload)
        config.database.schemas = None
        store = MagicMock()
        store.read_schema.return_value = _schema()
        store.read_distributions.return_value = earlier
        cli_workload._collect_distributions(store, conn, config, MagicMock())
        return store

    def test_nothing_declared_reads_and_writes_nothing(self):
        conn = MagicMock()
        self._run(conn).write_distributions.assert_not_called()
        conn.cursor.assert_not_called()

    def test_a_column_without_a_stats_row_is_omitted_and_named(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [_stats_row([0.4], ["b-b"])]
        store = self._run(conn, distributions_by_column=["organization_id", {}])
        doc = store.write_distributions.call_args[0][0]
        assert [c["table"] for c in doc["columns"]] == ["public.issues"]
        assert any("events.organization_id" in n for n in doc["notes"])
        assert any("by_column {}" in n for n in doc["notes"])
        # The shared connection is rolled back before the one query, which
        # takes the keys as a single row-value list.
        assert conn.method_calls[0] == call.rollback()
        keys = (("public", "events", "organization_id"), KEY)
        assert cursor.execute.call_args[0] == (dist._PG_STATS_SQL, (keys,))

    @pytest.mark.parametrize("earlier", [None, {"columns": [{}], "notes": []}])
    def test_a_failed_read_is_named_once_and_keeps_an_earlier_one(self, earlier):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = RuntimeError("permission denied for pg_stats")
        store = self._run(conn, earlier, distributions_by_column=["organization_id"])
        if earlier:
            store.write_distributions.assert_not_called()
        else:
            doc = store.write_distributions.call_args[0][0]
            assert "permission denied" in doc["notes"][0]


class TestInitCheck:
    def _run(self, readable_rows, **workload):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = readable_rows
        config = MagicMock()
        config.database.workload = WorkloadConfig(**workload)
        config.database.schemas = None
        config.database.username = "planetscale_workload"
        logger = MagicMock()
        status = cli_workload._check_distributions(conn, _schema(), config, logger)
        errors = " ".join(c.args[0] for c in logger.error.call_args_list)
        infos = " ".join(c.args[0] for c in logger.info.call_args_list)
        return status, errors, infos

    def test_a_declaration_that_resolves_badly_stops_init(self):
        status, errors, _ = self._run([], distributions_by_column=["org_id"])
        assert status == cli_workload.EXIT_USAGE and "org_id" in errors

    def test_a_column_without_a_grant_stops_init_with_the_grant(self):
        rows = [{"granted": True, "row_security": False}] + [
            {"granted": False, "row_security": False}
        ]
        status, errors, _ = self._run(rows, distributions_by_column=["organization_id"])
        assert status == cli_workload.EXIT_CAPABILITY
        assert (
            "GRANT SELECT (organization_id) ON public.issues TO planetscale_workload;"
            in errors
        )
        assert "public.events" not in errors

    def test_row_security_stops_init_without_a_grant_line(self):
        rows = [{"granted": True, "row_security": True}]
        status, errors, _ = self._run(rows, distributions_explicit=["issues.id"])
        assert status == cli_workload.EXIT_CAPABILITY
        assert "row-level security" in errors and "GRANT" not in errors

    def test_a_readable_declaration_names_each_column_and_the_form(self):
        rows = [{"granted": True, "row_security": False}]
        status, _, infos = self._run(
            rows, distributions_explicit=["issues.id"], distributions_hash_values=False
        )
        assert status == cli_workload.EXIT_OK
        assert "VERBATIM" in infos and "public.issues.id" in infos

    def test_both_reads_ask_for_dict_rows_on_any_connection(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"granted": True, "row_security": False}
        cursor.fetchall.return_value = []
        dist.unreadable_targets(conn, [_target()])
        dist.collect_distributions(
            conn, _schema(), ["organization_id"], [], None, MagicMock()
        )
        assert conn.cursor.call_args_list == [call(cursor_factory=RealDictCursor)] * 2


def _column(value="b-b", frequency=0.9, column="organization_id", type_name="uuid"):
    return {
        "table": "public.issues",
        "column": column,
        "declared": "explicit",
        "type": type_name,
        "n_distinct": 1.0,
        "null_frac": 0.0,
        "mcv": [{"value": value, "frequency": frequency}],
        "mcv_coverage": frequency,
        "last_analyze": None,
        "last_autoanalyze": None,
    }


def _write(tmp_path, *columns, notes=(), hash_values=True):
    merged = {
        "basis": "windowed",
        "statements": {},
        "covered_seconds": 3600.0,
        "coverage": {
            "window_start": "2026-09-03T08:00:00+00:00",
            "window_end": "2026-09-04T08:00:00+00:00",
        },
        "server": {"hostname": "db.example", "version": "16.4"},
        "columns": [],
        "tables": {},
    }
    doc = {
        "columns_declared": {"by_column": [], "explicit": ["issues.organization_id"]},
        "columns": list(columns),
        "notes": list(notes),
    }
    summary = write_bundle(
        tmp_path,
        merged,
        {"table_analysis": []},
        collector_version="test",
        distributions=doc,
        distributions_hash_values=hash_values,
    )
    path = tmp_path / "distributions.json"
    written = json.loads(path.read_text()) if path.exists() else None
    return summary, written, (tmp_path / "README.md").read_text()


class TestWriteDistributions:
    def test_hashed_by_default_with_the_golden_hash(self, tmp_path):
        summary, written, readme = _write(tmp_path, _column(UUID, 0.4))
        assert (written["schema_version"], written["source"]) == (1, "pg_stats_mcv")
        assert written["values_hashed"] is True
        assert written["capture_id"] == summary["capture_id"]
        assert written["columns_declared"]["explicit"] == ["issues.organization_id"]
        assert written["columns"][0]["mcv"] == [
            {"value_hash": UUID_HASH, "frequency": 0.4}
        ]
        assert "VERBATIM" not in readme and "Treat those hashes as the values" in readme
        assert "- `public.issues.organization_id`" in readme

    def test_verbatim_mode_declares_itself_and_says_so_loudly(self, tmp_path):
        summary, written, readme = _write(tmp_path, _column(), hash_values=False)
        assert written["values_hashed"] is False
        assert written["columns"][0]["mcv"] == [{"value": "b-b", "frequency": 0.9}]
        assert any("VERBATIM" in c for c in summary["caveats"])
        assert "VERBATIM" in readme and "Treat those hashes" not in readme
        assert "it contains no rows from your data" not in readme.lower()

    def test_no_readings_no_file_but_the_notes_stand(self, tmp_path):
        summary, written, _ = _write(tmp_path, notes=["distributions: nope"])
        assert written is None and summary["distributions"]["taken"] is False
        assert "distributions: nope" in summary["caveats"]

    def test_a_malformed_column_is_omitted_never_the_bundle(self, tmp_path):
        malformed = {k: v for k, v in _column(column="x").items() if k != "mcv"}
        summary, written, _ = _write(tmp_path, malformed, _column(UUID))
        assert [c["column"] for c in written["columns"]] == ["organization_id"]
        assert any("public.issues.x not written" in c for c in summary["caveats"])

    def test_an_unhashable_value_drops_only_itself_and_never_reaches_a_caveat(
        self, tmp_path
    ):
        col = _column("1.50", frequency=0.6, column="balance", type_name="numeric")
        col["mcv"].append({"value": "NaN", "frequency": 0.3})
        col["mcv_coverage"] = 0.9
        summary, written, readme = _write(tmp_path, col)
        shipped = written["columns"][0]
        assert len(shipped["mcv"]) == 1 and shipped["mcv_coverage"] == 0.6
        assert any("1 value(s) left out" in c for c in summary["caveats"])
        assert "NaN" not in readme
