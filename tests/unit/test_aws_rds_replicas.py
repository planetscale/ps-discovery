import logging

import boto3
import pytest
from moto import mock_aws

from planetscale_discovery.cloud.analyzers.aws_analyzer import AWSAnalyzer
from planetscale_discovery.config.config_manager import AWSConfig

REGION = "us-east-1"
PW = "password123"


@pytest.fixture
def aws_env(aws_credentials):
    with mock_aws():
        yield boto3.client("rds", region_name=REGION)


def _analyzer() -> AWSAnalyzer:
    cfg = AWSConfig(enabled=True, regions=[REGION], discover_all=True)
    analyzer = AWSAnalyzer(cfg, logging.getLogger("rds-replica-test"))
    assert analyzer.authenticate() is True
    return analyzer


class TestRdsReadReplicaLinks:
    def test_primary_and_replica_name_each_other(self, aws_env):
        aws_env.create_db_instance(
            DBInstanceIdentifier="app-primary",
            DBInstanceClass="db.r6g.large",
            Engine="postgres",
            MasterUsername="admin",
            MasterUserPassword=PW,
            AllocatedStorage=100,
        )
        aws_env.create_db_instance_read_replica(
            DBInstanceIdentifier="app-replica",
            SourceDBInstanceIdentifier="app-primary",
        )

        result = _analyzer().analyze()["resources"][REGION]
        by_id = {i["db_instance_identifier"]: i for i in result["rds_instances"]}

        primary = by_id["app-primary"]
        replica = by_id["app-replica"]
        assert primary["read_replica_db_instance_identifiers"] == ["app-replica"]
        assert primary["read_replica_source_db_instance_identifier"] is None
        assert replica["read_replica_source_db_instance_identifier"] == "app-primary"
        assert replica["read_replica_db_instance_identifiers"] == []

    def test_standalone_instance_has_empty_links(self, aws_env):
        aws_env.create_db_instance(
            DBInstanceIdentifier="solo",
            DBInstanceClass="db.t3.micro",
            Engine="postgres",
            MasterUsername="admin",
            MasterUserPassword=PW,
            AllocatedStorage=20,
        )

        [solo] = _analyzer().analyze()["resources"][REGION]["rds_instances"]

        assert solo["read_replica_source_db_instance_identifier"] is None
        assert solo["read_replica_db_instance_identifiers"] == []
        assert solo["read_replica_source_db_cluster_identifier"] is None
        assert solo["read_replica_db_cluster_identifiers"] == []
        assert solo["replica_mode"] is None


class TestAuroraClusterReplicaLinks:
    def test_replica_cluster_names_its_source(self, aws_env):
        west = boto3.client("rds", region_name="us-west-2")
        source_arn = west.create_db_cluster(
            DBClusterIdentifier="app-cluster",
            Engine="aurora-postgresql",
            MasterUsername="admin",
            MasterUserPassword=PW,
        )["DBCluster"]["DBClusterArn"]
        aws_env.create_db_cluster(
            DBClusterIdentifier="app-cluster-east",
            Engine="aurora-postgresql",
            MasterUsername="admin",
            MasterUserPassword=PW,
            ReplicationSourceIdentifier=source_arn,
        )
        aws_env.create_db_cluster(
            DBClusterIdentifier="app-cluster-solo",
            Engine="aurora-postgresql",
            MasterUsername="admin",
            MasterUserPassword=PW,
        )

        result = _analyzer().analyze()["resources"][REGION]
        by_id = {c["identifier"]: c for c in result["aurora_clusters"]}

        assert by_id["app-cluster-east"]["replication_source_identifier"] == source_arn
        assert by_id["app-cluster-solo"]["replication_source_identifier"] is None
        assert by_id["app-cluster-solo"]["read_replica_identifiers"] == []


class TestFocusedPathReplicaLinks:
    def test_cross_region_replica_keeps_the_source_arn(self, mocker):
        analyzer = AWSAnalyzer(
            AWSConfig(enabled=True, regions=[REGION]), logging.getLogger("t")
        )
        analyzer.session = mocker.Mock()
        mocker.patch.object(analyzer, "_collect_region_metrics")
        source_arn = "arn:aws:rds:us-west-2:123456789012:db:app-primary"

        analysis = analyzer._analyze_single_rds_instance(
            {
                "DBInstanceIdentifier": "app-replica-east",
                "Engine": "postgres",
                "DBInstanceClass": "db.r6g.large",
                "ReadReplicaSourceDBInstanceIdentifier": source_arn,
                "ReplicaMode": "open-read-only",
            },
            REGION,
        )

        assert analysis["read_replica_source_db_instance_identifier"] == source_arn
        assert analysis["replica_mode"] == "open-read-only"
        assert analysis["read_replica_db_instance_identifiers"] == []

    def test_aurora_replica_cluster_names_its_source(self, mocker):
        analyzer = AWSAnalyzer(
            AWSConfig(enabled=True, regions=[REGION]), logging.getLogger("t")
        )
        mocker.patch.object(analyzer, "_collect_region_metrics")
        source_arn = "arn:aws:rds:us-west-2:123456789012:cluster:app-cluster"

        analysis = analyzer._analyze_single_aurora_cluster(
            {
                "DBClusterIdentifier": "app-cluster-east",
                "Engine": "aurora-postgresql",
                "EngineMode": "provisioned",
                "ReplicationSourceIdentifier": source_arn,
            },
            REGION,
        )

        assert analysis["replication_source_identifier"] == source_arn
        assert analysis["read_replica_identifiers"] == []
