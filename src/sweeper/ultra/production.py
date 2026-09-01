from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from ..dock import cleanup_verified_staging, membership, promote as v2_promote, staged
from ..state import State
from .runtime import UltraRuntime, canonical, digest
from .capabilities import CapabilityRouter


PRODUCTION_LEASE_AUTHORITY_ENV = "SWEEPER_PRODUCTION_LEASE_DB"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _production_lease_authority(workspace: Path,
                                configured: Optional[Path]) -> Path:
    raw = configured or os.environ.get(PRODUCTION_LEASE_AUTHORITY_ENV)
    if not raw:
        raise ValueError(
            "a global production lease authority is required; pass "
            "lease_authority or configure %s" % PRODUCTION_LEASE_AUTHORITY_ENV
        )
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("the production lease authority path must be absolute")
    authority = candidate.resolve()
    local = Path(workspace).resolve()
    if authority == local or local in authority.parents:
        raise ValueError(
            "the production lease authority must be global, not workspace-local"
        )
    return authority


def _validated_unit(workspace: Path, owner_id: str, starting_live_revision: str) -> Dict[str, object]:
    validation_path = workspace / "dock-validation.json"
    if not validation_path.is_file():
        raise ValueError("V2 dock validation is required before Ultra production")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    state = State(workspace / "state.sqlite3")
    try:
        items = staged(state)
        current = membership(items)
    finally:
        state.close()
    approved = validation.get("items") or {}
    if validation.get("passed") is not True or current != approved:
        raise ValueError("Ultra/V2 staged membership differs from the validated unit")
    membership_sha = digest(approved)
    object_set_sha = digest(sorted(approved.values()))
    validation_sha = _sha256(validation_path)
    staging_receipt = {"schemaVersion": 1, "adapter": "sweeper-v2-dock",
        "membershipSha256": membership_sha, "itemCount": len(approved),
        "validationSha256": validation_sha}
    staging_sha = digest(staging_receipt)
    total_bytes = sum(Path(str(item.get("local_path") or "")).stat().st_size for item in items)
    unit_id = "unit-%s" % membership_sha[:24]
    member_ids = sorted(approved)
    binding = {"taskId": owner_id, "laneId": "uploader", "unitId": unit_id,
        "catalogSha256": membership_sha, "manuscriptSetSha256": object_set_sha,
        "validationAttestationSha256": validation_sha,
        "stagingVerificationSha256": staging_sha,
        "startingLiveRevision": starting_live_revision,
        "membershipSha256": membership_sha, "itemCount": len(member_ids),
        "memberIds": member_ids}
    return {"unitId": unit_id, "binding": binding, "approved": approved,
            "workingSetBytes": total_bytes, "stagingReceipt": staging_receipt}


def promote_with_v2(workspace: Path, publisher: List[str], verifier: List[str],
                    owner_id: str, starting_live_revision: str,
                    cleaner: Optional[List[str]] = None,
                    lease_authority: Optional[Path] = None) -> dict:
    """Ultra owns production; V2 executes its proven upload/verify adapters."""
    authority = _production_lease_authority(workspace, lease_authority)
    unit = _validated_unit(workspace, owner_id, starting_live_revision)
    runtime = UltraRuntime(authority)
    lease = runtime.acquire_writer(owner_id=owner_id, binding=unit["binding"],
        free_bytes=shutil.disk_usage(workspace).free,
        largest_working_set_bytes=int(unit["workingSetBytes"]), ttl_seconds=1800,
        command_key="writer-acquire:%s:%s" % (unit["unitId"], owner_id))
    fence = int(lease["fence"])
    try:
        runtime.begin_publish(owner_id=owner_id, fence=fence,
            unit_id=str(unit["unitId"]),
            command_key="publish-start:%s:%s" % (unit["unitId"], fence))
        routed = CapabilityRouter().execute("publication", ultra=None,
            v2=lambda: v2_promote(workspace, publisher, verifier))
        promotion = routed.value
        binding = unit["binding"]
        verification_receipt = {
            "adapter": "sweeper-v2",
            "promotion": promotion,
            "staging": unit["stagingReceipt"],
            "itemCount": binding["itemCount"],
            "memberIds": binding["memberIds"],
            "membershipSha256": binding["membershipSha256"],
            "catalogSha256": binding["catalogSha256"],
            "manuscriptSetSha256": binding["manuscriptSetSha256"],
            "validationAttestationSha256": binding[
                "validationAttestationSha256"
            ],
            "stagingVerificationSha256": binding[
                "stagingVerificationSha256"
            ],
        }
        runtime.record_live_verification(owner_id=owner_id, fence=fence,
            unit_id=str(unit["unitId"]), verified_items=promotion["verified"],
            receipt=verification_receipt,
            command_key="live-verified:%s:%s" % (unit["unitId"], fence))
        cleanup = cleanup_verified_staging(workspace, cleaner) if cleaner else None
        runtime.release_writer(owner_id=owner_id, fence=fence,
            command_key="writer-release:%s:%s" % (unit["unitId"], fence))
        snapshot = runtime.snapshot()
        return {"engine": "ultra", "uploaderAdapter": routed.executor,
                "fallbackUsed": routed.fallback_used, "unitId": unit["unitId"],
                "writerFence": fence, "promotion": promotion, "cleanup": cleanup,
                "runtimeHead": snapshot["headEventHash"], "passed": True}
    except Exception as error:
        runtime.writer_recovery_required(owner_id=owner_id, fence=fence,
            reason="%s: %s" % (type(error).__name__, error),
            command_key="writer-recovery:%s:%s" % (unit["unitId"], fence))
        raise
    finally:
        runtime.close()
