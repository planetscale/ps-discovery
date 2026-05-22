"""
Mock Neon API responses for testing.

These fixtures mirror the shapes Neon's Platform API actually returns today
(post-org-migration). Projects belong to organizations and identify their
owner via `owner_id` (a string), not a nested `owner` object. Plan tiers
live on the organization, not the project.

To refresh from a live API:
  curl -H "Authorization: Bearer $NEON_API_KEY" \\
       https://console.neon.tech/api/v2/users/me/organizations
  curl -H "Authorization: Bearer $NEON_API_KEY" \\
       "https://console.neon.tech/api/v2/projects?org_id=<org-id>&limit=2"
"""

NEON_USERS_ME_RESPONSE = {
    "id": "user-test-id-12345",
    "login": "testuser",
    "email": "testuser@example.com",
    "name": "Test",
    "last_name": "User",
    "plan": "free",
    "projects_limit": 0,
    "branches_limit": 0,
}

# /users/me/organizations — the source of truth for the plan tier
NEON_ORGS_RESPONSE = {
    "organizations": [
        {
            "id": "org-test-alpha",
            "name": "Alpha Co",
            "handle": "alpha-co-org",
            "plan": "launch",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "managed_by": "console",
            "require_mfa": False,
        },
        {
            "id": "org-test-beta",
            "name": "Beta Inc",
            "handle": "beta-inc-org",
            "plan": "free",
            "created_at": "2026-02-01T00:00:00Z",
            "updated_at": "2026-02-01T00:00:00Z",
            "managed_by": "console",
            "require_mfa": False,
        },
    ]
}

# /projects 400 when projects are org-scoped and no org_id is provided
NEON_PROJECTS_ORG_REQUIRED_400 = {
    "request_id": "test-request-id",
    "code": "",
    "message": (
        "org_id is required, you can find it on your " "organization settings page"
    ),
}

NEON_PROJECTS_RESPONSE = {
    "projects": [
        {
            "id": "project-abc-123",
            "name": "production-app",
            "region_id": "aws-us-east-2",
            "pg_version": 16,
            "created_at": "2024-06-01T10:00:00Z",
            "updated_at": "2024-12-15T14:30:00Z",
            "owner_id": "org-test-alpha",
            "org_id": "org-test-alpha",
        },
        {
            "id": "project-def-456",
            "name": "staging-app",
            "region_id": "aws-eu-central-1",
            "pg_version": 17,
            "created_at": "2025-01-10T08:00:00Z",
            "updated_at": "2025-03-20T12:00:00Z",
            "owner_id": "org-test-beta",
            "org_id": "org-test-beta",
        },
    ],
    "pagination": {},
}

NEON_SINGLE_PROJECT_RESPONSE = {
    "project": {
        "id": "project-abc-123",
        "name": "production-app",
        "region_id": "aws-us-east-2",
        "pg_version": 16,
        "created_at": "2024-06-01T10:00:00Z",
        "updated_at": "2024-12-15T14:30:00Z",
        "owner_id": "org-test-alpha",
        "org_id": "org-test-alpha",
    }
}

NEON_BRANCHES_RESPONSE = {
    "branches": [
        {
            "id": "br-main-abc123",
            "name": "main",
            "default": True,
            "protected": True,
            "current_state": "ready",
            "parent_id": None,
            "logical_size": 5368709120,
            "physical_size": 8589934592,
            "created_at": "2024-06-01T10:00:00Z",
            "updated_at": "2024-12-15T14:30:00Z",
        },
        {
            "id": "br-dev-def456",
            "name": "dev/feature-auth",
            "default": False,
            "protected": False,
            "current_state": "ready",
            "parent_id": "br-main-abc123",
            "logical_size": 5368709120,
            "physical_size": 1073741824,
            "created_at": "2025-02-01T09:00:00Z",
            "updated_at": "2025-03-10T11:00:00Z",
        },
    ]
}

NEON_ENDPOINTS_RESPONSE = {
    "endpoints": [
        {
            "id": "ep-rw-abc123",
            "type": "read_write",
            "current_state": "active",
            "branch_id": "br-main-abc123",
            "autoscaling_limit_min_cu": 0.5,
            "autoscaling_limit_max_cu": 4,
            "pooler_enabled": True,
            "pooler_mode": "transaction",
            "suspend_timeout_seconds": 300,
            "created_at": "2024-06-01T10:00:00Z",
        },
        {
            "id": "ep-ro-def456",
            "type": "read_only",
            "current_state": "idle",
            "branch_id": "br-main-abc123",
            "autoscaling_limit_min_cu": 0.25,
            "autoscaling_limit_max_cu": 2,
            "pooler_enabled": False,
            "pooler_mode": None,
            "suspend_timeout_seconds": 60,
            "created_at": "2024-09-15T16:00:00Z",
        },
    ]
}

NEON_DATABASES_RESPONSE = {
    "databases": [
        {
            "id": 12345,
            "name": "neondb",
            "owner_name": "neondb_owner",
            "created_at": "2024-06-01T10:00:00Z",
        },
        {
            "id": 12346,
            "name": "analytics",
            "owner_name": "analytics_user",
            "created_at": "2024-08-15T12:00:00Z",
        },
    ]
}

NEON_ROLES_RESPONSE = {
    "roles": [
        {
            "name": "neondb_owner",
            "protected": False,
            "created_at": "2024-06-01T10:00:00Z",
        },
        {
            "name": "analytics_user",
            "protected": False,
            "created_at": "2024-08-15T12:00:00Z",
        },
    ]
}

# Paginated response with cursor for testing pagination
NEON_PROJECTS_PAGE_1 = {
    "projects": [
        {
            "id": "project-page1-001",
            "name": "app-one",
            "region_id": "aws-us-east-2",
            "pg_version": 16,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-06-01T00:00:00Z",
            "owner_id": "org-test-alpha",
            "org_id": "org-test-alpha",
        },
    ],
    "pagination": {
        "cursor": "next-page-cursor-abc",
    },
}

NEON_PROJECTS_PAGE_2 = {
    "projects": [
        {
            "id": "project-page2-002",
            "name": "app-two",
            "region_id": "aws-eu-central-1",
            "pg_version": 17,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-03-01T00:00:00Z",
            "owner_id": "org-test-beta",
            "org_id": "org-test-beta",
        },
    ],
    "pagination": {},
}
