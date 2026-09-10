"""Tests for SQL literal redaction."""

import pytest

from planetscale_discovery.common.sanitize import (
    FALLBACK_PLACEHOLDER,
    redact_sql,
    statement_kind,
)


class TestRedactSql:
    @pytest.mark.parametrize(
        "sql,expected",
        [
            (
                "UPDATE t SET x=1 WHERE email = 'alice@example.com'",
                "UPDATE t SET x=$1 WHERE email = $2",
            ),
            # A -- inside a literal is not a comment.
            (
                "UPDATE t SET x=1 WHERE email = 'alice--bob@example.com'",
                "UPDATE t SET x=$1 WHERE email = $2",
            ),
            (
                "SELECT * FROM t WHERE note = 'has -- dashes' AND id = 5",
                "SELECT * FROM t WHERE note = $1 AND id = $2",
            ),
            (
                "SELECT 1 WHERE s = 'a/*b' AND t = 'c*/d'",
                "SELECT $1 WHERE s = $2 AND t = $3",
            ),
            # '' is an escaped quote, not the end of the literal.
            ("SELECT * FROM t WHERE s = 'it''s ok'", "SELECT * FROM t WHERE s = $1"),
            ("-- note\nSELECT 1", "SELECT $1"),
            ("SELECT /* hi */ 1 FROM t", "SELECT $1 FROM t"),
            # A credential is a string literal like any other.
            ("CREATE ROLE app PASSWORD 'hunter2'", "CREATE ROLE app PASSWORD $1"),
        ],
    )
    def test_literals_become_placeholders(self, sql, expected):
        assert redact_sql(sql) == expected

    def test_output_is_valid_postgres(self):
        """? is not a PostgreSQL placeholder, so redacted text must use $N."""
        parser = pytest.importorskip("pglast.parser")
        for sql in (
            "SELECT a FROM t ORDER BY 1",
            "SELECT * FROM t WHERE b = 'x'",
            "SELECT 'lit' FROM t",
            "INSERT INTO t (a, b) VALUES ('x', 2)",
        ):
            parser.parse_sql(redact_sql(sql))

    def test_existing_placeholders_are_not_reused(self):
        """Server-normalized text already uses $1; ours continue after it."""
        out = redact_sql("SELECT * FROM t WHERE a = $1 AND b = 'x'")
        assert out == "SELECT * FROM t WHERE a = $1 AND b = $2"

    def test_server_normalized_text_is_unchanged(self):
        sql = "SELECT * FROM orders WHERE tenant_id = $1 AND status = $2"
        assert redact_sql(sql) == sql

    def test_identifiers_and_structure_survive(self):
        out = redact_sql("SELECT id FROM public.orders JOIN t ON t.id = orders.id")
        assert "public.orders" in out
        assert "JOIN" in out

    @pytest.mark.parametrize("sql", ["", "   ", None])
    def test_empty_input_returns_none(self, sql):
        assert redact_sql(sql) is None

    def test_no_input_literal_survives_a_corpus(self):
        secret = "sup3rsecret-value"
        for template in (
            "SELECT * FROM t WHERE a = '{v}'",
            "INSERT INTO t (a) VALUES ('{v}')",
            "UPDATE t SET a = '{v}' WHERE b = 1",
            "DELETE FROM t WHERE a = '{v}'",
            "CREATE ROLE r PASSWORD '{v}'",
            "SELECT * FROM t WHERE a = '{v}' -- trailing",
            "CREATE ROLE r PASSWORD $${v}$$",
            "DO $$ BEGIN UPDATE t SET a = '{v}'; END $$",
        ):
            assert secret not in (redact_sql(template.format(v=secret)) or "")

    def test_dollar_quoted_password_is_redacted(self):
        assert "hunter2" not in redact_sql("CREATE ROLE app PASSWORD $$hunter2$$")

    def test_dollar_quoted_function_body_is_redacted(self):
        out = redact_sql("DO $$ BEGIN UPDATE t SET a='x'; END $$")
        assert "'x'" not in out
        assert "END" not in out

    def test_unparseable_input_fails_closed(self, monkeypatch):
        """Returning the input on failure would defeat the point."""
        import planetscale_discovery.common.sanitize as module

        def boom(*args, **kwargs):
            raise RuntimeError("tokenizer exploded")

        monkeypatch.setattr(module.sqlparse, "format", boom)
        assert redact_sql("SELECT 'secret'") == FALLBACK_PLACEHOLDER


class TestQuotedIdentifiersSurvive:
    """A double-quoted identifier is a name, not data."""

    @pytest.mark.parametrize(
        "sql",
        [
            'SELECT "public"."orders" FROM x',
            'SELECT d.datname as "Name" FROM pg_database d',
            'SELECT "od--d" FROM t',
            'SELECT $1 FROM ONLY "public"."t" x WHERE "c" OPERATOR(pg_catalog.=) $2',
        ],
    )
    def test_quoted_identifiers_are_preserved(self, sql):
        out = redact_sql(sql)
        for quoted in [w for w in sql.split() if w.startswith('"')]:
            assert quoted.rstrip(",") in out, f"{quoted} was redacted: {out}"

    def test_a_literal_beside_a_quoted_identifier_is_still_redacted(self):
        out = redact_sql('SELECT "col" FROM t WHERE "col" = \'secret\'')
        assert '"col"' in out
        assert "secret" not in out


class TestStatementKind:
    @pytest.mark.parametrize(
        "sql,kind",
        [
            ("SELECT 1", "SELECT"),
            ("UPDATE t SET a = 1", "UPDATE"),
            ("INSERT INTO t VALUES (1)", "INSERT"),
            ("DELETE FROM t", "DELETE"),
            ("CREATE INDEX i ON t (a)", "CREATE"),
        ],
    )
    def test_leading_keyword(self, sql, kind):
        assert statement_kind(sql) == kind

    def test_no_input(self):
        assert statement_kind(None) is None
