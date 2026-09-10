"""Replace literal values in SQL statement text with PostgreSQL placeholders.

``pg_stat_activity.query`` is never normalized by PostgreSQL, so it holds the
literal values of the running statement. ``pg_stat_statements`` is usually
normalized but not always: utility statements are stored verbatim. Both go
through here.
"""

import re
from typing import Optional

import sqlparse
from sqlparse import tokens as T

# $N, not ?, because ? is not a PostgreSQL placeholder and does not parse.
FALLBACK_PLACEHOLDER = "$1"

_EXISTING_PLACEHOLDER = re.compile(r"\$(\d+)")

_DOLLAR_QUOTE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")

_PREFIXED_PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9_$])(?:[EeBbXxNn]|[Uu]&)(\$\d+)")

# Token types whose value is data. A credential is a string literal like any other.
LITERAL_TOKENS = frozenset(
    {
        T.Literal.String.Single,
        T.Number.Integer,
        T.Number.Float,
        T.Number.Hexadecimal,
    }
)

# String.Symbol is a quoted *identifier*; redacting it breaks "public"."orders".
IDENTIFIER_TOKENS = frozenset({T.Literal.String.Symbol})


def redact_sql(sql: Optional[str]) -> Optional[str]:
    """Return the statement's shape, literals replaced by $N placeholders."""
    if not sql or not str(sql).strip():
        return None

    text = _empty_dollar_quotes(str(sql))
    try:
        statements = sqlparse.parse(sqlparse.format(text, strip_comments=True))
        # Continue after the highest placeholder the server already assigned.
        next_number = _highest_placeholder(text) + 1
        pieces = []
        for statement in statements:
            for token in statement.flatten():
                if token.ttype in LITERAL_TOKENS:
                    pieces.append(f"${next_number}")
                    next_number += 1
                else:
                    pieces.append(token.value)
    except Exception:
        # Fail closed: returning the input would defeat the point.
        return FALLBACK_PLACEHOLDER

    redacted = _PREFIXED_PLACEHOLDER.sub(r"\1", "".join(pieces))
    return " ".join(redacted.split()) or FALLBACK_PLACEHOLDER


def _empty_dollar_quotes(text: str) -> str:
    """Keep every dollar-quote delimiter, drop what is between them."""
    out = []
    position = 0
    while True:
        opening = _DOLLAR_QUOTE.search(text, position)
        if not opening:
            out.append(text[position:])
            return "".join(out)
        delimiter = opening.group(0)
        body_starts = opening.end()
        closing = text.find(delimiter, body_starts)
        out.append(text[position:body_starts])
        if closing < 0:
            return "".join(out)
        out.append(delimiter)
        position = closing + len(delimiter)


def statement_kind(sql: Optional[str]) -> Optional[str]:
    """The leading keyword, e.g. SELECT or UPDATE."""
    if not sql:
        return None
    parsed = sqlparse.parse(str(sql))
    if not parsed:
        return None
    return (parsed[0].get_type() or "UNKNOWN").upper()


def _highest_placeholder(text: str) -> int:
    numbers = [int(m) for m in _EXISTING_PLACEHOLDER.findall(text)]
    return max(numbers) if numbers else 0
