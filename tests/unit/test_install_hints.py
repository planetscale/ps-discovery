"""No customer-facing text tells users to install this project from an index.

Neither `ps-discovery` nor `planetscale-discovery-tools` is published to a
package index, so `pip install "ps-discovery[aws]"` would install whatever
project owns that name there. Hints point at `./setup.sh` instead.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# What ships in the release zip, its INSTALL.txt and release notes (written
# by build_release.sh), or the public repository.
SOURCES = [
    *sorted((ROOT / "planetscale_discovery").rglob("*.py")),
    *sorted((ROOT / "docs").rglob("*.md")),
    *sorted(p for p in (ROOT / "config-examples").rglob("*") if p.is_file()),
    *[
        ROOT / name
        for name in (
            "README.md",
            "AGENTS.md",
            "CONTRIBUTING.md",
            "CHANGELOG.md",
            "RELEASING.md",
            "setup.sh",
            "ps-discovery",
            "build_release.sh",
            "setup.py",
            "pyproject.toml",
            "requirements.txt",
        )
    ],
]

# An index treats case, "-", "_" and "." alike, so match every spelling, but
# not the ./ps-discovery wrapper or a path ending in it.
PROJECT_NAME = re.compile(
    r"(?<![\w./-])(ps[-_.]discovery|planetscale[-_.]discovery[-_.]tools)(?![\w-])",
    re.IGNORECASE,
)
INSTALL = re.compile(r"\binstall\b", re.IGNORECASE)
EXTRAS = re.compile(r"\s*\[")


def install_hints(text):
    """Lines that name the project with extras, or on a line about installing.

    A shell line continuation is joined first, so a command split over two
    lines is read as one.
    """
    text = re.sub(r"\\\n\s*", " ", text)
    found = []
    for line in text.splitlines():
        for match in PROJECT_NAME.finditer(line):
            if EXTRAS.match(line, match.end()) or INSTALL.search(line):
                found.append(line.strip())
                break
    return found


def test_no_hint_installs_the_project_by_name_from_an_index():
    """Catches an install hint naming `ps-discovery` or
    `planetscale-discovery-tools`, which resolves on the public index."""
    found = [
        f"{path.relative_to(ROOT)}: {line}"
        for path in SOURCES
        for line in install_hints(path.read_text())
    ]
    assert found == []


def test_the_scan_sees_a_hint_written_the_old_way():
    """Catches the scan missing a spelling, a quoting style, a hint with no
    extras, an option or another package before the name, or a command split
    over two lines."""
    for text in (
        'pip install "ps-discovery[aws]"',
        "pip install 'planetscale-discovery-tools[mysql]'",
        "Install with `ps-discovery[snowflake]`.",
        "pip install ps-discovery",
        "pipx install --force planetscale-discovery-tools",
        "pip install -i https://example.invalid/simple ps-discovery",
        "pip install requests PlanetScale_Discovery.Tools",
        "pip install \\\n    planetscale_discovery_tools",
        "install the ps-discovery package from PyPI",
    ):
        assert install_hints(text), text


def test_the_scan_leaves_the_supported_paths_alone():
    """Catches the scan flagging the wrapper or a local install, which would
    fail the suite on correct text."""
    for text in (
        'pip install -e ".[aws]"',
        "./ps-discovery --config x.yaml",
        "Run ./setup.sh again and select AWS.",
        "pipx install -e .",
    ):
        assert install_hints(text) == [], text
