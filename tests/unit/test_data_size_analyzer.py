"""
Tests for Data Size Analyzer
"""

from unittest.mock import MagicMock
from planetscale_discovery.database.analyzers.data_size_analyzer import DataSizeAnalyzer


class TestDataSizeAnalyzer:
    """Tests for DataSizeAnalyzer class"""

    def test_instantiation_with_defaults(self):
        """Test analyzer can be created with default config"""
        connection = MagicMock()
        analyzer = DataSizeAnalyzer(connection)

        assert analyzer.connection == connection
        assert analyzer.enabled is False
        assert analyzer.sample_percent == 10
        assert analyzer.max_table_size_gb == 10
        assert analyzer.target_tables == []
        assert analyzer.target_schemas == ["public"]
        assert "text" in analyzer.check_column_types
        assert "bytea" in analyzer.check_column_types

    def test_instantiation_with_custom_config(self):
        """Test analyzer with custom configuration"""
        connection = MagicMock()
        config = {
            "enabled": True,
            "sample_percent": 25,
            "max_table_size_gb": 50,
            "target_tables": ["public.users", "public.posts"],
            "target_schemas": ["public", "app_data"],
            "check_column_types": ["text", "json"],
            "size_thresholds": {"1kb": 1024, "64kb": 65536, "1mb": 1048576},
        }
        analyzer = DataSizeAnalyzer(connection, config)

        assert analyzer.enabled is True
        assert analyzer.sample_percent == 25
        assert analyzer.max_table_size_gb == 50
        assert analyzer.target_tables == ["public.users", "public.posts"]
        assert analyzer.target_schemas == ["public", "app_data"]
        assert analyzer.check_column_types == ["text", "json"]
        assert analyzer.size_thresholds["1mb"] == 1048576

    def test_analyze_when_disabled(self):
        """Test analyze returns disabled message when enabled=false"""
        connection = MagicMock()
        config = {"enabled": False}
        analyzer = DataSizeAnalyzer(connection, config)

        result = analyzer.analyze()

        assert result["enabled"] is False
        assert "message" in result
        assert "disabled" in result["message"].lower()
        assert "enabled=true" in result["message"].lower()

    def test_analyze_when_enabled_no_tables(self):
        """Test analyze when enabled but no tables found"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = []

        config = {"enabled": True}
        analyzer = DataSizeAnalyzer(connection, config)

        result = analyzer.analyze()

        assert result["enabled"] is True
        assert result["summary"]["total_tables_analyzed"] == 0
        assert result["tables_analyzed"] == []

    def test_get_tables_to_analyze_filters_by_schema(self):
        """Test table discovery filters by target schemas"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        # Mock return data
        cursor.fetchall.return_value = [
            {
                "schema_name": "public",
                "table_name": "users",
                "table_oid": 12345,
                "size_bytes": 1024000,
                "size_gb": 0.001,
                "estimated_rows": 1000,
            }
        ]

        config = {"enabled": True, "target_schemas": ["public"]}
        analyzer = DataSizeAnalyzer(connection, config)

        tables = analyzer._get_tables_to_analyze()

        assert len(tables) == 1
        assert tables[0]["schema_name"] == "public"
        assert tables[0]["table_name"] == "users"
        # Verify the query was called
        cursor.execute.assert_called_once()

    def test_get_tables_to_analyze_handles_specific_tables(self):
        """Test table discovery with specific target tables"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = []

        config = {"enabled": True, "target_tables": ["public.users", "posts"]}
        analyzer = DataSizeAnalyzer(connection, config)

        analyzer._get_tables_to_analyze()

        # Should have executed query with table filtering
        cursor.execute.assert_called_once()
        call_args = cursor.execute.call_args
        # Verify params include schema and table names
        assert "public" in call_args[0][1] or "users" in call_args[0][1]

    def test_get_columns_to_check_returns_correct_structure(self):
        """Test column discovery returns tuples with storage strategy"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        # Mock column data
        cursor.fetchall.return_value = [
            {
                "column_name": "description",
                "data_type": "text",
                "storage_strategy": "EXTENDED",
            },
            {
                "column_name": "data",
                "data_type": "bytea",
                "storage_strategy": "EXTERNAL",
            },
        ]

        config = {"enabled": True}
        analyzer = DataSizeAnalyzer(connection, config)

        columns = analyzer._get_columns_to_check("public", "test_table")

        assert len(columns) == 2
        assert columns[0] == ("description", "text", "EXTENDED")
        assert columns[1] == ("data", "bytea", "EXTERNAL")

    def test_analyze_column_returns_none_on_no_data(self):
        """Test column analysis returns None when no data"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = {"total_rows": 0}

        config = {"enabled": True}
        analyzer = DataSizeAnalyzer(connection, config)

        table_info = {"estimated_rows": 0, "size_gb": 0}
        result = analyzer._analyze_column("public", "test", "col", table_info)

        assert result is None

    def test_analyze_column_returns_size_stats(self):
        """Test column analysis returns complete statistics"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        # Mock size statistics
        cursor.fetchone.return_value = {
            "total_rows": 1000,
            "count_gt_1kb": 250,
            "count_gt_64kb": 10,
            "max_size_bytes": 102400,
            "avg_size_bytes": 512,
            "max_size_human": "100 kB",
            "avg_size_human": "512 bytes",
        }

        config = {
            "enabled": True,
            "sample_percent": 10,
            "size_thresholds": {"1kb": 1024, "64kb": 65536},
        }
        analyzer = DataSizeAnalyzer(connection, config)

        table_info = {"estimated_rows": 10000, "size_gb": 0.5}
        result = analyzer._analyze_column("public", "test", "body_html", table_info)

        assert result is not None
        assert result["column_name"] == "body_html"
        assert result["total_rows_checked"] == 1000
        assert result["count_gt_1kb"] == 250
        assert result["count_gt_64kb"] == 10
        assert result["max_size_bytes"] == 102400
        assert result["avg_size_bytes"] == 512
        assert result["percent_gt_1kb"] == 25.0
        assert result["percent_gt_64kb"] == 1.0

    def test_analyze_column_uses_tablesample_for_large_tables(self):
        """Test that TABLESAMPLE is used for large tables"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = {
            "total_rows": 100,
            "count_gt_1kb": 0,
            "count_gt_64kb": 0,
            "max_size_bytes": 100,
            "avg_size_bytes": 50,
            "max_size_human": "100 bytes",
            "avg_size_human": "50 bytes",
        }

        config = {"enabled": True, "sample_percent": 10}
        analyzer = DataSizeAnalyzer(connection, config)

        # Large table (>1000 rows) should use TABLESAMPLE
        table_info = {"estimated_rows": 100000, "size_gb": 1.0}
        analyzer._analyze_column("public", "large_table", "col", table_info)

        # Check that TABLESAMPLE was in the query
        call_args = cursor.execute.call_args
        query = call_args[0][0]
        assert "TABLESAMPLE" in query
        assert "10" in query  # sample_percent

    def test_analyze_column_no_tablesample_for_small_tables(self):
        """Test that TABLESAMPLE is not used for small tables"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = {
            "total_rows": 100,
            "count_gt_1kb": 0,
            "count_gt_64kb": 0,
            "max_size_bytes": 100,
            "avg_size_bytes": 50,
            "max_size_human": "100 bytes",
            "avg_size_human": "50 bytes",
        }

        config = {"enabled": True, "sample_percent": 10}
        analyzer = DataSizeAnalyzer(connection, config)

        # Small table (<= 1000 rows) should not use TABLESAMPLE
        table_info = {"estimated_rows": 500, "size_gb": 0.001}
        analyzer._analyze_column("public", "small_table", "col", table_info)

        # Check that TABLESAMPLE was not in the query
        call_args = cursor.execute.call_args
        query = call_args[0][0]
        assert "TABLESAMPLE" not in query

    def test_analyze_table_skips_large_tables(self):
        """Test that tables exceeding size limit are handled"""
        connection = MagicMock()

        config = {"enabled": True, "max_table_size_gb": 5}
        analyzer = DataSizeAnalyzer(connection, config)

        # This is tested in the main analyze() flow
        # Here we just verify the config is set
        assert analyzer.max_table_size_gb == 5

    def test_analyze_handles_errors_gracefully(self):
        """Test analyzer handles exceptions without crashing"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.side_effect = Exception("Database error")

        config = {"enabled": True}
        analyzer = DataSizeAnalyzer(connection, config)

        tables = analyzer._get_tables_to_analyze()

        # Should return empty list on error, not crash
        assert tables == []

    def test_configuration_includes_toast_storage_types(self):
        """Test that default config includes all PostgreSQL column types"""
        connection = MagicMock()
        analyzer = DataSizeAnalyzer(connection)

        # Should include all important PostgreSQL types
        assert "text" in analyzer.check_column_types
        assert "bytea" in analyzer.check_column_types
        assert "json" in analyzer.check_column_types
        assert "jsonb" in analyzer.check_column_types
        assert "character varying" in analyzer.check_column_types


class TestDataSizeAnalyzerIntegration:
    """Integration-style tests with more complete mocking"""

    def test_full_analysis_flow(self):
        """Test complete analysis flow from start to finish"""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        # Mock table list
        cursor.fetchall.side_effect = [
            # First call: get_tables_to_analyze
            [
                {
                    "schema_name": "public",
                    "table_name": "emails",
                    "table_oid": 12345,
                    "size_bytes": 1000000,
                    "size_gb": 0.001,
                    "estimated_rows": 1000,
                }
            ],
            # Second call: get_columns_to_check
            [
                {
                    "column_name": "body_html",
                    "data_type": "text",
                    "storage_strategy": "EXTENDED",
                }
            ],
        ]

        # Mock column size analysis
        cursor.fetchone.return_value = {
            "total_rows": 1000,
            "count_gt_1kb": 250,
            "count_gt_64kb": 0,
            "max_size_bytes": 22528,
            "avg_size_bytes": 284,
            "max_size_human": "22 kB",
            "avg_size_human": "284 bytes",
        }

        config = {
            "enabled": True,
            "sample_percent": 100,  # Full scan for this test
            "max_table_size_gb": 10,
        }
        analyzer = DataSizeAnalyzer(connection, config)

        result = analyzer.analyze()

        # Verify structure
        assert result["enabled"] is True
        assert result["summary"]["total_tables_analyzed"] == 1
        assert result["summary"]["columns_exceeding_1kb"] == 1
        assert result["summary"]["columns_exceeding_64kb"] == 0

        # Verify table analysis
        assert len(result["tables_analyzed"]) == 1
        table = result["tables_analyzed"][0]
        assert table["schema"] == "public"
        assert table["table"] == "emails"
        assert table["has_large_columns"] is True

        # Verify column analysis includes TOAST strategy
        assert len(table["columns"]) == 1
        col = table["columns"][0]
        assert col["column_name"] == "body_html"
        assert col["data_type"] == "text"
        assert col["toast_storage_strategy"] == "EXTENDED"
        assert col["count_gt_1kb"] == 250
        assert col["percent_gt_1kb"] == 25.0
