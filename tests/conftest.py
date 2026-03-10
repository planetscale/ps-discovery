"""
Shared pytest configuration and fixtures
"""

import pytest
import os
import tempfile
from unittest.mock import Mock, patch


@pytest.fixture(scope="session")
def aws_credentials():
    """Mock AWS Credentials for moto"""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def mock_postgres_connection():
    """Create a mock PostgreSQL connection"""
    with patch("psycopg2.connect") as mock_connect:
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Default responses for common queries
        mock_cursor.fetchone.return_value = {
            "version": "PostgreSQL 17.4 on x86_64-pc-linux-gnu"
        }
        mock_cursor.fetchall.return_value = []

        yield mock_conn, mock_cursor


@pytest.fixture
def mock_aws_session():
    """Create a mock AWS session with clients"""
    with patch("boto3.Session") as mock_session:
        mock_session_instance = Mock()
        mock_session.return_value = mock_session_instance

        # Create mock clients
        mock_rds_client = Mock()
        mock_ec2_client = Mock()
        mock_cloudwatch_client = Mock()

        def client_factory(service, **kwargs):
            return {
                "rds": mock_rds_client,
                "ec2": mock_ec2_client,
                "cloudwatch": mock_cloudwatch_client,
            }[service]

        mock_session_instance.client.side_effect = client_factory

        # Default empty responses
        mock_rds_client.describe_db_instances.return_value = {"DBInstances": []}
        mock_rds_client.describe_db_clusters.return_value = {"DBClusters": []}
        mock_ec2_client.describe_vpcs.return_value = {"Vpcs": []}
        mock_ec2_client.describe_security_groups.return_value = {"SecurityGroups": []}
        mock_ec2_client.describe_subnets.return_value = {"Subnets": []}
        mock_ec2_client.describe_route_tables.return_value = {"RouteTables": []}
        mock_ec2_client.describe_network_acls.return_value = {"NetworkAcls": []}

        yield {
            "session": mock_session_instance,
            "rds": mock_rds_client,
            "ec2": mock_ec2_client,
            "cloudwatch": mock_cloudwatch_client,
        }


@pytest.fixture
def temp_directory():
    """Create a temporary directory for test files"""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture
def sample_config():
    """Sample configuration for testing"""
    return {
        "database": {
            "host": "test-host.amazonaws.com",
            "port": 5432,
            "database": "testdb",
            "username": "postgres",
            "password": "testpass",
        },
        "aws": {
            "profile": "test-profile",
            "regions": ["us-east-1"],
            "target_database": None,
        },
    }


@pytest.fixture
def mock_logger():
    """Create a mock logger"""
    with patch("logging.getLogger") as mock_get_logger:
        mock_logger_instance = Mock()
        mock_get_logger.return_value = mock_logger_instance
        yield mock_logger_instance


# Pytest markers for test categorization
def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line("markers", "unit: marks tests as unit tests")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "aws: marks tests that require AWS services")
    config.addinivalue_line(
        "markers", "db: marks tests that require database connection"
    )
    config.addinivalue_line("markers", "slow: marks tests as slow running")


# Test collection and reporting
def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on test location"""
    for item in items:
        # Add unit marker to tests in unit directory
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)

        # Add integration marker to tests in integration directory
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)

        # Add slow marker to tests that use the slow marker
        if item.get_closest_marker("slow"):
            item.add_marker(pytest.mark.slow)


# Skip tests based on environment
def pytest_runtest_setup(item):
    """Skip tests based on environment or missing dependencies"""
    # Skip AWS tests if credentials not available (in CI without AWS setup)
    if item.get_closest_marker("aws"):
        try:
            import boto3

            # Try to create a session to test credentials
            session = boto3.Session()
            if not (os.environ.get("AWS_ACCESS_KEY_ID") or session.get_credentials()):
                pytest.skip("AWS credentials not available")
        except Exception:
            pytest.skip("AWS SDK not properly configured")

    # Skip database tests if psycopg2 not available
    if item.get_closest_marker("db"):
        try:
            pass
        except ImportError:
            pytest.skip("psycopg2 not available")
