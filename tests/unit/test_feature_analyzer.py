"""
Unit tests for PostgreSQL Feature Analyzer
"""

import pytest
from unittest.mock import MagicMock, patch
from planetscale_discovery.database.analyzers.feature_analyzer import FeatureAnalyzer


class TestFeatureAnalyzer:
    """Test cases for FeatureAnalyzer"""

    @pytest.fixture
    def mock_connection(self):
        """Create a mock database connection with context manager cursor."""
        connection = MagicMock()
        cursor_mock = MagicMock()
        connection.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
        connection.cursor.return_value.__exit__ = MagicMock(return_value=None)
        return connection, cursor_mock

    @pytest.fixture
    def analyzer(self, mock_connection):
        """Create FeatureAnalyzer instance with mock connection."""
        connection, _ = mock_connection
        return FeatureAnalyzer(connection)

    # -------------------------------------------------------------------------
    # analyze() top-level structure
    # -------------------------------------------------------------------------

    def test_analyze_returns_expected_structure(self, analyzer):
        """Test that analyze() returns all expected top-level keys."""
        with (
            patch.object(analyzer, "_get_extensions_analysis", return_value={}),
            patch.object(analyzer, "_get_custom_data_types", return_value={}),
            patch.object(analyzer, "_get_advanced_column_types", return_value={}),
            patch.object(analyzer, "_get_full_text_search", return_value={}),
            patch.object(analyzer, "_get_foreign_data_wrappers", return_value={}),
            patch.object(analyzer, "_get_publication_subscription", return_value={}),
            patch.object(analyzer, "_get_partitioning_features", return_value={}),
            patch.object(analyzer, "_get_large_objects", return_value={}),
            patch.object(analyzer, "_get_background_workers", return_value=[]),
            patch.object(analyzer, "_get_event_triggers", return_value=[]),
        ):
            result = analyzer.analyze()

        expected_keys = {
            "extensions_analysis",
            "custom_data_types",
            "advanced_column_types",
            "full_text_search",
            "foreign_data_wrappers",
            "publication_subscription",
            "partitioning_features",
            "large_objects",
            "background_workers",
            "event_triggers",
        }
        assert set(result.keys()) == expected_keys

    # -------------------------------------------------------------------------
    # _get_server_version_num
    # -------------------------------------------------------------------------

    def test_get_server_version_num(self, analyzer, mock_connection):
        """Test version number retrieval and caching."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchone.return_value = {"server_version_num": "140011"}

        version = analyzer._get_server_version_num()
        assert version == 140011

        # Second call should use cache (no additional cursor calls)
        version2 = analyzer._get_server_version_num()
        assert version2 == 140011
        # cursor.execute should only have been called once
        assert cursor_mock.execute.call_count == 1

    def test_get_server_version_num_error(self, analyzer, mock_connection):
        """Test version number returns 0 on error."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("connection lost")

        version = analyzer._get_server_version_num()
        assert version == 0

    # -------------------------------------------------------------------------
    # _get_extensions_analysis
    # -------------------------------------------------------------------------

    def test_extensions_analysis_with_data(self, analyzer, mock_connection):
        """Test extension detection with mock data."""
        _, cursor_mock = mock_connection

        extensions_rows = [
            {
                "extname": "pg_stat_statements",
                "extversion": "1.10",
                "schema_name": "public",
                "extrelocatable": True,
                "config_tables": None,
                "config_conditions": None,
                "description": "track planning and execution statistics",
            },
            {
                "extname": "uuid-ossp",
                "extversion": "1.1",
                "schema_name": "public",
                "extrelocatable": True,
                "config_tables": None,
                "config_conditions": None,
                "description": "generate universally unique identifiers",
            },
        ]

        dependency_rows = [
            {"extension": "postgis_topology", "depends_on": "postgis"},
        ]

        ext_objects_row = {
            "tables": 0,
            "views": 1,
            "sequences": 0,
            "foreign_tables": 0,
        }

        # fetchall calls: extensions query, dependencies query
        # fetchone calls: extension objects for each extension
        cursor_mock.fetchall.side_effect = [extensions_rows, dependency_rows]
        cursor_mock.fetchone.side_effect = [ext_objects_row, ext_objects_row]

        result = analyzer._get_extensions_analysis()

        assert result["extension_count"] == 2
        assert len(result["installed_extensions"]) == 2
        assert result["installed_extensions"][0]["extname"] == "pg_stat_statements"
        assert len(result["extension_dependencies"]) == 1
        assert result["postgis_analysis"] == {}  # no postgis in list
        assert "extension_objects" in result

    def test_extensions_analysis_with_postgis(self, analyzer, mock_connection):
        """Test that PostGIS analysis is triggered when postgis extension is present."""
        _, cursor_mock = mock_connection

        extensions_rows = [
            {
                "extname": "postgis",
                "extversion": "3.4.0",
                "schema_name": "public",
                "extrelocatable": False,
                "config_tables": None,
                "config_conditions": None,
                "description": "PostGIS geometry and geography",
            },
        ]

        cursor_mock.fetchall.side_effect = [extensions_rows, []]
        cursor_mock.fetchone.side_effect = [
            {"tables": 2, "views": 1, "sequences": 0, "foreign_tables": 0},
        ]

        with patch.object(
            analyzer, "_analyze_postgis", return_value={"postgis_version": "3.4.0"}
        ) as mock_postgis:
            result = analyzer._get_extensions_analysis()
            mock_postgis.assert_called_once()
            assert result["postgis_analysis"]["postgis_version"] == "3.4.0"

    def test_extensions_analysis_empty(self, analyzer, mock_connection):
        """Test extension analysis with no extensions."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], []]

        result = analyzer._get_extensions_analysis()

        assert result["extension_count"] == 0
        assert result["installed_extensions"] == []
        assert result["extension_dependencies"] == []

    def test_extensions_analysis_error(self, analyzer, mock_connection):
        """Test extension analysis error handling."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("permission denied")

        result = analyzer._get_extensions_analysis()

        assert "error" in result
        assert "permission denied" in result["error"]

    # -------------------------------------------------------------------------
    # _get_custom_data_types
    # -------------------------------------------------------------------------

    def test_custom_data_types_with_data(self, analyzer, mock_connection):
        """Test custom type analysis with composite, enum, domain, range types."""
        _, cursor_mock = mock_connection

        composite_rows = [
            {
                "schema_name": "public",
                "type_name": "address",
                "type_category": "c",
                "description": None,
                "owner": "postgres",
            },
        ]
        enum_rows = [
            {
                "schema_name": "public",
                "type_name": "status",
                "enum_values": ["active", "inactive"],
                "description": None,
                "owner": "postgres",
            },
        ]
        domain_rows = [
            {
                "schema_name": "public",
                "type_name": "email",
                "base_type": "character varying(255)",
                "not_null": True,
                "has_default_value": False,
                "check_constraint": None,
                "description": None,
                "owner": "postgres",
            },
        ]
        range_rows = [
            {
                "schema_name": "public",
                "type_name": "float_range",
                "subtype": "double precision",
                "description": None,
                "owner": "postgres",
            },
        ]

        usage_row = {"usage_count": 3}

        cursor_mock.fetchall.side_effect = [
            composite_rows,
            enum_rows,
            domain_rows,
            range_rows,
        ]
        # One usage query per custom type (4 types total)
        cursor_mock.fetchone.side_effect = [usage_row] * 4

        result = analyzer._get_custom_data_types()

        assert result["summary"]["composite_count"] == 1
        assert result["summary"]["enum_count"] == 1
        assert result["summary"]["domain_count"] == 1
        assert result["summary"]["range_count"] == 1
        assert result["summary"]["total_custom_types"] == 4
        assert result["composite_types"][0]["type_name"] == "address"
        assert result["enum_types"][0]["enum_values"] == ["active", "inactive"]
        assert result["domain_types"][0]["base_type"] == "character varying(255)"
        assert result["range_types"][0]["subtype"] == "double precision"
        assert len(result["type_usage"]) == 4

    def test_custom_data_types_empty(self, analyzer, mock_connection):
        """Test custom type analysis with no custom types."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], [], [], []]

        result = analyzer._get_custom_data_types()

        assert result["summary"]["total_custom_types"] == 0
        assert result["composite_types"] == []
        assert result["enum_types"] == []
        assert result["domain_types"] == []
        assert result["range_types"] == []
        assert result["type_usage"] == {}

    def test_custom_data_types_error(self, analyzer, mock_connection):
        """Test custom type analysis error handling."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("query failed")

        result = analyzer._get_custom_data_types()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_advanced_column_types
    # -------------------------------------------------------------------------

    def test_advanced_column_types_with_data(self, analyzer, mock_connection):
        """Test advanced column type detection."""
        _, cursor_mock = mock_connection

        array_cols = [
            {
                "schema_name": "public",
                "table_name": "users",
                "column_name": "tags",
                "data_type": "text[]",
                "array_dimensions": 1,
            },
        ]
        json_cols = [
            {
                "schema_name": "public",
                "table_name": "events",
                "column_name": "payload",
                "data_type": "jsonb",
            },
        ]
        hstore_cols = []
        spatial_cols = []
        lob_cols = []

        cursor_mock.fetchall.side_effect = [
            array_cols,
            json_cols,
            hstore_cols,
            spatial_cols,
            lob_cols,
        ]

        result = analyzer._get_advanced_column_types()

        assert result["summary"]["array_count"] == 1
        assert result["summary"]["json_count"] == 1
        assert result["summary"]["hstore_count"] == 0
        assert result["summary"]["spatial_count"] == 0
        assert result["summary"]["lob_count"] == 0
        assert result["array_columns"][0]["column_name"] == "tags"
        assert result["json_columns"][0]["data_type"] == "jsonb"

    def test_advanced_column_types_empty(self, analyzer, mock_connection):
        """Test advanced column types with no results."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], [], [], [], []]

        result = analyzer._get_advanced_column_types()

        assert result["summary"]["array_count"] == 0
        assert result["summary"]["json_count"] == 0

    def test_advanced_column_types_error(self, analyzer, mock_connection):
        """Test advanced column types error handling."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("access denied")

        result = analyzer._get_advanced_column_types()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_full_text_search
    # -------------------------------------------------------------------------

    def test_full_text_search_with_data(self, analyzer, mock_connection):
        """Test full-text search analysis."""
        _, cursor_mock = mock_connection

        ts_configs = [
            {
                "config_name": "english_custom",
                "schema_name": "public",
                "owner": "postgres",
            },
        ]
        ts_dicts = [
            {
                "dictionary_name": "english_stem",
                "schema_name": "public",
                "owner": "postgres",
                "template_name": "snowball",
            },
        ]
        tsvector_cols = [
            {
                "schema_name": "public",
                "table_name": "articles",
                "column_name": "search_vector",
                "data_type": "tsvector",
            },
        ]
        fts_indexes = [
            {
                "schema_name": "public",
                "table_name": "articles",
                "index_name": "idx_articles_search",
                "index_type": "gin",
                "index_definition": "CREATE INDEX idx_articles_search ON public.articles USING gin (search_vector)",
            },
        ]

        cursor_mock.fetchall.side_effect = [
            ts_configs,
            ts_dicts,
            tsvector_cols,
            fts_indexes,
        ]

        result = analyzer._get_full_text_search()

        assert result["summary"]["custom_ts_configs"] == 1
        assert result["summary"]["custom_ts_dictionaries"] == 1
        assert result["summary"]["tsvector_columns"] == 1
        assert result["summary"]["fts_indexes"] == 1

    def test_full_text_search_empty(self, analyzer, mock_connection):
        """Test FTS analysis with no data."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], [], [], []]

        result = analyzer._get_full_text_search()

        assert result["summary"]["custom_ts_configs"] == 0
        assert result["summary"]["tsvector_columns"] == 0

    def test_full_text_search_error(self, analyzer, mock_connection):
        """Test FTS analysis error handling."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("query error")

        result = analyzer._get_full_text_search()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_foreign_data_wrappers
    # -------------------------------------------------------------------------

    def test_foreign_data_wrappers_with_data(self, analyzer, mock_connection):
        """Test FDW detection with servers, tables, and user mappings."""
        _, cursor_mock = mock_connection

        fdws = [
            {
                "fdw_name": "postgres_fdw",
                "owner": "postgres",
                "handler": "postgres_fdw_handler",
                "validator": "postgres_fdw_validator",
                "options": None,
            },
        ]
        foreign_servers = [
            {
                "server_name": "remote_db",
                "owner": "postgres",
                "fdw_name": "postgres_fdw",
                "server_type": None,
                "server_version": None,
                "options": ["host=remote.example.com", "dbname=prod"],
            },
        ]
        foreign_tables = [
            {
                "schema_name": "public",
                "table_name": "remote_users",
                "server_name": "remote_db",
                "table_options": ["schema_name=public", "table_name=users"],
                "estimated_rows": 50000,
            },
        ]
        user_mappings = [
            {
                "server_name": "remote_db",
                "username": "app_user",
                "options": ["user=remote_user"],
            },
        ]

        cursor_mock.fetchall.side_effect = [
            fdws,
            foreign_servers,
            foreign_tables,
            user_mappings,
        ]

        result = analyzer._get_foreign_data_wrappers()

        assert result["summary"]["fdw_count"] == 1
        assert result["summary"]["foreign_server_count"] == 1
        assert result["summary"]["foreign_table_count"] == 1
        assert result["summary"]["user_mapping_count"] == 1
        assert result["foreign_data_wrappers"][0]["fdw_name"] == "postgres_fdw"
        assert result["foreign_tables"][0]["table_name"] == "remote_users"

    def test_foreign_data_wrappers_no_servers_skips_user_mappings(
        self, analyzer, mock_connection
    ):
        """Test that user mapping query is skipped when no foreign servers exist."""
        _, cursor_mock = mock_connection

        # No FDWs, no servers, no tables
        cursor_mock.fetchall.side_effect = [[], [], []]

        result = analyzer._get_foreign_data_wrappers()

        assert result["summary"]["fdw_count"] == 0
        assert result["summary"]["foreign_server_count"] == 0
        assert result["summary"]["user_mapping_count"] == 0
        # Only 3 queries executed (fdws, servers, tables); user_mappings skipped
        assert cursor_mock.execute.call_count == 3

    def test_foreign_data_wrappers_user_mapping_permission_error(
        self, analyzer, mock_connection
    ):
        """Test graceful handling when user mapping query fails (managed DB)."""
        _, cursor_mock = mock_connection

        fdws = [
            {
                "fdw_name": "postgres_fdw",
                "owner": "postgres",
                "handler": "-",
                "validator": "-",
                "options": None,
            }
        ]
        servers = [
            {
                "server_name": "srv",
                "owner": "postgres",
                "fdw_name": "postgres_fdw",
                "server_type": None,
                "server_version": None,
                "options": None,
            }
        ]
        tables = []

        call_count = [0]

        def fetchall_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return fdws
            elif call_count[0] == 2:
                return servers
            elif call_count[0] == 3:
                return tables
            elif call_count[0] == 4:
                raise Exception("permission denied for relation pg_user_mapping")
            return []

        cursor_mock.fetchall.side_effect = fetchall_side_effect

        result = analyzer._get_foreign_data_wrappers()

        # Should still succeed; user_mappings defaults to empty
        assert result["summary"]["user_mapping_count"] == 0
        assert "error" not in result

    def test_foreign_data_wrappers_empty(self, analyzer, mock_connection):
        """Test FDW analysis with no results."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], [], []]

        result = analyzer._get_foreign_data_wrappers()

        assert result["summary"]["fdw_count"] == 0

    def test_foreign_data_wrappers_error(self, analyzer, mock_connection):
        """Test FDW analysis error handling."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("connection reset")

        result = analyzer._get_foreign_data_wrappers()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_publication_subscription
    # -------------------------------------------------------------------------

    def test_publication_subscription_with_data(self, analyzer, mock_connection):
        """Test pub/sub analysis with publications and subscriptions."""
        _, cursor_mock = mock_connection

        publications = [
            {
                "publication_name": "my_pub",
                "owner": "postgres",
                "all_tables": False,
                "pubinsert": True,
                "pubupdate": True,
                "pubdelete": True,
                "pubtruncate": True,
            },
        ]
        pub_tables = [
            {
                "publication_name": "my_pub",
                "schema_name": "public",
                "table_name": "orders",
            },
        ]
        subscriptions = [
            {
                "subscription_name": "my_sub",
                "owner": "postgres",
                "enabled": True,
                "connection_info": "host=primary dbname=mydb",
                "slot_name": "my_sub",
                "sync_commit": "off",
                "publications": ["my_pub"],
            },
        ]

        cursor_mock.fetchall.side_effect = [publications, pub_tables, subscriptions]

        result = analyzer._get_publication_subscription()

        assert result["summary"]["publication_count"] == 1
        assert result["summary"]["subscription_count"] == 1
        assert result["summary"]["published_table_count"] == 1

    def test_publication_subscription_empty(self, analyzer, mock_connection):
        """Test pub/sub analysis with no data."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.side_effect = [[], [], []]

        result = analyzer._get_publication_subscription()

        assert result["summary"]["publication_count"] == 0
        assert result["summary"]["subscription_count"] == 0

    def test_publication_subscription_error(self, analyzer, mock_connection):
        """Test pub/sub analysis error handling."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("query timeout")

        result = analyzer._get_publication_subscription()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_partitioning_features
    # -------------------------------------------------------------------------

    def test_partitioning_with_data(self, analyzer, mock_connection):
        """Test partitioning detection on PostgreSQL 10+."""
        _, cursor_mock = mock_connection

        # First call for version check
        cursor_mock.fetchone.side_effect = [
            {"server_version_num": "140011"},  # version query
            {"partition_count": 4},  # partition count for first table
        ]

        partitioned_tables = [
            {
                "schema_name": "public",
                "table_name": "events",
                "partition_key": "RANGE (created_at)",
                "estimated_rows": 1000000,
                "table_size_bytes": 536870912,
            },
        ]
        cursor_mock.fetchall.side_effect = [partitioned_tables]

        result = analyzer._get_partitioning_features()

        assert result["summary"]["partitioned_table_count"] == 1
        assert result["summary"]["total_partitions"] == 4
        assert result["partitioned_tables"][0]["table_name"] == "events"
        assert result["partition_counts"]["public.events"] == 4

    def test_partitioning_pre_pg10(self, analyzer, mock_connection):
        """Test partitioning returns empty on PostgreSQL < 10."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchone.return_value = {"server_version_num": "90624"}

        result = analyzer._get_partitioning_features()

        assert result["summary"]["partitioned_table_count"] == 0
        assert result["summary"]["total_partitions"] == 0
        assert "note" in result["summary"]
        assert result["partitioned_tables"] == []

    def test_partitioning_empty(self, analyzer, mock_connection):
        """Test partitioning with no partitioned tables."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchone.return_value = {"server_version_num": "150007"}
        cursor_mock.fetchall.side_effect = [[]]

        result = analyzer._get_partitioning_features()

        assert result["summary"]["partitioned_table_count"] == 0
        assert result["summary"]["total_partitions"] == 0

    def test_partitioning_error(self, analyzer, mock_connection):
        """Test partitioning error handling."""
        _, cursor_mock = mock_connection
        # Version query succeeds, but partition query fails
        cursor_mock.fetchone.return_value = {"server_version_num": "150007"}
        cursor_mock.fetchall.side_effect = Exception("relation not found")

        result = analyzer._get_partitioning_features()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_large_objects
    # -------------------------------------------------------------------------

    def test_large_objects_with_data(self, analyzer, mock_connection):
        """Test large object analysis."""
        _, cursor_mock = mock_connection

        lob_summary = {"lob_count": 10, "owned_lobs": 8}
        lobs_by_owner = [
            {"owner": "app_user", "lob_count": 5},
            {"owner": "postgres", "lob_count": 3},
        ]

        cursor_mock.fetchone.return_value = lob_summary
        cursor_mock.fetchall.return_value = lobs_by_owner

        result = analyzer._get_large_objects()

        assert result["summary"]["lob_count"] == 10
        assert result["summary"]["owned_lobs"] == 8
        assert len(result["lobs_by_owner"]) == 2

    def test_large_objects_empty(self, analyzer, mock_connection):
        """Test large object analysis with no objects."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchone.return_value = {"lob_count": 0, "owned_lobs": 0}
        cursor_mock.fetchall.return_value = []

        result = analyzer._get_large_objects()

        assert result["summary"]["lob_count"] == 0

    def test_large_objects_error(self, analyzer, mock_connection):
        """Test large object analysis error handling."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("permission denied")

        result = analyzer._get_large_objects()

        assert "error" in result

    # -------------------------------------------------------------------------
    # _get_background_workers
    # -------------------------------------------------------------------------

    def test_background_workers_with_data(self, analyzer, mock_connection):
        """Test background worker detection."""
        _, cursor_mock = mock_connection

        workers = [
            {
                "pid": 100,
                "backend_type": "autovacuum launcher",
                "backend_start": "2024-01-01T00:00:00",
            },
            {
                "pid": 101,
                "backend_type": "logical replication launcher",
                "backend_start": "2024-01-01T00:00:00",
            },
        ]
        cursor_mock.fetchall.return_value = workers

        result = analyzer._get_background_workers()

        assert len(result) == 2
        assert result[0]["backend_type"] == "autovacuum launcher"

    def test_background_workers_empty(self, analyzer, mock_connection):
        """Test background workers with no results."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = []

        result = analyzer._get_background_workers()

        assert result == []

    def test_background_workers_error(self, analyzer, mock_connection):
        """Test background workers error handling."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("stats collector down")

        result = analyzer._get_background_workers()

        assert result == []

    # -------------------------------------------------------------------------
    # _get_event_triggers
    # -------------------------------------------------------------------------

    def test_event_triggers_with_data(self, analyzer, mock_connection):
        """Test event trigger detection."""
        _, cursor_mock = mock_connection

        triggers = [
            {
                "trigger_name": "audit_ddl",
                "event": "ddl_command_end",
                "owner": "postgres",
                "function_name": "log_ddl_change",
                "enabled": "O",
                "tags": None,
            },
        ]
        cursor_mock.fetchall.return_value = triggers

        result = analyzer._get_event_triggers()

        assert len(result) == 1
        assert result[0]["trigger_name"] == "audit_ddl"
        assert result[0]["event"] == "ddl_command_end"

    def test_event_triggers_empty(self, analyzer, mock_connection):
        """Test event triggers with no results."""
        _, cursor_mock = mock_connection
        cursor_mock.fetchall.return_value = []

        result = analyzer._get_event_triggers()

        assert result == []

    def test_event_triggers_error(self, analyzer, mock_connection):
        """Test event triggers error handling."""
        _, cursor_mock = mock_connection
        cursor_mock.execute.side_effect = Exception("catalog error")

        result = analyzer._get_event_triggers()

        assert result == []

    # -------------------------------------------------------------------------
    # Error handling: queries fail mid-analysis
    # -------------------------------------------------------------------------

    def test_extension_object_count_failure_continues(self, analyzer, mock_connection):
        """Test that failure to count extension objects does not stop analysis."""
        _, cursor_mock = mock_connection

        extensions_rows = [
            {
                "extname": "broken_ext",
                "extversion": "1.0",
                "schema_name": "public",
                "extrelocatable": True,
                "config_tables": None,
                "config_conditions": None,
                "description": None,
            },
        ]

        cursor_mock.fetchall.side_effect = [extensions_rows, []]

        # Extension objects query fails
        cursor_mock.fetchone.side_effect = Exception(
            "could not query extension objects"
        )

        result = analyzer._get_extensions_analysis()

        # Should still return valid result
        assert result["extension_count"] == 1
        assert result["extension_objects"] == {}

    def test_type_usage_failure_continues(self, analyzer, mock_connection):
        """Test that failure to get type usage does not stop custom type analysis."""
        _, cursor_mock = mock_connection

        enum_rows = [
            {
                "schema_name": "public",
                "type_name": "status",
                "enum_values": ["a", "b"],
                "description": None,
                "owner": "postgres",
            },
        ]

        cursor_mock.fetchall.side_effect = [[], enum_rows, [], []]
        cursor_mock.fetchone.side_effect = Exception("usage query failed")

        result = analyzer._get_custom_data_types()

        assert result["summary"]["enum_count"] == 1
        assert result["type_usage"] == {}
