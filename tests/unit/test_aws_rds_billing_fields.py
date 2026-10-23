import logging

import pytest

from planetscale_discovery.cloud.analyzers.aws_analyzer import AWSAnalyzer
from planetscale_discovery.config.config_manager import AWSConfig

REGION = "us-east-1"

BILLED_INSTANCE = {
    "DBInstanceIdentifier": "app-primary",
    "Engine": "postgres",
    "EngineVersion": "13.20",
    "DBInstanceClass": "db.r6g.4xlarge",
    "AllocatedStorage": 2000,
    "StorageType": "gp3",
    "Iops": 12000,
    "StorageThroughput": 500,
    "MaxAllocatedStorage": 4000,
    "DedicatedLogVolume": True,
    "EngineLifecycleSupport": "open-source-rds-extended-support",
}

UNBILLED_INSTANCE = {
    "DBInstanceIdentifier": "app-small",
    "Engine": "postgres",
    "EngineVersion": "17.4",
    "DBInstanceClass": "db.t3.micro",
}


def _analyzer(mocker) -> AWSAnalyzer:
    analyzer = AWSAnalyzer(
        AWSConfig(enabled=True, regions=[REGION]), logging.getLogger("t")
    )
    analyzer.session = mocker.Mock()
    mocker.patch.object(analyzer, "_collect_region_metrics")
    return analyzer


def _full_path(analyzer, mocker, instance):
    mocker.patch.object(analyzer, "_fetch_rds_db_instances", return_value=[instance])
    [analysis] = analyzer._analyze_rds_instances(REGION)
    return analysis


def _focused_path(analyzer, mocker, instance):
    return analyzer._analyze_single_rds_instance(instance, REGION)


@pytest.mark.parametrize("path", [_full_path, _focused_path])
class TestRdsBillingFields:
    def test_billed_storage_and_support_fields_are_kept(self, mocker, path):
        analysis = path(_analyzer(mocker), mocker, BILLED_INSTANCE)

        assert analysis["iops"] == 12000
        assert analysis["storage_throughput"] == 500
        assert analysis["max_allocated_storage"] == 4000
        assert analysis["dedicated_log_volume"] is True
        assert analysis["engine_lifecycle_support"] == (
            "open-source-rds-extended-support"
        )

    def test_absent_fields_stay_unset(self, mocker, path):
        analysis = path(_analyzer(mocker), mocker, UNBILLED_INSTANCE)

        assert analysis["storage_throughput"] is None
        assert analysis["max_allocated_storage"] is None
        assert analysis["dedicated_log_volume"] is False
        assert analysis["engine_lifecycle_support"] is None
