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

_IDENTIFIER_CHAR = re.compile(r"[A-Za-z0-9_$]")

_ESCAPE_STRING = re.compile(r"(?<![A-Za-z0-9_$])[Ee]$")

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
        previous_number = False
        for index, (ttype, value) in enumerate(tokens):
            if ttype is T.Error and value == "'":
                pieces.append(f"${next_number}")
                break
            if value == "/" and _opens_comment(tokens, index):
                break
            number = _is_number(ttype, value)
            if number and previous_number:
                continue
            previous_number = number
            if ttype in T.Comment:
                pieces.append(" ")
            elif ttype in LITERAL_TOKENS or number:
                pieces.append(f"${next_number}")
                next_number += 1
            else:
                pieces.append(value)
    except Exception:
        # Fail closed: returning the input would defeat the point.
        return FALLBACK_PLACEHOLDER

    redacted = _PREFIXED_PLACEHOLDER.sub(r"\1", "".join(pieces))
    return " ".join(redacted.split()) or FALLBACK_PLACEHOLDER


def _is_number(ttype: Any, value: str) -> bool:
    if ttype in (T.Number.Integer, T.Number.Float, T.Number.Hexadecimal):
        return True
    return ttype in T.Name and value[:1].isdigit()


def _opens_comment(tokens: List[Tuple[Any, str]], index: int) -> bool:
    following = tokens[index + 1][1] if index + 1 < len(tokens) else ""
    return following.startswith("*")


def _empty_dollar_quotes(text: str) -> str:
    """Keep every dollar-quote delimiter, drop what is between them."""
    out = []
    position = 0
    while position < len(text):
        skipped = _skip_quoted_or_comment(text, position)
        if skipped > position:
            out.append(text[position:skipped])
            position = skipped
            continue
        opening = _DOLLAR_QUOTE.match(text, position)
        previous = text[position - 1] if position else ""
        if not opening or _IDENTIFIER_CHAR.match(previous):
            out.append(text[position])
            position += 1
            continue
        delimiter = opening.group(0)
        closing = text.find(delimiter, opening.end())
        out.append(delimiter)
        if closing < 0:
            return "".join(out)
        out.append(delimiter)
        position = closing + len(delimiter)
    return "".join(out)


def _skip_quoted_or_comment(text: str, position: int) -> int:
    if text.startswith("--", position):
        end = text.find("\n", position)
        return len(text) if end < 0 else end
    if text.startswith("/*", position):
        end = text.find("*/", position + 2)
        return len(text) if end < 0 else end + 2
    quote = text[position]
    if quote not in ("'", '"'):
        return position
    backslash = quote == "'" and _ESCAPE_STRING.search(text[:position]) is not None
    index = position + 1
    while index < len(text):
        if backslash and text[index] == "\\":
            index += 2
        elif text[index] == quote:
            if not text.startswith(quote, index + 1):
                return index + 1
            index += 2
        else:
            index += 1
    return len(text)


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
