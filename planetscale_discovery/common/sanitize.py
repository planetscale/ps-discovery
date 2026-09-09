"""
Replace literal values in SQL statement text.

``pg_stat_activity.query`` is never normalized by PostgreSQL: it holds the exact
text of the statement currently running, including its literal values. Emitting
it verbatim puts real customer data in a report, so every statement text passes
through here first.

``pg_stat_statements`` text is usually normalized by the server, but not
always -- utility statements are stored verbatim, so a ``CREATE ROLE ...
PASSWORD 'x'`` appears in plaintext -- so it goes through the same path.

The tokenizing is done by ``sqlparse``, which gets the cases that matter and are
easy to get wrong: a ``--`` or ``/*`` inside a string literal is not a comment,
and ``''`` is an escaped quote rather than the end of one.
"""

from typing import Optional

import sqlparse
from sqlparse import tokens as T

PLACEHOLDER = "?"


def _is_data(ttype) -> bool:
    # Double-quoted text is a PostgreSQL identifier, not data.
    return ttype is not None and ttype in T.Literal and ttype is not T.String.Symbol


def redact_sql(sql: Optional[str]) -> Optional[str]:
    """Return the statement's shape, with literal values replaced.

    Returns None for empty input, and the placeholder-only string for text that
    cannot be tokenized -- never the original, since the point is that the
    original may carry data.
    """
    if not sql or not str(sql).strip():
        return None

    text = str(sql)
    try:
        statements = sqlparse.parse(sqlparse.format(text, strip_comments=True))
        pieces = [
            PLACEHOLDER if _is_data(token.ttype) else token.value
            for statement in statements
            for token in statement.flatten()
        ]
    except Exception:
        # Fail closed. Returning the input on a parse failure would defeat the
        # whole purpose of this function.
        return PLACEHOLDER

    return " ".join("".join(pieces).split()) or PLACEHOLDER


def statement_kind(sql: Optional[str]) -> Optional[str]:
    """The leading keyword, e.g. ``SELECT`` or ``UPDATE``.

    Useful on its own: "a long-running UPDATE" is most of what a reader needs
    from a lock report, and it carries no data at all.
    """
    if not sql:
        return None
    kind = sqlparse.parse(str(sql))
    if not kind:
        return None
    return (kind[0].get_type() or "UNKNOWN").upper()
