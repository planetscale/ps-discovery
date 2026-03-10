"""
Unit tests for PostgreSQL Configuration Analyzer
"""

import pytest
from unittest.mock import MagicMock, patch
from planetscale_discovery.database.analyzers.config_analyzer import ConfigAnalyzer
from tests.fixtures.database_responses import (
    POSTGRES_VERSION_RESPONSE,
    POSTGRES_VERSION_NUM_RESPONSE,
    POSTGRES_SETTINGS_RESPONSE,
)


class TestConfigAnalyzer:
    """Test cases for ConfigAnalyzer"""

    @pytest.fixture
    def mock_connection(self):
        """Create a mock database connection"""
        connection = MagicMock()
        cursor_mock = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor_mock
        connection.cursor.return_value.__exit__.return_value = None
        return connection, cursor_mock

    @pytest.fixture
    def config_analyzer(self, mock_connection):
        """Create ConfigAnalyzer instance with mock connection"""
        connection, _ = mock_connection
        return ConfigAnalyzer(connection)

    def test_get_version_info_postgres_17(self, config_analyzer, mock_connection):
        """Test PostgreSQL 17.x version parsing"""
        _, cursor_mock = mock_connection

        cursor_mock.fetchone.side_effect = [
            POSTGRES_VERSION_RESPONSE[0],
            POSTGRES_VERSION_NUM_RESPONSE[0],
            {},  # compile_info query
        ]
        cursor_mock.fetchall.return_value = []

        result = config_analyzer._get_version_info()

        assert (
            result["version_string"]
            == "PostgreSQL 17.4 on aarch64-unknown-linux-gnu, compiled by gcc (GCC) 12.4.0, 64-bit"
        )
        assert result["version_num"] == 170004
        assert result["major_version"] == 17
        assert result["minor_version"] == 4
        assert result["patch_version"] == 0  # PostgreSQL 10+ doesn't use patch version

    def test_get_version_info_postgres_9(self, config_analyzer, mock_connection):
        """Test PostgreSQL 9.x version parsing (legacy format)"""
        _, cursor_mock = mock_connection

        cursor_mock.fetchone.side_effect = [
            {"version": "PostgreSQL 9.6.24 on x86_64-pc-linux-gnu"},
            {"server_version_num": "90624"},
            {},
        ]
        cursor_mock.fetchall.return_value = []

        result = config_analyzer._get_version_info()

        assert result["major_version"] == 9
        assert result["minor_version"] == 6
        assert result["patch_version"] == 24

    def test_get_server_settings(self, config_analyzer, mock_connection):
        """Test retrieving server settings"""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = POSTGRES_SETTINGS_RESPONSE

        result = config_analyzer._get_server_settings()

        assert len(result) == 2
        assert result[0]["name"] == "max_connections"
        assert result[0]["setting"] == "100"
        assert result[1]["name"] == "shared_buffers"
        assert result[1]["setting"] == "32768"

    def test_get_memory_settings(self, config_analyzer, mock_connection):
        """Test retrieving memory-related settings"""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = [
            {
                "name": "shared_buffers",
                "setting": "32768",
                "unit": "8kB",
                "source": "configuration file",
                "context": "postmaster",
            },
            {
                "name": "work_mem",
                "setting": "4096",
                "unit": "kB",
                "source": "configuration file",
                "context": "user",
            },
        ]

        result = config_analyzer._get_memory_settings()

        assert "settings" in result
        assert "memory_in_bytes" in result
        assert (
            result["memory_in_bytes"]["shared_buffers"] == 32768 * 8 * 1024
        )  # 8kB units
        assert result["memory_in_bytes"]["work_mem"] == 4096 * 1024  # kB units

    def test_get_modified_settings(self, config_analyzer, mock_connection):
        """Test retrieving modified settings"""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = [
            {
                "name": "max_connections",
                "setting": "200",
                "boot_val": "100",
                "source": "configuration file",
            }
        ]

        result = config_analyzer._get_modified_settings()

        assert len(result) == 1
        assert result[0]["name"] == "max_connections"
        assert result[0]["setting"] == "200"
        assert result[0]["boot_val"] == "100"

    def test_analyze_complete(self, config_analyzer, mock_connection):
        """Test complete analysis workflow"""
        _, cursor_mock = mock_connection

        # Mock all the individual method calls
        with (
            patch.object(config_analyzer, "_get_version_info") as mock_version,
            patch.object(config_analyzer, "_get_server_settings") as mock_settings,
            patch.object(config_analyzer, "_get_runtime_settings") as mock_runtime,
            patch.object(config_analyzer, "_get_modified_settings") as mock_modified,
            patch.object(
                config_analyzer, "_get_restart_required_settings"
            ) as mock_restart,
            patch.object(config_analyzer, "_get_memory_settings") as mock_memory,
            patch.object(
                config_analyzer, "_get_connection_settings"
            ) as mock_connection_settings,
            patch.object(config_analyzer, "_get_wal_settings") as mock_wal,
            patch.object(
                config_analyzer, "_get_replication_settings"
            ) as mock_replication,
            patch.object(config_analyzer, "_get_security_settings") as mock_security,
            patch.object(
                config_analyzer, "_get_performance_settings"
            ) as mock_performance,
        ):

            mock_version.return_value = {"version_num": 170004}
            mock_settings.return_value = []
            mock_runtime.return_value = {}
            mock_modified.return_value = []
            mock_restart.return_value = []
            mock_memory.return_value = {}
            mock_connection_settings.return_value = {}
            mock_wal.return_value = {}
            mock_replication.return_value = {}
            mock_security.return_value = {}
            mock_performance.return_value = {}

            result = config_analyzer.analyze()

            assert "version_info" in result
            assert "server_settings" in result
            assert "runtime_settings" in result
            assert "modified_settings" in result
            assert "memory_settings" in result
            assert "connection_settings" in result
            assert "wal_settings" in result
            assert "replication_settings" in result
            assert "security_settings" in result
            assert "performance_settings" in result

    def test_error_handling(self, config_analyzer, mock_connection):
        """Test error handling in version info retrieval"""
        _, cursor_mock = mock_connection
        cursor_mock.fetchone.side_effect = Exception("Database error")

        result = config_analyzer._get_version_info()

        assert "error" in result
        assert result["error"] == "Database error"

    @pytest.mark.parametrize(
        "version_num,expected_major,expected_minor,expected_patch",
        [
            (170004, 17, 4, 0),  # PostgreSQL 17.4
            (160002, 16, 2, 0),  # PostgreSQL 16.2
            (150007, 15, 7, 0),  # PostgreSQL 15.7
            (140011, 14, 11, 0),  # PostgreSQL 14.11
            (130015, 13, 15, 0),  # PostgreSQL 13.15
            (120019, 12, 19, 0),  # PostgreSQL 12.19
            (110022, 11, 22, 0),  # PostgreSQL 11.22
            (100023, 10, 23, 0),  # PostgreSQL 10.23
            (90624, 9, 6, 24),  # PostgreSQL 9.6.24 (legacy)
            (90518, 9, 5, 18),  # PostgreSQL 9.5.18 (legacy)
        ],
    )
    def test_version_parsing_edge_cases(
        self,
        config_analyzer,
        mock_connection,
        version_num,
        expected_major,
        expected_minor,
        expected_patch,
    ):
        """Test version parsing for various PostgreSQL versions"""
        _, cursor_mock = mock_connection

        cursor_mock.fetchone.side_effect = [
            {
                "version": f"PostgreSQL {expected_major}.{expected_minor}.{expected_patch}"
            },
            {"server_version_num": str(version_num)},
            {},
        ]
        cursor_mock.fetchall.return_value = []

        result = config_analyzer._get_version_info()

        assert result["major_version"] == expected_major
        assert result["minor_version"] == expected_minor
        assert result["patch_version"] == expected_patch
