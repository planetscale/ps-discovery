"""Tests for Aurora Serverless capacity and storage metric collection."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from planetscale_discovery.cloud.analyzers.aws_analyzer import AWSAnalyzer
from planetscale_discovery.cloud.analyzers.aws_metrics import (
    AURORA_CLUSTER_METRICS,
    AURORA_INSTANCE_METRICS,
    GET_METRIC_DATA_MAX_QUERIES,
    P95_BASIS_STAT,
    P95_STAT,
    RDS_INSTANCE_METRICS,
    MetricTarget,
    Series,
    build_metric_queries,
    chunk_queries,
    cluster_metric_table,
    derive_serverless_capacity,
    instance_metric_table,
    nearest_rank_p95,
    query_stats,
    summarize_target,
)

BASE = datetime(2026, 9, 10, tzinfo=timezone.utc)


def hours(count):
    return [BASE + timedelta(hours=i) for i in range(count)]


class TestPercentile:
    def test_empty_sample_has_no_percentile(self):
        assert nearest_rank_p95([]) is None

    def test_single_datapoint_is_its_own_percentile(self):
        assert nearest_rank_p95([7.5]) == 7.5

    def test_nearest_rank_picks_the_95th_of_a_hundred(self):
        assert nearest_rank_p95(list(range(1, 101))) == 95


class TestQueryBuilding:
    def test_p95_reuses_the_average_query_instead_of_adding_one(self):
        stats = query_stats(("Average", P95_STAT, "Maximum"))
        assert stats == ["Average", "Maximum"]

    def test_p95_adds_an_average_query_when_none_was_asked_for(self):
        assert query_stats((P95_STAT, "Maximum")) == ["Maximum", P95_BASIS_STAT]

    def test_query_ids_are_unique_and_route_back_to_their_target(self):
        targets = [
            MetricTarget(
                "instance:a",
                "DBInstanceIdentifier",
                "a",
                {
                    "CPUUtilization": ("cpu_utilization_pct", ("Average", "Maximum")),
                },
            ),
            MetricTarget(
                "cluster:b",
                "DBClusterIdentifier",
                "b",
                {
                    "VolumeBytesUsed": ("volume_bytes_used", ("Average",)),
                },
            ),
        ]

        queries, routing = build_metric_queries(targets, 3600)

        assert len(queries) == 3
        assert len({q["Id"] for q in queries}) == 3
        assert routing[queries[0]["Id"]][0] == "instance:a"
        assert routing[queries[2]["Id"]] == ("cluster:b", "VolumeBytesUsed", "Average")

    def test_hyphenated_identifiers_do_not_leak_into_query_ids(self):
        targets = [
            MetricTarget(
                "instance:prod-aave-1",
                "DBInstanceIdentifier",
                "prod-aave-1",
                {"CPUUtilization": ("cpu_utilization_pct", ("Average",))},
            )
        ]

        queries, _ = build_metric_queries(targets, 3600)

        assert "-" not in queries[0]["Id"]
        dimensions = queries[0]["MetricStat"]["Metric"]["Dimensions"]
        assert dimensions[0]["Value"] == "prod-aave-1"

    def test_batches_respect_the_get_metric_data_query_cap(self):
        queries = [{"Id": f"m{i}"} for i in range(GET_METRIC_DATA_MAX_QUERIES + 3)]

        batches = chunk_queries(queries)

        assert len(batches) == 2
        assert len(batches[0]) == GET_METRIC_DATA_MAX_QUERIES
        assert len(batches[1]) == 3


class TestTableSelection:
    def test_serverless_aurora_gets_the_acu_metrics(self):
        table = instance_metric_table("aurora-postgresql", "db.serverless")
        assert "ServerlessDatabaseCapacity" in table

    def test_provisioned_aurora_has_no_acu_metrics(self):
        table = instance_metric_table("aurora-postgresql", "db.r6g.large")
        assert table is AURORA_INSTANCE_METRICS
        assert "ServerlessDatabaseCapacity" not in table

    def test_plain_rds_gets_free_storage_space_not_aurora_local_storage(self):
        table = instance_metric_table("postgres", "db.m6g.large")
        assert table is RDS_INSTANCE_METRICS
        assert "FreeStorageSpace" in table
        assert "FreeLocalStorage" not in table

    def test_serverless_v1_cluster_gets_acu_at_the_cluster_level(self):
        assert "ServerlessDatabaseCapacity" in cluster_metric_table("serverless")
        assert cluster_metric_table("provisioned") is AURORA_CLUSTER_METRICS


class TestSummarize:
    def test_a_metric_with_no_datapoints_is_unavailable_not_zero(self):
        table = {"DBLoad": ("db_load", ("Average", "Maximum"))}

        summary, counts, unavailable = summarize_target(table, {})

        assert summary == {}
        assert counts == {}
        assert unavailable == ["DBLoad"]

    def test_average_series_reduces_to_a_mean_and_a_derived_p95(self):
        table = {"CPUUtilization": ("cpu_utilization_pct", ("Average", P95_STAT))}
        series = {("CPUUtilization", "Average"): Series(hours(4), [10, 20, 30, 40])}

        summary, counts, unavailable = summarize_target(table, series)

        assert summary["cpu_utilization_pct"]["avg"] == 25
        assert summary["cpu_utilization_pct"][P95_STAT] == 40
        assert counts["CPUUtilization"] == 4
        assert unavailable == []

    def test_minimum_and_maximum_reduce_across_the_window(self):
        table = {
            "ServerlessDatabaseCapacity": (
                "serverless_database_capacity_acu",
                ("Minimum", "Maximum"),
            )
        }
        series = {
            ("ServerlessDatabaseCapacity", "Minimum"): Series(hours(3), [8, 9, 8]),
            ("ServerlessDatabaseCapacity", "Maximum"): Series(hours(3), [40, 64, 50]),
        }

        summary, _, _ = summarize_target(table, series)

        assert summary["serverless_database_capacity_acu"]["min"] == 8
        assert summary["serverless_database_capacity_acu"]["max"] == 64


class TestDeriveServerlessCapacity:
    def test_missing_series_gives_no_derived_figures(self):
        assert derive_serverless_capacity(None, None) == {"aligned_datapoints": 0}

    def test_disjoint_timestamps_align_nothing(self):
        acu = Series(hours(3), [8, 16, 32])
        cpu = Series(
            [BASE + timedelta(days=5, hours=i) for i in range(3)], [50, 50, 50]
        )

        derived = derive_serverless_capacity(acu, cpu)

        assert derived["aligned_datapoints"] == 0
        assert "avg" not in derived

    def test_only_overlapping_timestamps_are_used(self):
        acu = Series(hours(4), [8, 8, 8, 8])
        cpu = Series(hours(2), [50, 50])

        derived = derive_serverless_capacity(acu, cpu, vcpu_per_acu=0.25)

        assert derived["aligned_datapoints"] == 2
        assert derived["avg"] == pytest.approx(1.0)

    def test_join_beats_the_product_of_means_when_scaling_tracks_load(self):
        timestamps = hours(4)
        acu = Series(timestamps, [4, 4, 64, 64])
        cpu = Series(timestamps, [10, 10, 90, 90])

        derived = derive_serverless_capacity(acu, cpu, vcpu_per_acu=0.25)

        product_of_means = (34 * 0.25) * 50 / 100
        assert derived["avg"] == pytest.approx(7.25)
        assert product_of_means == pytest.approx(4.25)
        assert derived["avg"] > product_of_means


class FakeConfig:
    def __init__(self):
        self.regions = ["us-east-1"]
        self.profile = None
        self.target_database = None


def make_analyzer():
    analyzer = AWSAnalyzer(FakeConfig())
    analyzer.session = MagicMock()
    return analyzer


def metric_data_response(queries, value=50.0, points=4):
    timestamps = hours(points)
    return {
        "MetricDataResults": [
            {
                "Id": query["Id"],
                "Timestamps": list(timestamps),
                "Values": [value] * points,
            }
            for query in queries
        ]
    }


class TestRegionCollection:
    def test_a_region_costs_one_get_metric_data_call(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        cloudwatch.get_metric_data.side_effect = lambda **kw: metric_data_response(
            kw["MetricDataQueries"]
        )

        instances = [
            {
                "db_instance_identifier": "instance-1",
                "engine": "aurora-postgresql",
                "db_instance_class": "db.serverless",
            },
            {
                "db_instance_identifier": "instance-2",
                "engine": "aurora-postgresql",
                "db_instance_class": "db.serverless",
            },
        ]
        clusters = [
            {
                "identifier": "cluster-1",
                "engine_mode": "provisioned",
                "allocated_storage": 1,
            }
        ]

        analyzer._collect_region_metrics("us-east-1", instances, clusters)

        assert cloudwatch.get_metric_data.call_count == 1

    def test_serverless_instance_gets_acu_and_derived_memory(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        cloudwatch.get_metric_data.side_effect = lambda **kw: metric_data_response(
            kw["MetricDataQueries"], value=32.0
        )

        instance = {
            "db_instance_identifier": "instance-1",
            "engine": "aurora-postgresql",
            "db_instance_class": "db.serverless",
        }

        analyzer._collect_region_metrics("us-east-1", [instance], [])

        capacity = instance["capacity"]
        assert capacity["type"] == "serverless_v2"
        assert capacity["acu"]["max"] == 32.0
        assert capacity["effective_memory_gib"]["max"] == 64.0
        assert capacity["assumptions"]["memory_gib_per_acu"] == 2.0
        assert capacity["observed_busy_vcpu"]["aligned_datapoints"] == 4

    def test_provisioned_instance_reports_its_class_and_no_acu(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        cloudwatch.get_metric_data.side_effect = lambda **kw: metric_data_response(
            kw["MetricDataQueries"], value=12.0
        )

        instance = {
            "db_instance_identifier": "instance-1",
            "engine": "aurora-postgresql",
            "db_instance_class": "db.r6g.large",
        }

        analyzer._collect_region_metrics("us-east-1", [instance], [])

        assert instance["capacity"] == {
            "type": "provisioned",
            "instance_class": "db.r6g.large",
            "db_load_avg_active_sessions": {"avg": 12.0, "p95": 12.0, "max": 12.0},
        }
        assert instance["cpu_utilization_avg"] == 12.0
        assert instance["cpu_utilization_max"] == 12.0

    def test_cluster_volume_is_reported_beside_the_placeholder_allocated_storage(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        gib = 1024**3
        cloudwatch.get_metric_data.side_effect = lambda **kw: metric_data_response(
            kw["MetricDataQueries"], value=150 * gib
        )

        cluster = {
            "identifier": "cluster-1",
            "engine_mode": "provisioned",
            "allocated_storage": 1,
        }

        analyzer._collect_region_metrics("us-east-1", [], [cluster])

        assert cluster["allocated_storage"] == 1
        assert cluster["storage"]["allocated_storage_gb"] == 1
        assert cluster["storage"]["volume_used_gib"] == pytest.approx(150.0)
        assert cluster["metrics"]["volume_bytes_used"]["max"] == 150 * gib

    def test_serverless_v1_cluster_carries_capacity_at_the_cluster_level(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        cloudwatch.get_metric_data.side_effect = lambda **kw: metric_data_response(
            kw["MetricDataQueries"], value=16.0
        )

        cluster = {
            "identifier": "cluster-1",
            "engine_mode": "serverless",
            "allocated_storage": 1,
        }

        analyzer._collect_region_metrics("us-east-1", [], [cluster])

        assert cluster["capacity"]["type"] == "serverless_v1"
        assert cluster["capacity"]["effective_memory_gib"]["max"] == 32.0

    def test_unreturned_metrics_are_listed_as_unavailable(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value

        def only_cpu(**kw):
            wanted = [
                q
                for q in kw["MetricDataQueries"]
                if q["MetricStat"]["Metric"]["MetricName"] == "CPUUtilization"
            ]
            return metric_data_response(wanted)

        cloudwatch.get_metric_data.side_effect = only_cpu

        instance = {
            "db_instance_identifier": "instance-1",
            "engine": "aurora-postgresql",
            "db_instance_class": "db.serverless",
        }

        analyzer._collect_region_metrics("us-east-1", [instance], [])

        assert "DBLoad" in instance["metrics"]["unavailable"]
        assert "db_load" not in instance["metrics"]
        assert instance["metrics"]["collection_error"] is None

    def test_plain_rds_storage_reports_used_space_from_free_space(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        gib = 1024**3
        cloudwatch.get_metric_data.side_effect = lambda **kw: metric_data_response(
            kw["MetricDataQueries"], value=20 * gib
        )

        instance = {
            "db_instance_identifier": "pg-1",
            "engine": "postgres",
            "db_instance_class": "db.m6g.large",
            "allocated_storage": 100,
        }

        analyzer._collect_region_metrics("us-east-1", [instance], [])

        assert instance["capacity"]["type"] == "provisioned"
        assert instance["storage"]["allocated_gib"] == 100
        assert instance["storage"]["free_bytes"]["min"] == 20 * gib
        assert instance["storage"]["used_gib_max"] == pytest.approx(80.0)


class TestPagination:
    def test_pages_concatenate_rather_than_overwrite(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value

        def paged(**kwargs):
            queries = kwargs["MetricDataQueries"]
            if "NextToken" not in kwargs:
                return {
                    "MetricDataResults": [
                        {
                            "Id": query["Id"],
                            "Timestamps": hours(2),
                            "Values": [10.0, 10.0],
                        }
                        for query in queries
                    ],
                    "NextToken": "page-2",
                }
            return {
                "MetricDataResults": [
                    {
                        "Id": query["Id"],
                        "Timestamps": [BASE + timedelta(hours=2 + i) for i in range(2)],
                        "Values": [30.0, 30.0],
                    }
                    for query in queries
                ]
            }

        cloudwatch.get_metric_data.side_effect = paged

        instance = {
            "db_instance_identifier": "instance-1",
            "engine": "aurora-postgresql",
            "db_instance_class": "db.serverless",
        }

        analyzer._collect_region_metrics("us-east-1", [instance], [])

        assert cloudwatch.get_metric_data.call_count == 2
        counts = instance["metrics"]["datapoint_counts"]
        assert counts["ServerlessDatabaseCapacity"] == 4
        assert instance["capacity"]["acu"]["avg"] == pytest.approx(20.0)

    def test_a_region_past_the_query_cap_splits_into_batches(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        batches = []

        def record(**kwargs):
            batches.append([query["Id"] for query in kwargs["MetricDataQueries"]])
            return metric_data_response(kwargs["MetricDataQueries"], value=8.0)

        cloudwatch.get_metric_data.side_effect = record

        instances = [
            {
                "db_instance_identifier": f"instance-{i}",
                "engine": "aurora-postgresql",
                "db_instance_class": "db.serverless",
            }
            for i in range(10)
        ]

        analyzer._collect_region_metrics("us-east-1", instances, [])

        assert len(batches) == 2
        assert len(batches[0]) == GET_METRIC_DATA_MAX_QUERIES
        assert set(batches[0]).isdisjoint(batches[1])
        for instance in instances:
            assert instance["capacity"]["acu"]["avg"] == pytest.approx(8.0)


class TestDegradation:
    def test_access_denied_warns_once_across_every_region(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        cloudwatch.get_metric_data.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetMetricData"
        )

        for region in ("us-east-1", "eu-west-1", "sa-east-1"):
            instance = {
                "db_instance_identifier": f"instance-{region}",
                "engine": "aurora-postgresql",
                "db_instance_class": "db.serverless",
            }
            analyzer._collect_region_metrics(region, [instance], [])

            assert instance["metrics"]["collection_error"] is not None
            assert instance["capacity"]["type"] == "serverless_v2"
            assert "acu" not in instance["capacity"]

        assert len(analyzer.warnings) == 1
        assert "GetMetricData" in analyzer.warnings[0]["message"]
        assert cloudwatch.get_metric_data.call_count == 1

    def test_a_region_with_no_databases_makes_no_call(self):
        analyzer = make_analyzer()

        analyzer._collect_region_metrics("us-east-1", [], [])

        analyzer.session.client.assert_not_called()


class TestFocusedPath:
    def test_single_instance_analysis_returns_metrics(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        cloudwatch.get_metric_data.side_effect = lambda **kw: metric_data_response(
            kw["MetricDataQueries"], value=24.0
        )

        analysis = analyzer._analyze_single_rds_instance(
            {
                "DBInstanceIdentifier": "instance-1",
                "Engine": "aurora-postgresql",
                "DBInstanceClass": "db.serverless",
                "DbiResourceId": "db-ABC",
                "DBClusterIdentifier": "cluster-1",
            },
            "us-east-1",
        )

        assert analysis["dbi_resource_id"] == "db-ABC"
        assert analysis["db_cluster_identifier"] == "cluster-1"
        assert analysis["capacity"]["acu"]["max"] == 24.0
        assert analysis["metrics"]["collection_error"] is None

    def test_focused_serverless_v1_cluster_keeps_its_scaling_config(self):
        analyzer = make_analyzer()
        cloudwatch = analyzer.session.client.return_value
        cloudwatch.get_metric_data.side_effect = lambda **kw: metric_data_response(
            kw["MetricDataQueries"], value=4.0
        )

        analysis = analyzer._analyze_single_aurora_cluster(
            {
                "DBClusterIdentifier": "cluster-1",
                "Engine": "aurora-postgresql",
                "EngineMode": "serverless",
                "ScalingConfigurationInfo": {
                    "MinCapacity": 2,
                    "MaxCapacity": 16,
                    "AutoPause": True,
                    "SecondsUntilAutoPause": 300,
                },
            },
            "us-east-1",
        )

        assert analysis["serverless_v1_config"]["min_capacity"] == 2
        assert analysis["serverless_v1_config"]["max_capacity"] == 16
        assert analysis["serverless_v1_config"]["auto_pause"] is True
        assert analysis["capacity"]["type"] == "serverless_v1"
