"""
CloudWatch metric tables and reductions for the AWS analyzer.

Aurora Serverless v2 reports ``db.serverless`` as its instance class, so the
describe APIs carry no vCPU, memory or storage figure. The capacity actually
allocated is only in CloudWatch.
"""

from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple
from datetime import datetime

RDS_NAMESPACE = "AWS/RDS"

METRIC_WINDOW_DAYS = 7
METRIC_PERIOD_SECONDS = 3600
GET_METRIC_DATA_MAX_QUERIES = 500

SERVERLESS_INSTANCE_CLASS = "db.serverless"
AURORA_ACU_MEMORY_GIB = 2.0
AURORA_ACU_VCPU = 0.25

BYTES_PER_GIB = 1024**3

P95_STAT = "p95"
P95_BASIS_STAT = "Average"
P95_BASIS = "nearest-rank p95 of per-period Average datapoints"

CAPACITY_TYPE_SERVERLESS_V2 = "serverless_v2"
CAPACITY_TYPE_SERVERLESS_V1 = "serverless_v1"
CAPACITY_TYPE_PROVISIONED = "provisioned"

BUSY_VCPU_METHOD = (
    "per-period CPUUtilization x ServerlessDatabaseCapacity x vcpu_per_acu, "
    "joined on timestamp"
)
BUSY_VCPU_CAVEAT = (
    "CPUUtilization on db.serverless is a percentage of currently-allocated "
    "capacity, not of max_capacity"
)

STAT_KEYS = {
    "Average": "avg",
    "Minimum": "min",
    "Maximum": "max",
    "Sum": "sum",
}

_AVG_MAX = ("Average", "Maximum")
_AVG_P95_MAX = ("Average", P95_STAT, "Maximum")
_MIN_AVG = ("Minimum", "Average")
_MAX_ONLY = ("Maximum",)
_SUM_ONLY = ("Sum",)

SHARED_INSTANCE_METRICS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "CPUUtilization": ("cpu_utilization_pct", _AVG_P95_MAX),
    "DBLoad": ("db_load", _AVG_P95_MAX),
    "DBLoadCPU": ("db_load_cpu", _AVG_P95_MAX),
    "DBLoadNonCPU": ("db_load_non_cpu", _AVG_P95_MAX),
    "FreeableMemory": ("freeable_memory_bytes", _MIN_AVG),
    "SwapUsage": ("swap_usage_bytes", _AVG_MAX),
    "DatabaseConnections": ("database_connections", _AVG_P95_MAX),
    "ReadIOPS": ("read_iops", _AVG_P95_MAX),
    "WriteIOPS": ("write_iops", _AVG_P95_MAX),
    "ReadThroughput": ("read_throughput_bytes_per_sec", _AVG_MAX),
    "WriteThroughput": ("write_throughput_bytes_per_sec", _AVG_MAX),
    "ReadLatency": ("read_latency_seconds", _AVG_P95_MAX),
    "WriteLatency": ("write_latency_seconds", _AVG_P95_MAX),
    "NetworkReceiveThroughput": ("network_receive_bytes_per_sec", _AVG_MAX),
    "NetworkTransmitThroughput": ("network_transmit_bytes_per_sec", _AVG_MAX),
    "MaximumUsedTransactionIDs": ("maximum_used_transaction_ids", _MAX_ONLY),
    "OldestReplicationSlotLag": ("oldest_replication_slot_lag_bytes", _MAX_ONLY),
    "Deadlocks": ("deadlocks", _SUM_ONLY),
    "EngineUptime": ("engine_uptime_seconds", ("Minimum",)),
}

AURORA_INSTANCE_METRICS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    **SHARED_INSTANCE_METRICS,
    "BufferCacheHitRatio": ("buffer_cache_hit_ratio_pct", _MIN_AVG),
    "CommitLatency": ("commit_latency_seconds", _AVG_P95_MAX),
    "CommitThroughput": ("commit_throughput_per_sec", _AVG_MAX),
    "FreeLocalStorage": ("free_local_storage_bytes", _MIN_AVG),
    "TempStorageIops": ("temp_storage_iops", _AVG_MAX),
    "TempStorageThroughput": ("temp_storage_bytes_per_sec", _AVG_MAX),
    "AuroraReplicaLag": ("aurora_replica_lag_ms", _AVG_MAX),
}

AURORA_SERVERLESS_INSTANCE_METRICS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    **AURORA_INSTANCE_METRICS,
    "ServerlessDatabaseCapacity": (
        "serverless_database_capacity_acu",
        ("Minimum", "Average", P95_STAT, "Maximum"),
    ),
    "ACUUtilization": ("acu_utilization_pct", _AVG_MAX),
}

RDS_INSTANCE_METRICS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    **SHARED_INSTANCE_METRICS,
    "FreeStorageSpace": ("free_storage_space_bytes", _MIN_AVG),
    "TransactionLogsDiskUsage": ("transaction_logs_disk_usage_bytes", _MAX_ONLY),
    "DiskQueueDepth": ("disk_queue_depth", _AVG_MAX),
    "ReplicaLag": ("replica_lag_seconds", _AVG_MAX),
    "EBSByteBalance%": ("ebs_byte_balance_pct", _MIN_AVG),
    "EBSIOBalance%": ("ebs_io_balance_pct", _MIN_AVG),
}

AURORA_CLUSTER_METRICS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "VolumeBytesUsed": ("volume_bytes_used", _AVG_MAX),
    "VolumeReadIOPs": ("volume_read_ios_per_period", _AVG_MAX),
    "VolumeWriteIOPs": ("volume_write_ios_per_period", _AVG_MAX),
    "SnapshotStorageUsed": ("snapshot_storage_bytes", _MAX_ONLY),
    "BackupRetentionPeriodStorageUsed": (
        "backup_retention_storage_bytes",
        _MAX_ONLY,
    ),
    "TotalBackupStorageBilled": ("total_backup_storage_billed_bytes", _MAX_ONLY),
    "AuroraGlobalDBReplicationLag": ("global_db_replication_lag_ms", _AVG_MAX),
    "AuroraGlobalDBReplicatedWriteIO": (
        "global_db_replicated_write_ios_per_period",
        _AVG_MAX,
    ),
    "AuroraGlobalDBDataTransferBytes": (
        "global_db_data_transfer_bytes_per_period",
        _AVG_MAX,
    ),
}

AURORA_SERVERLESS_V1_CLUSTER_METRICS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    **AURORA_CLUSTER_METRICS,
    "ServerlessDatabaseCapacity": (
        "serverless_database_capacity_acu",
        ("Minimum", "Average", P95_STAT, "Maximum"),
    ),
    "ACUUtilization": ("acu_utilization_pct", _AVG_MAX),
}

INSTANCE_DIMENSION = "DBInstanceIdentifier"
CLUSTER_DIMENSION = "DBClusterIdentifier"


class MetricTarget(NamedTuple):
    """One resource whose metrics are fetched in the region-wide batch."""

    key: str
    dimension_name: str
    dimension_value: str
    table: Dict[str, Tuple[str, Tuple[str, ...]]]


class Series(NamedTuple):
    """One returned CloudWatch series, timestamps ascending."""

    timestamps: List[datetime]
    values: List[float]


def instance_metric_table(
    engine: str, instance_class: str
) -> Dict[str, Tuple[str, Tuple[str, ...]]]:
    """Pick the metric table for an RDS or Aurora instance."""
    if not (engine or "").startswith("aurora"):
        return RDS_INSTANCE_METRICS
    if instance_class == SERVERLESS_INSTANCE_CLASS:
        return AURORA_SERVERLESS_INSTANCE_METRICS
    return AURORA_INSTANCE_METRICS


def cluster_metric_table(engine_mode: str) -> Dict[str, Tuple[str, Tuple[str, ...]]]:
    """Pick the metric table for an Aurora cluster."""
    if engine_mode == "serverless":
        return AURORA_SERVERLESS_V1_CLUSTER_METRICS
    return AURORA_CLUSTER_METRICS


def query_stats(stats: Sequence[str]) -> List[str]:
    """Return the statistics that need their own CloudWatch query."""
    needed = [stat for stat in stats if stat != P95_STAT]
    if P95_STAT in stats and P95_BASIS_STAT not in needed:
        needed.append(P95_BASIS_STAT)
    return needed


def build_metric_queries(
    targets: Iterable[MetricTarget], period: int
) -> Tuple[List[Dict[str, Any]], Dict[str, Tuple[str, str, str]]]:
    """Build GetMetricData queries plus a map from query id back to its target.

    A side map is required because a CloudWatch query ``Id`` must match
    ``[a-z][a-zA-Z0-9_]*`` and database identifiers contain hyphens.
    """
    queries: List[Dict[str, Any]] = []
    routing: Dict[str, Tuple[str, str, str]] = {}

    for target in targets:
        for metric_name, (_, stats) in sorted(target.table.items()):
            for stat in query_stats(stats):
                query_id = f"m{len(queries)}"
                queries.append(
                    {
                        "Id": query_id,
                        "MetricStat": {
                            "Metric": {
                                "Namespace": RDS_NAMESPACE,
                                "MetricName": metric_name,
                                "Dimensions": [
                                    {
                                        "Name": target.dimension_name,
                                        "Value": target.dimension_value,
                                    }
                                ],
                            },
                            "Period": period,
                            "Stat": stat,
                        },
                        "ReturnData": True,
                    }
                )
                routing[query_id] = (target.key, metric_name, stat)

    return queries, routing


def chunk_queries(
    queries: Sequence[Dict[str, Any]], size: int = GET_METRIC_DATA_MAX_QUERIES
) -> List[List[Dict[str, Any]]]:
    """Split queries into batches GetMetricData will accept."""
    batches: List[List[Dict[str, Any]]] = []
    for start in range(0, len(queries), size):
        batches.append(list(queries[start : start + size]))  # noqa: E203
    return batches


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def nearest_rank_p95(values: Sequence[float]) -> Optional[float]:
    """Return the nearest-rank 95th percentile of an unordered sample."""
    if not values:
        return None
    ordered = sorted(values)
    index = -(-95 * len(ordered) // 100) - 1
    return ordered[max(index, 0)]


def reduce_series(stat: str, series: Series) -> Optional[float]:
    """Reduce one returned series to the single figure the payload carries."""
    if not series.values:
        return None
    if stat == "Average":
        return _mean(series.values)
    if stat == "Minimum":
        return min(series.values)
    if stat == "Maximum":
        return max(series.values)
    if stat == "Sum":
        return sum(series.values)
    return None


def summarize_target(
    table: Dict[str, Tuple[str, Tuple[str, ...]]],
    series_by_metric: Dict[Tuple[str, str], Series],
) -> Tuple[Dict[str, Any], Dict[str, int], List[str]]:
    """Build the per-resource metrics block from its returned series."""
    summary: Dict[str, Any] = {}
    datapoint_counts: Dict[str, int] = {}
    unavailable: List[str] = []

    for metric_name, (payload_key, stats) in sorted(table.items()):
        figures: Dict[str, float] = {}
        count = 0

        for stat in stats:
            if stat == P95_STAT:
                basis = series_by_metric.get((metric_name, P95_BASIS_STAT))
                value = nearest_rank_p95(basis.values) if basis else None
                key = P95_STAT
            else:
                series = series_by_metric.get((metric_name, stat))
                value = reduce_series(stat, series) if series else None
                key = STAT_KEYS[stat]
                if series:
                    count = max(count, len(series.values))
            if value is not None:
                figures[key] = value

        if figures:
            summary[payload_key] = figures
            datapoint_counts[metric_name] = count
        else:
            unavailable.append(metric_name)

    return summary, datapoint_counts, unavailable


def derive_serverless_capacity(
    acu: Optional[Series], cpu: Optional[Series], vcpu_per_acu: float = AURORA_ACU_VCPU
) -> Dict[str, Any]:
    """Join ACU and CPU by timestamp, then reduce the derived busy-vCPU series."""
    if not acu or not cpu:
        return {"aligned_datapoints": 0}

    cpu_by_timestamp = dict(zip(cpu.timestamps, cpu.values))
    # Joining per timestamp is required: mean(cpu) * mean(acu) understates load.
    busy = [
        acu_value * vcpu_per_acu * cpu_by_timestamp[timestamp] / 100.0
        for timestamp, acu_value in zip(acu.timestamps, acu.values)
        if timestamp in cpu_by_timestamp
    ]

    derived: Dict[str, Any] = {
        "aligned_datapoints": len(busy),
        "method": BUSY_VCPU_METHOD,
        "caveat": BUSY_VCPU_CAVEAT,
    }
    if busy:
        derived["avg"] = _mean(busy)
        derived["p95"] = nearest_rank_p95(busy)
        derived["max"] = max(busy)
    return derived


def acu_to_memory_gib(acu_figures: Dict[str, float]) -> Dict[str, float]:
    """Convert each ACU figure to the memory AWS documents for it."""
    return {key: value * AURORA_ACU_MEMORY_GIB for key, value in acu_figures.items()}
