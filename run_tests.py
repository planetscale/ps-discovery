#!/usr/bin/env python3
"""
Test runner script for PlanetScale Discovery Tools
Provides a simple interface to run different test suites
"""

import sys
import subprocess
import argparse


def run_command(cmd, description):
    """Run a command and handle the result"""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print("=" * 60)

    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        print(f"\n❌ FAILED: {description}")
        return False
    else:
        print(f"\n✅ PASSED: {description}")
        return True


def main():
    parser = argparse.ArgumentParser(
        description="PlanetScale Discovery Tools Test Runner"
    )
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument(
        "--integration", action="store_true", help="Run integration tests only"
    )
    parser.add_argument(
        "--coverage", action="store_true", help="Run tests with coverage"
    )
    parser.add_argument(
        "--fast", action="store_true", help="Run fast tests only (skip slow tests)"
    )
    parser.add_argument("--lint", action="store_true", help="Run linting checks")
    parser.add_argument(
        "--format", action="store_true", help="Run code formatting checks"
    )
    parser.add_argument("--type-check", action="store_true", help="Run type checking")
    parser.add_argument("--all", action="store_true", help="Run all tests and checks")
    parser.add_argument(
        "--install-deps", action="store_true", help="Install test dependencies first"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # If no specific test type is selected, run all by default
    if not any(
        [
            args.unit,
            args.integration,
            args.coverage,
            args.fast,
            args.lint,
            args.format,
            args.type_check,
        ]
    ):
        args.all = True

    success = True
    results = []

    # Install dependencies if requested
    if args.install_deps:
        cmd = ["pip", "install", "-e", ".[test]"]
        if not run_command(cmd, "Installing test dependencies"):
            return 1

    # Determine pytest args
    pytest_args = ["pytest"]
    if args.verbose:
        pytest_args.append("-v")

    # Run unit tests
    if args.unit or args.all:
        cmd = pytest_args + ["-m", "unit", "tests/unit/"]
        if args.coverage:
            cmd.extend(["--cov=planetscale_discovery", "--cov-report=term-missing"])
        result = run_command(cmd, "Unit tests")
        results.append(("Unit tests", result))
        success = success and result

    # Run integration tests
    if args.integration or args.all:
        cmd = pytest_args + ["-m", "integration", "tests/integration/"]
        if args.fast:
            cmd.extend(["-m", "not slow"])
        result = run_command(cmd, "Integration tests")
        results.append(("Integration tests", result))
        success = success and result

    # Run all tests with coverage
    if args.coverage:
        cmd = pytest_args + [
            "--cov=planetscale_discovery",
            "--cov-report=html",
            "--cov-report=term-missing",
            "--cov-report=xml",
        ]
        if args.fast:
            cmd.extend(["-m", "not slow"])
        result = run_command(cmd, "Coverage tests")
        results.append(("Coverage tests", result))
        success = success and result

    # Run linting
    if args.lint or args.all:
        try:
            result = run_command(
                ["flake8", "planetscale_discovery", "tests"], "Code linting"
            )
            results.append(("Linting", result))
            success = success and result
        except FileNotFoundError:
            print("❌ flake8 not found. Install with: pip install flake8")
            results.append(("Linting", False))
            success = False

    # Run formatting check
    if args.format or args.all:
        try:
            result = run_command(
                ["black", "--check", "planetscale_discovery", "tests"],
                "Code formatting check",
            )
            results.append(("Format check", result))
            success = success and result
        except FileNotFoundError:
            print("❌ black not found. Install with: pip install black")
            results.append(("Format check", False))
            success = False

    # Run type checking
    if args.type_check or args.all:
        try:
            result = run_command(["mypy", "planetscale_discovery"], "Type checking")
            results.append(("Type checking", result))
            success = success and result
        except FileNotFoundError:
            print("❌ mypy not found. Install with: pip install mypy")
            results.append(("Type checking", False))
            success = False

    # Print summary
    print(f"\n{'='*60}")
    print("TEST SUMMARY")
    print("=" * 60)

    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{test_name:<20} {status}")

    if success:
        print("\n🎉 All tests and checks passed!")
        return 0
    else:
        print("\n💥 Some tests or checks failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
