"""Tests for the provider name and error reporting in cloud analysis metadata."""

import logging

from planetscale_discovery.cloud.analyzers import gcp_analyzer
from planetscale_discovery.cloud.analyzers.aws_analyzer import AWSAnalyzer
from planetscale_discovery.cloud.analyzers.gcp_analyzer import GCPAnalyzer


class FakeAWSConfig:
    def __init__(self):
        self.regions = ["us-east-1"]
        self.profile = None
        self.target_database = None


class FakeGCPConfig:
    def __init__(self):
        self.project_id = "project-123"
        self.regions = ["us-central1"]


class TestProviderName:
    def test_aws_provider_is_the_provider_name_not_the_logger(self):
        analyzer = AWSAnalyzer(FakeAWSConfig(), logging.getLogger("discovery"))

        assert analyzer.provider == "aws"
        assert analyzer.get_analysis_metadata()["provider"] == "aws"

    def test_aws_uses_the_logger_it_was_given(self):
        logger = logging.getLogger("discovery")

        analyzer = AWSAnalyzer(FakeAWSConfig(), logger)

        assert analyzer.logger is logger


class TestMetadataReportsErrors:
    def test_aws_metadata_carries_warnings_raised_during_analysis(self):
        analyzer = AWSAnalyzer(FakeAWSConfig())
        analyzer.regions = ["us-east-1"]

        def fail(region):
            analyzer.add_warning(f"could not read {region}")
            raise RuntimeError("region unavailable")

        analyzer._analyze_region = fail

        results = analyzer.analyze()

        assert results["metadata"]["warning_count"] == 1
        assert results["metadata"]["error_count"] == 1
        assert results["metadata"]["warnings"][0]["message"] == (
            "could not read us-east-1"
        )

    def test_gcp_metadata_carries_errors_raised_during_analysis(self, monkeypatch):
        monkeypatch.setattr(gcp_analyzer, "HAS_GCP_LIBS", True)
        analyzer = GCPAnalyzer(FakeGCPConfig())
        analyzer.regions = ["us-central1"]
        analyzer.errors.clear()

        def fail(region):
            raise RuntimeError("region unavailable")

        analyzer._analyze_region = fail
        analyzer._generate_gcp_summary = lambda resources: {}
        analyzer._assess_complexity = lambda resources: {}

        results = analyzer.analyze()

        messages = [error["message"] for error in results["metadata"]["errors"]]
        assert "Failed to analyze region us-central1" in messages
        assert results["metadata"]["provider"] == "gcp"
