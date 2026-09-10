"""Numbered codes for every condition a capture reports.

Two blocks, and the split is what the operator does next:

``E1xx``  the capture cannot proceed. ``init`` stops with exit code 5.
``W2xx``  the capture proceeds, and the result carries a qualification.

There is no third case. A capture without ``pg_stat_statements`` has no query
workload, and a sharding scheme is planned from the workload, so a run that
cannot read it is a failure rather than a lesser success. Scan counters say a
table is read often; they never say which column a query filtered on.

Every code has a section in ``docs/workload_capture.md`` under the same number.
``tests/unit/test_workload_codes.py`` fails when a code is emitted with no
section, or a section documents a code the tool cannot emit.
"""

from typing import Dict

# The capture cannot proceed. Each is a reason init stops.
FATAL_CODES: Dict[str, str] = {
    # pg_stat_statements states, in the order the probe distinguishes them.
    "absent": "E101",
    "not_installed": "E102",
    "not_installed_not_preloaded": "E103",
    "not_preloaded": "E104",
    "unreadable": "E105",
    "tracking_disabled": "E108",
    "insufficient_statistics_privileges": "E106",
    "replica_target": "E107",
}

# The capture proceeds. Each qualifies the result.
WARNING_CODES: Dict[str, str] = {
    # Server settings, reported once by init.
    "nested_statements_not_tracked": "W201",
    "statement_eviction_possible": "W202",
    "io_timing_off": "W203",
    "transaction_boundaries_invisible": "W204",
    "statement_text_may_be_masked": "W213",
    # What one snapshot collected, reported by any collect.
    "relation_read_failed": "W205",
    "no_tables_in_scope": "W206",
    "statistics_not_readable": "W207",
    "pg_stat_statements_unavailable": "W208",
    "columns_unavailable": "W209",
    "statement_read_failed": "W210",
    "row_cap_reached": "W211",
    "statement_text_masked": "W212",
    "column_stats_read_failed": "W214",
}

ALL_CODES: Dict[str, str] = {**FATAL_CODES, **WARNING_CODES}


def code_for(name: str) -> str:
    """The number for a condition name, or an empty string when it has none."""
    return ALL_CODES.get(name, "")


def label(name: str) -> str:
    """How a condition is written in the log: ``W202 statement_eviction_possible``.

    An unnumbered name is still printed rather than swallowed: a missing number
    is a documentation bug, not a reason to hide the condition.
    """
    number = code_for(name)
    return f"{number} {name}" if number else name
