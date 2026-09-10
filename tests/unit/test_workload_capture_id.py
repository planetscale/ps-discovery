"""One capture, one short id, on every file it produced.

Files get separated: someone extracts an archive next to another one, or
attaches a single file to a ticket. The id is what ties them back together, and
it is derived from the capture rather than random so it can also confirm *which*
capture, not merely that two files share one.
"""

from planetscale_discovery.workload.bundle import (
    bundle_dir_name,
    capture_id,
    write_bundle,
)

from .test_workload_bundle import SCHEMA, merged, stmt

SERVER = {"database": "app", "database_oid": 16401, "version_num": 170000}


def window(
    start="2026-08-25 10:00:00+00:00", end="2026-08-25 13:00:00+00:00", **server
):
    return {
        "server": {**SERVER, **server},
        "coverage": {"window_start": start, "window_end": end},
    }


class TestItIdentifiesTheCapture:
    def test_it_is_eight_characters(self):
        assert len(capture_id(window())) == 8

    def test_it_is_hexadecimal(self):
        int(capture_id(window()), 16)

    def test_the_same_capture_gives_the_same_id(self):
        """Re-running finalize on one session must not rename its bundle."""
        assert capture_id(window()) == capture_id(window())

    def test_a_different_window_gives_a_different_id(self):
        assert capture_id(window()) != capture_id(
            window(end="2026-08-25 14:00:00+00:00")
        )

    def test_a_different_database_gives_a_different_id(self):
        assert capture_id(window()) != capture_id(window(database="other"))

    def test_the_same_name_on_another_server_gives_a_different_id(self):
        """Two customers both called their database 'app'."""
        assert capture_id(window()) != capture_id(window(database_oid=99))

    def test_it_survives_a_capture_with_nothing_recorded(self):
        assert len(capture_id({})) == 8


class TestItNamesTheBundleDirectory:
    def test_the_directory_carries_the_id(self):
        assert bundle_dir_name(window()) == f"workload-{capture_id(window())}"


class TestItReachesEveryFile:
    def _bundle(self, tmp_path):
        payload = merged([stmt("a")])
        payload["server"] = SERVER
        payload["coverage"] = {
            "intervals_used": 1,
            "intervals": [],
            "intervals_skipped": [],
            "window_start": "2026-08-25 10:00:00+00:00",
            "window_end": "2026-08-25 13:00:00+00:00",
        }
        summary = write_bundle(tmp_path, payload, SCHEMA, "t")
        return summary, capture_id(payload)

    def test_the_manifest_carries_it(self, tmp_path):
        summary, identity = self._bundle(tmp_path)
        import json

        payload = json.loads((tmp_path / "manifest.json").read_text())
        assert payload["capture_id"] == identity

    def test_the_query_log_heads_with_it(self, tmp_path):
        _, identity = self._bundle(tmp_path)
        assert f"-- neki:capture {identity}" in (tmp_path / "workload.sql").read_text()

    def test_the_schema_heads_with_it(self, tmp_path):
        _, identity = self._bundle(tmp_path)
        assert (
            (tmp_path / "schema.sql")
            .read_text()
            .startswith(f"-- neki:capture {identity}")
        )

    def test_the_readme_names_it(self, tmp_path):
        _, identity = self._bundle(tmp_path)
        assert identity in (tmp_path / "README.md").read_text()

    def test_the_summary_returns_it(self, tmp_path):
        summary, identity = self._bundle(tmp_path)
        assert summary["capture_id"] == identity

    def test_the_schema_still_parses_with_the_header(self, tmp_path):
        """A comment is legal SQL, but the parser has to agree."""
        import pytest

        parser = pytest.importorskip("pglast.parser")
        self._bundle(tmp_path)
        parser.parse_sql((tmp_path / "schema.sql").read_text())
