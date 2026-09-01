from __future__ import annotations

import argparse
import fcntl
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import FrozenSet

from .internet_archive import (
    ArchiveDiscoveryConfig,
    ArchiveQuery,
    DiscoveryReport,
    discover_archive,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(payload, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def _activity(root: Path, event: str, **details) -> None:
    path = root / "journal" / "activity.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"observedAt": _now(), "event": event, **details}
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        output.flush()
        os.fsync(output.fileno())


@dataclass(frozen=True)
class DiscoveryLaneConfig:
    campaign_id: str
    root: Path
    query: str
    max_accepted: int
    max_candidates: int
    modulus: int
    buckets: FrozenSet[int]
    rows: int = 500
    requests_per_second: float = 1.0
    publication_authorized: bool = False

    def __post_init__(self) -> None:
        if self.publication_authorized:
            raise ValueError("publication is not authorized for a discovery lane")
        if self.max_accepted < 1:
            raise ValueError("max_accepted must be positive")


def _state(config: DiscoveryLaneConfig, report: DiscoveryReport, stage: str) -> dict:
    return {
        "schemaVersion": 1,
        "campaignId": config.campaign_id,
        "stage": stage,
        "currentAction": "Archive identity discovery",
        "updatedAt": _now(),
        "accepted": 0,
        "target": config.max_accepted,
        "candidateCount": report.screened_owned,
        "candidateTarget": config.max_candidates,
        "discovered": report.screened_source,
        "discoveryFrontier": report.screened_source,
        "checkpoints": report.checkpoints,
        "completionReason": report.completed_reason,
        "nextCursor": report.next_cursor or "",
        "partitionModulus": config.modulus,
        "partitionBuckets": sorted(config.buckets),
        "publicationAuthorized": False,
        "published": 0,
        "liveVerified": 0,
    }


def run_discovery_lane(config: DiscoveryLaneConfig, *, opener=None,
                       sleeper=time.sleep) -> DiscoveryReport:
    config.root.mkdir(parents=True, exist_ok=True)
    lock_path = config.root / "coordinator.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"campaign root is already locked: {config.root}") from error
        initial = DiscoveryReport(0, 0, 0, "", None)
        frontier_path = config.root / "discovery" / "frontier.json"
        if frontier_path.exists():
            raw = json.loads(frontier_path.read_text(encoding="utf-8"))
            initial = DiscoveryReport(
                int(raw.get("screenedSource", 0)), int(raw.get("screenedOwned", 0)),
                int(raw.get("checkpoints", 0)), str(raw.get("completedReason", "")),
                raw.get("nextCursor"),
            )
        _atomic_json(config.root / "state.json", _state(config, initial, "discovery"))
        _activity(config.root, "coordinator-started", pid=os.getpid())

        def progress(report: DiscoveryReport) -> None:
            stage = "bounded-frontier-complete" if report.completed_reason else "discovery"
            _atomic_json(config.root / "state.json", _state(config, report, stage))
            _activity(config.root, "discovery-checkpoint",
                      discovered=report.screened_source,
                      owned=report.screened_owned,
                      checkpoints=report.checkpoints)

        adapter = ArchiveDiscoveryConfig(
            query=ArchiveQuery(
                config.query,
                ("identifier", "title", "creator", "alternative_title"),
                ("identifier asc",),
                config.rows,
            ),
            modulus=config.modulus,
            buckets=config.buckets,
            max_candidates=config.max_candidates,
            requests_per_second=config.requests_per_second,
            checkpoint_root=config.root / "discovery",
        )
        kwargs = {"sleeper": sleeper, "progress": progress}
        if opener is not None:
            kwargs["opener"] = opener
        report = discover_archive(adapter, **kwargs)
        stage = ("source-rate-limited-retrying" if report.completed_reason == "transient-deferred"
                 else "bounded-frontier-complete")
        _atomic_json(config.root / "state.json", _state(config, report, stage))
        _activity(config.root, "coordinator-stopped", reason=report.completed_reason)
        return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a staging-only Internet Archive discovery lane")
    parser.add_argument("--campaign-config", required=True, type=Path)
    args = parser.parse_args(argv)
    raw = json.loads(args.campaign_config.read_text(encoding="utf-8"))
    config = DiscoveryLaneConfig(
        campaign_id=str(raw["campaignId"]),
        root=Path(raw["root"]).expanduser().resolve(),
        query=str(raw["query"]),
        max_accepted=int(raw["maxAccepted"]),
        max_candidates=int(raw["maxCandidates"]),
        modulus=int(raw["partitionModulus"]),
        buckets=frozenset(int(value) for value in raw["partitionBuckets"]),
        rows=int(raw.get("checkpointSize", 500)),
        requests_per_second=float(raw.get("requestsPerSecond", 1.0)),
        publication_authorized=bool(raw.get("publicationAuthorized", False)),
    )
    report = run_discovery_lane(config)
    print(json.dumps(report.__dict__, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
