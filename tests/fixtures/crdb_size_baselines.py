"""Lab size baselines for CockroachDB live checks. Not shipped in product code."""

SIZE_BASELINE_DATABASES_LAB = ("tpcc", "phantom_app", "movr")
SIZE_BASELINE_DATABASES_SECURE = ("tpcc", "phantom_app", "mr_app")
SIZE_BASELINE_DATABASES = SIZE_BASELINE_DATABASES_LAB
SIZE_ACCEPTANCE = {
    "lab": {
        "label": "insecure 26300 / compat lab",
        "databases": SIZE_BASELINE_DATABASES_LAB,
        "table_count": 24,
        "logical_bytes_gib": 4.68,
    },
    "secure": {
        "label": "secure TLS 3-region",
        "databases": SIZE_BASELINE_DATABASES_SECURE,
        "table_count": 34,
        "per_database_mib": {
            "tpcc": 816.8,
            "phantom_app": 215.3,
            "mr_app": 1.4,
        },
        "logical_bytes_gib": 1.03,
        "tolerance": 0.002,
    },
}
