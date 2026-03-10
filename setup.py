#!/usr/bin/env python3
"""
PostgreSQL Environment Discovery Tool Setup

A comprehensive PostgreSQL environment discovery tool.
"""

from setuptools import setup, find_packages
import pathlib

# Read the README file
README = (pathlib.Path(__file__).parent / "README.md").read_text()

setup(
    name="planetscale-discovery-tools",
    version="1.1.0",
    description="Comprehensive database and cloud infrastructure discovery tools",
    long_description=README,
    long_description_content_type="text/markdown",
    author="PlanetScale",
    author_email="engineering@planetscale.com",
    url="https://planetscale.com",
    license="Apache-2.0",
    # Package discovery
    packages=find_packages(),
    package_dir={"planetscale_discovery": "planetscale_discovery"},
    # Dependencies
    install_requires=[
        "psycopg2-binary>=2.9.11",
        "pyyaml>=6.0.3",
    ],
    # Optional dependencies
    extras_require={
        "aws": [
            "boto3>=1.42.52",
            "botocore>=1.42.52",
        ],
        "gcp": [
            "google-cloud-resource-manager>=1.16.0",
            "google-cloud-compute>=1.43.0",
            "google-cloud-monitoring>=2.29.1",
            "google-cloud-alloydb>=0.7.0",
            "google-auth>=2.48.0",
            "google-api-python-client>=2.190.0",
        ],
        "supabase": [
            "requests>=2.32.5",
        ],
        "heroku": [
            "requests>=2.32.5",
        ],
        "all": [
            "boto3>=1.42.52",
            "botocore>=1.42.52",
            "google-cloud-resource-manager>=1.16.0",
            "google-cloud-compute>=1.43.0",
            "google-cloud-monitoring>=2.29.1",
            "google-cloud-alloydb>=0.7.0",
            "google-auth>=2.48.0",
            "google-api-python-client>=2.190.0",
            "requests>=2.32.5",
        ],
    },
    # Entry points for command-line scripts
    entry_points={
        "console_scripts": [
            "ps-discovery=planetscale_discovery.cli:main",
        ],
    },
    # Python version requirement
    python_requires=">=3.9",
    # Classification
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: System Administrators",
        "Topic :: Database",
        "Topic :: System :: Systems Administration",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
    # Additional metadata
    keywords="postgresql database discovery cloud aws gcp supabase heroku",
    project_urls={
        "Documentation": "https://docs.planetscale.com",
        "Source": "https://github.com/planetscale/pg-discovery-tool",
    },
)
