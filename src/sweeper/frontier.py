from __future__ import annotations

import hashlib
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _local_path(location: str) -> Path | None:
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme == "file":
        return Path(urllib.parse.unquote(parsed.path))
    if not parsed.scheme:
        return Path(location)
    return None


def fingerprint(location: str) -> str:
    """Bind retirement to exact local bytes or to a stable remote frontier ID."""
    local = _local_path(location)
    if local is not None and local.is_file():
        digest = hashlib.sha256()
        with local.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return f"sha256:{digest.hexdigest()}"
    return "location:" + hashlib.sha256(location.encode()).hexdigest()


class FrontierRetirement:
    """Durable exhausted-frontier memory that reopens changed local manifests."""

    def __init__(self, workspace: Path) -> None:
        self.path = workspace / "retired-frontiers.json"
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            self.entries = {}

    @staticmethod
    def key(source_id: str, location: str) -> str:
        return hashlib.sha256(f"{source_id}\0{location}".encode()).hexdigest()

    def is_retired(self, source_id: str, location: str) -> bool:
        entry = self.entries.get(self.key(source_id, location), {})
        return entry.get("fingerprint") == fingerprint(location)

    def rotation_status(self, source_id: str, locations: list[str]) -> dict:
        """Describe the next usable set without treating one empty set as a stop."""
        rows = [
            {"index": index, "location": location,
             "retired": self.is_retired(source_id, location)}
            for index, location in enumerate(locations)
        ]
        active = [row for row in rows if not row["retired"]]
        return {
            "configuredFrontiers": len(rows),
            "retiredFrontiers": len(rows) - len(active),
            "nextFrontierIndex": active[0]["index"] if active else None,
            "nextFrontier": active[0]["location"] if active else None,
            "sourceFrontierExhausted": bool(rows) and not active,
        }

    def retire(self, source_id: str, location: str) -> dict:
        entry = {
            "source": source_id,
            "location": location,
            "fingerprint": fingerprint(location),
            "retiredAt": _now(),
            "reason": "frontier-fully-screened",
            "acceptedArtifactsChanged": False,
        }
        self.entries[self.key(source_id, location)] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schemaVersion": 1, "entries": self.entries},
                                        indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return entry
