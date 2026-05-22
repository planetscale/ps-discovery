"""
Unit tests for MySQL Schema Analyzer - new sections
(column_analysis, check_constraint_analysis, index cardinality)
"""

import pytest
from unittest.mock import MagicMock, patch

from planetscale_discovery.database.mysql_analyzers.schema_analyzer import (
    MySQLSchemaAnalyzer,
)


class TestMySQLSchemaAnalyzer:
    """Test cases for MySQLSchemaAnalyzer new analysis sections."""

    @pytest.fixture
    def mock_connection(self):
        """Create a mock MySQL connection with cursor."""
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value = cursor
        return connection, cursor

    @pytest.fixture
    def analyzer(self, mock_connection):
        connection, _ = mock_connection
        return MySQLSchemaAnalyzer(connection)

    # ---------------------------------------------------------------
    # analyze() - structure includes new keys
    # ---------------------------------------------------------------

    def test_analyze_includes_new_keys(self, analyzer):
        """analyze() result should contain column_analysis and check_constraint_analysis."""
        with (
            patch.object(analyzer, "_get_database_list", return_value=["testdb"]),
            patch.object(
                analyzer,
                "_get_table_analysis",
                return_value=[{"schema_name": "testdb"}],
            ),
            patch.object(analyzer, "_get_column_analysis", return_value=[]),
            patch.object(analyzer, "_get_index_analysis", return_value=[]),
            patch.object(analyzer, "_get_view_analysis", return_value=[]),
            patch.object(analyzer, "_get_routine_analysis", return_value=[]),
            patch.object(analyzer, "_get_trigger_analysis", return_value=[]),
            patch.object(analyzer, "_get_constraint_analysis", return_value=[]),
            patch.object(analyzer, "_get_check_constraints", return_value=[]),
            patch.object(analyzer, "_get_partition_analysis", return_value=[]),
            patch.object(analyzer, "_get_db_object_counts", return_value=[]),
            patch.object(analyzer, "_get_db_storage_engines", return_value=[]),
            patch.object(analyzer, "_get_db_index_types", return_value=[]),
            patch.object(analyzer, "_get_db_column_types", return_value=[]),
        ):
            result = analyzer.analyze()

        assert "column_analysis" in result
        assert "check_constraint_analysis" in result

    def test_analyze_per_database_includes_new_keys(self, analyzer):
        """Per-database iteration path should also include new keys."""
        with (
            patch.object(analyzer, "_get_database_list", return_value=["db1", "db2"]),
            # Return only db1 in tables to trigger iteration
            patch.object(
                analyzer, "_get_table_analysis", return_value=[{"schema_name": "db1"}]
            ),
            patch.object(analyzer, "_get_column_analysis", return_value=[]),
            patch.object(analyzer, "_get_index_analysis", return_value=[]),
            patch.object(analyzer, "_get_view_analysis", return_value=[]),
            patch.object(analyzer, "_get_routine_analysis", return_value=[]),
            patch.object(analyzer, "_get_trigger_analysis", return_value=[]),
            patch.object(analyzer, "_get_constraint_analysis", return_value=[]),
            patch.object(analyzer, "_get_check_constraints", return_value=[]),
            patch.object(analyzer, "_get_partition_analysis", return_value=[]),
            patch.object(analyzer, "_get_db_object_counts", return_value=[]),
            patch.object(analyzer, "_get_db_storage_engines", return_value=[]),
            patch.object(analyzer, "_get_db_index_types", return_value=[]),
            patch.object(analyzer, "_get_db_column_types", return_value=[]),
        ):
            result = analyzer.analyze()

        assert "column_analysis" in result
        assert "check_constraint_analysis" in result

    # ---------------------------------------------------------------
    # _get_column_analysis
    # ---------------------------------------------------------------

    def test_column_analysis_returns_rows(self, analyzer, mock_connection):
        """_get_column_analysis should return column detail from execute_query."""
        expected = [
            {
                "schema_name": "mydb",
                "table_name": "users",
                "column_name": "id",
                "ordinal_position": 1,
                "data_type": "bigint",
                "column_type": "bigint unsigned",
                "is_nullable": "NO",
                "column_default": None,
                "extra": "auto_increment",
                "character_set_name": None,
                "collation_name": None,
                "column_comment": "",
                "generation_expression": None,
            }
        ]
        with patch.object(analyzer, "execute_query", return_value=expected):
            result = analyzer._get_column_analysis()

        assert len(result) == 1
        assert result[0]["column_name"] == "id"
        assert result[0]["extra"] == "auto_increment"
        assert result[0]["column_type"] == "bigint unsigned"

    def test_column_analysis_detects_generated_columns(self, analyzer):
        """Generated/virtual columns should have generation_expression populated."""
        rows = [
            {
                "schema_name": "mydb",
                "table_name": "orders",
                "column_name": "total_cost",
                "ordinal_position": 5,
                "data_type": "decimal",
                "column_type": "decimal(10,2)",
                "is_nullable": "YES",
                "column_default": None,
                "extra": "VIRTUAL GENERATED",
                "character_set_name": None,
                "collation_name": None,
                "column_comment": "",
                "generation_expression": "`qty` * `unit_price`",
            }
        ]
        with patch.object(analyzer, "execute_query", return_value=rows):
            result = analyzer._get_column_analysis()

        assert result[0]["generation_expression"] == "`qty` * `unit_price`"
        assert "GENERATED" in result[0]["extra"]

    def test_column_analysis_error_returns_empty(self, analyzer):
        """_get_column_analysis should return [] on error and record the gap."""
        with patch.object(
            analyzer, "execute_query", side_effect=Exception("access denied")
        ):
            result = analyzer._get_column_analysis()

        assert result == []
        assert len(analyzer.errors) == 1

    # ---------------------------------------------------------------
    # _get_check_constraints
    # ---------------------------------------------------------------

    def test_check_constraints_returns_rows(self, analyzer):
        """_get_check_constraints should return CHECK constraint definitions."""
        expected = [
            {
                "schema_name": "mydb",
                "table_name": "products",
                "constraint_name": "chk_price_positive",
                "check_clause": "`price` > 0",
            }
        ]
        with patch.object(analyzer, "execute_query", return_value=expected):
            result = analyzer._get_check_constraints()

        assert len(result) == 1
        assert result[0]["check_clause"] == "`price` > 0"

    def test_check_constraints_graceful_on_old_mysql(self, analyzer):
        """Should return [] without error on MySQL < 8.0.16 where table doesn't exist."""
        with patch.object(
            analyzer,
            "execute_query",
            side_effect=Exception("Table 'check_constraints' doesn't exist"),
        ):
            result = analyzer._get_check_constraints()

        assert result == []
        # Should NOT record an error for expected missing table
        assert len(analyzer.errors) == 0

    def test_check_constraints_unexpected_error_records_gap(self, analyzer):
        """Unexpected errors should be recorded as analysis gaps."""
        with patch.object(
            analyzer,
            "execute_query",
            side_effect=Exception("connection lost"),
        ):
            result = analyzer._get_check_constraints()

        assert result == []
        assert len(analyzer.errors) == 1

    # ---------------------------------------------------------------
    # _get_index_analysis - cardinality and prefix_lengths
    # ---------------------------------------------------------------

    def test_index_analysis_includes_cardinality(self, analyzer):
        """Index analysis should include cardinality and prefix_lengths."""
        expected = [
            {
                "schema_name": "mydb",
                "table_name": "users",
                "index_name": "PRIMARY",
                "index_type": "BTREE",
                "is_unique": 1,
                "is_primary": 1,
                "columns": "id",
                "column_count": 1,
                "nullable": "",
                "cardinality": 150000,
                "prefix_lengths": None,
            },
            {
                "schema_name": "mydb",
                "table_name": "users",
                "index_name": "idx_email",
                "index_type": "BTREE",
                "is_unique": 1,
                "is_primary": 0,
                "columns": "email",
                "column_count": 1,
                "nullable": "YES",
                "cardinality": 149500,
                "prefix_lengths": "30",
            },
        ]
        with patch.object(analyzer, "execute_query", return_value=expected):
            result = analyzer._get_index_analysis()

        assert result[0]["cardinality"] == 150000
        assert result[0]["prefix_lengths"] is None
        assert result[1]["prefix_lengths"] == "30"


class TestQuoteIdentifier:
    """Safe identifier quoting for USE <db> and friends."""

    def test_plain_identifier_is_backtick_wrapped(self):
        from planetscale_discovery.database.mysql_analyzers.schema_analyzer import (
            _quote_identifier,
        )

        assert _quote_identifier("my_db") == "`my_db`"

    def test_embedded_backticks_are_doubled(self):
        """MySQL escapes a backtick inside a quoted identifier by doubling it."""
        from planetscale_discovery.database.mysql_analyzers.schema_analyzer import (
            _quote_identifier,
        )

        # A hypothetical db named `weird``name` becomes ``weird````name``
        assert _quote_identifier("weird`name") == "`weird``name`"

    def test_quoting_neutralises_injection_shape(self):
        """A db name trying to close the quote can't break out of the identifier."""
        from planetscale_discovery.database.mysql_analyzers.schema_analyzer import (
            _quote_identifier,
        )

        injected = "foo`; DROP TABLE x; --"
        quoted = _quote_identifier(injected)
        assert quoted == "`foo``; DROP TABLE x; --`"
        # The semicolon and DROP end up inside the identifier, not as separate SQL.
        assert quoted.startswith("`") and quoted.endswith("`")


class TestPerDatabaseSchemaRestore:
    """Verify _analyze_per_database restores the connection's default schema."""

    def _make_analyzer(self, original_db, executed):
        """Build an analyzer whose cursor records every executed statement
        and reports `original_db` as the current default schema."""
        connection = MagicMock()

        def make_cursor():
            cursor = MagicMock()

            def execute(sql, *_args, **_kwargs):
                executed.append(sql)

            cursor.execute.side_effect = execute
            cursor.fetchone.return_value = (original_db,) if original_db else (None,)
            return cursor

        connection.cursor.side_effect = make_cursor
        return MySQLSchemaAnalyzer(connection)

    def test_original_schema_restored_after_iteration(self):
        executed = []
        analyzer = self._make_analyzer("app_prod", executed)

        with (
            patch.object(analyzer, "_get_table_analysis", return_value=[]),
            patch.object(analyzer, "_get_column_analysis", return_value=[]),
            patch.object(analyzer, "_get_index_analysis", return_value=[]),
            patch.object(analyzer, "_get_view_analysis", return_value=[]),
            patch.object(analyzer, "_get_routine_analysis", return_value=[]),
            patch.object(analyzer, "_get_trigger_analysis", return_value=[]),
            patch.object(analyzer, "_get_constraint_analysis", return_value=[]),
            patch.object(analyzer, "_get_check_constraints", return_value=[]),
            patch.object(analyzer, "_get_partition_analysis", return_value=[]),
            patch.object(analyzer, "_get_db_object_counts", return_value=[]),
            patch.object(analyzer, "_get_db_storage_engines", return_value=[]),
            patch.object(analyzer, "_get_db_index_types", return_value=[]),
            patch.object(analyzer, "_get_db_column_types", return_value=[]),
        ):
            analyzer._analyze_per_database(["db_one", "db_two"])

        # Last USE should restore the original default schema, not leave us on db_two.
        use_statements = [s for s in executed if s.startswith("USE ")]
        assert use_statements[-1] == "USE `app_prod`"
        assert "USE `db_one`" in use_statements
        assert "USE `db_two`" in use_statements

    def test_no_restore_attempt_when_original_unknown(self):
        executed = []
        analyzer = self._make_analyzer(None, executed)

        with (
            patch.object(analyzer, "_get_table_analysis", return_value=[]),
            patch.object(analyzer, "_get_column_analysis", return_value=[]),
            patch.object(analyzer, "_get_index_analysis", return_value=[]),
            patch.object(analyzer, "_get_view_analysis", return_value=[]),
            patch.object(analyzer, "_get_routine_analysis", return_value=[]),
            patch.object(analyzer, "_get_trigger_analysis", return_value=[]),
            patch.object(analyzer, "_get_constraint_analysis", return_value=[]),
            patch.object(analyzer, "_get_check_constraints", return_value=[]),
            patch.object(analyzer, "_get_partition_analysis", return_value=[]),
            patch.object(analyzer, "_get_db_object_counts", return_value=[]),
            patch.object(analyzer, "_get_db_storage_engines", return_value=[]),
            patch.object(analyzer, "_get_db_index_types", return_value=[]),
            patch.object(analyzer, "_get_db_column_types", return_value=[]),
        ):
            analyzer._analyze_per_database(["db_one"])

        use_statements = [s for s in executed if s.startswith("USE ")]
        # We USE db_one to analyze it but should not attempt a restore.
        assert use_statements == ["USE `db_one`"]

    def test_original_schema_restored_even_if_iteration_raises(self):
        executed = []
        analyzer = self._make_analyzer("app_prod", executed)

        with (
            patch.object(
                analyzer,
                "_get_table_analysis",
                side_effect=RuntimeError("boom"),
            ),
        ):
            with pytest.raises(RuntimeError):
                analyzer._analyze_per_database(["db_one"])

        use_statements = [s for s in executed if s.startswith("USE ")]
        assert use_statements[-1] == "USE `app_prod`"
