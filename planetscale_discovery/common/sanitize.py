"""Replace literal values in SQL statement text with PostgreSQL placeholders.

``pg_stat_activity.query`` is never normalized by PostgreSQL, so it holds the
literal values of the running statement. ``pg_stat_statements`` is usually
normalized but not always: utility statements are stored verbatim. Both go
through here.
"""

import re
from typing import Any, List, Optional, Tuple

from sqlparse import lexer
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
        # Continue after the highest placeholder the server already assigned.
        next_number = _highest_placeholder(text) + 1
        pieces = []
        tokens = list(lexer.tokenize(text))
        for index, (ttype, value) in enumerate(tokens):
            if ttype is T.Error and value == "'":
                pieces.append(f"${next_number}")
                break
            if value == "/" and _opens_comment(tokens, index):
                break
            if ttype in T.Comment:
                pieces.append(" ")
            elif ttype in LITERAL_TOKENS:
                pieces.append(f"${next_number}")
                next_number += 1
            else:
                pieces.append(value)
    except Exception:
        # Fail closed: returning the input would defeat the point.
        return FALLBACK_PLACEHOLDER

    redacted = _PREFIXED_PLACEHOLDER.sub(r"\1", "".join(pieces))
    return " ".join(redacted.split()) or FALLBACK_PLACEHOLDER


def _opens_comment(tokens: List[Tuple[Any, str]], index: int) -> bool:
    following = tokens[index + 1][1] if index + 1 < len(tokens) else ""
    return following.startswith("*")


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
    if not sql or not str(sql).strip():
        return None
    try:
        return _leading_keyword(str(sql))
    except Exception:
        return "UNKNOWN"


def _leading_keyword(sql: str) -> str:
    tokens = (
        (ttype, value)
        for ttype, value in lexer.tokenize(sql)
        if ttype not in T.Whitespace and ttype not in T.Comment
    )
    ttype, value = next(tokens, (None, ""))
    if ttype in (T.Keyword.DML, T.Keyword.DDL):
        return value.upper()
    if ttype != T.Keyword.CTE:
        return "UNKNOWN"
    depth = 0
    for ttype, value in tokens:
        if ttype == T.Punctuation and value == "(":
            depth += 1
        elif ttype == T.Punctuation and value == ")":
            depth -= 1
        elif depth == 0 and ttype == T.Punctuation and value == ";":
            break
        elif depth == 0 and ttype == T.Keyword.DML:
            return value.upper()
    return "UNKNOWN"


def _highest_placeholder(text: str) -> int:
    numbers = [int(m) for m in _EXISTING_PLACEHOLDER.findall(text)]
    return max(numbers) if numbers else 0
