"""Every warning the tool emits must be numbered and documented.

A warning that names a symptom with no remedy leaves the operator stuck. These
tests read the source for the codes it can emit and the documentation for the
codes it explains, and fail when the two disagree in either direction.
"""

import re
from pathlib import Path

import pytest

from planetscale_discovery.workload.codes import (
    ALL_CODES,
    FATAL_CODES,
    WARNING_CODES,
    code_for,
    label,
)

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "workload_capture.md"
WORKLOAD = ROOT / "planetscale_discovery" / "workload"

# Codes the source emits, found the same way a reader would.
EMITTED = set()
for source in (WORKLOAD / "collect.py", WORKLOAD / "probe.py"):
    EMITTED |= set(re.findall(r'"code": "([a-z_]+)"', source.read_text()))

# The pg_stat_statements states, which init prints as a warning of their own.
STATES = set(
    re.findall(r'^    "([a-z_]+)": \(', (WORKLOAD / "probe.py").read_text(), re.M)
)

DOCUMENTED = dict(
    (name, number)
    for number, name in re.findall(
        r"^### ([EW]\d{3}) ([a-z_]+)$", DOC.read_text(), re.M
    )
)


class TestEveryCodeIsNumbered:
    @pytest.mark.parametrize("name", sorted(EMITTED))
    def test_an_emitted_code_has_a_number(self, name):
        assert code_for(name), f'"code": "{name}" is emitted with no number'

    @pytest.mark.parametrize("name", sorted(STATES))
    def test_a_capability_state_has_a_number(self, name):
        assert code_for(name), f"pg_stat_statements state {name} has no number"

    @pytest.mark.parametrize("name", sorted(STATES))
    def test_a_capability_state_stops_the_capture(self, name):
        """The extension is required, so every state that is not ok is fatal."""
        assert name in FATAL_CODES

    def test_the_numbers_are_unique(self):
        numbers = list(ALL_CODES.values())
        assert len(numbers) == len(set(numbers))

    def test_a_condition_that_stops_a_capture_is_e1xx(self):
        assert all(n.startswith("E1") for n in FATAL_CODES.values())

    def test_a_condition_that_does_not_stop_a_capture_is_w2xx(self):
        assert all(n.startswith("W2") for n in WARNING_CODES.values())

    def test_no_name_is_both_fatal_and_a_warning(self):
        """One name, one severity, or the reader cannot act on the code."""
        assert not (set(FATAL_CODES) & set(WARNING_CODES))

    def test_the_label_carries_both(self):
        assert label("io_timing_off") == "W203 io_timing_off"

    def test_an_unknown_name_is_still_printed(self):
        """A missing number is a docs bug, not a reason to hide the condition."""
        assert label("something_new") == "something_new"


class TestEveryCodeIsDocumented:
    @pytest.mark.parametrize("name", sorted(ALL_CODES))
    def test_it_has_a_section(self, name):
        assert (
            name in DOCUMENTED
        ), f"{code_for(name)} {name} has no section in {DOC.name}"

    @pytest.mark.parametrize("name", sorted(ALL_CODES))
    def test_the_section_number_matches(self, name):
        assert DOCUMENTED[name] == ALL_CODES[name]

    @pytest.mark.parametrize("name", sorted(ALL_CODES))
    def test_the_section_says_what_to_do(self, name):
        """A section that only restates the warning is not a resolution."""
        body = _section(name)
        assert len(body.split()) >= 25, f"{name}: section is too short to resolve it"

    def test_no_section_documents_a_code_that_cannot_be_emitted(self):
        assert set(DOCUMENTED) <= set(ALL_CODES)

    def test_the_doc_explains_the_two_blocks(self):
        text = DOC.read_text()
        assert "`E1xx`" in text and "`W2xx`" in text


def _section(name):
    text = DOC.read_text()
    start = text.index(f"### {ALL_CODES[name]} {name}")
    rest = text[start:]
    end = rest.find("\n### ", 1)
    stop = rest.find("\n## ", 1)
    if 0 <= stop < (end if end >= 0 else len(rest)):
        end = stop
    return rest[:end] if end >= 0 else rest
