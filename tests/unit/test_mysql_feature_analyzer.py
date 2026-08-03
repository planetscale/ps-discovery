"""
Tests for MySQL Feature Analyzer — covers technology detection across
information_schema counts, status counters, and global variables.
"""

from unittest.mock import MagicMock, patch

from planetscale_discovery.database.mysql_analyzers.feature_analyzer import (
    MySQLFeatureAnalyzer,
    SYSTEM_DATABASES,
)


def _make_analyzer():
    mock_conn = MagicMock()
    return MySQLFeatureAnalyzer(mock_conn)


class TestVariableCollection:
    def test_get_variables_parses_rows(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("have_ssl", "YES"),
            ("innodb_support_xa", "ON"),
        ]
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_variables()
        assert result == {"have_ssl": "YES", "innodb_support_xa": "ON"}

    def test_get_variables_empty_on_error(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Access denied")
        analyzer.connection.cursor.return_value = cursor

        result = analyzer._get_variables()
        assert result == {}
        assert len(analyzer.errors) == 1


class TestVariableBasedDetectors:
    """Detectors that consume the variables dict directly."""

    def test_ssl_yes_is_enabled(self):
        analyzer = _make_analyzer()
        assert analyzer._detect_ssl({"have_ssl": "YES"}) is True

    def test_ssl_disabled_is_false(self):
        analyzer = _make_analyzer()
        assert analyzer._detect_ssl({"have_ssl": "DISABLED"}) is False

    def test_ssl_missing_key_is_false(self):
        analyzer = _make_analyzer()
        assert analyzer._detect_ssl({}) is False

    def test_galera_active_requires_wsrep_on_and_provider(self):
        analyzer = _make_analyzer()
        assert (
            analyzer._detect_galera(
                {
                    "wsrep_on": "ON",
                    "wsrep_provider": "/usr/lib/galera/libgalera_smm.so",
                }
            )
            is True
        )

    def test_galera_false_when_wsrep_off_despite_vars_present(self):
        analyzer = _make_analyzer()
        assert (
            analyzer._detect_galera(
                {
                    "wsrep_on": "OFF",
                    "wsrep_provider": "none",
                    "wsrep_ready": "OFF",
                }
            )
            is False
        )

    def test_galera_false_when_on_but_no_provider(self):
        analyzer = _make_analyzer()
        assert (
            analyzer._detect_galera({"wsrep_on": "ON", "wsrep_provider": "none"})
            is False
        )

    def test_galera_absent_without_wsrep_vars(self):
        analyzer = _make_analyzer()
        assert analyzer._detect_galera({"innodb_buffer_pool_size": "1G"}) is False

    def test_xa_false_when_counters_zero(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("Com_xa_start", 0),
            ("Com_xa_commit", 0),
        ]
        analyzer.connection.cursor.return_value = cursor

        assert analyzer._detect_xa() is False

    def test_xa_detected_from_usage_counters(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("Com_xa_commit", 10),
            ("Com_xa_rollback", 0),
        ]
        analyzer.connection.cursor.return_value = cursor

        assert analyzer._detect_xa() is True


class TestStatusCounterDetectors:
    """Detectors that consume SHOW GLOBAL STATUS rows."""

    def test_lock_tables_positive_when_counter_gt_zero(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.fetchone.return_value = ("Com_lock_tables", 42)
        analyzer.connection.cursor.return_value = cursor

        assert analyzer._detect_lock_tables() is True
        cursor.execute.assert_called_once_with(
            "SHOW GLOBAL STATUS LIKE 'Com_lock_tables'"
        )

    def test_lock_tables_false_when_counter_zero(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.fetchone.return_value = ("Com_lock_tables", 0)
        analyzer.connection.cursor.return_value = cursor

        assert analyzer._detect_lock_tables() is False

    def test_prepared_stmts_positive_when_counter_gt_zero(self):
        analyzer = _make_analyzer()
        cursor = MagicMock()
        cursor.fetchone.return_value = ("Prepared_stmt_count", 7)
        analyzer.connection.cursor.return_value = cursor

        assert analyzer._detect_prepared_stmts({}) is True


class TestInformationSchemaDetectors:
    """Detectors that count rows in information_schema (mocked via execute_query_single)."""

    def test_fulltext_positive_when_count_gt_zero(self):
        analyzer = _make_analyzer()
        with patch.object(analyzer, "execute_query_single", return_value={"cnt": 3}):
            assert analyzer._has_fulltext_indexes() is True

    def test_fulltext_false_when_count_zero(self):
        analyzer = _make_analyzer()
        with patch.object(analyzer, "execute_query_single", return_value={"cnt": 0}):
            assert analyzer._has_fulltext_indexes() is False

    def test_foreign_keys_positive(self):
        analyzer = _make_analyzer()
        with patch.object(analyzer, "execute_query_single", return_value={"cnt": 12}):
            assert analyzer._has_foreign_keys() is True

    def test_spatial_columns_false_when_count_zero(self):
        analyzer = _make_analyzer()
        with patch.object(analyzer, "execute_query_single", return_value={"cnt": 0}):
            assert analyzer._has_spatial_columns() is False

    def test_partitions_positive(self):
        analyzer = _make_analyzer()
        with patch.object(analyzer, "execute_query_single", return_value={"cnt": 1}):
            assert analyzer._has_partitions() is True

    def test_innodb_compression_false_when_count_zero(self):
        analyzer = _make_analyzer()
        with patch.object(analyzer, "execute_query_single", return_value={"cnt": 0}):
            assert analyzer._has_innodb_compression() is False


class TestUserSchemaFilter:
    def test_filter_excludes_all_system_databases(self):
        analyzer = _make_analyzer()
        clause = analyzer._user_schema_filter("table_schema")

        assert clause.startswith("table_schema NOT IN (")
        for system_db in SYSTEM_DATABASES:
            assert f"'{system_db}'" in clause


class TestAnalyzeTopLevel:
    def test_analyze_returns_technologies_key(self):
        analyzer = _make_analyzer()
        with (
            patch.object(analyzer, "_get_variables", return_value={"have_ssl": "YES"}),
            patch.object(
                analyzer, "_detect_technologies", return_value={"ssl": True}
            ) as detect_mock,
        ):
            result = analyzer.analyze()

        detect_mock.assert_called_once()
        assert "technologies_detected" in result
        assert result["technologies_detected"] == {"ssl": True}
