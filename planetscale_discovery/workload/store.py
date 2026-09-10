"""Persist workload snapshots as timestamped files."""

import gzip
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

SNAPSHOT_GLOB = "snapshot-*.json.gz"
SCHEMA_FILE = "schema.json"


class WorkloadStore:
    """A directory of snapshots, plus the schema captured once at init."""

    def __init__(self, directory: str):
        self.directory = Path(directory)

    @property
    def snapshots_dir(self) -> Path:
        return self.directory / "snapshots"

    @property
    def schema_path(self) -> Path:
        return self.directory / SCHEMA_FILE

    @property
    def output_dir(self) -> Path:
        return self.directory / "bundle"

    def exists(self) -> bool:
        return self.schema_path.is_file()

    def create(self) -> None:
        """Lay out the directories, owner-only."""
        for path in (self.directory, self.snapshots_dir):
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def write_schema(self, schema: Dict[str, Any]) -> Path:
        """Store the schema captured at init, for finalize to render from."""
        _atomic_write(
            self.schema_path, json.dumps(schema, indent=2, default=str).encode()
        )
        return self.schema_path

    def read_schema(self) -> Dict[str, Any]:
        with open(self.schema_path, encoding="utf-8") as handle:
            return json.load(handle)

    def append_snapshot(self, snapshot: Dict[str, Any]) -> Path:
        """Write one snapshot. Never modifies an existing file."""
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        stamp = _stamp(snapshot)
        path = self.snapshots_dir / f"snapshot-{stamp}.json.gz"
        # Suffix rather than overwrite a same-second collect.
        suffix = 1
        while path.exists():
            path = self.snapshots_dir / f"snapshot-{stamp}-{suffix}.json.gz"
            suffix += 1
        _atomic_write(path, gzip.compress(json.dumps(snapshot, default=str).encode()))
        return path

    def snapshot_paths(self) -> List[Path]:
        if not self.snapshots_dir.is_dir():
            return []
        return sorted(self.snapshots_dir.glob(SNAPSHOT_GLOB))

    def read_snapshots(self) -> List[Dict[str, Any]]:
        """Every snapshot, ordered by the server clock recorded inside it."""
        snapshots = []
        for path in self.snapshot_paths():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    snapshots.append(json.load(handle))
            except Exception as e:  # pragma: no cover - corrupt file
                snapshots.append(
                    {"status": "unreadable", "path": str(path), "error": str(e)}
                )
        return sorted(snapshots, key=lambda s: str(s.get("captured_at_server") or ""))

    def total_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.snapshot_paths())

    def status(self) -> Dict[str, Any]:
        """Session state, with no database connection."""
        snapshots = self.read_snapshots()
        usable = [s for s in snapshots if s.get("status") in ("ok", "degraded")]
        stamps = [
            s["captured_at_server"] for s in usable if s.get("captured_at_server")
        ]
        return {
            "directory": str(self.directory),
            "initialized": self.exists(),
            "snapshots": len(snapshots),
            "usable_snapshots": len(usable),
            "first_snapshot": min(stamps) if stamps else None,
            "last_snapshot": max(stamps) if stamps else None,
            "total_bytes": self.total_bytes(),
            "ready_to_finalize": len(usable) >= 2,
        }


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via temp file and rename, owner-only from the moment it exists."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def _stamp(snapshot: Dict[str, Any]) -> str:
    """A YYYYMMDDTHHMMSS stamp from the server clock, else the client's."""
    raw = snapshot.get("captured_at_server") or snapshot.get("captured_at_client")
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 14:
        return "unknown"
    return f"{digits[:8]}T{digits[8:14]}"
