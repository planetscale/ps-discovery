"""
Mock PlanetScale API responses for testing.

Shapes follow https://planetscale.com/docs/openapi.yaml. List endpoints share
one envelope: type, current_page, per_page, next_page, total_count,
total_pages, data.

To refresh from a live API:
  curl -H "Authorization: $PLANETSCALE_SERVICE_TOKEN_ID:$PLANETSCALE_SERVICE_TOKEN" \
       https://api.planetscale.com/v1/organizations
"""


def _list_envelope(data, next_page=None, current_page=1):
    """Wrap items in the PlanetScale paginated list envelope."""
    return {
        "type": "list",
        "current_page": current_page,
        "per_page": 100,
        "next_page": next_page,
        "next_page_url": None,
        "prev_page": None,
        "prev_page_url": None,
        "total_count": len(data),
        "total_pages": 1 if next_page is None else 2,
        "data": data,
    }


PLANETSCALE_ORGANIZATIONS_RESPONSE = _list_envelope(
    [
        {
            "id": "org-id-1",
            "name": "test-org",
            "created_at": "2025-01-15T10:00:00.000Z",
        }
    ]
)

PLANETSCALE_ORGANIZATION_DETAIL_RESPONSE = {
    "id": "org-id-1",
    "name": "test-org",
    "plan": "scaler_pro",
    "database_count": 2,
    "single_tenancy": False,
    "created_at": "2025-01-15T10:00:00.000Z",
}

PLANETSCALE_CLUSTER_SIZE_SKUS_RESPONSE = _list_envelope(
    [
        {
            "name": "PS_10",
            "display_name": "PS-10",
            "cpu": "1/8",
            "ram": 1,
            "storage": 10,
            "provider": "aws",
            "metal": False,
        },
        {
            "name": "PS_80",
            "display_name": "PS-80",
            "cpu": "2",
            "ram": 8,
            "storage": 100,
            "provider": "aws",
            "metal": False,
        },
    ]
)

# One postgresql database and one mysql database. The mysql one must be skipped.
PLANETSCALE_DATABASES_MIXED_RESPONSE = _list_envelope(
    [
        {
            "id": "db-id-pg",
            "name": "orders-pg",
            "kind": "postgresql",
            "state": "ready",
            "ready": True,
            "branches_count": 2,
            "production_branches_count": 1,
            "development_branches_count": 1,
            "created_at": "2025-02-01T10:00:00.000Z",
            "region": {
                "id": "region-1",
                "slug": "us-east",
                "provider": "aws",
                "display_name": "US East",
            },
        },
        {
            "id": "db-id-mysql",
            "name": "legacy-mysql",
            "kind": "mysql",
            "state": "ready",
            "ready": True,
            "branches_count": 1,
            "production_branches_count": 1,
            "development_branches_count": 0,
            "created_at": "2024-06-01T10:00:00.000Z",
            "region": {
                "id": "region-1",
                "slug": "us-east",
                "provider": "aws",
                "display_name": "US East",
            },
        },
    ]
)

PLANETSCALE_BRANCHES_RESPONSE = _list_envelope(
    [
        {
            "id": "branch-id-main",
            "name": "main",
            "kind": "postgresql",
            "production": True,
            "state": "ready",
            "ready": True,
            "metal": False,
            "cluster_name": "PS_80",
            "cluster_iops": 3000,
            "minimum_storage_bytes": 107374182400,
            "maximum_storage_bytes": 4398046511104,
            "storage_autoscaling": True,
            "storage_shrinking": True,
            "storage_type": "gp3",
            "storage_iops": 3000,
            "storage_throughput_mibs": 125,
            "replicas": 2,
            "has_replicas": True,
            "has_read_only_replicas": False,
            "parent_branch": None,
            "created_at": "2025-02-01T10:00:00.000Z",
            "region": {"id": "region-1", "slug": "us-east", "provider": "aws"},
        },
        {
            "id": "branch-id-dev",
            "name": "dev",
            "kind": "postgresql",
            "production": False,
            "state": "ready",
            "ready": True,
            "metal": False,
            "cluster_name": "PS_10",
            "cluster_iops": 1000,
            "has_replicas": False,
            "has_read_only_replicas": False,
            "parent_branch": "main",
            "created_at": "2025-03-01T10:00:00.000Z",
            "region": {"id": "region-1", "slug": "us-east", "provider": "aws"},
        },
    ]
)

# /metrics/instant. Values arrive per pod, and only the primary volume counts.
PLANETSCALE_INSTANT_STORAGE_METRICS_RESPONSE = {
    "type": "InstantMetrics",
    "branch": {"id": "branch-id-main", "type": "Branch", "name": "main"},
    "metrics": [
        {
            "metric": "planetscale_volume_disk_usage_bytes",
            "label": "volume_disk_usage",
            "values": [
                {"pod": "pod-replica-1", "role": "replica", "value": 129273905152},
                {"pod": "pod-primary-0", "role": "primary", "value": 129285554176},
            ],
        },
        {
            "metric": "planetscale_volume_capacity_bytes",
            "label": "volume_usage",
            "values": [
                {"pod": "pod-replica-1", "role": "replica", "value": 406998380544},
                {"pod": "pod-primary-0", "role": "primary", "value": 406998380544},
            ],
        },
        {
            "metric": "planetscale_volume_usage_percentage",
            "label": "volume_usage",
            "values": [
                {"pod": "pod-replica-1", "role": "replica", "value": 31.76},
                {"pod": "pod-primary-0", "role": "primary", "value": 31.77},
            ],
        },
    ],
}

PLANETSCALE_DATABASES_TWO_POSTGRES_RESPONSE = _list_envelope(
    [
        {
            "id": "db-id-pg",
            "name": "orders-pg",
            "kind": "postgresql",
            "branches_count": 1,
            "region": {"slug": "us-east"},
        },
        {
            "id": "db-id-pg-2",
            "name": "billing-pg",
            "kind": "postgresql",
            "branches_count": 1,
            "region": {"slug": "us-east"},
        },
    ]
)

PLANETSCALE_DATABASES_PAGE_1 = _list_envelope(
    [
        {
            "id": "db-id-1",
            "name": "db-one",
            "kind": "postgresql",
            "region": {"slug": "us-east"},
        }
    ],
    next_page=2,
    current_page=1,
)

PLANETSCALE_DATABASES_PAGE_2 = _list_envelope(
    [
        {
            "id": "db-id-2",
            "name": "db-two",
            "kind": "postgresql",
            "region": {"slug": "us-west"},
        }
    ],
    next_page=None,
    current_page=2,
)
