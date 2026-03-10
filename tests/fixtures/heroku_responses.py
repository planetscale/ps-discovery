"""
Mock Heroku API responses for testing.
"""

HEROKU_ACCOUNT_RESPONSE = {
    "id": "01234567-89ab-cdef-0123-456789abcdef",
    "email": "test@example.com",
    "name": "Test User",
}

HEROKU_APPS_RESPONSE = [
    {
        "id": "app-uuid-1",
        "name": "my-production-app",
        "region": {"id": "region-uuid-1", "name": "us"},
        "created_at": "2024-01-15T10:00:00Z",
        "updated_at": "2024-06-01T12:00:00Z",
    },
    {
        "id": "app-uuid-2",
        "name": "my-staging-app",
        "region": {"id": "region-uuid-2", "name": "eu"},
        "created_at": "2024-03-01T08:00:00Z",
        "updated_at": "2024-06-01T12:00:00Z",
    },
    {
        "id": "app-uuid-3",
        "name": "my-worker-app",
        "region": {"id": "region-uuid-1", "name": "us"},
        "created_at": "2024-02-10T14:00:00Z",
        "updated_at": "2024-06-01T12:00:00Z",
    },
]

HEROKU_ADDONS_WITH_POSTGRES = [
    {
        "id": "addon-uuid-1",
        "name": "postgresql-rigid-12345",
        "addon_service": {"id": "service-uuid", "name": "heroku-postgresql"},
        "plan": {"id": "plan-uuid-1", "name": "heroku-postgresql:standard-0"},
        "state": "provisioned",
        "created_at": "2024-01-15T10:05:00Z",
        "config_vars": ["DATABASE_URL", "HEROKU_POSTGRESQL_COPPER_URL"],
    },
]

HEROKU_ADDONS_NO_POSTGRES = [
    {
        "id": "addon-uuid-2",
        "name": "redis-clean-67890",
        "addon_service": {"id": "service-uuid-2", "name": "heroku-redis"},
        "plan": {"id": "plan-uuid-2", "name": "heroku-redis:mini"},
        "state": "provisioned",
        "created_at": "2024-01-15T10:05:00Z",
        "config_vars": ["REDIS_URL"],
    },
]

HEROKU_CONFIG_VARS_WITH_POOLING = {
    "DATABASE_URL": "postgres://user:pass@host:5432/db",
    "HEROKU_POSTGRESQL_COPPER_URL": "postgres://user:pass@host:5432/db",
    "DATABASE_CONNECTION_POOL_URL": "postgres://user:pass@pooler:6432/db",
    "SECRET_KEY": "supersecret",
}

HEROKU_CONFIG_VARS_NO_POOLING = {
    "DATABASE_URL": "postgres://user:pass@host:5432/db",
    "HEROKU_POSTGRESQL_COPPER_URL": "postgres://user:pass@host:5432/db",
    "SECRET_KEY": "supersecret",
}

HEROKU_CONFIG_VARS_WITH_FOLLOWERS = {
    "DATABASE_URL": "postgres://user:pass@host:5432/db",
    "HEROKU_POSTGRESQL_COPPER_URL": "postgres://user:pass@host:5432/db",
    "HEROKU_POSTGRESQL_AMBER_URL": "postgres://user:pass@follower1:5432/db",
    "HEROKU_POSTGRESQL_GREEN_URL": "postgres://user:pass@follower2:5432/db",
}

HEROKU_ADDON_ATTACHMENTS = [
    {
        "id": "attachment-uuid-1",
        "name": "DATABASE",
        "addon": {"id": "addon-uuid-1", "name": "postgresql-rigid-12345"},
        "app": {"id": "app-uuid-1", "name": "my-production-app"},
    },
    {
        "id": "attachment-uuid-2",
        "name": "SHARED_DB",
        "addon": {"id": "addon-uuid-1", "name": "postgresql-rigid-12345"},
        "app": {"id": "app-uuid-3", "name": "my-worker-app"},
    },
]

HEROKU_RATE_LIMIT_HEADERS = {
    "RateLimit-Remaining": "0",
    "Retry-After": "30",
}

# Response from https://api.data.heroku.com/client/v11/databases/{addon_id}
HEROKU_DATA_API_DATABASE_DETAILS = {
    "addon_id": "addon-uuid-1",
    "name": "postgresql-rigid-12345",
    "heroku_resource_id": "addon-uuid-1",
    "created_at": "2024-01-15 10:05:00 +0000",
    "num_connections": 12,
    "num_connections_waiting": 0,
    "num_tables": 45,
    "current_transaction": 886,
    "num_bytes": 71015181459,
    "postgres_version": "17.5",
    "plan": "standard-0",
    "port": 5432,
    "database_name": "d1234abcdef",
    "database_user": "u_test_user",
    "available_for_ingress": True,
    "resource_url": "postgres://user:pass@host:5432/db",
    "database_password": "secret_password_redacted",
    "waiting?": False,
    "credentials": 1,
    "leader": None,
    "info": [
        {"name": "Plan", "values": ["Standard 0"]},
        {"name": "Status", "values": ["Available"]},
        {"name": "Data Size", "values": ["66.1 GB / 64 GB (103.28%)"]},
        {"name": "Tables", "values": [45]},
        {"name": "PG Version", "values": ["17.5"]},
        {"name": "Connections", "values": ["12/200"]},
        {"name": "Connection Pooling", "values": ["Available"]},
        {"name": "Credentials", "values": [1]},
        {"name": "Fork/Follow", "values": ["Available"]},
        {"name": "Rollback", "values": ["earliest from 2024-06-01 12:00 UTC"]},
        {"name": "Created", "values": ["2024-01-15 10:05 "]},
        {"name": "Region", "values": ["us"]},
        {"name": "Data Encryption", "values": ["In Use"]},
        {"name": "Continuous Protection", "values": ["On"]},
        {"name": "Maintenance", "values": ["not required"]},
        {
            "name": "Maintenance window",
            "values": ["Thursdays 21:30 to Fridays 01:30 UTC"],
        },
        {"name": "Followers", "values": [], "resolve_db_name": True},
        {"name": "Forks", "values": [], "resolve_db_name": True},
    ],
}
