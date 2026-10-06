"""Join hashes for the distribution tier, byte-identical to the planner's
reference hasher (pinned by tests/unit/fixtures/distribution_vectors.json)."""

import hashlib
import math
import unicodedata


def canonical_numeric(rendering: str) -> str:
    """Plain decimal by exact string surgery (the planner's canonical rule):
    no float conversion, so any precision canonicalizes losslessly."""
    s = rendering.strip()
    neg = s.startswith("-")
    if s[:1] in ("+", "-"):
        s = s[1:]
    mantissa, marker, exp_text = s.lower().partition("e")
    try:
        exp = int(exp_text) if marker else 0
    except ValueError:
        raise ValueError("bad exponent") from None
    int_part, _, frac_part = mantissa.partition(".")
    digits = int_part + frac_part
    if not digits.isdigit():
        raise ValueError("a non-digit in a numeric value")
    point = len(int_part) + exp
    pad = max(0, -point)
    digits = "0" * pad + digits.ljust(point, "0")
    point += pad
    out = digits[:point].lstrip("0") or "0"
    if digits[point:].rstrip("0"):
        out += "." + digits[point:].rstrip("0")
    return "-" + out if neg and out != "0" else out  # -0 normalizes to 0


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_string(value: str) -> str:
    """sha256 of "s:" + NFC — the pipeline's canonical string hash."""
    return _sha256_hex("s:" + unicodedata.normalize("NFC", value))


def hash_integer(rendering: str) -> str:
    c = canonical_numeric(rendering)
    if "." in c:
        raise ValueError("an integer value has a fractional part")
    return _sha256_hex("n:" + c)


def hash_decimal(rendering: str) -> str:
    return _sha256_hex("n:" + canonical_numeric(rendering))


def hash_float(rendering: str) -> str:
    """Via repr (shortest round-trip) first; NaN and the infinities by name."""
    try:
        f = float(rendering)
    except ValueError:
        raise ValueError("a float value that does not parse") from None
    return _sha256_hex(
        "n:" + (canonical_numeric(repr(f)) if math.isfinite(f) else repr(f))
    )


# The dispatch families, spelled the way format_type() names them.
_INT_TYPES = {"smallint", "integer", "bigint", "oid"}
_DECIMAL_TYPES = {"numeric"}
_FLOAT_TYPES = {"real", "double precision"}
# First word only, so every time, timestamp and interval spelling refuses,
# arrays too: the reference cuts at a space, so a string hash would never join.
_REFUSED_TYPES = {"bytea", "bit", "date", "time", "timestamp", "interval"}


def base_type(type_name: str) -> str:
    """The type's base: lowercased, parameters stripped; arrays stay whole."""
    return type_name.strip().lower().split("(", 1)[0].strip()


def refusal_reason(type_name: str) -> str:
    """Why a column type is outside the tier, or "" when measurable."""
    if base_type(type_name).split(" ")[0] not in _REFUSED_TYPES:
        return ""
    return (
        f"a {type_name} column is outside the distribution tier: binary and "
        "date/time values have no canonical encoding here"
    )


def join_hash(type_name: str, value: str) -> str:
    """The canonical join hash, dispatched on the column's type."""
    base = base_type(type_name)
    if base in _INT_TYPES:
        return hash_integer(value)
    if base in _DECIMAL_TYPES:
        return hash_decimal(value)
    if base in _FLOAT_TYPES:
        return hash_float(value)
    return hash_string(value)
