"""Unit tests for CockroachFeatureAnalyzer."""

from unittest.mock import MagicMock, patch

from planetscale_discovery.database.cockroach_analyzers.feature_analyzer import (
    CockroachFeatureAnalyzer,
)


def test_custom_types_carry_a_null_extension():
    """Catches a CockroachDB type row without the extension field that a
    PostgreSQL type row carries, or a read of pg_depend for it. CockroachDB
    has no extensions."""
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    connection.cursor.return_value.__enter__.return_value = cursor
    analyzer = CockroachFeatureAnalyzer(connection)

    with patch.object(analyzer, "_get_server_version_num", return_value=140000):
        analyzer._get_custom_data_types()

    statements = [" ".join(c[0][0].split()) for c in cursor.execute.call_args_list]
    assert len(statements) == 4
    for sql in statements:
        assert "NULL::text as extension FROM pg_type t" in sql
        assert "pg_depend" not in sql
