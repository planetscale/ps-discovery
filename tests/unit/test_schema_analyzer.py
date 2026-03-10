"""
Unit tests for PostgreSQL Schema Analyzer
"""

import pytest
from unittest.mock import MagicMock, patch
from planetscale_discovery.database.analyzers.schema_analyzer import SchemaAnalyzer


class TestSchemaAnalyzer:
    """Test cases for SchemaAnalyzer"""

    @pytest.fixture
    def mock_connection(self):
        """Create a mock database connection with context-managed cursor"""
        connection = MagicMock()
        cursor_mock = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor_mock
        connection.cursor.return_value.__exit__.return_value = None
        return connection, cursor_mock

    @pytest.fixture
    def analyzer(self, mock_connection):
        """Create SchemaAnalyzer instance with mock connection"""
        connection, _ = mock_connection
        return SchemaAnalyzer(connection)

    # ---------------------------------------------------------------
    # analyze() - overall structure
    # ---------------------------------------------------------------

    def test_analyze_returns_expected_keys(self, analyzer):
        """Test that analyze() returns a dict with all expected top-level keys"""
        with (
            patch.object(analyzer, "_get_database_catalog", return_value=[]),
            patch.object(analyzer, "_get_schema_inventory", return_value=[]),
            patch.object(analyzer, "_get_table_analysis", return_value=[]),
            patch.object(analyzer, "_get_index_analysis", return_value=[]),
            patch.object(analyzer, "_get_constraint_analysis", return_value=[]),
            patch.object(analyzer, "_get_function_analysis", return_value=[]),
            patch.object(analyzer, "_get_trigger_analysis", return_value=[]),
            patch.object(analyzer, "_get_view_analysis", return_value=[]),
            patch.object(analyzer, "_get_sequence_analysis", return_value=[]),
            patch.object(analyzer, "_get_partition_analysis", return_value=[]),
            patch.object(analyzer, "_get_inheritance_analysis", return_value=[]),
        ):
            result = analyzer.analyze()

        expected_keys = {
            "database_catalog",
            "schema_inventory",
            "table_analysis",
            "index_analysis",
            "constraint_analysis",
            "function_analysis",
            "trigger_analysis",
            "view_analysis",
            "sequence_analysis",
            "partition_analysis",
            "inheritance_analysis",
        }
        assert set(result.keys()) == expected_keys

    def test_analyze_calls_all_sub_methods(self, analyzer):
        """Test that analyze() invokes every sub-analysis method exactly once"""
        methods = [
            "_get_database_catalog",
            "_get_schema_inventory",
            "_get_table_analysis",
            "_get_index_analysis",
            "_get_constraint_analysis",
            "_get_function_analysis",
            "_get_trigger_analysis",
            "_get_view_analysis",
            "_get_sequence_analysis",
            "_get_partition_analysis",
            "_get_inheritance_analysis",
        ]
        patches = {}
        for m in methods:
            patches[m] = patch.object(analyzer, m, return_value=[])

        with (
            patches[methods[0]] as p0,
            patches[methods[1]] as p1,
            patches[methods[2]] as p2,
            patches[methods[3]] as p3,
            patches[methods[4]] as p4,
            patches[methods[5]] as p5,
            patches[methods[6]] as p6,
            patches[methods[7]] as p7,
            patches[methods[8]] as p8,
            patches[methods[9]] as p9,
            patches[methods[10]] as p10,
        ):
            analyzer.analyze()
            for p in [p0, p1, p2, p3, p4, p5, p6, p7, p8, p9, p10]:
                p.assert_called_once()

    # ---------------------------------------------------------------
    # _get_server_version_num()
    # ---------------------------------------------------------------

    def test_get_server_version_num(self, analyzer, mock_connection):
        """Test version number retrieval and caching"""
        _, cursor_mock = mock_connection
        cursor_mock.fetchone.return_value = {"server_version_num": "140011"}

        version = analyzer._get_server_version_num()
        assert version == 140011

        # Second call should use cached value, no additional query
        cursor_mock.fetchone.return_value = {"server_version_num": "999999"}
        version2 = analyzer._get_server_version_num()
        assert version2 == 140011  # cached

    def test_get_server_version_num_on_error(self, analyzer, mock_connection):
        """Test version number defaults to 0 on failure"""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("connection lost")

        version = analyzer._get_server_version_num()
        assert version == 0

    # ---------------------------------------------------------------
    # _get_database_catalog()
    # ---------------------------------------------------------------

    def test_get_database_catalog_success(self, mock_connection):
        """Test database catalog returns list of database info dicts"""
        connection, cursor_mock = mock_connection

        # We need two separate cursor contexts:
        # 1st: main catalog query
        # 2nd: database_settings sub-query
        main_row = {
            "database_name": "mydb",
            "encoding": "UTF8",
            "collation": "en_US.UTF-8",
            "ctype": "en_US.UTF-8",
            "is_template": False,
            "allow_connections": True,
            "connection_limit": -1,
            "size_bytes": 73000000,
            "size_human": "70 MB",
            "owner": "postgres",
            "tablespace": "pg_default",
        }

        # Track which cursor call we're on
        cursor_calls = []
        main_cursor = MagicMock()
        detail_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            if len(cursor_calls) == 0:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=detail_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        main_cursor.fetchall.return_value = [main_row]
        detail_cursor.fetchall.return_value = [{"config_setting": "work_mem=64MB"}]

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_database_catalog()

        assert len(result) == 1
        assert result[0]["database_name"] == "mydb"
        assert result[0]["encoding"] == "UTF8"
        assert result[0]["owner"] == "postgres"
        assert result[0]["database_settings"] == ["work_mem=64MB"]

    def test_get_database_catalog_settings_query_fails(self, mock_connection):
        """Test that failure fetching per-db settings degrades gracefully"""
        connection, _ = mock_connection

        main_row = {
            "database_name": "mydb",
            "encoding": "UTF8",
            "collation": "C",
            "ctype": "C",
            "is_template": False,
            "allow_connections": True,
            "connection_limit": -1,
            "size_bytes": 1000,
            "size_human": "1000 bytes",
            "owner": "postgres",
            "tablespace": "pg_default",
        }

        cursor_calls = []
        main_cursor = MagicMock()
        detail_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            if len(cursor_calls) == 0:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=detail_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        main_cursor.fetchall.return_value = [main_row]
        detail_cursor.execute.side_effect = Exception("permission denied")

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_database_catalog()

        assert len(result) == 1
        assert result[0]["database_settings"] == []

    def test_get_database_catalog_main_query_fails(self, analyzer, mock_connection):
        """Test that total failure of catalog query returns empty list"""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("connection lost")

        result = analyzer._get_database_catalog()
        assert result == []

    # ---------------------------------------------------------------
    # _get_schema_inventory()
    # ---------------------------------------------------------------

    def test_get_schema_inventory_success(self, mock_connection):
        """Test schema inventory returns schemas with object counts"""
        connection, _ = mock_connection

        schema_row = {
            "schema_name": "public",
            "owner": "postgres",
            "schema_oid": 2200,
            "description": "standard public schema",
            "is_system_schema": False,
        }

        cursor_calls = []
        main_cursor = MagicMock()
        priv_cursor = MagicMock()
        counts_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            elif idx == 1:
                ctx.__enter__ = MagicMock(return_value=priv_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=counts_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        main_cursor.fetchall.return_value = [schema_row]

        # Privileges sub-query: first check returns a row (view exists), second returns privileges
        priv_cursor.fetchone.return_value = {"?column?": 1}
        priv_cursor.fetchall.return_value = [
            {
                "grantee": "PUBLIC",
                "privilege_type": "USAGE",
                "is_grantable": "NO",
            }
        ]

        counts_cursor.fetchone.return_value = {
            "table_count": 5,
            "view_count": 2,
            "materialized_view_count": 1,
            "sequence_count": 3,
            "foreign_table_count": 0,
        }

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_schema_inventory()

        assert len(result) == 1
        assert result[0]["schema_name"] == "public"
        assert result[0]["owner"] == "postgres"
        assert len(result[0]["privileges"]) == 1
        assert result[0]["object_counts"]["table_count"] == 5

    def test_get_schema_inventory_main_query_fails(self, analyzer, mock_connection):
        """Test schema inventory returns empty list on total failure"""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("query error")

        result = analyzer._get_schema_inventory()
        assert result == []

    def test_get_schema_inventory_empty(self, analyzer, mock_connection):
        """Test schema inventory with no schemas"""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = []

        result = analyzer._get_schema_inventory()
        assert result == []

    # ---------------------------------------------------------------
    # _get_table_analysis()
    # ---------------------------------------------------------------

    def test_get_table_analysis_with_tables(self, mock_connection):
        """Test table analysis returns enriched table info"""
        connection, _ = mock_connection

        table_row = {
            "schema_name": "public",
            "table_name": "users",
            "table_oid": 16385,
            "owner": "postgres",
            "table_type": "r",
            "persistence": "p",
            "is_partition": False,
            "has_indexes": True,
            "has_rules": False,
            "has_triggers": False,
            "has_inheritance": False,
            "estimated_rows": 1000,
            "table_size_bytes": 81920,
            "table_size_human": "80 kB",
            "total_size_bytes": 131072,
            "total_size_human": "128 kB",
            "description": None,
        }

        column_row = {
            "column_name": "id",
            "column_number": 1,
            "data_type": "integer",
            "not_null": True,
            "has_default": True,
            "has_default_value": True,
            "description": None,
            "is_dropped": False,
            "storage_type": "p",
            "collation_oid": 0,
        }

        stats_row = {
            "schemaname": "public",
            "tablename": "users",
            "attname": "id",
            "n_distinct": -1.0,
            "correlation": 1.0,
        }

        cursor_calls = []
        version_cursor = MagicMock()
        main_cursor = MagicMock()
        col_cursor = MagicMock()
        stats_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=version_cursor)
            elif idx == 1:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            elif idx == 2:
                ctx.__enter__ = MagicMock(return_value=col_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=stats_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory

        # Version query
        version_cursor.fetchone.return_value = {"server_version_num": "140011"}

        # Main table query
        main_cursor.fetchall.return_value = [table_row]

        # Column sub-query
        col_cursor.fetchall.return_value = [column_row]

        # Statistics sub-query
        stats_cursor.fetchall.return_value = [stats_row]

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_table_analysis()

        assert len(result) == 1
        assert result[0]["table_name"] == "users"
        assert result[0]["schema_name"] == "public"
        assert result[0]["estimated_rows"] == 1000
        assert len(result[0]["columns"]) == 1
        assert result[0]["columns"][0]["column_name"] == "id"
        assert len(result[0]["statistics"]) == 1

    def test_get_table_analysis_empty(self, mock_connection):
        """Test table analysis with no tables returns empty list"""
        connection, _ = mock_connection

        cursor_calls = []
        version_cursor = MagicMock()
        main_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=version_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        version_cursor.fetchone.return_value = {"server_version_num": "140011"}
        main_cursor.fetchall.return_value = []

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_table_analysis()
        assert result == []

    def test_get_table_analysis_column_query_fails(self, mock_connection):
        """Test table analysis degrades gracefully when column query fails"""
        connection, _ = mock_connection

        table_row = {
            "schema_name": "public",
            "table_name": "orders",
            "table_oid": 16386,
            "owner": "postgres",
            "table_type": "r",
            "persistence": "p",
            "is_partition": False,
            "has_indexes": False,
            "has_rules": False,
            "has_triggers": False,
            "has_inheritance": False,
            "estimated_rows": 500,
            "table_size_bytes": 8192,
            "table_size_human": "8192 bytes",
            "total_size_bytes": 8192,
            "total_size_human": "8192 bytes",
            "description": None,
        }

        cursor_calls = []
        version_cursor = MagicMock()
        main_cursor = MagicMock()
        col_cursor = MagicMock()
        stats_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=version_cursor)
            elif idx == 1:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            elif idx == 2:
                ctx.__enter__ = MagicMock(return_value=col_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=stats_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        version_cursor.fetchone.return_value = {"server_version_num": "140011"}
        main_cursor.fetchall.return_value = [table_row]
        col_cursor.execute.side_effect = Exception("permission denied")
        stats_cursor.fetchall.return_value = []

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_table_analysis()

        assert len(result) == 1
        assert result[0]["columns"] == []
        assert result[0]["statistics"] == []

    def test_get_table_analysis_main_query_fails(self, analyzer, mock_connection):
        """Test table analysis returns empty list on total failure"""
        _, cursor_mock = mock_connection
        # Version query succeeds, then main query fails
        cursor_mock.fetchone.return_value = {"server_version_num": "140011"}
        analyzer._version_num = 140011
        cursor_mock.execute.side_effect = Exception("connection reset")

        result = analyzer._get_table_analysis()
        assert result == []

    def test_get_table_analysis_partitioned_table_pg10(self, mock_connection):
        """Test table analysis handles partitioned tables on PostgreSQL 10+"""
        connection, _ = mock_connection

        table_row = {
            "schema_name": "public",
            "table_name": "events",
            "table_oid": 16400,
            "owner": "postgres",
            "table_type": "p",
            "persistence": "p",
            "is_partition": False,
            "has_indexes": True,
            "has_rules": False,
            "has_triggers": False,
            "has_inheritance": True,
            "estimated_rows": 0,
            "table_size_bytes": 0,
            "table_size_human": "0 bytes",
            "total_size_bytes": 0,
            "total_size_human": "0 bytes",
            "description": None,
        }

        cursor_calls = []
        version_cursor = MagicMock()
        main_cursor = MagicMock()
        col_cursor = MagicMock()
        stats_cursor = MagicMock()
        partition_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=version_cursor)
            elif idx == 1:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            elif idx == 2:
                ctx.__enter__ = MagicMock(return_value=col_cursor)
            elif idx == 3:
                ctx.__enter__ = MagicMock(return_value=stats_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=partition_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        version_cursor.fetchone.return_value = {"server_version_num": "140011"}
        main_cursor.fetchall.return_value = [table_row]
        col_cursor.fetchall.return_value = []
        stats_cursor.fetchall.return_value = []
        partition_cursor.fetchone.return_value = {
            "partition_key": "RANGE (created_at)",
            "partdefid": 0,
        }

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_table_analysis()

        assert len(result) == 1
        assert result[0]["table_type"] == "p"
        assert "partition_info" in result[0]
        assert result[0]["partition_info"]["partition_key"] == "RANGE (created_at)"

    def test_get_table_analysis_pre_pg10(self, mock_connection):
        """Test table analysis on PostgreSQL 9.x (no declarative partitioning)"""
        connection, _ = mock_connection

        table_row = {
            "schema_name": "public",
            "table_name": "legacy_table",
            "table_oid": 16500,
            "owner": "postgres",
            "table_type": "r",
            "persistence": "p",
            "is_partition": False,
            "has_indexes": False,
            "has_rules": False,
            "has_triggers": False,
            "has_inheritance": False,
            "estimated_rows": 100,
            "table_size_bytes": 8192,
            "table_size_human": "8192 bytes",
            "total_size_bytes": 8192,
            "total_size_human": "8192 bytes",
            "description": None,
        }

        cursor_calls = []
        version_cursor = MagicMock()
        main_cursor = MagicMock()
        col_cursor = MagicMock()
        stats_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=version_cursor)
            elif idx == 1:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            elif idx == 2:
                ctx.__enter__ = MagicMock(return_value=col_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=stats_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        version_cursor.fetchone.return_value = {"server_version_num": "90624"}
        main_cursor.fetchall.return_value = [table_row]
        col_cursor.fetchall.return_value = []
        stats_cursor.fetchall.return_value = []

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_table_analysis()

        assert len(result) == 1
        assert result[0]["table_name"] == "legacy_table"

    # ---------------------------------------------------------------
    # _get_index_analysis()
    # ---------------------------------------------------------------

    def test_get_index_analysis_success(self, mock_connection):
        """Test index analysis with usage stats"""
        connection, _ = mock_connection

        index_row = {
            "schema_name": "public",
            "index_name": "users_pkey",
            "table_name": "users",
            "index_type": "btree",
            "estimated_rows": 1000,
            "index_size_bytes": 16384,
            "index_size_human": "16 kB",
            "is_unique": True,
            "is_primary": True,
            "is_exclusion": False,
            "is_immediate": True,
            "is_clustered": False,
            "is_valid": True,
            "check_xmin": False,
            "is_ready": True,
            "is_live": True,
            "index_definition": "CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id)",
            "index_expressions": None,
            "index_predicate": None,
            "description": None,
        }

        usage_stats = {
            "idx_scan": 5000,
            "idx_tup_read": 10000,
            "idx_tup_fetch": 9500,
        }

        cursor_calls = []
        main_cursor = MagicMock()
        stats_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=stats_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        main_cursor.fetchall.return_value = [index_row]
        stats_cursor.fetchone.return_value = usage_stats

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_index_analysis()

        assert len(result) == 1
        assert result[0]["index_name"] == "users_pkey"
        assert result[0]["is_primary"] is True
        assert result[0]["index_type"] == "btree"
        assert result[0]["usage_stats"]["idx_scan"] == 5000

    def test_get_index_analysis_no_usage_stats(self, mock_connection):
        """Test index analysis when usage stats query returns None"""
        connection, _ = mock_connection

        index_row = {
            "schema_name": "public",
            "index_name": "idx_email",
            "table_name": "users",
            "index_type": "btree",
            "estimated_rows": 0,
            "index_size_bytes": 8192,
            "index_size_human": "8192 bytes",
            "is_unique": False,
            "is_primary": False,
            "is_exclusion": False,
            "is_immediate": True,
            "is_clustered": False,
            "is_valid": True,
            "check_xmin": False,
            "is_ready": True,
            "is_live": True,
            "index_definition": "CREATE INDEX idx_email ON public.users USING btree (email)",
            "index_expressions": None,
            "index_predicate": None,
            "description": None,
        }

        cursor_calls = []
        main_cursor = MagicMock()
        stats_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=stats_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        main_cursor.fetchall.return_value = [index_row]
        stats_cursor.fetchone.return_value = None

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_index_analysis()

        assert len(result) == 1
        assert result[0]["index_name"] == "idx_email"
        assert "usage_stats" not in result[0]

    def test_get_index_analysis_empty(self, analyzer, mock_connection):
        """Test index analysis with no indexes"""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = []

        result = analyzer._get_index_analysis()
        assert result == []

    def test_get_index_analysis_main_query_fails(self, analyzer, mock_connection):
        """Test index analysis returns empty list on failure"""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("query failed")

        result = analyzer._get_index_analysis()
        assert result == []

    def test_get_index_analysis_stats_query_fails(self, mock_connection):
        """Test index analysis continues when stats sub-query fails"""
        connection, _ = mock_connection

        index_row = {
            "schema_name": "public",
            "index_name": "idx_test",
            "table_name": "test",
            "index_type": "btree",
            "estimated_rows": 0,
            "index_size_bytes": 8192,
            "index_size_human": "8192 bytes",
            "is_unique": False,
            "is_primary": False,
            "is_exclusion": False,
            "is_immediate": True,
            "is_clustered": False,
            "is_valid": True,
            "check_xmin": False,
            "is_ready": True,
            "is_live": True,
            "index_definition": "CREATE INDEX idx_test ON public.test USING btree (col)",
            "index_expressions": None,
            "index_predicate": None,
            "description": None,
        }

        cursor_calls = []
        main_cursor = MagicMock()
        stats_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=stats_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        main_cursor.fetchall.return_value = [index_row]
        stats_cursor.execute.side_effect = Exception("permission denied")

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_index_analysis()

        assert len(result) == 1
        assert result[0]["index_name"] == "idx_test"
        # usage_stats should not be present since the query failed
        assert "usage_stats" not in result[0]

    # ---------------------------------------------------------------
    # _get_constraint_analysis()
    # ---------------------------------------------------------------

    def test_get_constraint_analysis_success(self, analyzer, mock_connection):
        """Test constraint analysis with various constraint types"""
        _, cursor_mock = mock_connection

        constraints = [
            {
                "schema_name": "public",
                "table_name": "users",
                "constraint_name": "users_pkey",
                "constraint_type": "p",
                "is_deferrable": False,
                "is_deferred": False,
                "is_validated": True,
                "constraint_definition": "PRIMARY KEY (id)",
                "check_source": None,
                "constrained_columns": "1",
                "foreign_schema": None,
                "foreign_table": None,
                "foreign_columns": None,
                "foreign_update_action": None,
                "foreign_delete_action": None,
                "foreign_match_type": None,
            },
            {
                "schema_name": "public",
                "table_name": "orders",
                "constraint_name": "orders_user_id_fkey",
                "constraint_type": "f",
                "is_deferrable": False,
                "is_deferred": False,
                "is_validated": True,
                "constraint_definition": "FOREIGN KEY (user_id) REFERENCES users(id)",
                "check_source": None,
                "constrained_columns": "2",
                "foreign_schema": "public",
                "foreign_table": "users",
                "foreign_columns": "1",
                "foreign_update_action": "a",
                "foreign_delete_action": "c",
                "foreign_match_type": "s",
            },
        ]

        cursor_mock.fetchall.return_value = constraints

        result = analyzer._get_constraint_analysis()

        assert len(result) == 2
        assert result[0]["constraint_name"] == "users_pkey"
        assert result[0]["constraint_type"] == "p"
        assert result[1]["constraint_name"] == "orders_user_id_fkey"
        assert result[1]["constraint_type"] == "f"
        assert result[1]["foreign_table"] == "users"

    def test_get_constraint_analysis_empty(self, analyzer, mock_connection):
        """Test constraint analysis with no constraints"""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = []

        result = analyzer._get_constraint_analysis()
        assert result == []

    def test_get_constraint_analysis_fails(self, analyzer, mock_connection):
        """Test constraint analysis returns empty list on failure"""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("access denied")

        result = analyzer._get_constraint_analysis()
        assert result == []

    # ---------------------------------------------------------------
    # _get_function_analysis()
    # ---------------------------------------------------------------

    def test_get_function_analysis_success(self, analyzer, mock_connection):
        """Test function analysis returns function metadata"""
        _, cursor_mock = mock_connection

        cursor_mock.fetchall.return_value = [
            {
                "schema_name": "public",
                "function_name": "update_timestamp",
                "language": "plpgsql",
                "num_args": 0,
                "arguments": "",
                "return_type": "trigger",
                "volatility": "v",
                "is_strict": False,
                "is_security_definer": False,
                "is_leakproof": False,
                "estimated_cost": 100,
                "estimated_rows": 0,
                "source_code": "BEGIN NEW.updated_at = now(); RETURN NEW; END;",
                "description": None,
                "owner": "postgres",
            }
        ]

        result = analyzer._get_function_analysis()

        assert len(result) == 1
        assert result[0]["function_name"] == "update_timestamp"
        assert result[0]["language"] == "plpgsql"
        assert result[0]["return_type"] == "trigger"

    def test_get_function_analysis_empty(self, analyzer, mock_connection):
        """Test function analysis with no functions"""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = []

        result = analyzer._get_function_analysis()
        assert result == []

    def test_get_function_analysis_fails(self, analyzer, mock_connection):
        """Test function analysis returns empty list on failure"""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("error")

        result = analyzer._get_function_analysis()
        assert result == []

    # ---------------------------------------------------------------
    # _get_trigger_analysis()
    # ---------------------------------------------------------------

    def test_get_trigger_analysis_success(self, analyzer, mock_connection):
        """Test trigger analysis returns trigger metadata"""
        _, cursor_mock = mock_connection

        cursor_mock.fetchall.return_value = [
            {
                "schema_name": "public",
                "table_name": "users",
                "trigger_name": "trg_update_timestamp",
                "function_name": "update_timestamp",
                "trigger_type": 29,
                "is_enabled": "O",
                "is_internal": False,
                "trigger_definition": "CREATE TRIGGER trg_update_timestamp BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION update_timestamp()",
            }
        ]

        result = analyzer._get_trigger_analysis()

        assert len(result) == 1
        assert result[0]["trigger_name"] == "trg_update_timestamp"
        assert result[0]["function_name"] == "update_timestamp"

    def test_get_trigger_analysis_fails(self, analyzer, mock_connection):
        """Test trigger analysis returns empty list on failure"""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("error")

        result = analyzer._get_trigger_analysis()
        assert result == []

    # ---------------------------------------------------------------
    # _get_view_analysis()
    # ---------------------------------------------------------------

    def test_get_view_analysis_success(self, analyzer, mock_connection):
        """Test view analysis returns views and materialized views"""
        _, cursor_mock = mock_connection

        cursor_mock.fetchall.return_value = [
            {
                "schema_name": "public",
                "view_name": "active_users",
                "view_type": "v",
                "owner": "postgres",
                "view_definition": "SELECT id, name FROM users WHERE active = true",
                "description": None,
                "materialized_size_bytes": None,
                "materialized_size_human": None,
            },
            {
                "schema_name": "public",
                "view_name": "user_stats",
                "view_type": "m",
                "owner": "postgres",
                "view_definition": "SELECT count(*) FROM users",
                "description": "Materialized user stats",
                "materialized_size_bytes": 8192,
                "materialized_size_human": "8192 bytes",
            },
        ]

        result = analyzer._get_view_analysis()

        assert len(result) == 2
        assert result[0]["view_type"] == "v"
        assert result[1]["view_type"] == "m"
        assert result[1]["materialized_size_bytes"] == 8192

    def test_get_view_analysis_fails(self, analyzer, mock_connection):
        """Test view analysis returns empty list on failure"""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("error")

        result = analyzer._get_view_analysis()
        assert result == []

    # ---------------------------------------------------------------
    # _get_sequence_analysis()
    # ---------------------------------------------------------------

    def test_get_sequence_analysis_success(self, analyzer, mock_connection):
        """Test sequence analysis returns sequence metadata"""
        _, cursor_mock = mock_connection

        cursor_mock.fetchall.return_value = [
            {
                "schema_name": "public",
                "sequence_name": "users_id_seq",
                "owner": "postgres",
                "start_value": 1,
                "min_value": 1,
                "max_value": 9223372036854775807,
                "increment": 1,
                "is_cycle": False,
                "cache_size": 1,
                "last_value": 1042,
                "is_called": None,
            }
        ]

        result = analyzer._get_sequence_analysis()

        assert len(result) == 1
        assert result[0]["sequence_name"] == "users_id_seq"
        assert result[0]["last_value"] == 1042
        assert result[0]["increment"] == 1

    def test_get_sequence_analysis_fails(self, analyzer, mock_connection):
        """Test sequence analysis returns empty list on failure"""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("error")

        result = analyzer._get_sequence_analysis()
        assert result == []

    # ---------------------------------------------------------------
    # _get_partition_analysis()
    # ---------------------------------------------------------------

    def test_get_partition_analysis_pg10_plus(self, mock_connection):
        """Test partition analysis on PostgreSQL 10+"""
        connection, _ = mock_connection

        cursor_calls = []
        version_cursor = MagicMock()
        main_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=version_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        version_cursor.fetchone.return_value = {"server_version_num": "140011"}
        main_cursor.fetchall.return_value = [
            {
                "parent_schema": "public",
                "parent_table": "events",
                "partition_schema": "public",
                "partition_name": "events_2024_01",
                "partition_bound": "FOR VALUES FROM ('2024-01-01') TO ('2024-02-01')",
                "partition_size_bytes": 81920,
                "partition_size_human": "80 kB",
                "estimated_rows": 5000,
            }
        ]

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_partition_analysis()

        assert len(result) == 1
        assert result[0]["parent_table"] == "events"
        assert result[0]["partition_name"] == "events_2024_01"

    def test_get_partition_analysis_pre_pg10(self, mock_connection):
        """Test partition analysis returns empty on pre-PG10"""
        connection, _ = mock_connection

        version_cursor = MagicMock()
        cursor_calls = []

        def cursor_factory():
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=version_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        version_cursor.fetchone.return_value = {"server_version_num": "90624"}

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_partition_analysis()
        assert result == []

    def test_get_partition_analysis_fails(self, mock_connection):
        """Test partition analysis returns empty list on failure"""
        connection, _ = mock_connection

        cursor_calls = []
        version_cursor = MagicMock()
        main_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=version_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        version_cursor.fetchone.return_value = {"server_version_num": "140011"}
        main_cursor.execute.side_effect = Exception("query failed")

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_partition_analysis()
        assert result == []

    # ---------------------------------------------------------------
    # _get_inheritance_analysis()
    # ---------------------------------------------------------------

    def test_get_inheritance_analysis_pg10_plus(self, mock_connection):
        """Test inheritance analysis on PostgreSQL 10+"""
        connection, _ = mock_connection

        cursor_calls = []
        version_cursor = MagicMock()
        main_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=version_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        version_cursor.fetchone.return_value = {"server_version_num": "140011"}
        main_cursor.fetchall.return_value = [
            {
                "parent_schema": "public",
                "parent_table": "base_table",
                "child_schema": "public",
                "child_table": "child_table",
                "inheritance_sequence": 1,
                "is_partition": False,
            }
        ]

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_inheritance_analysis()

        assert len(result) == 1
        assert result[0]["parent_table"] == "base_table"
        assert result[0]["child_table"] == "child_table"
        assert result[0]["is_partition"] is False

    def test_get_inheritance_analysis_fails(self, mock_connection):
        """Test inheritance analysis returns empty list on failure"""
        connection, _ = mock_connection

        cursor_calls = []
        version_cursor = MagicMock()
        main_cursor = MagicMock()

        def cursor_factory():
            ctx = MagicMock()
            idx = len(cursor_calls)
            if idx == 0:
                ctx.__enter__ = MagicMock(return_value=version_cursor)
            else:
                ctx.__enter__ = MagicMock(return_value=main_cursor)
            ctx.__exit__ = MagicMock(return_value=None)
            cursor_calls.append(1)
            return ctx

        connection.cursor.side_effect = cursor_factory
        version_cursor.fetchone.return_value = {"server_version_num": "140011"}
        main_cursor.execute.side_effect = Exception("error")

        analyzer = SchemaAnalyzer(connection)
        result = analyzer._get_inheritance_analysis()
        assert result == []

    # ---------------------------------------------------------------
    # Instantiation
    # ---------------------------------------------------------------

    def test_instantiation_defaults(self):
        """Test SchemaAnalyzer can be created with default config"""
        connection = MagicMock()
        analyzer = SchemaAnalyzer(connection)

        assert analyzer.connection is connection
        assert analyzer.config == {}
        assert analyzer._version_num is None

    def test_instantiation_with_config(self):
        """Test SchemaAnalyzer accepts custom config"""
        connection = MagicMock()
        config = {"some_option": True}
        analyzer = SchemaAnalyzer(connection, config=config)

        assert analyzer.config == config
