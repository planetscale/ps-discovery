"""
Tests for SQL literal redaction.

The tokenizing is sqlparse's job, so these do not re-test it. They cover the
cases that motivated the change -- a comment marker or an escaped quote inside a
string literal -- and the promise the function makes: no literal value survives,
and a parse failure fails closed rather than returning the input.
"""

import pytest

from planetscale_discovery.common.sanitize import (
    PLACEHOLDER,
    redact_sql,
    statement_kind,
)


class TestRedactSql:
    @pytest.mark.parametrize(
        "sql,expected",
        [
            (
                "UPDATE t SET x=1 WHERE email = 'alice@example.com'",
                "UPDATE t SET x=? WHERE email = ?",
            ),
            # A -- inside a literal is not a comment. Treating it as one used to
            # truncate the statement and leave the literal's prefix behind.
            (
                "UPDATE t SET x=1 WHERE email = 'alice--bob@example.com'",
                "UPDATE t SET x=? WHERE email = ?",
            ),
            (
                "SELECT * FROM t WHERE note = 'has -- dashes' AND id = 5",
                "SELECT * FROM t WHERE note = ? AND id = ?",
            ),
            (
                "SELECT 1 WHERE s = 'a/*b' AND t = 'c*/d'",
                "SELECT ? WHERE s = ? AND t = ?",
            ),
            # '' is an escaped quote, not the end of the literal.
            ("SELECT * FROM t WHERE s = 'it''s ok'", "SELECT * FROM t WHERE s = ?"),
            ("-- note\nSELECT 1", "SELECT ?"),
            ("SELECT /* hi */ 1 FROM t", "SELECT ? FROM t"),
            # A credential is a string literal like any other.
            ("CREATE ROLE app PASSWORD 'hunter2'", "CREATE ROLE app PASSWORD ?"),
            # sqlparse tags a dollar-quoted body as a bare Token.Literal.
            ("CREATE ROLE app PASSWORD $$hunter2$$", "CREATE ROLE app PASSWORD ?"),
            ("DO $$ BEGIN UPDATE t SET a='x'; END $$", "DO ?"),
        ],
    )
    def test_literals_are_replaced(self, sql, expected):
        assert redact_sql(sql) == expected

    def test_server_placeholders_are_left_alone(self):
        """Already-normalized pg_stat_statements text must survive."""
        assert redact_sql("SELECT * FROM orders WHERE tenant_id = $1") == (
            "SELECT * FROM orders WHERE tenant_id = $1"
        )

    def test_identifiers_and_structure_survive(self):
        out = redact_sql(
            "SELECT id, name FROM public.orders JOIN t ON t.id = orders.id"
        )
        assert "public.orders" in out
        assert "JOIN" in out

    def test_quoted_identifiers_survive(self):
        out = redact_sql('SELECT "users"."id" FROM "users" WHERE "users"."id" = 42')
        assert out == 'SELECT "users"."id" FROM "users" WHERE "users"."id" = ?'

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
            'SELECT * FROM "t" WHERE "a" = \'{v}\'',
        ):
            assert secret not in (redact_sql(template.format(v=secret)) or "")

    def test_unparseable_input_fails_closed(self, monkeypatch):
        """Returning the input on failure would defeat the point."""
        import planetscale_discovery.common.sanitize as module

        def boom(*args, **kwargs):
            raise RuntimeError("tokenizer exploded")

        monkeypatch.setattr(module.sqlparse, "format", boom)
        assert redact_sql("SELECT 'secret'") == PLACEHOLDER


class TestStatementKind:
    @pytest.mark.parametrize(
        "sql,kind",
        [
            ("SELECT 1", "SELECT"),
            ("UPDATE t SET a = 1", "UPDATE"),
            ("INSERT INTO t VALUES (1)", "INSERT"),
            ("DELETE FROM t", "DELETE"),
        ],
    )
    def test_leading_keyword(self, sql, kind):
        assert statement_kind(sql) == kind

    def test_no_input(self):
        assert statement_kind(None) is None
