"""Tests for the schema fingerprint and the schema copies a capture keeps.

init reads the schema once, and finalize reads it again. A column dropped or
renamed between the two left no record, so a statement from before the change
could not be explained. Each collect now records a fingerprint, and keeps a
copy of each schema that is new to the session.
"""

from unittest.mock import MagicMock

import pytest

from planetscale_discovery.workload.bundle import (
    SCHEMA_CHANGES_LISTED,
    schema_change_caveats,
    schema_read_caveat,
    write_bundle,
)
from planetscale_discovery.workload.collect import (
    SCHEMA_FINGERPRINT_SQL,
    WorkloadCollector,
)
from planetscale_discovery.workload.merge import merge_snapshots
from planetscale_discovery.workload.store import WorkloadStore

FP_A = "a" * 64
FP_B = "b" * 64
T0 = "2026-10-14 10:00:00+00:00"
T1 = "2026-10-14 11:00:00+00:00"
T2 = "2026-10-14 12:00:00+00:00"
T3 = "2026-10-14 13:00:00+00:00"
SERVER = {"database": "app", "database_oid": 1, "version_num": 170000}
SCHEMA = {
    "table_analysis": [
        {
            "schema_name": "public",
            "table_name": "orders",
            "table_type": "r",
            "estimated_rows": 1000,
            "columns": [{"column_name": "id", "data_type": "bigint"}],
        }
    ],
    "index_analysis": [],
    "constraint_analysis": [],
}


class TestTheFingerprintQuery:
    def test_the_digest_is_sha256(self):
        """md5() fails on a FIPS build of PostgreSQL."""
        assert "sha256(" in SCHEMA_FINGERPRINT_SQL
        assert "md5(" not in SCHEMA_FINGERPRINT_SQL

    def test_each_literal_placeholder_survives_the_parameters(self):
        """The query takes parameters, so an unescaped %s would be filled in."""
        rendered = SCHEMA_FINGERPRINT_SQL % {"excluded": "ARRAY['pg_catalog']"}
        assert rendered.count("%s") == SCHEMA_FINGERPRINT_SQL.count("%%s")
        assert "format('idx %s'" in rendered
        assert "ARRAY['pg_catalog']" in rendered

    @pytest.mark.parametrize(
        "part",
        ["pg_attrdef", "pg_get_viewdef", "pg_sequence", "attidentity", "'rel "],
    )
    def test_it_covers_each_kind_of_definition(self, part):
        """A default, a view body, a sequence and a table with no column are
        all schema changes that a column list alone does not show."""
        assert part in SCHEMA_FINGERPRINT_SQL

    def test_temporary_tables_do_not_change_it(self):
        assert "relpersistence <> 't'" in SCHEMA_FINGERPRINT_SQL
        assert "^pg_(toast|temp)" in SCHEMA_FINGERPRINT_SQL


def collector_with(fingerprint=None, error=None):
    """A collector whose server has no tables and returns this fingerprint."""
    collector = WorkloadCollector(MagicMock())
    queries = []

    def rows(sql, params=None):
        queries.append(sql)
        if sql == SCHEMA_FINGERPRINT_SQL:
            if error:
                raise error
            return [{"fingerprint": fingerprint}]
        if sql == "SELECT clock_timestamp() AS captured_at_server":
            return [{"captured_at_server": T0}]
        return []

    collector._rows = rows
    return collector, queries


class TestTheSnapshotRecordsTheFingerprint:
    def test_the_fingerprint_is_recorded(self):
        collector, _ = collector_with(FP_A)
        snapshot = collector.collect()
        assert snapshot["schema_fingerprint"] == FP_A
        assert snapshot["schema_version"] == 3

    def test_the_fingerprint_query_runs_last(self):
        """A failed query aborts the transaction for the queries after it."""
        collector, queries = collector_with(FP_A)
        collector.collect()
        assert queries[-1] == SCHEMA_FINGERPRINT_SQL

    def test_an_earlier_failure_is_rolled_back_before_the_query(self):
        """pg_stat_statements_info does not exist before PostgreSQL 14, so
        that read fails on every collect there and aborts the transaction."""
        collector, queries = collector_with(FP_A)
        collector.connection.rollback.side_effect = lambda: queries.append("ROLLBACK")
        snapshot = collector.collect()
        assert queries[-3:-1] == ["ROLLBACK", SCHEMA_FINGERPRINT_SQL]
        assert snapshot["schema_fingerprint"] == FP_A

    def test_the_transaction_ends_after_the_query(self):
        """The query holds ACCESS SHARE locks until the transaction ends, and a
        waiting ALTER TABLE makes every query on its table wait too."""
        collector, queries = collector_with(FP_A)
        collector.connection.rollback.side_effect = lambda: queries.append("ROLLBACK")
        collector.collect()
        assert queries[-2:] == [SCHEMA_FINGERPRINT_SQL, "ROLLBACK"]

    def test_a_failed_read_is_a_warning_and_rolls_back(self):
        collector, _ = collector_with(error=RuntimeError("statement timeout"))
        snapshot = collector.collect()
        assert snapshot["schema_fingerprint"] is None
        codes = [w["code"] for w in snapshot["warnings"]]
        assert "schema_fingerprint_failed" in codes
        assert snapshot["status"] == "degraded"
        # Once before the query, and once after it.
        assert collector.connection.rollback.call_count == 2


class TestSchemaCopies:
    def _store(self, tmp_path):
        store = WorkloadStore(tmp_path / "wl")
        store.create()
        return store

    def test_a_copy_round_trips(self, tmp_path):
        store = self._store(tmp_path)
        assert store.has_schema_copy(FP_A) is False
        store.write_schema_copy(FP_A, SCHEMA, "collect", T1)
        copy = store.read_schema_copy(FP_A)
        assert copy == {
            "fingerprint": FP_A,
            "read_by": "collect",
            "at": T1,
            "schema": SCHEMA,
        }
        assert store.read_schema_copy(FP_B) is None

    def test_copies_are_owner_only(self, tmp_path):
        store = self._store(tmp_path)
        path = store.write_schema_copy(FP_A, SCHEMA, "collect", T1)
        assert path.stat().st_mode & 0o777 == 0o600
        assert store.schema_copies_dir.stat().st_mode & 0o777 == 0o700

    def test_one_file_per_fingerprint(self, tmp_path):
        """Two overlapping collects that see the same schema write one copy."""
        store = self._store(tmp_path)
        store.write_schema_copy(FP_A, SCHEMA, "collect", T1)
        store.write_schema_copy(FP_A, SCHEMA, "collect", T2)
        assert len(store.schema_copy_paths()) == 1

    @pytest.mark.parametrize("name", ["../schema", "ABC", "a" * 63, ""])
    def test_a_name_that_is_not_a_digest_is_refused(self, tmp_path, name):
        """The fingerprint goes into a path."""
        with pytest.raises(ValueError):
            self._store(tmp_path).schema_copy_path(name)

    def test_copies_count_toward_the_session_size(self, tmp_path):
        store = self._store(tmp_path)
        before = store.total_bytes()
        path = store.write_schema_copy(FP_A, SCHEMA, "collect", T1)
        assert store.total_bytes() == before + path.stat().st_size
        assert store.schema_copy_bytes() == path.stat().st_size


def snap(fingerprint, at):
    return {
        "status": "ok",
        "captured_at_server": at,
        "server": SERVER,
        "statements": [],
        "tables": [],
        "indexes": [],
        "schema_fingerprint": fingerprint,
    }


class TestTheMergeDatesEachChange:
    def test_a_change_is_dated_by_the_two_snapshots_around_it(self):
        merged = merge_snapshots([snap(FP_A, T0), snap(FP_A, T1), snap(FP_B, T2)])
        assert merged["schema_changes"] == [{"start": T1, "end": T2}]
        assert merged["schema_fingerprints_missing"] == 0
        assert merged["schema_fingerprint"] == FP_B

    def test_a_snapshot_with_no_fingerprint_widens_the_span(self):
        merged = merge_snapshots([snap(FP_A, T0), snap(None, T1), snap(FP_B, T2)])
        assert merged["schema_changes"] == [{"start": T0, "end": T2}]
        assert merged["schema_fingerprints_missing"] == 1

    def test_a_schema_that_changes_back_counts_twice(self):
        merged = merge_snapshots([snap(FP_A, T0), snap(FP_B, T1), snap(FP_A, T2)])
        assert len(merged["schema_changes"]) == 2
        assert merged["schema_fingerprint"] == FP_A

    def test_a_failed_read_is_counted_apart_from_an_older_snapshot(self):
        older = snap(None, T1)
        del older["schema_fingerprint"]
        merged = merge_snapshots([snap(FP_A, T0), older, snap(None, T2)])
        assert merged["schema_fingerprints_missing"] == 2
        assert merged["schema_fingerprints_failed"] == 1

    def test_a_last_snapshot_with_no_fingerprint_names_no_copy(self):
        merged = merge_snapshots([snap(FP_A, T0), snap(None, T1)])
        assert merged["schema_fingerprint"] is None

    def test_one_snapshot_carries_its_fingerprint(self):
        merged = merge_snapshots([snap(FP_A, T0)], allow_partial=True)
        assert merged["schema_changes"] == []
        assert merged["schema_fingerprint"] == FP_A


def merged_window(usable=3, missing=0, changes=(), failed=0, **extra):
    result = {
        "usable": True,
        "basis": "windowed",
        "covered_seconds": 7200.0,
        "statements": {},
        "totals": {"distinct_statements": 0, "calls": 0},
        "coverage": {
            "intervals_used": usable - 1,
            "intervals_skipped": [],
            "snapshots_usable": usable,
            "window_start": T0,
            "window_end": T3,
        },
        "tables": {},
        "indexes": {},
        "lifetime": {"tables": {}, "indexes": {}},
        "resets_observed": 0,
        "schema_changes": list(changes),
        "schema_fingerprints_missing": missing,
        "schema_fingerprints_failed": failed,
        "schema_fingerprint": FP_B,
    }
    result.update(extra)
    return result


class TestTheCaveats:
    def test_each_change_is_named(self):
        changes = [{"start": T0, "end": T1}, {"start": T2, "end": T3}]
        caveats = schema_change_caveats(merged_window(changes=changes))
        assert len(caveats) == 1
        assert caveats[0].startswith("The schema changed 2 time(s) during the window")
        assert f"between {T0} and {T1}; between {T2} and {T3}." in caveats[0]
        assert "that schema.sql does not hold" in caveats[0]

    def test_a_long_list_is_cut(self):
        changes = [{"start": T0, "end": T1}] * (SCHEMA_CHANGES_LISTED + 2)
        caveat = schema_change_caveats(merged_window(changes=changes))[0]
        assert caveat.count("between") == SCHEMA_CHANGES_LISTED
        assert "; and 2 more." in caveat

    def test_a_schema_that_did_not_change_has_no_caveat(self):
        assert schema_change_caveats(merged_window()) == []

    def test_a_capture_from_an_earlier_version_says_it_cannot_tell(self):
        caveats = schema_change_caveats(merged_window(usable=3, missing=3))
        assert caveats == [
            "No snapshot holds a schema fingerprint: 3 from an earlier version "
            "of this tool. So this bundle cannot say whether the schema changed "
            "during the window."
        ]

    def test_a_snapshot_from_an_earlier_version_is_not_called_a_failure(self):
        """A session that spans the upgrade has old snapshots with no key."""
        caveat = schema_change_caveats(merged_window(usable=3, missing=1))[0]
        assert caveat.startswith(
            "1 of 3 snapshot(s) hold no schema fingerprint: 1 from an earlier "
            "version of this tool."
        )
        assert "W217" not in caveat

    def test_a_failed_read_names_w217(self):
        merged = merged_window(usable=4, missing=2, failed=1)
        caveat = schema_change_caveats(merged)[0]
        assert caveat.startswith(
            "2 of 4 snapshot(s) hold no schema fingerprint: 1 from an earlier "
            "version of this tool and 1 where the read failed "
            "(W217 schema_fingerprint_failed)."
        )

    def test_a_merge_from_before_the_fingerprint_says_nothing(self):
        merged = merged_window()
        del merged["schema_changes"]
        assert schema_change_caveats(merged) == []


class TestTheReadme:
    def _readme(self, tmp_path, merged, schema_read=None):
        summary = write_bundle(tmp_path, merged, SCHEMA, "t", schema_read=schema_read)
        return summary, (tmp_path / "README.md").read_text()

    def test_the_changes_are_counted(self, tmp_path):
        changes = [{"start": T1, "end": T2}]
        summary, readme = self._readme(tmp_path, merged_window(changes=changes))
        assert summary["schema"]["changes"] == 1
        assert "| Schema changes during the window | 1 |" in readme

    def test_no_change_is_a_zero(self, tmp_path):
        _, readme = self._readme(tmp_path, merged_window())
        assert "| Schema changes during the window | 0 |" in readme

    def test_an_unknown_count_has_no_row(self, tmp_path):
        """0 would claim that the schema did not change."""
        summary, readme = self._readme(tmp_path, merged_window(missing=3))
        assert summary["schema"]["changes"] is None
        assert "Schema changes during the window" not in readme

    def test_a_copy_from_collect_is_named(self, tmp_path):
        read = {"read": "collect", "at": T1, "unchanged_through": T3}
        summary, readme = self._readme(tmp_path, merged_window(), read)
        assert (
            f"| Schema read | by collect, at {T1}. The last snapshot, at {T3}, "
            "saw the same schema |"
        ) in readme
        assert not any("init read" in c for c in summary["caveats"])

    def test_a_copy_from_the_last_snapshot_names_one_time(self, tmp_path):
        read = {"read": "collect", "at": T3, "unchanged_through": T3}
        _, readme = self._readme(tmp_path, merged_window(), read)
        assert f"| Schema read | by collect, at {T3} |" in readme

    def test_the_init_read_that_still_held_has_no_caveat(self, tmp_path):
        read = {"read": "init", "at": T0, "unchanged_through": T3}
        summary, readme = self._readme(tmp_path, merged_window(), read)
        assert schema_read_caveat(read) is None
        assert (
            f"| Schema read | by init, before the first snapshot. The last "
            f"snapshot, at {T3}, saw the same schema |"
        ) in readme

    def test_the_init_read_with_no_fingerprint_keeps_its_caveat(self):
        assert "finalize could not read it again" in schema_read_caveat(
            {"read": "init", "before": T0}
        )


class TestCollectKeepsACopy:
    def _keep(self, mocker, stored=False, after=FP_A, read=SCHEMA, error=None, used=0):
        from planetscale_discovery.config.config_manager import WorkloadConfig
        from planetscale_discovery.workload.cli_workload import _keep_schema

        store = mocker.Mock()
        store.has_schema_copy.return_value = stored
        store.schema_copy_bytes.return_value = used
        config = mocker.Mock()
        config.database.workload = WorkloadConfig(max_session_mb=100)
        collector = mocker.Mock()
        collector.schema_fingerprint.return_value = after
        analyzer = mocker.patch(
            "planetscale_discovery.workload.cli_workload._collect_schema",
            return_value=read,
            side_effect=error,
        )
        connection = mocker.Mock()
        snapshot = {"schema_fingerprint": FP_A, "captured_at_server": T1}
        snapshot["warnings"] = []
        _keep_schema(store, connection, collector, snapshot, config, mocker.Mock())
        return store, snapshot, connection, analyzer

    def test_a_new_fingerprint_is_read_and_kept(self, mocker):
        store, snapshot, connection, _ = self._keep(mocker)
        store.write_schema_copy.assert_called_once_with(FP_A, SCHEMA, "collect", T1)
        assert snapshot["warnings"] == []
        connection.rollback.assert_called_once()

    def test_a_known_fingerprint_is_not_read(self, mocker):
        store, _, _, analyzer = self._keep(mocker, stored=True)
        analyzer.assert_not_called()
        store.write_schema_copy.assert_not_called()

    def test_a_schema_that_changes_during_the_read_is_not_kept(self, mocker):
        """The copy would hold a schema that the fingerprint does not name."""
        store, snapshot, _, _ = self._keep(mocker, after=FP_B)
        store.write_schema_copy.assert_not_called()
        assert [w["code"] for w in snapshot["warnings"]] == ["schema_copy_failed"]

    def test_a_failed_read_is_a_warning_and_rolls_back(self, mocker):
        """The distribution read comes next on the same connection."""
        store, snapshot, connection, _ = self._keep(
            mocker, error=RuntimeError("permission denied")
        )
        store.write_schema_copy.assert_not_called()
        assert [w["code"] for w in snapshot["warnings"]] == ["schema_copy_failed"]
        assert "permission denied" in snapshot["warnings"][0]["detail"]
        connection.rollback.assert_called_once()

    def test_the_copies_stop_at_a_quarter_of_the_session_cap(self, mocker):
        """max_session_mb stops the capture. A schema that changes on every
        tick must stop the copies first, and the snapshots continue."""
        store, snapshot, _, analyzer = self._keep(mocker, used=25 * 2**20)
        analyzer.assert_not_called()
        store.write_schema_copy.assert_not_called()
        assert "1/4 of max_session_mb" in snapshot["warnings"][0]["detail"]

    def test_copies_below_the_quarter_continue(self, mocker):
        store, _, _, _ = self._keep(mocker, used=25 * 2**20 - 1)
        store.write_schema_copy.assert_called_once()

    def test_a_failed_write_is_a_warning_and_keeps_the_snapshot(self, mocker):
        """The snapshot is appended after this, so an exception would lose it."""
        from planetscale_discovery.workload.cli_workload import _keep_schema

        store = mocker.Mock()
        store.has_schema_copy.return_value = False
        store.schema_copy_bytes.return_value = 0
        store.write_schema_copy.side_effect = OSError("No space left on device")
        collector = mocker.Mock()
        collector.schema_fingerprint.return_value = FP_A
        mocker.patch(
            "planetscale_discovery.workload.cli_workload._collect_schema",
            return_value=SCHEMA,
        )
        snapshot = {"schema_fingerprint": FP_A, "captured_at_server": T1}
        snapshot["warnings"] = []
        _keep_schema(store, mocker.Mock(), collector, snapshot, None, mocker.Mock())
        assert [w["code"] for w in snapshot["warnings"]] == ["schema_copy_failed"]
        assert "No space left on device" in snapshot["warnings"][0]["detail"]

    def test_an_empty_read_is_not_kept(self, mocker):
        """The analyzer returns an empty result, not an error, when a
        privilege is missing."""
        store, snapshot, _, _ = self._keep(mocker, read={})
        store.write_schema_copy.assert_not_called()
        assert [w["code"] for w in snapshot["warnings"]] == ["schema_copy_failed"]


class TestInitKeepsItsRead:
    def _keep(self, mocker, before, baseline):
        from planetscale_discovery.workload.cli_workload import _keep_init_schema

        store = mocker.Mock()
        snapshot = {"schema_fingerprint": baseline, "captured_at_server": T0}
        _keep_init_schema(store, before, SCHEMA, snapshot)
        return store

    def test_the_same_fingerprint_before_and_after_keeps_the_read(self, mocker):
        store = self._keep(mocker, FP_A, FP_A)
        store.write_schema_copy.assert_called_once_with(FP_A, SCHEMA, "init", T0)

    def test_a_failed_write_is_a_warning_on_the_baseline(self, mocker):
        from planetscale_discovery.workload.cli_workload import _keep_init_schema

        store = mocker.Mock()
        store.write_schema_copy.side_effect = OSError("read-only file system")
        snapshot = {"schema_fingerprint": FP_A, "captured_at_server": T0}
        snapshot["warnings"] = []
        _keep_init_schema(store, FP_A, SCHEMA, snapshot)
        assert [w["code"] for w in snapshot["warnings"]] == ["schema_copy_failed"]

    @pytest.mark.parametrize("before, baseline", [(FP_A, FP_B), (None, FP_A)])
    def test_otherwise_the_first_collect_reads_it(self, mocker, before, baseline):
        store = self._keep(mocker, before, baseline)
        store.write_schema_copy.assert_not_called()


class TestFinalizeWithoutAConnection:
    def _finalize(self, mocker, copy):
        from planetscale_discovery.workload.cli_workload import _finalize_schema

        mocker.patch(
            "planetscale_discovery.workload.cli_workload._connect",
            side_effect=OSError("connection refused"),
        )
        store = mocker.Mock()
        store.read_schema.return_value = {"table_analysis": [{"from": "init"}]}
        store.read_schema_copy.return_value = copy
        merged = {
            "coverage": {"window_start": T0, "window_end": T3},
            "schema_fingerprint": FP_B,
        }
        return store, _finalize_schema(store, merged, mocker.Mock(), mocker.Mock())

    def test_the_copy_for_the_last_fingerprint_is_rendered(self, mocker):
        copy = {"fingerprint": FP_B, "read_by": "collect", "at": T2, "schema": SCHEMA}
        store, (schema, read) = self._finalize(mocker, copy)
        store.read_schema_copy.assert_called_once_with(FP_B)
        assert schema is SCHEMA
        assert read == {
            "read": "collect",
            "at": T2,
            "unchanged_through": T3,
            "error": "connection refused",
        }

    def test_a_copy_that_cannot_be_read_falls_back_to_init(self, mocker):
        """A bad copy must not stop finalize before it tries to connect."""
        import gzip

        from planetscale_discovery.workload.cli_workload import _finalize_schema

        mocker.patch(
            "planetscale_discovery.workload.cli_workload._connect",
            side_effect=OSError("connection refused"),
        )
        store = mocker.Mock()
        store.read_schema.return_value = {"table_analysis": [{"from": "init"}]}
        store.read_schema_copy.side_effect = gzip.BadGzipFile("not a gzipped file")
        merged = {"coverage": {"window_start": T0}, "schema_fingerprint": FP_B}
        logger = mocker.Mock()
        schema, read = _finalize_schema(store, merged, mocker.Mock(), logger)
        assert schema == {"table_analysis": [{"from": "init"}]}
        assert read["read"] == "init"
        assert "cannot be read" in logger.warning.call_args_list[0][0][0]

    def test_with_no_copy_the_schema_from_init_is_rendered(self, mocker):
        _, (schema, read) = self._finalize(mocker, None)
        assert schema == {"table_analysis": [{"from": "init"}]}
        assert read == {"read": "init", "before": T0, "error": "connection refused"}


class TestTheWiring:
    """The helpers are tested above. These check that collect and init call
    them, because a missing call disables the feature with no error."""

    def _config(self):
        from types import SimpleNamespace

        from planetscale_discovery.config.config_manager import WorkloadConfig

        database = SimpleNamespace(
            workload=WorkloadConfig(), schemas=None, statement_timeout="300s"
        )
        return SimpleNamespace(database=database, engine="postgres")

    def _collector(self, mocker, at):
        collector = mocker.Mock()
        collector.schema_fingerprint.return_value = FP_A
        collector.collect.side_effect = lambda: {
            "status": "ok",
            "statements": [],
            "tables": [],
            "indexes": [],
            "captured_at_server": at,
            "warnings": [],
            "schema_fingerprint": FP_A,
        }
        return collector

    def test_collect_keeps_one_copy_per_fingerprint(self, tmp_path, mocker):
        from planetscale_discovery.workload import cli_workload

        store = WorkloadStore(tmp_path / "wl")
        store.create()
        store.write_schema(SCHEMA)
        mocker.patch.object(cli_workload, "_connect", return_value=mocker.Mock())
        mocker.patch.object(
            cli_workload, "_new_collector", return_value=self._collector(mocker, T1)
        )
        read = mocker.patch.object(cli_workload, "_collect_schema", return_value=SCHEMA)

        for _ in range(2):
            cli_workload._snapshot(store, self._config(), mocker.Mock(), optional=False)

        assert [p.name for p in store.schema_copy_paths()] == [f"schema-{FP_A}.json.gz"]
        assert store.read_schema_copy(FP_A)["read_by"] == "collect"
        assert read.call_count == 1
        assert len(store.snapshot_paths()) == 2

    def test_init_keeps_its_read(self, tmp_path, mocker):
        from planetscale_discovery.workload import cli_workload

        mocker.patch.object(cli_workload, "_connect", return_value=mocker.Mock())
        probe = mocker.patch.object(cli_workload, "CapabilityProbe")
        probe.return_value.run.return_value = {
            "can_collect_relations": True,
            "can_collect_statements": True,
            "server": {"is_replica": False},
            "gaps": [],
        }
        mocker.patch.object(
            cli_workload, "_new_collector", return_value=self._collector(mocker, T0)
        )
        mocker.patch.object(cli_workload, "_collect_schema", return_value=SCHEMA)
        mocker.patch.object(cli_workload, "_print_cron_hint")

        class Args:
            session = str(tmp_path / "wl")
            cleanup = False
            check = False
            force = False
            allow_replica = False

        assert cli_workload._init(Args(), self._config(), mocker.Mock()) == 0
        copy = WorkloadStore(tmp_path / "wl").read_schema_copy(FP_A)
        assert copy["read_by"] == "init"
        assert copy["schema"] == SCHEMA
