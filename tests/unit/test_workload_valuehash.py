"""The distribution tier's value hashing, pinned against golden
vectors generated from the sharding planner's reference
implementation — the cross-language drift tripwire. If this file's
outputs move, the planner's reader and this writer disagree, and the
failure must be loud here first."""

import hashlib
import json
from pathlib import Path

import pytest

from planetscale_discovery.workload.valuehash import (
    canonical_numeric,
    hash_integer,
    hash_string,
    join_hash,
    refusal_reason,
)

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "distribution_vectors.json").read_text()
)

# Each kind as format_type() spells a column of it.
KIND_TO_TYPE = {
    "string": "text",
    "integer": "bigint",
    "decimal": "numeric(10,4)",
    "float": "double precision",
}


@pytest.mark.parametrize(
    "row", VECTORS, ids=[f"{r['kind']}:{r['input']!r}" for r in VECTORS]
)
def test_golden_vectors_reproduce(row):
    if row["kind"] == "timestamp":
        # The tier refuses timestamp columns; a timestamp read as measured is the defect.
        assert refusal_reason("timestamp(3) with time zone") != ""
        return
    assert join_hash(KIND_TO_TYPE[row["kind"]], row["input"]) == row["join_hash"]


def test_canonical_numeric_is_exact_string_surgery():
    # Leading zeros, trailing fractional zeros, exponent shift, -0: never a
    # float round-trip.
    assert canonical_numeric("0123") == "123"
    assert canonical_numeric("1.2300") == "1.23"
    assert canonical_numeric("1.23e2") == "123"
    assert canonical_numeric("0.0001") == "0.0001"
    assert canonical_numeric("1e-3") == "0.001"
    assert canonical_numeric("-0.00") == "0"
    assert canonical_numeric("+42") == "42"
    assert canonical_numeric("-.5") == "-0.5"
    for bad in ("", "12a3", "1e", "."):
        with pytest.raises(ValueError):
            canonical_numeric(bad)
    with pytest.raises(ValueError):
        hash_integer("1.5")


def test_float_specials_hash_by_name():
    # PostgreSQL's spellings; sha256("n:nan") and sha256("n:-inf").
    for rendering, name in (("NaN", b"n:nan"), ("-Infinity", b"n:-inf")):
        expected = hashlib.sha256(name).hexdigest()
        assert join_hash("double precision", rendering) == expected


def test_type_dispatch():
    for refused in (
        "bytea",
        "date",
        "time without time zone",
        "interval day",
        "timestamp with time zone[]",
    ):
        assert "outside the distribution tier" in refusal_reason(refused)
    assert refusal_reason("character varying(36)") == ""
    # Type names cut only at "(": an array column takes the string arm.
    assert join_hash("integer[]", "{1,2,3}") == hash_string("{1,2,3}")
