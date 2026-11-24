"""requirements/check_setup_lock.py: the lock's pins and hashes per target.

CI runs the script against PyPI with uv. These tests check its decisions with
no network: which lock entry applies on a target, and whether a target's wheel
has a hash in the lock.
"""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_setup_lock", ROOT / "requirements" / "check_setup_lock.py"
)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

LOCK = (ROOT / "requirements" / "setup-lock.txt").read_text()
INTEL_MAC = "x86_64-apple-darwin"
ARM_MAC = "aarch64-apple-darwin"
LINUX = "x86_64-manylinux_2_28"


def _remark(lock, pin, marker):
    """The lock with one pin's marker replaced."""
    return re.sub(
        re.escape(pin) + r" ; [^\\]*\\", f"{pin} ; {marker} \\\\", lock, count=1
    )


def test_a_pin_whose_marker_is_false_on_the_target_is_not_pinned():
    """Catches the check accepting a version the lock pins only for another
    platform, which pip in --require-hashes mode refuses."""
    darwin = check.environment(INTEL_MAC, (3, 10))
    resolved = {"cryptography": "46.0.7"}
    assert check.unpinned(resolved, check.parse_lock(LOCK), darwin) == []
    remarked = _remark(LOCK, "cryptography==46.0.7", "sys_platform == 'win32'")
    assert check.unpinned(resolved, check.parse_lock(remarked), darwin) == [
        "cryptography==46.0.7"
    ]


def test_a_version_the_lock_pins_for_another_target_is_not_pinned():
    """Catches a pin matched by name and version alone, whatever its marker."""
    entries = check.parse_lock(LOCK)
    linux = check.environment(LINUX, (3, 10))
    assert check.unpinned({"cryptography": "50.0.2"}, entries, linux) == []
    assert check.unpinned({"cryptography": "46.0.7"}, entries, linux) == [
        "cryptography==46.0.7"
    ]


def test_apple_silicon_matches_the_lock_as_arm64():
    """Catches the Apple silicon target evaluating markers as aarch64, which
    the lock's arm64 markers never match."""
    entries = check.parse_lock(LOCK)
    arm_mac = check.applicable(entries, check.environment(ARM_MAC, (3, 10)))
    arm_linux = check.applicable(
        entries, check.environment("aarch64-manylinux_2_28", (3, 10))
    )
    assert arm_mac["snowflake-connector-python"][0] == "4.4.0"
    assert arm_linux["snowflake-connector-python"][0] == "4.8.0"


MAC_WHEEL = "a" * 64
LINUX_WHEEL = "b" * 64
SDIST = "c" * 64


def _published(name, version):
    return [
        {
            "filename": f"{name}-{version}-cp38-abi3-macosx_10_9_universal2.whl",
            "digests": {"sha256": MAC_WHEEL},
        },
        {
            "filename": f"{name}-{version}-cp38-abi3-manylinux_2_28_x86_64.whl",
            "digests": {"sha256": LINUX_WHEEL},
        },
        {"filename": f"{name}-{version}.tar.gz", "digests": {"sha256": SDIST}},
    ]


def _hash_problems(platform, macos, hashes):
    pins = {"cryptography": ("46.0.7", hashes)}
    accepted = check.platform_tags(platform, macos, (3, 10))
    return check.hash_problems(pins, {"cryptography": "46.0.7"}, accepted, _published)


def test_a_target_whose_wheel_hash_the_lock_lacks_fails():
    """Catches a lock that installs on Linux but whose macOS wheel hash is
    missing, which pip refuses only on a Mac."""
    assert _hash_problems(LINUX, None, {LINUX_WHEEL}) == []
    assert _hash_problems(INTEL_MAC, (13, 0), {LINUX_WHEEL}) == [
        "cryptography==46.0.7: no wheel for this target in the lock"
    ]
    assert _hash_problems(ARM_MAC, (14, 0), {LINUX_WHEEL, SDIST}) == [
        "cryptography==46.0.7: no wheel for this target in the lock"
    ]
    assert _hash_problems(INTEL_MAC, (13, 0), {MAC_WHEEL}) == []
    assert _hash_problems(LINUX, None, {MAC_WHEEL}) == [
        "cryptography==46.0.7: no wheel for this target in the lock"
    ]


def test_a_wheel_for_a_newer_glibc_or_macos_does_not_count():
    """Catches a target accepting wheels its oldest supported OS cannot
    install."""
    linux = check.platform_tags(LINUX, None, (3, 12))
    mac = check.platform_tags(INTEL_MAC, (13, 0), (3, 12))
    files = [
        ("x-1-cp312-cp312-manylinux_2_34_x86_64.whl", "1"),
        ("x-1-cp312-cp312-manylinux2014_x86_64.whl", "2"),
        ("x-1-cp312-cp312-macosx_14_0_x86_64.whl", "3"),
        ("x-1-cp312-cp312-macosx_10_13_x86_64.whl", "4"),
        ("x-1-cp312-cp312-macosx_11_0_arm64.whl", "5"),
    ]
    published = [{"filename": f, "digests": {"sha256": h}} for f, h in files]
    assert check.installable_hashes(published, linux) == {"2"}
    assert check.installable_hashes(published, mac) == {"4"}


def test_apple_silicon_installs_arm64_wheels():
    """Catches the Apple silicon target asking for aarch64 wheels, which no
    macOS wheel is tagged with."""
    arm_mac = check.platform_tags(ARM_MAC, (14, 0), (3, 12))
    files = [
        ("x-1-cp312-cp312-macosx_11_0_arm64.whl", "1"),
        ("x-1-cp312-cp312-macosx_15_0_arm64.whl", "2"),
        ("x-1-cp312-cp312-macosx_10_13_x86_64.whl", "3"),
        ("x-1-cp312-cp312-manylinux_2_28_aarch64.whl", "4"),
    ]
    published = [{"filename": f, "digests": {"sha256": h}} for f, h in files]
    assert check.installable_hashes(published, arm_mac) == {"1"}


def test_a_hash_pypi_does_not_publish_is_reported():
    """Catches a corrupted hash in the lock for a platform CI never installs
    on."""
    entries = [("cryptography", "46.0.7", None, {MAC_WHEEL, "d" * 64})]
    assert check.unpublished_hashes(entries, _published) == [
        "cryptography==46.0.7: " + "d" * 64
    ]
    entries = [("cryptography", "46.0.7", None, {MAC_WHEEL, LINUX_WHEEL})]
    assert check.unpublished_hashes(entries, _published) == []


# A two-entry lock: cryptography 50 everywhere but Intel Macs, 48 there.
ARM_MAC_WHEEL, LINUX_X86, LINUX_ARM, UNIVERSAL2 = "1" * 64, "2" * 64, "3" * 64, "4" * 64
TINY_LOCK = """cryptography==50.0.2 ; platform_machine != 'x86_64' or sys_platform != 'darwin' \\
{new}
cryptography==48.0.1 ; platform_machine == 'x86_64' and sys_platform == 'darwin' \\
    --hash=sha256:{old}
"""


def _tiny_published(name, version):
    wheels = {
        "50.0.2": [
            ("cp39-abi3-macosx_11_0_arm64", ARM_MAC_WHEEL),
            ("cp39-abi3-manylinux_2_28_x86_64", LINUX_X86),
            ("cp39-abi3-manylinux_2_28_aarch64", LINUX_ARM),
        ],
        "48.0.1": [("cp39-abi3-macosx_10_9_universal2", UNIVERSAL2)],
    }[version]
    return [
        {"filename": f"{name}-{version}-{tag}.whl", "digests": {"sha256": digest}}
        for tag, digest in wheels
    ]


def _tiny_resolve(packages, lock, platform, macos, python):
    intel_mac = platform == "x86_64-apple-darwin"
    return {"cryptography": "48.0.1" if intel_mac else "50.0.2"}


def _run_main(tmp_path, monkeypatch, new_hashes, resolve=_tiny_resolve):
    new = " \\\n".join(f"    --hash=sha256:{h}" for h in new_hashes)
    lock = tmp_path / "lock.txt"
    lock.write_text(TINY_LOCK.format(new=new, old=UNIVERSAL2))
    setup_in = tmp_path / "setup.in"
    setup_in.write_text("cryptography>=42\n")
    monkeypatch.setattr(check, "resolve", resolve)
    monkeypatch.setattr(check, "pypi_files", _tiny_published)
    return check.main(["--lock", str(lock), "--setup-in", str(setup_in)])


def test_the_check_exits_zero_only_when_every_target_installs(
    tmp_path, monkeypatch, capsys
):
    """Catches the check exiting 0 over a broken lock, which turns the CI job
    that guards the Mac and ARM targets green."""
    every = [ARM_MAC_WHEEL, LINUX_X86, LINUX_ARM]
    assert _run_main(tmp_path, monkeypatch, every) == 0
    assert "FAIL" not in capsys.readouterr().out

    assert _run_main(tmp_path, monkeypatch, [LINUX_X86, LINUX_ARM]) == 1
    out = capsys.readouterr().out
    assert "FAIL  aarch64-apple-darwin (macOS 11) Python 3.10" in out
    assert "cryptography==50.0.2: no wheel for this target in the lock" in out
    assert "FAIL  x86_64-manylinux_2_28" not in out


def test_a_hash_pypi_does_not_publish_fails_the_check(tmp_path, monkeypatch, capsys):
    """Catches an unpublished hash reported without failing the run."""
    every = [ARM_MAC_WHEEL, LINUX_X86, LINUX_ARM, "5" * 64]
    assert _run_main(tmp_path, monkeypatch, every) == 1
    assert "FAIL  not a hash PyPI publishes: cryptography==50.0.2" in (
        capsys.readouterr().out
    )


def test_a_target_uv_cannot_resolve_fails_the_check(tmp_path, monkeypatch, capsys):
    """Catches a resolution failure reported without failing the run."""

    def fails_on_intel_macs(packages, lock, platform, macos, python):
        if platform == "x86_64-apple-darwin":
            raise RuntimeError("No solution found")
        return _tiny_resolve(packages, lock, platform, macos, python)

    every = [ARM_MAC_WHEEL, LINUX_X86, LINUX_ARM]
    assert _run_main(tmp_path, monkeypatch, every, fails_on_intel_macs) == 1
    out = capsys.readouterr().out
    assert "FAIL  x86_64-apple-darwin (macOS 13) Python 3.10" in out
    assert "No solution found" in out


def test_cryptography_is_patched_wherever_a_wheel_exists():
    """Catches the lock pinning a cryptography below 50 on any target but Intel
    Macs, the one platform with no wheel of 49 or later."""
    entries = check.parse_lock(LOCK)
    old = []
    for platform, macos in check.TARGETS:
        for python in check.PYTHONS:
            env = check.environment(platform, python)
            version = check.applicable(entries, env)["cryptography"][0]
            if int(version.split(".")[0]) < 50:
                old.append((platform, python))
    assert old == [("x86_64-apple-darwin", python) for python in check.PYTHONS]


def test_apple_silicon_is_checked_at_its_oldest_macos():
    """Catches the Apple silicon target checked at a newer macOS than Apple
    silicon Macs run, which would accept a pin whose only wheel needs it."""
    floor = dict(check.TARGETS)[ARM_MAC]
    accepted = check.platform_tags(ARM_MAC, floor, (3, 10))
    files = [
        ("x-1-cp310-cp310-macosx_11_0_arm64.whl", "11"),
        ("x-1-cp310-cp310-macosx_12_0_arm64.whl", "12"),
        ("x-1-cp310-cp310-macosx_14_0_arm64.whl", "14"),
    ]
    published = [{"filename": f, "digests": {"sha256": h}} for f, h in files]
    assert check.installable_hashes(published, accepted) == {"11"}
