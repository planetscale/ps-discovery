"""./setup.sh installs only exact, hash-checked packages from the shipped locks.

setup.sh used to install every package by lower bound, so each run took the
newest release on PyPI with no hash check. It now installs from
requirements/setup-lock.txt as constraints, with pip itself from
requirements/setup-pip.txt.

The tests run setup.sh itself, with pip and python3 replaced by stubs that
record their arguments, so what they check is what pip would be asked to do,
however the script spells the call.
"""

import re
import shutil
import subprocess  # nosec B404
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
SETUP_IN = (ROOT / "requirements" / "setup.in").read_text()
SETUP_LOCK = (ROOT / "requirements" / "setup-lock.txt").read_text()
PIP_LOCK = (ROOT / "requirements" / "setup-pip.txt").read_text()

PROVIDERS = [
    "aws",
    "gcp",
    "azure",
    "supabase",
    "heroku",
    "neon",
    "planetscale",
    "snowflake",
    "none",
]
# (engine, provider, workload) for each run: every provider, and MySQL.
RUNS = [("postgres", provider, "yes") for provider in PROVIDERS] + [
    ("mysql", "none", "no")
]

# Records one invocation per line, arguments separated by tabs. Each PIP_*
# variable in pip's environment, which pip reads as an option, comes first as
# env:NAME, so it fails the checks on the invocation's arguments.
RECORD = """{ for v in $(compgen -e | grep '^PIP_'); do printf 'env:%s\\t' "$v"; done
  printf '%s\\t' "$@"; echo; } >> "$STUB_PIP_LOG"
"""
PIP_STUB = "#!/bin/bash\n" + RECORD + "exit 0\n"
# Answers setup.sh's version probe with $STUB_PYTHON_OUTPUT, records a pip run
# through `python3 -m pip`, and succeeds at everything else (the venv's
# `--version` check, `-m planetscale_discovery --help`, config-template).
PYTHON_STUB = (
    """#!/bin/bash
if [ "$1" = "-c" ]; then
    printf '%s\\n' "$STUB_PYTHON_OUTPUT"
    exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
    shift 2
"""
    + RECORD
    + """fi
exit 0
"""
)


def _stub(path, text):
    path.write_text(text)
    path.chmod(0o755)


def run_setup(
    tmp,
    engine="postgres",
    provider="none",
    workload="no",
    python="3.12",
    missing=(),
):
    """Runs setup.sh in a release-shaped directory under tmp, without the lock
    files named in missing.

    Returns (completed process, list of pip invocations as argument lists).
    """
    release = tmp / "release"
    (release / "planetscale_discovery").mkdir(parents=True)
    (release / "planetscale_discovery" / "cli.py").write_text("")
    (release / "requirements").mkdir()
    for name in ("setup-lock.txt", "setup-pip.txt"):
        if name not in missing:
            shutil.copy(ROOT / "requirements" / name, release / "requirements" / name)
    shutil.copy(ROOT / "setup.sh", release / "setup.sh")

    # An existing venv, so setup.sh uses venv/bin/pip without creating one.
    venv_bin = release / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "activate").write_text("")
    # Every name a venv or PATH gives pip and Python, so a stray call through
    # any of them is recorded.
    path_bin = tmp / "bin"
    path_bin.mkdir()
    suffixes = ["", "3"] + [f"3.{minor}" for minor in range(10, 15)]
    for directory in (venv_bin, path_bin):
        for suffix in suffixes:
            _stub(directory / f"pip{suffix}", PIP_STUB)
            _stub(directory / f"python{suffix}", PYTHON_STUB)

    log = tmp / "pip.log"
    log.write_text("")
    env = {
        "PATH": f"{path_bin}:/usr/bin:/bin",
        "HOME": str(tmp),
        "STUB_PIP_LOG": str(log),
        "STUB_PYTHON_OUTPUT": python,
        "PSDISCOVERY_ENGINE": engine,
        "PSDISCOVERY_PROVIDER": provider,
        "PSDISCOVERY_WORKLOAD": workload,
    }
    result = subprocess.run(  # nosec B603 B607
        ["bash", "setup.sh"],
        cwd=release,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    invocations = [line.split("\t")[:-1] for line in log.read_text().splitlines()]
    return result, invocations


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Each run's pip invocations, keyed by (engine, provider, workload)."""
    found = {}
    for key in RUNS:
        result, invocations = run_setup(tmp_path_factory.mktemp("setup"), *key)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Setup complete" in result.stdout
        found[key] = [args for args in invocations if args != ["--version"]]
    return found


# Options that take a value, so the next argument is not a requirement.
VALUED = {"-c", "-r", "--only-binary"}
# Options that would install from somewhere other than PyPI's locked files, or
# without the lock's checks.
FORBIDDEN = re.compile(
    r"^(--no-binary|--index-url|--extra-index-url|--find-links|--trusted-host"
    r"|--editable|--no-index|-i|-f|-e)(=.*)?$"
)


def requirements_in(args):
    """The requirement arguments of one pip invocation."""
    found, skip = [], False
    for arg in args[1:]:
        if skip:
            skip = False
        elif arg in VALUED:
            skip = True
        elif not arg.startswith("-"):
            found.append(arg)
    return found


def _pairs(args):
    return list(zip(args, args[1:]))


def _requirements_setup_sh_installs(runs):
    return sorted(
        {r for calls in runs.values() for c in calls for r in requirements_in(c)}
    )


def _name(requirement):
    return Requirement(requirement).name.lower().replace("_", "-")


def _locked(lock):
    """Each entry in a lock: (name, version, marker text, hash count).

    A universal lock can pin one package more than once, each under an
    environment marker.
    """
    entries = []
    for block in re.split(r"\n(?=[A-Za-z0-9])", lock):
        match = re.match(r"([A-Za-z0-9_.-]+)==([^\s;\\]+)\s*(?:;([^\\\n]*))?", block)
        if match:
            name, version, marker = match.groups()
            entries.append(
                (
                    name.lower().replace("_", "-"),
                    version,
                    (marker or "").strip(),
                    len(re.findall(r"--hash=sha256:", block)),
                )
            )
    return entries


def test_every_pip_call_installs_from_a_lock_with_hashes_and_wheels(runs):
    """Catches any pip call, however it is spelled, that skips the hash check,
    builds from source, reads another index, or leaves the lock out."""
    wrong = []
    for key, calls in runs.items():
        for args in calls:
            pairs = _pairs(args)
            locks = [
                p
                for p in pairs
                if p
                in (
                    ("-c", "requirements/setup-lock.txt"),
                    ("-r", "requirements/setup-pip.txt"),
                )
            ]
            if (
                args[0] != "install"
                or "--require-hashes" not in args
                or ("--only-binary", ":all:") not in pairs
                or len(locks) != 1
                or [a for a in args if FORBIDDEN.match(a)]
                or ("-r" in args) == bool(requirements_in(args))
            ):
                wrong.append((key, args))
    assert wrong == []


def test_pip_is_upgraded_from_its_lock_before_anything_else(runs):
    """Catches the dependencies installing with the venv's own pip, which may be
    too old to check the constraint file's hashes."""
    for calls in runs.values():
        assert ("-r", "requirements/setup-pip.txt") in _pairs(calls[0])
        assert [c for c in calls[1:] if "-r" in c] == []


def test_each_selection_installs_its_packages(runs):
    """Catches the harness running setup.sh without reaching an install, which
    would make every other test here pass on no calls."""
    installed = {
        key: sorted(r for c in calls for r in requirements_in(c))
        for key, calls in runs.items()
    }
    assert "boto3>=1.40.49" in installed[("postgres", "aws", "yes")]
    assert "pglast>=7,<9" in installed[("postgres", "none", "yes")]
    assert installed[("mysql", "none", "no")] == [
        "PyMySQL>=1.2.0",
        "pyyaml>=6.0.3",
        "sqlparse>=0.5.0",
    ]


def test_every_package_setup_sh_installs_is_in_setup_in(runs):
    """Catches a package added to setup.sh without being added to the lock's
    source, which pip would then refuse."""
    listed = {line.strip() for line in SETUP_IN.splitlines()}
    requirements = _requirements_setup_sh_installs(runs)
    assert len(requirements) >= 20
    assert [r for r in requirements if r not in listed] == []


def test_every_package_in_setup_in_is_pinned_with_hashes():
    """Catches setup.in changing without the lock being regenerated."""
    locked = {name for name, _, _, hashes in _locked(SETUP_LOCK) if hashes}
    wanted = {
        _name(line) for line in SETUP_IN.splitlines() if re.match(r"[A-Za-z]", line)
    }
    assert sorted(wanted - locked) == []


def test_every_locked_version_meets_what_setup_sh_asks_for(runs):
    """Catches a raised lower bound in setup.sh without a regenerated lock,
    which pip would refuse on every install."""
    entries = _locked(SETUP_LOCK)
    wrong = []
    for text in _requirements_setup_sh_installs(runs):
        requirement = Requirement(text)
        name = _name(text)
        versions = [version for n, version, _, _ in entries if n == name]
        assert versions, name
        wrong += [
            f"{name}=={v} vs {requirement.specifier}"
            for v in versions
            if v not in requirement.specifier
        ]
    assert wrong == []


def test_every_locked_package_has_an_exact_version_and_a_hash():
    """Catches a lock entry without a hash, which --require-hashes rejects only
    when that package is selected."""
    for lock in (SETUP_LOCK, PIP_LOCK):
        entries = _locked(lock)
        assert entries
        assert [e[:3] for e in entries if e[3] == 0] == []
    assert [e[0] for e in _locked(PIP_LOCK)] == ["pip"]


@pytest.mark.parametrize(
    "output, message",
    [
        ("3.15", "newer than this release supports"),
        ("sitecustomize: loaded\n3.15", "newer than this release supports"),
        ("3.9", "Python 3.10 or higher is required"),
        ("sitecustomize: loaded", "Could not read the Python version"),
    ],
)
def test_an_unsupported_python_stops_before_any_install(tmp_path, output, message):
    """Catches a Python outside 3.10 to 3.14 getting past the version check,
    including when python3 prints something before its version."""
    result, invocations = run_setup(tmp_path, python=output)
    assert result.returncode == 1
    assert message in result.stdout
    assert invocations == []


def test_output_before_the_version_does_not_stop_a_supported_python(tmp_path):
    """Catches the version check reading the first line python3 prints."""
    result, invocations = run_setup(tmp_path, python="sitecustomize: loaded\n3.12")
    assert result.returncode == 0, result.stdout
    assert invocations


@pytest.mark.parametrize("missing", ["setup-lock.txt", "setup-pip.txt"])
def test_a_missing_lock_stops_setup_before_the_venv_is_touched(tmp_path, missing):
    """Catches the lock check running after the venv is checked or recreated,
    which ends a missing-lock run with advice to delete the venv."""
    result, invocations = run_setup(tmp_path, missing=(missing,))
    assert result.returncode == 1
    assert f"requirements/{missing} is missing" in result.stdout
    assert "rm -rf venv" not in result.stdout
    assert invocations == []


@pytest.mark.skipif(not shutil.which("zip"), reason="build_release.sh needs zip")
def test_the_release_zip_ships_both_locks(tmp_path):
    """Catches a release zip without the locks setup.sh reads."""
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".git", "venv", ".venv", "dist", "tests", "__pycache__", ".*_cache"
        ),
    )
    result = subprocess.run(  # nosec B603 B607
        ["bash", "build_release.sh", "9.9.9"],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(source / "dist" / "ps-discovery-9.9.9.zip") as release:
        for name in ("setup-lock.txt", "setup-pip.txt"):
            shipped = release.read(f"ps-discovery-9.9.9/requirements/{name}")
            assert shipped == (ROOT / "requirements" / name).read_bytes(), name
