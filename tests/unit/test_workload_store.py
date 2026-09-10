"""Tests for the snapshot store."""

import gzip
import json

from planetscale_discovery.workload.store import WorkloadStore

T0 = "2026-08-25 10:00:00+00:00"
T1 = "2026-08-25 11:00:00+00:00"


def snap(at, status="ok", **extra):
    return {"status": status, "captured_at_server": at, "statements": [], **extra}


class TestLayout:
    def test_not_initialized_until_the_schema_is_written(self, tmp_path):
        store = WorkloadStore(tmp_path / "wl")
        assert store.exists() is False
        store.create()
        assert store.exists() is False
        store.write_schema({"table_analysis": []})
        assert store.exists() is True

    def test_directories_are_owner_only(self, tmp_path):
        store = WorkloadStore(tmp_path / "wl")
        store.create()
        assert store.directory.stat().st_mode & 0o777 == 0o700
        assert store.snapshots_dir.stat().st_mode & 0o777 == 0o700

    def test_files_are_owner_only(self, tmp_path):
        store = WorkloadStore(tmp_path / "wl")
        store.create()
        store.write_schema({"table_analysis": []})
        path = store.append_snapshot(snap(T0))
        assert store.schema_path.stat().st_mode & 0o777 == 0o600
        assert path.stat().st_mode & 0o777 == 0o600

    def test_schema_round_trips(self, tmp_path):
        store = WorkloadStore(tmp_path / "wl")
        store.create()
        store.write_schema({"table_analysis": [{"table_name": "t"}]})
        assert store.read_schema()["table_analysis"][0]["table_name"] == "t"


class TestSnapshots:
    def _store(self, tmp_path):
        store = WorkloadStore(tmp_path / "wl")
        store.create()
        return store

    def test_a_snapshot_is_gzipped_json(self, tmp_path):
        store = self._store(tmp_path)
        path = store.append_snapshot(snap(T0))
        with gzip.open(path, "rt") as handle:
            assert json.load(handle)["captured_at_server"] == T0

    def test_the_filename_carries_the_server_clock(self, tmp_path):
        path = self._store(tmp_path).append_snapshot(snap(T0))
        assert path.name.startswith("snapshot-20260825T100000")

    def test_two_collects_in_the_same_second_do_not_overwrite(self, tmp_path):
        store = self._store(tmp_path)
        first = store.append_snapshot(snap(T0))
        second = store.append_snapshot(snap(T0))
        assert first != second
        assert len(store.snapshot_paths()) == 2

    def test_snapshots_are_ordered_by_the_server_clock_not_the_filename(self, tmp_path):
        """A clock skew or a file copy must not reorder intervals."""
        store = self._store(tmp_path)
        store.append_snapshot(snap(T1))
        store.append_snapshot(snap(T0))
        stamps = [s["captured_at_server"] for s in store.read_snapshots()]
        assert stamps == [T0, T1]

    def test_a_corrupt_snapshot_is_reported_not_raised(self, tmp_path):
        store = self._store(tmp_path)
        store.append_snapshot(snap(T0))
        (store.snapshots_dir / "snapshot-20260825T120000Z.json.gz").write_bytes(b"nope")
        snapshots = store.read_snapshots()
        assert any(s.get("status") == "unreadable" for s in snapshots)

    def test_total_bytes_counts_the_snapshots(self, tmp_path):
        store = self._store(tmp_path)
        assert store.total_bytes() == 0
        store.append_snapshot(snap(T0))
        assert store.total_bytes() > 0


class TestStatus:
    def test_reports_readiness_and_the_window(self, tmp_path):
        store = WorkloadStore(tmp_path / "wl")
        store.create()
        store.write_schema({})
        store.append_snapshot(snap(T0))
        assert store.status()["ready_to_finalize"] is False
        store.append_snapshot(snap(T1))
        status = store.status()
        assert status["ready_to_finalize"] is True
        assert status["first_snapshot"] == T0
        assert status["last_snapshot"] == T1

    def test_failed_snapshots_are_not_counted_as_usable(self, tmp_path):
        store = WorkloadStore(tmp_path / "wl")
        store.create()
        store.write_schema({})
        store.append_snapshot(snap(T0))
        store.append_snapshot(snap(T1, status="failed"))
        status = store.status()
        assert status["snapshots"] == 2
        assert status["usable_snapshots"] == 1
        assert status["ready_to_finalize"] is False

    def test_an_uninitialized_directory_reports_so(self, tmp_path):
        assert WorkloadStore(tmp_path / "nope").status()["initialized"] is False
