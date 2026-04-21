"""
Tests for MySQL Config Analyzer — covers version parsing, variable
caching, cloud-platform detection, and RDS-specific configuration
collection.
"""

from unittest.mock import MagicMock, patch

from planetscale_discovery.database.mysql_analyzers.config_analyzer import (
    MySQLConfigAnalyzer,
)


def _make_analyzer():
    mock_conn = MagicMock()
    return MySQLConfigAnalyzer(mock_conn)


class TestVariableCollection:
    """SHOW GLOBAL VARIABLES parsing + caching."""

    def test_get_all_variables_parses_rows(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("version", "8.0.39"),
            ("datadir", "/var/lib/mysql/"),
        ]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_all_variables()
        assert result == {"version": "8.0.39", "datadir": "/var/lib/mysql/"}

    def test_get_all_variables_caches_across_calls(self):
        """Second call should hit the cache and not re-query."""
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.fetchall.return_value = [("version", "8.0.39")]
        analyzer.connection.cursor.return_value = cursor

        analyzer._get_all_variables()
        analyzer._get_all_variables()

        # execute was called exactly once despite two invocations
        assert cursor.execute.call_count == 1

    def test_get_all_variables_returns_empty_on_error(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Access denied")
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_all_variables()
        assert result == {}
        assert len(analyzer.errors) == 1


class TestVersionInfo:
    """version_info parses major.minor.patch and distribution flavour."""

    def test_version_info_parses_standard_mysql(self):
        analyzer = _make_analyzer()
        with patch.object(
            analyzer,
            "_get_all_variables",
            return_value={
                "version": "8.0.39",
                "version_comment": "MySQL Community Server - GPL",
                "hostname": "db-1",
                "datadir": "/var/lib/mysql/",
            },
        ):
            info = analyzer._get_version_info()

        assert info["major_version"] == 8
        assert info["minor_version"] == 0
        assert info["patch_version"] == 39
        assert info["version_num"] == 80039
        assert info["distribution"] == "MySQL"

    def test_version_info_detects_aurora_distribution(self):
        """Aurora detection relies on `version_comment`; the underlying
        `version` variable is the plain MySQL version."""
        analyzer = _make_analyzer()
        with patch.object(
            analyzer,
            "_get_all_variables",
            return_value={
                "version": "8.0.39",
                "version_comment": "Amazon Aurora MySQL",
            },
        ):
            info = analyzer._get_version_info()

        assert info["distribution"] == "Amazon Aurora"
        assert info["major_version"] == 8
        assert info["patch_version"] == 39

    def test_version_info_detects_mariadb_distribution(self):
        analyzer = _make_analyzer()
        with patch.object(
            analyzer,
            "_get_all_variables",
            return_value={
                "version": "10.11.6-MariaDB",
                "version_comment": "mariadb.org binary distribution",
            },
        ):
            info = analyzer._get_version_info()

        assert info["distribution"] == "MariaDB"
        assert info["major_version"] == 10
        assert info["minor_version"] == 11

    def test_version_info_detects_percona_distribution(self):
        analyzer = _make_analyzer()
        with patch.object(
            analyzer,
            "_get_all_variables",
            return_value={
                "version": "8.0.35-27",
                "version_comment": "Percona Server (GPL)",
            },
        ):
            info = analyzer._get_version_info()

        assert info["distribution"] == "Percona Server"

    def test_version_info_handles_two_part_version(self):
        """Version strings without a patch component shouldn't crash."""
        analyzer = _make_analyzer()
        with patch.object(
            analyzer,
            "_get_all_variables",
            return_value={"version": "8.0", "version_comment": ""},
        ):
            info = analyzer._get_version_info()

        assert info["major_version"] == 8
        assert info["minor_version"] == 0
        assert info["patch_version"] == 0


class TestCloudPlatformDetection:
    """_detect_cloud_platform classifies the source environment."""

    def _with_vars(self, variables):
        analyzer = _make_analyzer()
        return analyzer, patch.object(
            analyzer, "_get_all_variables", return_value=variables
        )

    def test_detects_aurora_via_aurora_version_variable(self):
        analyzer, ctx = self._with_vars(
            {"aurora_version": "3.08.0", "version_comment": "Source distribution"}
        )
        with ctx:
            result = analyzer._detect_cloud_platform()
        assert result["platform"] == "aws_aurora"
        assert result["details"]["aurora_version"] == "3.08.0"

    def test_detects_rds_via_datadir_path(self):
        analyzer, ctx = self._with_vars(
            {"version_comment": "MySQL Community Server", "datadir": "/rdsdbdata/db/"}
        )
        with ctx:
            result = analyzer._detect_cloud_platform()
        assert result["platform"] == "aws_rds"

    def test_detects_planetscale_via_vitess_version(self):
        analyzer, ctx = self._with_vars(
            {"version": "8.0.36-Vitess", "version_comment": ""}
        )
        with ctx:
            result = analyzer._detect_cloud_platform()
        assert result["platform"] == "planetscale"

    def test_detects_gcp_cloudsql_via_version_comment(self):
        analyzer, ctx = self._with_vars(
            {"version_comment": "(Google) Cloud SQL", "version": "8.0.36"}
        )
        with ctx:
            result = analyzer._detect_cloud_platform()
        assert result["platform"] == "gcp_cloudsql"

    def test_detects_on_premise_when_nothing_matches(self):
        analyzer, ctx = self._with_vars(
            {
                "version": "8.0.39",
                "version_comment": "MySQL Community Server - GPL",
                "datadir": "/var/lib/mysql/",
            }
        )
        with ctx:
            result = analyzer._detect_cloud_platform()
        assert result["platform"] == "on-premise"


class TestRDSConfiguration:
    """`CALL mysql.rds_show_configuration` parsing + permission handling."""

    def test_parses_name_value_description_rows(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("binlog retention hours", "168", "Binlog retention in hours"),
            ("source delay", "0", "Replication delay"),
        ]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_rds_configuration()
        assert result["binlog retention hours"]["value"] == "168"
        assert (
            result["binlog retention hours"]["description"]
            == "Binlog retention in hours"
        )
        assert result["source delay"]["value"] == "0"

    def test_permission_error_becomes_warning_not_error(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception(
            "EXECUTE command denied to user 'discovery'"
        )
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_rds_configuration()

        assert "error" in result
        assert len(analyzer.warnings) == 1
        assert "EXECUTE ON PROCEDURE" in analyzer.warnings[0]["message"]


class TestAnalyzeTopLevel:
    """analyze() orchestrates platform detection and conditional RDS call."""

    def test_includes_rds_config_for_aws_aurora(self):
        analyzer = _make_analyzer()
        with (
            patch.object(
                analyzer,
                "_get_all_variables",
                return_value={
                    "version": "8.0.mysql_aurora.3.08.0",
                    "aurora_version": "3.08.0",
                    "version_comment": "Source distribution",
                },
            ),
            patch.object(
                analyzer,
                "_get_rds_configuration",
                return_value={"foo": {"value": "bar"}},
            ) as rds_mock,
        ):
            result = analyzer.analyze()

        rds_mock.assert_called_once()
        assert "rds_config" in result
        assert result["cloud_platform"]["platform"] == "aws_aurora"

    def test_skips_rds_config_for_on_premise(self):
        analyzer = _make_analyzer()
        with (
            patch.object(
                analyzer,
                "_get_all_variables",
                return_value={
                    "version": "8.0.39",
                    "version_comment": "MySQL Community Server - GPL",
                    "datadir": "/var/lib/mysql/",
                },
            ),
            patch.object(analyzer, "_get_rds_configuration") as rds_mock,
        ):
            result = analyzer.analyze()

        rds_mock.assert_not_called()
        assert "rds_config" not in result
        assert result["cloud_platform"]["platform"] == "on-premise"
