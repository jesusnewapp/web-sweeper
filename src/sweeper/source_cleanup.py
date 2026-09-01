"""Receipt-bound disposal of re-downloadable source cache after staging."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .activity import record as activity_record
from .dock import atomic_json, command_json, now


def cleanup_staged_source_cache(workspace: Path, cleaner: list[str]) -> dict:
    receipt_path = workspace / "dock-staging.json"
    if not receipt_path.exists():
        raise ValueError("dock-staging.json is required before source-cache cleanup")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    items = receipt.get("items")
    if (receipt.get("passed") is not True or receipt.get("production_mutated") is not False or
            not isinstance(items, dict) or not items):
        raise ValueError("exact non-production staging receipt is incomplete")
    keys = sorted(map(str, items))
    result = command_json(cleaner, {
        "operation": "delete-rehydratable-source-cache",
        "items": items,
        "staging_receipt": receipt,
        "preserve": ["staged artifacts", "catalogs", "hashes", "journals",
                     "checkpoints", "receipts"],
        "requested_at": now(),
    })
    if sorted(map(str, result.get("deleted", []))) != keys:
        raise ValueError("cleaner did not confirm the exact staged source-cache membership")
    evidence = {
        "schema_version": 1,
        "cleaned_at": now(),
        "cleanup_boundary": "exact-non-production-staging-receipt",
        "item_count": len(keys),
        "deleted": keys,
        "bytes_reclaimed": int(result.get("bytes_reclaimed", 0)),
        "staging_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "rehydration_required_before_source-exactness-review": True,
        "passed": True,
    }
    atomic_json(workspace / "dock-source-cleanup.json", evidence)
    activity_record(workspace, "staged-source-cache-cleanup", lane="uploader",
                    status="passed", detail={"deleted": len(keys),
                                             "bytesReclaimed": evidence["bytes_reclaimed"]})
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--cleanup-command", nargs="+", required=True)
    args = parser.parse_args()
    print(json.dumps(cleanup_staged_source_cache(args.workspace.resolve(),
                                                  args.cleanup_command), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
