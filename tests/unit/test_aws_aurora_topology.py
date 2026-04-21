"""
Aurora topology tests for the AWS analyzer.

Exercises `_fetch_rds_db_instances` + `_expand_aurora_cluster_members`
against a moto-mocked RDS account so we get fidelity-to-API coverage
for the different shapes of Aurora clusters customers run.
"""

import logging

import boto3
import pytest
from moto import mock_aws

from planetscale_discovery.cloud.analyzers.aws_analyzer import AWSAnalyzer
from planetscale_discovery.config.config_manager import AWSConfig

REGION = "us-east-1"
PW = "password123"


def _build_analyzer(aws_config: AWSConfig) -> AWSAnalyzer:
    analyzer = AWSAnalyzer(aws_config, logging.getLogger("aurora-topology-test"))
    assert analyzer.authenticate() is True
    return analyzer


def _create_aurora_cluster(rds, cluster_id: str, engine: str, member_ids: list):
    """Create an Aurora cluster and its member instances under moto.

    The first member_id is the writer; the rest are readers.
    """
    rds.create_db_cluster(
        DBClusterIdentifier=cluster_id,
        Engine=engine,
        MasterUsername="admin",
        MasterUserPassword=PW,
    )
    for instance_id in member_ids:
        rds.create_db_instance(
            DBInstanceIdentifier=instance_id,
            DBInstanceClass="db.r6g.large",
            Engine=engine,
            DBClusterIdentifier=cluster_id,
            MasterUsername="admin",
            MasterUserPassword=PW,
        )


@pytest.fixture
def aws_env(aws_credentials):
    """Moto-mocked AWS account scoped to a single test."""
    with mock_aws():
        yield boto3.client("rds", region_name=REGION)


class TestAuroraClusterAutoExpansion:
    """Listing a cluster alone should auto-resolve its member instances."""

    def test_non_ha_single_writer_cluster(self, aws_env):
        """A single-writer cluster expands to exactly one member."""
        _create_aurora_cluster(
            aws_env,
            cluster_id="non-ha-cluster",
            engine="aurora-postgresql",
            member_ids=["non-ha-writer"],
        )

        cfg = AWSConfig(
            enabled=True,
            regions=[REGION],
            discover_all=False,
            resources={"aurora_clusters": ["non-ha-cluster"]},
        )
        result = _build_analyzer(cfg).analyze()["resources"][REGION]

        assert len(result["aurora_clusters"]) == 1
        assert len(result["rds_instances"]) == 1
        assert result["rds_instances"][0]["db_instance_identifier"] == "non-ha-writer"
        assert result["rds_instances"][0]["engine"] == "aurora-postgresql"

    def test_ha_writer_plus_reader_cluster(self, aws_env):
        """A writer + reader cluster expands to both members, writer tagged."""
        _create_aurora_cluster(
            aws_env,
            cluster_id="ha-cluster",
            engine="aurora-postgresql",
            member_ids=["ha-writer", "ha-reader"],
        )

        cfg = AWSConfig(
            enabled=True,
            regions=[REGION],
            discover_all=False,
            resources={"aurora_clusters": ["ha-cluster"]},
        )
        result = _build_analyzer(cfg).analyze()["resources"][REGION]

        ids = sorted(i["db_instance_identifier"] for i in result["rds_instances"])
        assert ids == ["ha-reader", "ha-writer"]

        cluster = result["aurora_clusters"][0]
        writers = [m for m in cluster["cluster_members"] if m["is_cluster_writer"]]
        readers = [m for m in cluster["cluster_members"] if not m["is_cluster_writer"]]
        assert len(writers) == 1 and writers[0]["instance_identifier"] == "ha-writer"
        assert len(readers) == 1 and readers[0]["instance_identifier"] == "ha-reader"

    def test_multiple_read_replicas(self, aws_env):
        """One writer plus multiple readers all expand out."""
        _create_aurora_cluster(
            aws_env,
            cluster_id="multi-replica-cluster",
            engine="aurora-mysql",
            member_ids=[
                "multi-writer",
                "multi-reader-1",
                "multi-reader-2",
                "multi-reader-3",
            ],
        )

        cfg = AWSConfig(
            enabled=True,
            regions=[REGION],
            discover_all=False,
            resources={"aurora_clusters": ["multi-replica-cluster"]},
        )
        result = _build_analyzer(cfg).analyze()["resources"][REGION]

        ids = sorted(i["db_instance_identifier"] for i in result["rds_instances"])
        assert ids == [
            "multi-reader-1",
            "multi-reader-2",
            "multi-reader-3",
            "multi-writer",
        ]

        cluster = result["aurora_clusters"][0]
        writer_ids = [
            m["instance_identifier"]
            for m in cluster["cluster_members"]
            if m["is_cluster_writer"]
        ]
        reader_ids = [
            m["instance_identifier"]
            for m in cluster["cluster_members"]
            if not m["is_cluster_writer"]
        ]
        assert writer_ids == ["multi-writer"]
        assert sorted(reader_ids) == [
            "multi-reader-1",
            "multi-reader-2",
            "multi-reader-3",
        ]

    def test_unrelated_resources_are_excluded(self, aws_env):
        """Narrowing to one cluster must not pull in unrelated instances or clusters."""
        _create_aurora_cluster(
            aws_env,
            cluster_id="target-cluster",
            engine="aurora-postgresql",
            member_ids=["target-writer", "target-reader"],
        )
        _create_aurora_cluster(
            aws_env,
            cluster_id="other-cluster",
            engine="aurora-postgresql",
            member_ids=["other-writer"],
        )
        aws_env.create_db_instance(
            DBInstanceIdentifier="standalone-rds",
            DBInstanceClass="db.t3.micro",
            Engine="postgres",
            MasterUsername="admin",
            MasterUserPassword=PW,
            AllocatedStorage=20,
        )

        cfg = AWSConfig(
            enabled=True,
            regions=[REGION],
            discover_all=False,
            resources={"aurora_clusters": ["target-cluster"]},
        )
        result = _build_analyzer(cfg).analyze()["resources"][REGION]

        rds_ids = sorted(i["db_instance_identifier"] for i in result["rds_instances"])
        aur_ids = sorted(c["identifier"] for c in result["aurora_clusters"])
        assert rds_ids == ["target-reader", "target-writer"]
        assert aur_ids == ["target-cluster"]

    def test_explicit_instance_and_cluster_auto_dedupe(self, aws_env):
        """Listing both a cluster and one of its members shouldn't double-count."""
        _create_aurora_cluster(
            aws_env,
            cluster_id="dedupe-cluster",
            engine="aurora-mysql",
            member_ids=["dedupe-writer", "dedupe-reader"],
        )

        cfg = AWSConfig(
            enabled=True,
            regions=[REGION],
            discover_all=False,
            resources={
                "aurora_clusters": ["dedupe-cluster"],
                "rds_instances": ["dedupe-writer"],
            },
        )
        result = _build_analyzer(cfg).analyze()["resources"][REGION]

        ids = sorted(i["db_instance_identifier"] for i in result["rds_instances"])
        assert ids == ["dedupe-reader", "dedupe-writer"]
