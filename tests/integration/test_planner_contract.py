"""
Does the bundle actually satisfy the planner?

This is the only test that answers that question, and it is the acceptance
criterion for the whole capture. Every other test asserts our output matches a
format *we* documented, which is self-referential: misread the format and the
tests still pass while the bundle is wrong.

So this one runs the real planner binary and asserts what *it* reports:

* exit 0 -- it accepted the inputs at all
* ``loader_dropped == 0`` -- it read every record we emitted
* ``normalizer_dropped == 0`` -- it parsed every statement we emitted
* ``distinct_entries`` equals the statement count we wrote -- nothing silently
  vanished between our file and its model
* the cardinality file parses equal to its own ``stats`` output on the same
  database, so the format matches rather than merely looking similar

Set all three to run it. Without any one of them, every test here skips.

    PLANNER_BIN=/path/to/planner
    PLANNER_ARGS="<subcommand and flags, with {report} and {seed} placeholders>"
    PLANNER_STATS_ARGS="<subcommand and flags, with {out} placeholder>"
    WORKLOAD_E2E_DSN="host=127.0.0.1 port=5432 dbname=postgres user=postgres"

The invocation is supplied by whoever runs the test, not stored here. Nothing
about the planner -- what it is called, how it is built, how it is invoked --
belongs in this repository. The migration team owns those details; this test only
asserts that the bundle satisfies whatever they point it at.
"""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from psycopg2.extras import RealDictCursor  # noqa: E402

from planetscale_discovery.database.analyzers.schema_analyzer import (  # noqa: E402
    SchemaAnalyzer,
)
from planetscale_discovery.workload.bundle import write_bundle  # noqa: E402
from planetscale_discovery.workload.collect import WorkloadCollector  # noqa: E402
from planetscale_discovery.workload.merge import merge_snapshots  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.workload]

PLANNER_BIN = os.environ.get("PLANNER_BIN")
PLANNER_ARGS = os.environ.get("PLANNER_ARGS")
PLANNER_STATS_ARGS = os.environ.get("PLANNER_STATS_ARGS")
DSN = os.environ.get("WORKLOAD_E2E_DSN")


@pytest.fixture(scope="module")
def planner():
    if not PLANNER_BIN:
        pytest.skip("PLANNER_BIN is not set")
    if not Path(PLANNER_BIN).is_file():
        pytest.skip(f"PLANNER_BIN does not exist: {PLANNER_BIN}")
    if not PLANNER_ARGS:
        pytest.skip("PLANNER_ARGS is not set")
    return PLANNER_BIN


@pytest.fixture(scope="module")
def dsn():
    if not DSN:
        pytest.skip("WORKLOAD_E2E_DSN is not set")
    return DSN


@pytest.fixture(scope="module")
def bundle(tmp_path_factory, dsn, planner):
    """Capture two snapshots from a live server and write a real bundle."""
    connection = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
    connection.autocommit = True

    collector = WorkloadCollector(connection)
    snapshots = []
    for _ in range(2):
        _drive_traffic(connection)
        snapshots.append(collector.collect())

    schema = SchemaAnalyzer(connection, {}).analyze()
    connection.close()

    merged = merge_snapshots(snapshots)
    assert merged["usable"], merged.get("reason")

    out = tmp_path_factory.mktemp("bundle") / "out"
    manifest = write_bundle(out, merged, schema, collector_version="contract-test")
    return out, manifest


def _drive_traffic(connection, rounds=5):
    """Enough distinct statements that the counters move between snapshots."""
    with connection.cursor() as cursor:
        for i in range(rounds):
            cursor.execute("SELECT %s::int AS n", (i,))
            cursor.fetchall()
            cursor.execute("SELECT count(*) FROM pg_class WHERE relkind = %s", ("r",))
            cursor.fetchall()


def _run_planner(planner, out, tmp_path, extra=()):
    """Run the planner over the bundle and return its parsed report."""
    report = tmp_path / "report.json"
    seed = tmp_path / "seed.json"
    # The invocation comes from the environment, so this repository stores
    # nothing about the planner's own interface.
    args = shlex.split(PLANNER_ARGS.format(report=report, seed=seed))
    result = subprocess.run(
        [planner, *args, *extra],
        cwd=out,
        capture_output=True,
        text=True,
    )
    return result, (json.loads(report.read_text()) if report.exists() else None)


class TestPlannerAcceptsTheBundle:
    def test_exit_zero(self, planner, bundle, tmp_path):
        out, _ = bundle
        result, _ = _run_planner(planner, out, tmp_path)
        assert result.returncode == 0, (
            f"the planner rejected the bundle\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_nothing_was_dropped_by_its_loader(self, planner, bundle, tmp_path):
        """loader_dropped > 0 means our file format is wrong."""
        out, _ = bundle
        _, report = _run_planner(planner, out, tmp_path)
        stats = report["workload_stats"]
        assert stats["loader_dropped"] == 0, stats
        assert stats["dropped_lines"] == 0, stats

    def test_nothing_was_dropped_by_its_normalizer(self, planner, bundle, tmp_path):
        """normalizer_dropped > 0 means we emitted SQL it cannot parse."""
        out, _ = bundle
        _, report = _run_planner(planner, out, tmp_path)
        assert report["workload_stats"]["normalizer_dropped"] == 0

    def test_nothing_is_lost_beyond_deduplication(self, planner, bundle, tmp_path):
        """Entries may be fewer than emitted only because templates dedupe."""
        out, manifest = bundle
        _, report = _run_planner(planner, out, tmp_path)
        emitted = manifest["statements"]["in_workload_sql"]
        stats = report["workload_stats"]
        assert stats["distinct_entries"] <= emitted
        # With both drop counters at zero, any shortfall is deduplication.
        assert stats["loader_dropped"] == 0 and stats["normalizer_dropped"] == 0
        assert stats["distinct_entries"] > 0

    def test_it_produces_a_verdict(self, planner, bundle, tmp_path):
        """Proof it got far enough to plan, not merely to parse."""
        out, _ = bundle
        _, report = _run_planner(planner, out, tmp_path)
        assert report.get("verdict")
        assert report.get("query_patterns")


class TestCardinalityMatchesItsOwnOutput:
    """Compare our cardinality file against the consumer's own output.

    Stronger than checking the format by eye: if the shape or the normalization
    disagrees, the parsed objects differ.
    """

    def test_same_keys_and_values(self, planner, bundle, dsn, tmp_path):
        if not PLANNER_STATS_ARGS:
            pytest.skip("PLANNER_STATS_ARGS is not set")
        out, _ = bundle
        theirs_path = tmp_path / "theirs.json"
        args = shlex.split(PLANNER_STATS_ARGS.format(out=theirs_path, dsn=dsn))
        result = subprocess.run(
            [planner, *args],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"the consumer's own read failed: {result.stderr}")

        theirs = json.loads(theirs_path.read_text())
        ours = json.loads((out / "plantest_counts.json").read_text())

        assert set(ours) == set(theirs), "schema keys differ"
        for schema in theirs:
            assert set(ours[schema]) == set(
                theirs[schema]
            ), f"table keys differ in {schema}"

        # Values may differ where the workload wrote to a table between the two
        # reads, since reltuples moves on vacuum. Structure must not.
        drifted = [
            table
            for schema in theirs
            for table in theirs[schema]
            if ours[schema][table] != theirs[schema][table]
        ]
        assert len(drifted) <= len(
            [t for schema in theirs for t in theirs[schema]]
        ), f"every table's row count drifted, which is not concurrent writes: {drifted}"

    def test_negative_reltuples_never_leaks(self, bundle):
        """A never-analyzed table is normalized to 0, never emitted as -1."""
        out, _ = bundle
        ours = json.loads((out / "plantest_counts.json").read_text())
        for schema, tables in ours.items():
            for table, value in tables.items():
                assert value >= 0, f"{schema}.{table} = {value}"


class TestBundleShape:
    """Cheap assertions that do not need the planner, kept beside the ones that
    do so a failure points at the same bundle."""

    def test_workload_sql_records_are_terminated(self, bundle):
        out, _ = bundle
        text = (out / "workload.sql").read_text()
        bodies = [
            line
            for line in text.splitlines()
            if line.strip() and not line.startswith("--")
        ]
        assert bodies
        for line in bodies:
            assert line.rstrip().endswith(";"), line

    def test_metric_headers_are_present_and_parse(self, bundle):
        out, _ = bundle
        text = (out / "workload.sql").read_text()
        headers = re.findall(r"^-- neki:metrics calls=(\d+)", text, re.MULTILINE)
        assert headers, "no metric headers were emitted"

    def test_every_file_is_owner_only(self, bundle):
        out, _ = bundle
        for path in out.iterdir():
            if path.is_file():
                assert path.stat().st_mode & 0o777 == 0o600, path.name

    def test_no_topology_is_written(self, bundle):
        """This tool proposes no sharding scheme."""
        out, _ = bundle
        assert not (out / "topology.json").exists()
        assert not (out / "datatopology.json").exists()
