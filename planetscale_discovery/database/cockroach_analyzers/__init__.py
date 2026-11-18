"""CockroachDB-native analyzers.

The PostgreSQL analyzers stay unchanged. When version() identifies
CockroachDB, discovery swaps these in so sizing and inventory use
crdb_internal sources instead of pg_relation_size / pg_database_size.
"""

from .config_analyzer import CockroachConfigAnalyzer
from .feature_analyzer import CockroachFeatureAnalyzer
from .performance_analyzer import CockroachPerformanceAnalyzer
from .schema_analyzer import CockroachSchemaAnalyzer

__all__ = [
    "CockroachConfigAnalyzer",
    "CockroachFeatureAnalyzer",
    "CockroachPerformanceAnalyzer",
    "CockroachSchemaAnalyzer",
]
