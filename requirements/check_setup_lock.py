"""Checks that requirements/setup-lock.txt installs on every platform and Python
that ./setup.sh supports, without needing those machines.

For each target, uv resolves every package in setup.in for that platform, with
the lock as constraints, wheels only. Then, for each package uv picks:

- the lock must pin that version under a marker that is true on the target,
  because pip in --require-hashes mode refuses a package the lock does not pin
  for the machine it runs on, and
- one of the lock's hashes for it must be the digest PyPI publishes for a wheel
  that the target can install, because pip refuses a file whose hash the lock
  does not list.

It also checks that every hash in the lock is one PyPI publishes for that
version, so a corrupted hash fails here and not on a customer's machine.

Run it after regenerating the lock. It needs uv on PATH and the packaging
library:
  uv run --no-project --with packaging==26.3 python requirements/check_setup_lock.py
"""

import argparse
import json
import os
import re
import subprocess  # nosec B404: runs uv with fixed arguments
import sys
import tempfile
import urllib.request
from pathlib import Path

from packaging import tags
from packaging.markers import Marker
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]

# Linux with glibc 2.28 or later, the oldest Intel and Apple silicon macOS
# versions supported, and the Pythons CI tests.
TARGETS = [
    ("x86_64-manylinux_2_28", None),
    ("aarch64-manylinux_2_28", None),
    ("x86_64-apple-darwin", (13, 0)),
    ("aarch64-apple-darwin", (11, 0)),
]
PYTHONS = [(3, 10), (3, 11), (3, 12), (3, 13), (3, 14)]


def parse_lock(text):
    """Each entry in a lock: (name, version, marker or None, set of hashes).

    A universal lock can pin one package more than once, each under an
    environment marker.
    """
    entries = []
    for block in re.split(r"\n(?=[A-Za-z0-9])", text):
        match = re.match(r"([A-Za-z0-9_.-]+)==([^\s;\\]+)\s*(?:;([^\\\n]*))?", block)
        if match:
            name, version, marker = match.groups()
            entries.append(
                (
                    canonicalize_name(name),
                    version,
                    Marker(marker.strip()) if marker and marker.strip() else None,
                    set(re.findall(r"--hash=sha256:([0-9a-f]{64})", block)),
                )
            )
    return entries


def environment(platform, python):
    """The marker environment pip evaluates on a target machine."""
    machine = platform.split("-")[0]
    darwin = "darwin" in platform
    if darwin and machine == "aarch64":
        machine = "arm64"  # what platform.machine() reports on Apple silicon
    return {
        "implementation_name": "cpython",
        "implementation_version": f"{python[0]}.{python[1]}.0",
        "os_name": "posix",
        "platform_machine": machine,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": "Darwin" if darwin else "Linux",
        "platform_version": "",
        "python_full_version": f"{python[0]}.{python[1]}.0",
        "python_version": f"{python[0]}.{python[1]}",
        "sys_platform": "darwin" if darwin else "linux",
    }


def applicable(entries, env):
    """The lock's pins whose marker is true in env: {name: (version, hashes)}."""
    pins = {}
    for name, version, marker, hashes in entries:
        if marker is None or marker.evaluate(env):
            pins[name] = (version, hashes)
    return pins


def unpinned(resolved, entries, env):
    """Each package uv resolved that the lock does not pin, at that version,
    under a marker true for the target."""
    pins = applicable(entries, env)
    return sorted(
        f"{name}=={version}"
        for name, version in resolved.items()
        if pins.get(name, (None,))[0] != version
    )


def platform_tags(platform, macos, python):
    """The wheel tags pip accepts on a target, as a set."""
    arch = platform.split("-")[0]
    if macos:
        platforms = list(
            tags.mac_platforms(macos, "arm64" if arch == "aarch64" else arch)
        )
    else:
        glibc_minor = int(platform.rsplit("_", 1)[1])
        platforms = []
        for minor in range(glibc_minor, 4, -1):
            platforms.append(f"manylinux_2_{minor}_{arch}")
            legacy = {17: "manylinux2014", 12: "manylinux2010", 5: "manylinux1"}
            if minor in legacy and (minor == 17 or arch == "x86_64"):
                platforms.append(f"{legacy[minor]}_{arch}")
        platforms.append(f"linux_{arch}")
    interpreter = f"cp{python[0]}{python[1]}"
    return set(tags.cpython_tags(python, platforms=platforms)) | set(
        tags.compatible_tags(python, interpreter, platforms)
    )


def installable_hashes(files, accepted):
    """The sha256 of each wheel among PyPI's files for a version whose tags the
    target accepts."""
    found = set()
    for item in files:
        if item["filename"].endswith(".whl"):
            wheel_tags = parse_wheel_filename(item["filename"])[3]
            if wheel_tags & accepted:
                found.add(item["digests"]["sha256"])
    return found


def hash_problems(pins, resolved, accepted, published):
    """For each package uv resolved, why pip would refuse its file on the
    target: no installable wheel has a hash the lock lists."""
    problems = []
    for name, version in sorted(resolved.items()):
        _, hashes = pins[name]
        if not installable_hashes(published(name, version), accepted) & hashes:
            problems.append(f"{name}=={version}: no wheel for this target in the lock")
    return problems


def unpublished_hashes(entries, published):
    """Each lock hash that PyPI does not publish for that version."""
    problems = []
    for name, version, _, hashes in entries:
        digests = {item["digests"]["sha256"] for item in published(name, version)}
        problems += [f"{name}=={version}: {h}" for h in sorted(hashes - digests)]
    return problems


_cache = {}


def pypi_files(name, version):
    """The files PyPI publishes for one version, from its JSON API."""
    key = (name, version)
    if key not in _cache:
        url = f"https://pypi.org/pypi/{name}/{version}/json"
        with urllib.request.urlopen(url, timeout=60) as response:  # nosec B310
            _cache[key] = json.load(response)["urls"]
    return _cache[key]


def resolve(packages, lock, platform, macos, python):
    """uv's resolution of a requirements file for one target, as
    {name: version}."""
    env = dict(os.environ)
    env["MACOSX_DEPLOYMENT_TARGET"] = "%d.%d" % (macos or (13, 0))
    out = subprocess.run(  # nosec B603 B607
        [
            "uv",
            "pip",
            "compile",
            str(packages),
            "-c",
            str(lock),
            "--python-platform",
            platform,
            "--python-version",
            "%d.%d" % python,
            "--only-binary",
            ":all:",
            "--no-header",
            "--no-annotate",
            "--quiet",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if out.returncode:
        raise RuntimeError(out.stdout + out.stderr)
    resolved = {}
    for line in out.stdout.splitlines():
        match = re.match(r"([A-Za-z0-9_.-]+)==(\S+)", line)
        if match:
            resolved[canonicalize_name(match.group(1))] = match.group(2)
    return resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", default=ROOT / "requirements" / "setup-lock.txt")
    parser.add_argument("--setup-in", default=ROOT / "requirements" / "setup.in")
    args = parser.parse_args(argv)

    entries = parse_lock(Path(args.lock).read_text())
    # Only the package lines: setup.in's own -c names requirements-lock.txt,
    # which setup.sh never reads.
    lines = Path(args.setup_in).read_text().splitlines()
    packages = tempfile.NamedTemporaryFile("w", suffix=".in", delete=False)
    with packages:
        packages.write("\n".join(x for x in lines if re.match(r"[A-Za-z]", x)))
    failures = 0
    for problem in unpublished_hashes(entries, pypi_files):
        print(f"FAIL  not a hash PyPI publishes: {problem}")
        failures += 1

    for platform, macos in TARGETS:
        for python in PYTHONS:
            label = (
                f"{platform}{f' (macOS {macos[0]})' if macos else ''} Python %d.%d"
                % python
            )
            try:
                resolved = resolve(packages.name, args.lock, platform, macos, python)
            except RuntimeError as error:
                print(f"FAIL  {label}")
                print(re.sub(r"(?m)^", "      ", str(error)))
                failures += 1
                continue
            env = environment(platform, python)
            problems = [
                f"{pin}: not pinned for this target"
                for pin in unpinned(resolved, entries, env)
            ]
            if not problems:
                accepted = platform_tags(platform, macos, python)
                problems = hash_problems(
                    applicable(entries, env), resolved, accepted, pypi_files
                )
            if problems:
                print(f"FAIL  {label}")
                for problem in problems:
                    print(f"      {problem}")
                failures += 1
            else:
                print(f"ok    {label}")

    os.unlink(packages.name)
    if failures:
        print(f"{failures} check(s) failed for {args.lock}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
