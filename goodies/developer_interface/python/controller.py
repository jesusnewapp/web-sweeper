#!/usr/bin/env python3
"""Source-neutral, read-mostly controller used by the developer interfaces."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_CONFIG = Path(__file__).with_name("controller.example.json")
_JSON_CACHE_LIMIT = 2048
_JSON_CACHE: Dict[str, tuple[tuple[int, int, int], Dict[str, Any]]] = {}
OPTIMIZATION_STAGES = (
    "discovery", "gate-0", "metadata", "retrieval", "conversion",
    "deduplication", "checkpoint", "staging", "publication", "live-verification",
)
OPTIMIZATION_CONTROLS = (
    "early-exit", "immutable-cache", "hash-reuse", "bounded-batching",
    "respectful-concurrency", "backpressure", "bounded-retry", "timeout",
    "resumability", "append-only-decisions", "memory-bound", "capacity-gate",
    "identity-projection", "source-pacing", "deterministic-order",
    "exclusive-ownership", "authoritative-observability", "recovery-receipt",
    "stale-state-invalidation", "integrity-fail-closed",
)
OPTIMIZATION_POINT_COUNT = len(OPTIMIZATION_STAGES) * len(OPTIMIZATION_CONTROLS)


def _read_json(path: Path) -> Dict[str, Any]:
    key = str(path)
    try:
        metadata = path.stat()
        signature = (metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        cached = _JSON_CACHE.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        value = json.loads(path.read_text(encoding="utf-8"))
        result = value if isinstance(value, dict) else {}
        _JSON_CACHE[key] = (signature, result)
        if len(_JSON_CACHE) > _JSON_CACHE_LIMIT:
            _JSON_CACHE.pop(next(iter(_JSON_CACHE)))
        return result
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _JSON_CACHE.pop(key, None)
        return {}


def _first(mapping: Dict[str, Any], names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        value = mapping.get(name)
        if value is not None:
            return value
    return default


def _timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _batch_number(root: Any) -> int:
    match = re.search(r"(?:batch|unit)_(\d+)", str(root or ""))
    return int(match.group(1)) if match else 0


def _progress_key(value: Dict[str, Any]) -> str:
    """Bind the inactivity clock to every durable progress signal.

    Accepted count is only one signal. A rate-limited discovery adapter may be
    healthy while it advances query pages or candidate inventory without yet
    accepting another item. Hashing the complete evidence vector prevents that
    legitimate work from being mislabeled as a stalled lane.
    """
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _publication_progress(root: Path) -> Dict[str, Any]:
    """Read exact progress, falling back to exact counters emitted by older writers."""
    progress = _read_json(root / "publication_progress.json")
    try:
        log = (root / "promotion.log").read_text(encoding="utf-8")
    except OSError:
        return progress
    value: Dict[str, Any] = {"phase": "fresh-live-delta"}
    delta = list(re.finditer(
        r"Fresh live delta checked (\d+) published books; removed (\d+), retained (\d+)\.",
        log,
    ))
    if delta:
        match = delta[-1]
        value.update({"phase": "room-allocation", "duplicateRemoved": int(match.group(2)),
                      "publishable": int(match.group(3))})
    uploads = list(re.finditer(r"Uploaded (\d+)/(\d+)", log))
    if uploads:
        # Legacy concurrent writers emitted completed item indexes out of
        # order. The maximum is a monotonic observed marker, not a fabricated
        # completion count. New writers persist an exact atomic counter above.
        maximum = max(int(match.group(1)) for match in uploads)
        target = int(uploads[-1].group(2))
        value.update({"phase": "storage-upload", "uploaded": maximum,
                      "uploadTarget": target, "legacyObservedMarker": True})
    published = list(re.finditer(r"Published (\d+) new or changed Codex records\.", log))
    if published:
        value.update({"phase": "publication-complete",
                      "published": int(published[-1].group(1))})
        if uploads:
            value["uploaded"] = int(uploads[-1].group(2))
    verified = list(re.finditer(r"Verified (\d+)/(\d+) live Codex books\.", log))
    if verified:
        match = verified[-1]
        value.update({"phase": "live-verification", "liveVerified": int(match.group(1)),
                      "verificationTarget": int(match.group(2))})
    # The writer updates its log and atomic progress JSON independently. During
    # that very small hand-off window the log can contain newer exact evidence
    # (for example ``Verified 1/1``) while the JSON still says zero. A normal
    # dictionary merge allowed that stale zero to overwrite the newer counter,
    # making the UI briefly regress to 0/1 after successful verification.
    merged = {**value, **progress}
    monotonic_counts = (
        "prepared", "duplicateRemoved", "publishable", "uploaded",
        "uploadTarget", "published", "liveVerified", "verificationTarget",
    )
    for name in monotonic_counts:
        if name in value or name in progress:
            merged[name] = max(_count(value.get(name)), _count(progress.get(name)))

    phase_order = {
        "fresh-live-delta": 0,
        "room-allocation": 1,
        "storage-upload": 2,
        "publication-complete": 3,
        "live-verification": 4,
        "complete": 5,
    }
    log_phase = str(value.get("phase", ""))
    json_phase = str(progress.get("phase", ""))
    merged["phase"] = max(
        (log_phase, json_phase),
        key=lambda phase: phase_order.get(phase, -1),
    )
    return merged


class SweeperController:
    """Loads trusted local state and exposes allow-listed control actions."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        requested = config_path or Path(os.environ.get("WEB_SWEEPER_CONFIG", DEFAULT_CONFIG))
        self.config_path = requested.expanduser().resolve()
        self.config = _read_json(self.config_path)
        root = self.config.get("projectRoot") or Path(__file__).resolve().parents[3]
        self.project_root = Path(root).expanduser().resolve()
        self._progress_observations: Dict[str, Dict[str, Any]] = {}
        self._stage_observations: Dict[str, Dict[str, Any]] = {}
        self._progress_file_samples: Dict[str, Dict[str, Any]] = {}
        self._decision_journal_samples: Dict[str, Dict[str, Any]] = {}
        self._accepted_growth_observations: Dict[str, Dict[str, Any]] = {}

    def _path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.project_root / path

    def _source_success_history(
        self, lane_id: str, definition: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        configured_prefix = str((definition or {}).get("historyPrefix", "")).strip()
        prefix = configured_prefix or {
            "open-library": "open_library_",
            "open-library-stories": "open_library_parallel_model_1_",
            "library-of-congress": "library_of_congress_",
        }.get(lane_id)
        if not prefix:
            return []
        imports = self.project_root / "work/judah_library/imports"
        rows: List[Dict[str, Any]] = []
        for root in imports.glob(f"{prefix}*"):
            if lane_id == "open-library" and root.name.startswith(
                ("open_library_christian_stories_", "open_library_parallel_model_1_")
            ):
                continue
            verification = _read_json(root / "staging_verification.json")
            upload_receipt = _read_json(root / "staging_upload_receipt.json")
            verified = _count(verification.get("verified"))
            receipt_staged = _count(upload_receipt.get("staged"))
            staged = verified if verified > 0 else receipt_staged
            isolated = (
                verification.get("productionMutated") is False
                if verified > 0
                else upload_receipt.get("productionMutated") is False
            )
            if staged < 1 or not isolated:
                continue
            promotion = _read_json(root / "promotion_validation.json")
            rows.append({
                "batchNumber": _batch_number(root.name),
                "root": str(root),
                "status": "live-verified" if _count(promotion.get("liveVerified")) > 0 else "staged",
                "staged": staged,
                "published": _count(promotion.get("published")),
                "liveVerified": _count(promotion.get("liveVerified")),
                "completedAt": (
                    promotion.get("completedAt")
                    or verification.get("verifiedAt")
                    or upload_receipt.get("stagedAt")
                    or ""
                ),
            })
        rows.sort(key=lambda row: (str(row["completedAt"]), int(row["batchNumber"])), reverse=True)
        return rows[:8]

    def _sample_progress_file(self, path: Path, metadata: os.stat_result) -> Dict[str, Any]:
        """Sample potentially large discovery journals at most every 30 seconds."""
        key = str(path)
        now = datetime.now(timezone.utc)
        cached = self._progress_file_samples.get(key)
        if cached and (now - cached["sampledAt"]).total_seconds() < 30:
            return dict(cached["value"])
        payload = _read_json(path)
        completed = set(map(str, payload.get("completed", [])))
        prior_completed = cached.get("completed", set()) if cached else set()
        newly_completed = completed - prior_completed
        recent_queries = sorted({
            checkpoint.rsplit("\n", 1)[0]
            for checkpoint in newly_completed
            if "\n" in checkpoint
        })
        value = {
            "pagesCompleted": len(completed),
            "candidateRecords": _count(payload.get("records")),
            "newlyCompletedPages": len(newly_completed),
            "recentQueries": recent_queries,
            "activeQuery": payload.get("activeQuery", ""),
            "activePage": _count(payload.get("activePage")),
            "lastCompletedQuery": payload.get("lastCompletedQuery", ""),
            "lastCompletedPage": _count(payload.get("lastCompletedPage")),
            "journalBytes": metadata.st_size,
            "journalUpdatedAt": datetime.fromtimestamp(
                metadata.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
            "sampledAt": now.isoformat().replace("+00:00", "Z"),
            "sampleCadenceSeconds": 30,
        }
        self._progress_file_samples[key] = {
            "sampledAt": now,
            "value": value,
            "completed": completed,
        }
        return dict(value)

    def _accepted_journal_count(self, path: Path) -> tuple[int, Optional[datetime]]:
        """Count current durable acceptances, honoring append-only revocations."""
        key = str(path)
        try:
            metadata = path.stat()
        except OSError:
            return 0, None
        cached = self._decision_journal_samples.get(key, {})
        same_file = cached.get("inode") == metadata.st_ino
        offset = int(cached.get("offset", 0)) if same_file else 0
        accepted_ids = set(cached.get("acceptedIds", ())) if same_file else set()
        if metadata.st_size < offset:
            offset = 0
            accepted_ids = set()
        try:
            with path.open("rb") as journal:
                journal.seek(offset)
                for raw in journal:
                    if not raw.endswith(b"\n"):
                        break
                    offset += len(raw)
                    try:
                        event = json.loads(raw)
                        identity = str(
                            event.get("archiveId") or
                            (event.get("identifiers") or {}).get("archive") or
                            event.get("id") or ""
                        ).strip()
                        if event.get("kind") == "accepted" and identity:
                            accepted_ids.add(identity)
                        elif event.get("kind") == "rejected" and identity:
                            accepted_ids.discard(identity)
                    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                        continue
        except OSError:
            return len(accepted_ids), None
        self._decision_journal_samples[key] = {
            "inode": metadata.st_ino,
            "offset": offset,
            "acceptedIds": sorted(accepted_ids),
        }
        observed = datetime.fromtimestamp(metadata.st_mtime, timezone.utc)
        return len(accepted_ids), observed

    def _lane(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        if definition.get("kind") == "publisher":
            return self._publisher_lane(definition)
        state_path = self._path(str(definition.get("statePath", "missing.json")))
        state = _read_json(state_path)
        current_root = _first(state, ("currentRoot", "root"), "")
        lane_id = str(definition.get("id", definition.get("name", "lane")))
        batch_number = _count(state.get("currentBatch")) or _batch_number(current_root)
        success_history = self._source_success_history(lane_id, definition)
        navigation = _read_json(
            self._path(str(definition.get("navigationPath", "missing-navigation.json")))
        )
        checkpoint_path = Path(current_root) / "checkpoint.json" if current_root else None
        checkpoint = _read_json(checkpoint_path) if checkpoint_path else {}
        progress = _read_json(Path(current_root) / "staging_upload_progress.json") if current_root else {}
        state_stage = str(_first(state, ("stage", "status"), "inactive"))
        accepted = _count(
            _first(
                checkpoint,
                ("acceptedCount", "catalogCount", "accepted"),
                _first(
                    state,
                    ("acceptedInCurrentBatch", "accepted", "membershipReconciliation"),
                    0,
                ),
            )
        )
        # A resumed worker can reuse the same root while rebuilding its current
        # membership.  During active acquisition/validation the checkpoint is
        # the previous completed snapshot, so the authoritative live counter
        # must come from state/journal until staging creates a new receipt.
        active_stage = state_stage.casefold()
        if (not progress.get("phase") and (any(marker in active_stage for marker in (
                "acquisition-running", "validation-running", "retrieval-running"))
                or active_stage in {"prepare", "discovery", "initialize"})):
            accepted = _count(_first(
                state,
                ("acceptedInCurrentBatch", "accepted", "membershipReconciliation"),
                0,
            ))
            # Older coordinators could carry an operator-pushed unit's count
            # into the next root. Once the unit number has advanced beyond the
            # last staged receipt, the current root's checkpoint/journal is
            # authoritative and stale partial-unit state must not win.
            if (batch_number > _count(state.get("lastStagedUnit"))
                    and state.get("partialUnitReason") == "operator-push"):
                accepted = _count(_first(
                    checkpoint, ("acceptedCount", "catalogCount", "accepted"), 0
                ))
        if isinstance(state.get("membershipReconciliation"), dict) and not accepted:
            accepted = _count(state["membershipReconciliation"].get("catalogMembers"))
        journal_accepted = 0
        journal_observed: Optional[datetime] = None
        if current_root:
            journal_accepted, journal_observed = self._accepted_journal_count(
                Path(current_root) / "progress.jsonl"
            )
            accepted = max(accepted, journal_accepted)
        if (current_root and checkpoint_path is not None and not checkpoint_path.exists()
                and journal_accepted == 0):
            # A newly advanced batch has no accepted membership yet. Never
            # carry the completed prior batch's reconciliation count into it.
            accepted = 0
        target = int(_first(state, ("currentBatchSize", "batchSize", "target"), definition.get("target", 0)) or 0)
        uploaded = int(_first(progress, ("uploaded", "uploadedCount", "verified"), 0) or 0)
        display_uploaded = uploaded
        progress_phase = str(progress.get("phase", ""))
        progress_total = int(progress.get("total") or accepted or target)
        staged_complete = bool(
            progress_phase == "complete"
            and uploaded > 0
            and uploaded >= progress_total
        )
        stage = progress_phase if progress_phase and progress_phase != "complete" else state_stage
        state_updated = _first(
            checkpoint,
            ("updatedAt", "checkpointTimestamp", "lastUpdated"),
            _first(state, ("updatedAt", "checkpointTimestamp"), ""),
        )
        progress_updated = _first(progress, ("updatedAt", "checkpointTimestamp"), "")
        state_observed = _timestamp(state_updated)
        progress_observed = _timestamp(progress_updated)
        supplemental_progress = []
        supplemental_detail: Dict[str, Any] = {}
        supplemental_observed: Optional[datetime] = None
        for value in definition.get("progressPaths", []):
            try:
                path = self._path(str(value))
                metadata = path.stat()
                observed_at = datetime.fromtimestamp(metadata.st_mtime, timezone.utc)
                if supplemental_observed is None or observed_at > supplemental_observed:
                    supplemental_observed = observed_at
                supplemental_progress.append({
                    "path": str(value),
                    "size": metadata.st_size,
                    "modifiedNs": metadata.st_mtime_ns,
                })
                supplemental_detail.update(self._sample_progress_file(path, metadata))
            except (OSError, TypeError):
                supplemental_progress.append({"path": str(value), "missing": True})
        observed_candidates = [value for value in (
            state_observed, progress_observed, supplemental_observed, journal_observed
        ) if value is not None]
        observed = max(observed_candidates) if observed_candidates else None
        updated = observed.isoformat().replace("+00:00", "Z") if observed else state_updated
        age = (datetime.now(timezone.utc) - observed).total_seconds() if observed else None
        progress_active = bool(
            progress_phase
            and progress_phase != "complete"
            and age is not None
            and age <= 300
        )
        state_status = str(state.get("status", "")).lower()
        configured_queued = bool(definition.get("queued")) and state_status != "running"
        terminal = (
            state_status in {"complete", "completed", "exhausted"}
            or any(marker in state_stage.casefold() for marker in (
                "campaign-complete", "batch-complete", "source-exhausted",
            ))
        )
        running = state_status == "running" or progress_active
        explicit_failure = bool(state.get("lastFailure")) or any(
            marker in state_status for marker in ("failed", "error", "blocked")
        )
        if configured_queued:
            health = "healthy"
        elif terminal:
            health = "healthy"
        elif explicit_failure:
            health = "failed"
        elif not running:
            health = "watch"
        elif age is None or age > int(definition.get("redAfterSeconds", 3600)):
            health = "stuck"
        elif age > int(definition.get("watchAfterSeconds", 900)):
            health = "watch"
        else:
            health = "healthy"
        display_accepted = accepted
        display_target = target
        detail = definition.get("detail", f"State: {state_path.name}")
        handoff_path = self._path(str(self.config.get(
            "pushHandoffsPath",
            "work/judah_library/cache/web_sweeper_push_handoffs.json",
        )))
        handoffs = _read_json(handoff_path).get("handoffs", [])
        pending_handoff = next((
            row for row in reversed(handoffs)
            if isinstance(row, dict)
            and str(row.get("lane")) == lane_id
            and Path(str(row.get("root"))).resolve() == Path(current_root).resolve()
        ), None) if isinstance(handoffs, list) and current_root else None
        staging_receipt = _read_json(
            Path(current_root) / "staging_upload_receipt.json"
        ) if current_root else {}
        promotion_receipt = _read_json(
            Path(current_root) / "promotion_validation.json"
        ) if current_root else {}
        staged_custody = _count(staging_receipt.get("staged"))
        live_custody = _count(promotion_receipt.get("liveVerified"))
        permanent_live_receipt = promotion_receipt.get("status") in {
            "published-and-five-gate-verified",
            "already-live-and-five-gate-accounted",
        }
        if permanent_live_receipt:
            # A durable promotion receipt retires the transient handoff.  Keep
            # the ledger for history, but never let it hold the source card in
            # publisher-custody after five-gate completion.
            pending_handoff = None
        if pending_handoff is not None:
            handed_off = _count(pending_handoff.get("books"))
            # Push transfers custody away from the acquisition model.  The
            # publisher queue continues to show the protected books, while the
            # source card resets immediately for every adapter/model.
            display_accepted = 0
            display_target = _count(definition.get("target")) or target
            detail = (
                f"Current model reset · {handed_off} protected in publisher custody"
            )
        if staged_custody > 0 and not permanent_live_receipt:
            display_accepted = staged_custody
            display_target = _count(definition.get("target")) or target
            detail = f"{staged_custody} accepted books protected · publisher custody"
        elif staged_custody > 0 and permanent_live_receipt:
            already_live = max(0, staged_custody - live_custody)
            # A completed unit belongs in immutable Success History, not in
            # the active card. The operating card now represents only the
            # next/current acquisition unit.
            display_accepted = 0
            display_uploaded = 0
            display_target = _count(definition.get("target")) or target
            stage = "ready-for-next-batch"
            detail = (
                f"Previous batch complete · {staged_custody} fully accounted "
                "in Success History"
            )
            if already_live:
                detail += f" · {already_live} duplicate was already live"
        restored_live = _count(checkpoint.get("restoredLiveOverlapCount"))
        if restored_live > 0 and staged_custody == 0:
            detail = (
                f"{accepted} accepted books protected · {restored_live} reconciled "
                "as already live"
            )
        if state.get("lastFailure"):
            detail = str(state["lastFailure"])
            retry_seconds = _count(state.get("automaticRetrySeconds"))
            if retry_seconds:
                detail += f" · automatic retry every {retry_seconds}s"
        discovery_age = (
            (datetime.now(timezone.utc) - supplemental_observed).total_seconds()
            if supplemental_observed else None
        )
        if (not progress_active and stage.casefold() in {"prepare", "discovery"} and
                discovery_age is not None and discovery_age <= 90):
            stage = "discovery"
            detail = "Discovery mode · moving smoothly · 30-second checkpoint signal"
        uploading_mode = progress_active or any(
            marker in stage.casefold() for marker in ("upload", "staging", "verification", "verify")
        )
        if uploading_mode:
            mode = "uploading"
        elif supplemental_observed is not None and discovery_age is not None and discovery_age <= 90:
            mode = "discovery"
        else:
            # Some adapters acquire as they search while others materialize a
            # bounded discovery inventory first. Keep one lane contract while
            # naming the actual active gate.
            mode = "acquisition"
        mode_detail = {
            "mode": mode,
            "stage": stage,
            "accepted": display_accepted,
            "target": target,
            "discovered": _count(_first(checkpoint, ("discovered", "discoveredCount"),
                                       _first(state, ("discovered", "discoveredCount"), 0))),
            "prefiltered": _count(_first(checkpoint, ("prefiltered", "prefilteredCount"),
                                        _first(state, ("prefiltered", "prefilteredCount"), 0))),
            "discoveryFrontier": _count(_first(state, ("discoveryFrontier", "frontier"), 0)),
            "candidateOffset": _count(_first(state, ("candidateOffset", "cursor", "page"), 0)),
            "uploaded": display_uploaded,
            "uploadTarget": int(progress.get("total") or target),
            "completionState": "staged" if staged_complete else "",
            "batchNumber": batch_number,
            "navigationQueries": navigation.get("queries", []),
            "navigationStatus": str(navigation.get("status", "")),
            "checkpointUpdatedAt": state_updated,
            "acceptedJournalCount": journal_accepted,
            "handoffPending": pending_handoff is not None,
            "handoffBooks": _count(pending_handoff.get("books"))
            if pending_handoff is not None else 0,
            "custodyStage": (
                "live-verified" if permanent_live_receipt
                else "publisher" if staged_custody > 0
                else "staging" if pending_handoff is not None
                else "acquisition"
            ),
            "custodyBooks": staged_custody or live_custody or (
                _count(pending_handoff.get("books"))
                if pending_handoff is not None else accepted
            ),
            "alreadyLiveReconciled": restored_live,
            "uploadUpdatedAt": progress_updated,
            **supplemental_detail,
        }
        review_total = _count(state.get("reviewTotal"))
        if review_total and not permanent_live_receipt:
            review_processed = _count(state.get("reviewProcessed"))
            review_remaining = max(0, review_total - review_processed)
            adapter_times = [
                _timestamp(state.get("heartbeatAt")),
                _timestamp(state.get("updatedAt")),
            ]
            adapter_times = [value for value in adapter_times if value is not None]
            adapter_heartbeat = max(adapter_times) if adapter_times else None
            adapter_active = bool(
                str(state.get("status", "")).casefold() == "running"
                and adapter_heartbeat is not None
                and (datetime.now(timezone.utc) - adapter_heartbeat).total_seconds() <= 90
            )
            mode = "acquisition"
            mode_detail.update({
                "mode": mode,
                "adapterActive": adapter_active,
                "adapterHeartbeatAt": state.get("heartbeatAt") or state.get("updatedAt"),
                "substageProgressLabel": (
                    "Source rate limited · automatic retry"
                    if stage.casefold() == "source-rate-limited-retrying"
                    else "Reviewing rights and complete source text"
                ),
                "substageProgressCurrent": review_processed,
                "substageProgressTarget": review_total,
                "nextStage": "Independent validation of accepted survivors",
                "reviewRemaining": review_remaining,
                "rightsDeferred": _count(state.get("rightsDeferred")),
                "reviewRejected": _count(state.get("reviewRejected")),
            })
            detail = (
                f"Review {'moving' if adapter_active else 'awaiting retry'} · "
                f"{review_processed}/{review_total} processed · {review_remaining} candidates remaining · "
                f"{accepted} authoritatively accepted"
            )
        if stage.casefold() == "ready-for-next-batch":
            if str(state.get("nextStage", "")).casefold() == "source-exhausted":
                mode_detail.update({
                    "substageProgressLabel": "Source exhausted",
                    "substageProgressCurrent": 1,
                    "substageProgressTarget": 1,
                    "nextStage": "Initialize only when new upstream identifiers appear",
                })
            else:
                mode_detail.update({
                    "substageProgressLabel": "Waiting for next batch",
                    "substageProgressCurrent": 0,
                    "substageProgressTarget": 1,
                    "nextStage": "Acquire lane lock when the next batch starts",
                })
        crawl_counts = state.get("counts") if isinstance(state.get("counts"), dict) else {}
        if crawl_counts:
            mode_detail.update({
                "pagesCompleted": _count(crawl_counts.get("visited")),
                "candidateRecords": _count(crawl_counts.get("candidates")),
                "discovered": _count(crawl_counts.get("candidates")),
            })
            if stage.casefold() == "capacity-protected":
                mode = "acquisition"
                mode_detail.update({
                    "mode": mode,
                    "substageProgressLabel": "Accepted books protected",
                    "substageProgressCurrent": accepted,
                    "substageProgressTarget": accepted or target,
                    "nextStage": "Resume after the 1 GiB capacity floor is restored",
                })
                detail = str(state.get("capacityMessage") or
                             "Not enough space · accepted books protected")
            elif stage.casefold() in {"validation-running", "validation-complete"}:
                validation_current = _count(state.get("validationCurrent"))
                validation_target = _count(state.get("validationTarget")) or accepted
                mode = "acquisition"
                mode_detail.update({
                    "mode": mode,
                    "substageProgressLabel": "Validating accepted manuscripts",
                    "substageProgressCurrent": validation_current,
                    "substageProgressTarget": validation_target,
                    "nextStage": "Protected staging handoff",
                })
                detail = (
                    f"Validation {'complete' if stage.casefold() == 'validation-complete' else 'moving'} · "
                    f"{validation_current}/{validation_target} accepted manuscripts"
                )
            elif stage.casefold() == "acquisition-running":
                retrieved = _count(crawl_counts.get("retrieved"))
                retrieval_target = _count(crawl_counts.get("retrievalTarget"))
                mode = "acquisition"
                mode_detail.update({
                    "mode": mode,
                    "substageProgressLabel": "Retrieving and accepting books",
                    "substageProgressCurrent": retrieved,
                    "substageProgressTarget": retrieval_target,
                    "nextStage": "Validate accepted manuscript unit",
                })
                detail = (
                    f"Retrieval moving · {retrieved}/{retrieval_target} books processed · "
                    f"{accepted} authoritatively accepted"
                )
            elif stage.casefold() == "preflight-running":
                preflighted = _count(crawl_counts.get("preflighted"))
                preflight_target = _count(crawl_counts.get("preflightTarget"))
                mode = "acquisition"
                mode_detail.update({
                    "mode": mode,
                    "substageProgressLabel": "Gate 0 candidate preflight",
                    "substageProgressCurrent": preflighted,
                    "substageProgressTarget": preflight_target,
                    "nextStage": "Retrieve approved complete-text editions",
                })
                detail = (
                    f"Gate 0 moving · {preflighted}/{preflight_target} candidates checked · "
                    f"{_count(crawl_counts.get('preflightSurvivors'))} survivors"
                )
            elif stage.casefold() == "preflight-complete":
                mode = "acquisition"
                mode_detail.update({
                    "mode": mode,
                    "substageProgressLabel": "Begin complete-text retrieval",
                    "substageProgressCurrent": 0,
                    "substageProgressTarget": _count(crawl_counts.get("preflightSurvivors")),
                    "nextStage": "Retrieve approved complete-text editions",
                })
                detail = (
                    f"Gate 0 complete · {_count(crawl_counts.get('preflightSurvivors'))} "
                    "survivors ready for complete-text retrieval"
                )
            elif stage.casefold() == "bounded-frontier-complete":
                mode = "discovery"
                mode_detail.update({
                    "mode": mode,
                    "substageProgressLabel": "Gate 0 candidate preflight",
                    "substageProgressCurrent": 0,
                    "substageProgressTarget": _count(crawl_counts.get("candidates")),
                    "nextStage": "Gate 0 identity and format preflight",
                })
                detail = (
                    f"Inventory complete · {_count(crawl_counts.get('visited'))} pages · "
                    f"{_count(crawl_counts.get('candidates'))} candidates · awaiting Gate 0 preflight"
                )
        pages_completed = _count(mode_detail.get("pagesCompleted"))
        discovery_baseline = _count(state.get("discoveryPagesBaseline"))
        discovery_target = _count(state.get("discoveryPagesTarget"))
        if discovery_target > 0:
            mode_detail.update({
                "gateProgressLabel": "Discovery pages",
                "gateProgressCurrent": max(0, pages_completed - discovery_baseline),
                "gateProgressTarget": discovery_target,
            })
        if progress_active and pending_handoff is None:
            # Staging is already a defined lane phase. A partial survivor
            # upload must never masquerade as a new acquisition batch target.
            display_accepted = accepted
            display_target = target
            detail = f"{accepted}/{target} accepted · staging"
        if progress_active and progress_total > 0:
            mode_detail.update({
                "substageProgressLabel": "Staging objects",
                "substageProgressCurrent": uploaded,
                "substageProgressTarget": progress_total,
            })
        elif (stage.casefold() in {"prepare", "discovery"}
              and accepted > 0 and target > 0):
            mode_detail.update({
                "substageProgressLabel": "Accepting books",
                "substageProgressCurrent": accepted,
                "substageProgressTarget": target,
            })
        # The current root may already appear in Success History as soon as it
        # receives an isolated-staging receipt. Count it through `accepted`
        # below, not once there and again here.
        historical_accepted = sum(max(
            _count(item.get("staged")),
            _count(item.get("published")),
            _count(item.get("liveVerified")),
        ) for item in success_history if not current_root or
            Path(str(item.get("root", ""))).resolve() != Path(current_root).resolve())
        accepted_cumulative = historical_accepted + max(
            accepted,
            _count(pending_handoff.get("books"))
            if pending_handoff is not None else 0,
        )
        if bool(definition.get("displayCumulativeProgress", False)):
            # Campaign-goal cards must retain visible custody across bounded
            # checkpoints. Advancing to a fresh unit resets only that unit's
            # counter; it must never make already staged or live-verified books
            # appear to have vanished from the campaign.
            display_accepted = accepted_cumulative
            display_target = _count(definition.get("target")) or target
            mode_detail.update({
                "campaignAccepted": accepted_cumulative,
                "campaignTarget": display_target,
                "currentUnitAccepted": accepted,
                "protectedPriorUnits": historical_accepted,
            })
            detail = (
                f"{accepted_cumulative}/{display_target} campaign custody · "
                f"{historical_accepted} protected in prior units · "
                f"Unit {batch_number} acquiring {accepted}"
            )
        screening_completed = bool(definition.get("screeningCompleted", False))
        screening_accepted = _count(definition.get("screeningAccepted"))
        screening_total = _count(definition.get("screeningTotal"))
        acceptance_rate = (
            (100.0 * screening_accepted / screening_total)
            if screening_completed and screening_total > 0 else None
        )
        exhausted_source = bool(
            acceptance_rate is not None and acceptance_rate < 1.0
        )
        accepted_evidence = [journal_observed]
        accepted_evidence.extend(_timestamp(item.get("completedAt")) for item in success_history)
        accepted_evidence = [value for value in accepted_evidence if value is not None]
        accepted_updated_at = (
            max(accepted_evidence).isoformat().replace("+00:00", "Z")
            if accepted_evidence else None
        )
        discovered_candidates = _count(_first(
            state, ("discoveredCandidateInventory", "discoveryCandidatesFound"), 0))
        qualified_candidates = _count(_first(
            state, ("qualifiedCandidateInventory", "candidateCount"), 0))
        candidate_count = _count(_first(
            state,
            ("qualifiedCandidateInventory", "candidateCount", "retrievalQueued", "sourceCandidateInventory",
             "candidateInventory", "discoveryCandidatesFound", "prefiltered",
             "prefilteredCount"),
            _first(checkpoint, ("candidateCount", "prefiltered", "prefilteredCount"), 0),
        ))
        if not candidate_count:
            candidate_count = _count(state.get("candidates"))
        if pending_handoff is not None:
            candidate_count = 0
            display_accepted = 0
            display_target = _count(definition.get("target")) or target
            mode_detail["handedOffBooks"] = _count(pending_handoff.get("books"))
            # A source-specific completed worker may continue refreshing its
            # old terminal stage after Push. Publisher custody is authoritative
            # for the active card until the permanent receipt arrives.
            stage = "publisher-custody"
            health = "healthy"
            detail = (
                f"Current model reset · {_count(pending_handoff.get('books'))} "
                "protected in publisher custody"
            )
            mode_detail.update({
                "stage": stage,
                "mode": "uploading",
                "accepted": 0,
                "candidateCount": 0,
                "handoffPending": True,
                "handoffBooks": _count(pending_handoff.get("books")),
                "custodyStage": "staging",
            })
        # Retrieval progress and source inventory are different quantities.
        # Never replace the candidate inventory with a moving remainder; that
        # made a healthy review appear to lose candidates as metadata advanced.
        mode_detail["discoveredCandidateInventory"] = discovered_candidates
        mode_detail["qualifiedCandidateInventory"] = qualified_candidates
        mode_detail["candidateCount"] = candidate_count
        mode_detail["candidateRecords"] = max(
            candidate_count,
            _count(mode_detail.get("candidateRecords")),
        )
        candidate_target = _count(state.get("candidateTarget")) or _count(
            definition.get("candidateTarget")
        )
        mode_detail["candidateTarget"] = candidate_target
        # TCP-style adapters preserve distinct upstream corpus boundaries even
        # when the lane rolls them up into one model.  Pass this neutral source
        # inventory through so current builds can display provenance and live
        # candidate counts without teaching the controller provider semantics.
        if isinstance(state.get("masterSources"), list):
            mode_detail["masterSources"] = state["masterSources"]
            mode_detail["candidateRecords"] = max(
                mode_detail["candidateRecords"],
                sum(_count(item.get("candidateRecords")) for item in state["masterSources"]
                    if isinstance(item, dict)),
            )
        return {
            "id": lane_id,
            "name": definition.get("name", "Unnamed lane"),
            "stage": stage,
            "accepted": display_accepted,
            "acceptedCumulative": accepted_cumulative,
            "acceptedUpdatedAt": accepted_updated_at,
            "candidateCount": candidate_count,
            "candidateTarget": candidate_target,
            "target": display_target,
            "uploaded": display_uploaded,
            "health": health,
            "detail": detail,
            "updatedAt": updated,
            "currentRoot": current_root,
            "mode": "queued" if configured_queued else "complete" if terminal else mode,
            "modeDetail": mode_detail,
            "authoritativeGrowthMetric": str(
                mode_detail.get("authoritativeGrowthMetric") or "accepted"
            ),
            "authoritativeGrowthCount": _count(
                mode_detail.get("authoritativeGrowthCount", accepted_cumulative)
            ),
            "batchNumber": batch_number,
            "successHistory": success_history,
            "screeningCompleted": screening_completed,
            "screeningAccepted": screening_accepted,
            "screeningTotal": screening_total,
            "acceptanceRate": acceptance_rate,
            "exhaustedSource": exhausted_source,
            "navigationQueries": navigation.get("queries", []),
            "progressEvidence": {
                "stage": stage,
                "accepted": accepted,
                "uploaded": display_uploaded,
                "discovered": _count(_first(checkpoint, ("discovered", "discoveredCount"),
                                           _first(state, ("discovered", "discoveredCount"), 0))),
                "prefiltered": _count(_first(checkpoint, ("prefiltered", "prefilteredCount"),
                                            _first(state, ("prefiltered", "prefilteredCount"), 0))),
                "candidateInventory": _count(_first(
                    state, ("sourceCandidateInventory", "candidateInventory",
                            "candidateCount", "discoveryCandidatesFound"), 0)),
                "candidateOffset": _count(_first(state, ("candidateOffset", "cursor", "page"), 0)),
                "discoveryFrontier": _count(_first(state, ("discoveryFrontier", "frontier"), 0)),
                "stateUpdatedAt": state.get("updatedAt", ""),
                "checkpointUpdatedAt": state_updated,
                "uploadUpdatedAt": progress_updated,
                "supplemental": supplemental_progress,
            },
        }

    def navigate(self, lane_id: str, queries: Any) -> Dict[str, Any]:
        """Save a bounded source-navigation pool without executing shell text."""
        lane = next((item for item in self.config.get("lanes", [])
                     if isinstance(item, dict) and str(item.get("id")) == lane_id), None)
        if not isinstance(lane, dict) or not lane.get("navigationPath"):
            raise ValueError(f"navigation is disabled for lane: {lane_id}")
        if not isinstance(queries, list):
            raise ValueError("navigation queries must be a list")
        cleaned: List[str] = []
        for raw in queries[:10]:
            query = " ".join(str(raw).strip().split())
            if not query:
                continue
            if not 2 <= len(query) <= 120 or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9 .,'&()\-/]*", query
            ):
                raise ValueError("navigation queries must be 2-120 plain-text characters")
            if query.casefold() not in {value.casefold() for value in cleaned}:
                cleaned.append(query)
        if not cleaned:
            raise ValueError("at least one navigation query is required")
        destination = self._path(str(lane["navigationPath"]))
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": 1,
            "lane": lane_id,
            "queries": cleaned,
            "status": "pending-safe-discovery-window",
            "requestedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        atomic_temporary = destination.with_suffix(destination.suffix + ".tmp")
        atomic_temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        atomic_temporary.replace(destination)
        return {"accepted": True, **payload}

    def _publication_campaign_lane(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        """Expose an active deterministic publication campaign from receipts.

        This is authoritative when a campaign glob is configured.  It avoids
        projecting an obsolete listener or an unrelated staging handoff onto
        the live-production card.
        """
        pattern = str(definition.get("publicationCampaignGlob") or "")
        pattern_path = Path(pattern).expanduser()
        if pattern_path.is_absolute():
            roots = sorted(pattern_path.parent.glob(pattern_path.name))
        else:
            roots = sorted(self.project_root.glob(pattern))
        completed: List[tuple[Path, Dict[str, Any]]] = []
        active_root: Optional[Path] = None
        for root in roots:
            verification = _read_json(root / "publication_verification.json")
            published_count = _count(verification.get("published"))
            verified_count = _count(verification.get("verified"))
            if published_count > 0 and verified_count >= published_count:
                completed.append((root, verification))
            elif active_root is None:
                active_root = root
        campaign_published = sum(_count(row.get("published")) for _, row in completed)
        campaign_verified = sum(_count(row.get("verified")) for _, row in completed)
        campaign_duplicates = sum(
            _count(row.get("removedLiveOverlaps")) for _, row in completed
        )
        total_books = _count(definition.get("publicationCampaignBooks")) or sum(
            _count(_read_json(root / "catalog.json").get("books", [])) for root in roots
        )
        total_batches = len(roots)
        completed_batches = len(completed)
        if active_root is None:
            stage = "campaign-complete"
            current_count = total_books
            current_target = total_books
            current_uploaded = campaign_published
            current_progress: Dict[str, Any] = {}
            updated = max(
                (str(row.get("verifiedAt") or "") for _, row in completed),
                default="",
            )
            health = "healthy"
            batch_index = total_batches
        else:
            current_progress = _publication_progress(active_root)
            stage = str(current_progress.get("phase") or "fresh-live-delta")
            catalog_count = _count(_read_json(active_root / "catalog.json").get("books", []))
            current_uploaded = _count(current_progress.get("uploaded"))
            current_published = _count(current_progress.get("published"))
            current_verified = _count(current_progress.get("liveVerified"))
            current_count = {
                "storage-upload": current_uploaded,
                "publication-complete": current_published,
                "live-verification": current_verified,
                "complete": current_verified,
            }.get(stage, 0)
            current_target = {
                "storage-upload": _count(current_progress.get("uploadTarget")) or catalog_count,
                "publication-complete": _count(current_progress.get("publishable")) or catalog_count,
                "live-verification": _count(current_progress.get("verificationTarget")) or catalog_count,
                "complete": _count(current_progress.get("verificationTarget")) or catalog_count,
            }.get(stage, catalog_count)
            updated = str(current_progress.get("updatedAt") or "")
            if not updated:
                try:
                    updated = datetime.fromtimestamp(
                        (active_root / "publication_progress.json").stat().st_mtime,
                        timezone.utc,
                    ).isoformat().replace("+00:00", "Z")
                except OSError:
                    updated = ""
            observed = _timestamp(updated)
            age = (datetime.now(timezone.utc) - observed).total_seconds() if observed else None
            if age is None or age > int(definition.get("redAfterSeconds", 3600)):
                health = "stuck"
            elif age > int(definition.get("watchAfterSeconds", 900)):
                health = "watch"
            else:
                health = "healthy"
            batch_index = roots.index(active_root) + 1
        gate_label = {
            "fresh-live-delta": "Fresh live duplicate delta",
            "room-allocation": "Duplicate-safe room allocation",
            "storage-upload": "Storage upload",
            "publication-complete": "Publishing",
            "live-verification": "Live verification",
            "complete": "Live verification",
            "campaign-complete": "Campaign complete",
        }.get(stage, "Publication")
        history = [{
            "batchNumber": roots.index(root) + 1,
            "root": str(root),
            "status": "live-verified",
            "staged": 0,
            "published": _count(row.get("published")),
            "liveVerified": _count(row.get("verified")),
            "completedAt": row.get("verifiedAt", ""),
        } for root, row in reversed(completed[-8:])]
        mode_detail = {
            "mode": "complete" if active_root is None else "uploading",
            "stage": stage,
            "prepared": _count(current_progress.get("prepared")),
            "duplicatesRemoved": campaign_duplicates,
            "uploaded": current_uploaded,
            "published": campaign_published,
            "liveVerified": campaign_verified,
            "gateProgressLabel": gate_label,
            "gateProgressCurrent": current_count,
            "gateProgressTarget": current_target,
            "substageProgressLabel": gate_label,
            "substageProgressCurrent": current_count,
            "substageProgressTarget": current_target,
            "campaignBatchesCompleted": completed_batches,
            "campaignBatchesTotal": total_batches,
            "campaignBatchIndex": batch_index,
            "campaignLiveVerified": campaign_verified,
            "campaignBooksTotal": total_books,
            "campaignDuplicatesRemoved": campaign_duplicates,
            "completionState": "published" if active_root is None else "",
            "writerSerialized": True,
            "currentRoot": str(active_root or ""),
        }
        return {
            "id": definition.get("id", "publisher"),
            "name": definition.get("name", "Stage-to-live publisher"),
            "stage": stage,
            "accepted": current_count,
            "candidateTarget": 0,
            "target": current_target,
            "uploaded": current_uploaded,
            "published": campaign_published,
            "liveVerified": campaign_verified,
            "health": health,
            "detail": (
                f"Campaign {campaign_verified}/{total_books} live-verified · "
                f"batch {batch_index}/{total_batches} · "
                f"{campaign_duplicates} duplicates safely removed"
            ),
            "queueReady": max(0, total_batches - completed_batches - (1 if active_root else 0)),
            "queueParked": 0,
            "queuePreflight": 0,
            "batchQueue": [],
            "updatedAt": updated,
            "currentRoot": str(active_root or ""),
            "mode": "complete" if active_root is None else "uploading",
            "modeDetail": mode_detail,
            "batchNumber": batch_index,
            "successHistory": history,
            "codexLive": max(
                (_count(row.get("publishedLiveTotal")) for _, row in completed),
                default=0,
            ),
            "progressEvidence": mode_detail,
        }

    def _publisher_lane(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        if definition.get("publicationCampaignGlob"):
            return self._publication_campaign_lane(definition)
        state_path = self._path(str(definition.get("statePath", "missing.json")))
        state = _read_json(state_path)
        advances = state.get("automaticAdvanceLog")
        latest = advances[-1] if isinstance(advances, list) and advances else {}
        current_root = str(state.get("currentUnit") or latest.get("root") or "")
        current = bool(state.get("currentUnit"))
        publisher_history = [{
            "batchNumber": _batch_number(row.get("root")),
            "root": str(row.get("root", "")),
            "status": "live-verified",
            "staged": 0,
            "published": _count(row.get("published")),
            "liveVerified": _count(row.get("liveVerified")),
            "completedAt": row.get("completedAt", ""),
        } for row in reversed(advances[-8:])] if isinstance(advances, list) else []
        completed_roots = {
            str(row.get("root") or "")
            for row in advances
            if isinstance(row, dict)
            and str(row.get("root") or "")
            and _count(row.get("published")) > 0
            and _count(row.get("liveVerified")) >= _count(row.get("published"))
        } if isinstance(advances, list) else set()
        batch_number = _batch_number(current_root)
        publication: Dict[str, Any] = {}
        promotion: Dict[str, Any] = {}
        if current:
            root = Path(current_root)
            catalog = _read_json(root / "catalog.json").get("books", [])
            publication = _read_json(root / "publication_verification.json")
            promotion = _read_json(root / "promotion_validation.json")
            progress = _publication_progress(root)
            accepted = _count(catalog)
            published = _count(progress.get("published", publication.get("published")))
            verified = _count(
                progress.get("liveVerified", promotion.get("liveVerified", publication.get("verified")))
            )
            duplicate_removed = _count(progress.get("duplicateRemoved"))
            uploaded = _count(progress.get("uploaded"))
            target = accepted
            updated = _first(
                promotion,
                ("checkedAt", "completedAt"),
                _first(publication, ("checkedAt", "completedAt"), state.get("checkedAt", "")),
            )
            stage = str(progress.get("phase") or state.get("currentAction", "publishing"))
            phase_count = {
                "storage-upload": uploaded,
                "publication-complete": published,
                "live-verification": verified,
                "complete": verified,
            }.get(stage, 0)
            target = {
                "storage-upload": _count(progress.get("uploadTarget")) or accepted,
                "publication-complete": _count(progress.get("publishable")) or accepted,
                "live-verification": _count(progress.get("verificationTarget")) or accepted,
                "complete": _count(progress.get("verificationTarget")) or accepted,
            }.get(stage, accepted)
        else:
            last_published = _count(latest.get("published"))
            last_verified = _count(latest.get("liveVerified"))
            if current_root:
                publication = _read_json(Path(current_root) / "publication_verification.json")
                promotion = _read_json(Path(current_root) / "promotion_validation.json")
            published = 0
            verified = 0
            accepted = 0
            target = 0
            updated = latest.get("completedAt") or state.get("checkedAt", "")
            duplicate_removed = 0
            uploaded = 0
            phase_count = 0
        observed = _timestamp(state.get("checkedAt"))
        age = (datetime.now(timezone.utc) - observed).total_seconds() if observed else None
        running = bool(state.get("listenerActive"))
        if not running:
            health = "failed"
        elif age is None or age > int(definition.get("redAfterSeconds", 3600)):
            health = "stuck"
        elif age > int(definition.get("watchAfterSeconds", 900)):
            health = "watch"
        else:
            health = "healthy"
        queue = state.get("queue") if isinstance(state.get("queue"), dict) else {}
        pending_units = _count(queue.get("pendingUnits"))
        parked_units = _count(queue.get("parkedUnchanged"))
        preflight_units = _count(queue.get("bookkeptPreflight"))
        ready_units = max(0, pending_units - parked_units - preflight_units)
        queued_behind_current = ready_units if current else 0
        if current:
            ready_units = 0
        if not current:
            stage = (
                "Ready for next exact staged unit"
                if ready_units > 0
                else "Listening for next exact staged unit"
            )
        batch_queue = []
        unit_rows = state.get("units") if isinstance(state.get("units"), list) else []
        for row in unit_rows:
            if not isinstance(row, dict):
                continue
            root_value = str(row.get("root") or "")
            bridge = row.get("bridge") if isinstance(row.get("bridge"), dict) else {}
            book_count = _count(bridge.get("accepted"))
            if not book_count and root_value:
                book_count = _count(_read_json(Path(root_value) / "catalog.json").get("books", []))
            raw_status = str(row.get("status") or "ready").casefold()
            staging_receipt = _read_json(Path(root_value) / "staging_upload_receipt.json") \
                if root_value else {}
            exact_staged = (
                _count(staging_receipt.get("staged")) == book_count
                and book_count > 0
                and staging_receipt.get("productionMutated") is False
            )
            if current and root_value == current_root:
                queue_status = stage
            elif exact_staged and "unsupported" in raw_status:
                # Source admission controls production eligibility, not staging
                # custody. Keep exact isolated staging visible while the source
                # remains fail-closed for the live publisher.
                queue_status = "Staged · awaiting source admission"
            elif raw_status in {"eligible", "ready"}:
                queue_status = "Ready to roll"
            elif "fail" in raw_status or "block" in raw_status:
                queue_status = "Blocked · publisher will retry"
            elif "park" in raw_status:
                queue_status = "Parked · unchanged"
            elif "preflight" in raw_status:
                queue_status = "Preflight"
            else:
                queue_status = str(row.get("status") or "Queued")
            batch_queue.append({
                "batchNumber": _batch_number(root_value),
                "name": Path(root_value).name if root_value else "unknown batch",
                "root": root_value,
                "books": book_count,
                "status": queue_status,
                "current": bool(current and root_value == current_root),
            })
        handoff_path = self._path(str(self.config.get(
            "pushHandoffsPath",
            "work/judah_library/cache/web_sweeper_push_handoffs.json",
        )))
        handoffs = _read_json(handoff_path).get("handoffs", [])
        if not isinstance(handoffs, list):
            handoffs = []
        source_state_value = str(definition.get("stagingSourceStatePath", "")).strip()
        if source_state_value:
            source_state = _read_json(self._path(source_state_value))
            source_root = str(source_state.get("currentRoot") or "")
            if source_root and not any(
                isinstance(row, dict) and str(row.get("root") or "") == source_root
                for row in handoffs
            ):
                handoffs = [*handoffs, {
                    "lane": str(definition.get("stagingSourceLane", "")),
                    "root": source_root,
                    "books": _count(source_state.get("acceptedInCurrentBatch")),
                    "requestedAt": source_state.get("updatedAt", ""),
                    "action": "follow-authoritative-staging-root",
                }]
        known_roots = {item["root"] for item in batch_queue}
        if isinstance(handoffs, list):
            for handoff in handoffs:
                if not isinstance(handoff, dict):
                    continue
                root_value = str(handoff.get("root") or "")
                # A permanent live-verification receipt retires the handoff from
                # the active publisher card. Its totals belong exclusively in
                # Success History; retaining it here makes a later push look
                # cumulative (for example, completed 690 + new 68 = 758).
                root = Path(root_value) if root_value else None
                permanent_receipt = (
                    _read_json(root / "promotion_validation.json")
                    if root is not None else {}
                )
                permanently_verified = (
                    _count(permanent_receipt.get("published")) > 0
                    and _count(permanent_receipt.get("liveVerified"))
                    >= _count(permanent_receipt.get("published"))
                )
                if (not root_value or root_value in known_roots
                        or root_value in completed_roots or permanently_verified):
                    continue
                assert root is not None
                receipt = _read_json(root / "staging_upload_receipt.json")
                verification = _read_json(root / "staging_verification.json")
                staging_progress = _read_json(root / "staging_upload_progress.json")
                staged = max(
                    _count(receipt.get("staged")),
                    _count(verification.get("verified")),
                )
                requested = _count(handoff.get("books"))
                staging_phase = str(staging_progress.get("phase") or "")
                staging_uploaded = _count(staging_progress.get("uploaded"))
                staging_target = _count(staging_progress.get("total")) or requested
                if staging_phase and staging_phase != "complete":
                    handoff_status = (
                        f"Staging upload · {staging_uploaded}/{staging_target}"
                    )
                elif staged > 0:
                    handoff_status = "Staged · waiting for publisher discovery"
                else:
                    handoff_status = "Approved handoff · preparing for staging"
                batch_queue.append({
                    "batchNumber": _batch_number(root_value),
                    "name": root.name,
                    "root": root_value,
                    "books": staged or requested,
                    "status": handoff_status,
                    "stageProgressCurrent": staging_uploaded,
                    "stageProgressTarget": staging_target,
                    "stageProgressUpdatedAt": staging_progress.get("updatedAt", ""),
                    "current": False,
                })
                known_roots.add(root_value)
        handoff_rows = [
            item for item in batch_queue
            if str(item.get("status", "")).startswith((
                "Approved handoff", "Staging upload", "Staged · waiting",
                "Staged · awaiting",
            ))
        ]
        if not current and handoff_rows:
            active_staging = next((
                item for item in handoff_rows
                if str(item.get("status", "")).startswith("Staging upload")
            ), None)
            if active_staging is not None:
                accepted = _count(active_staging.get("stageProgressCurrent"))
                target = _count(active_staging.get("stageProgressTarget"))
                phase_count = accepted
                stage = "staging-upload"
                uploaded = accepted
                current_root = str(active_staging.get("root") or "")
                batch_number = _batch_number(current_root)
                staging_observed = _timestamp(active_staging.get("stageProgressUpdatedAt"))
                if staging_observed is not None:
                    staging_age = (
                        datetime.now(timezone.utc) - staging_observed
                    ).total_seconds()
                    if staging_age <= int(definition.get("watchAfterSeconds", 900)):
                        health = "healthy"
                    elif staging_age <= int(definition.get("redAfterSeconds", 3600)):
                        health = "watch"
            else:
                handoff_books = sum(_count(item.get("books")) for item in handoff_rows)
                accepted = handoff_books
                target = handoff_books
                phase_count = handoff_books
                stage = (
                    "Staged · waiting for publisher discovery"
                    if all(str(item.get("status", "")).startswith("Staged")
                           for item in handoff_rows)
                    else "Approved handoff · preparing for staging"
                )
            updated = max((
                str(row.get("requestedAt") or "")
                for row in handoffs if isinstance(row, dict)
            ), default=updated)
        continuation = (
            state.get("automaticContinuation")
            if isinstance(state.get("automaticContinuation"), dict)
            else {}
        )
        mode = "uploading" if "upload" in stage.casefold() else "verification"
        mode_detail = {
            "mode": mode,
            "stage": stage,
            "prepared": accepted,
            "duplicatesRemoved": duplicate_removed,
            "uploaded": uploaded,
            "uploadTarget": target if mode == "uploading" else accepted,
            "published": published if current else last_published,
            "liveVerified": verified if current else last_verified,
            "queueReady": ready_units,
            "queueParked": parked_units,
            "queuePreflight": preflight_units,
            "batchQueue": batch_queue,
            "publicationReceipt": bool(publication),
            "promotionReceipt": bool(promotion),
            "completionState": (
                "published"
                if (published if current else last_published) > 0
                and (verified if current else last_verified)
                >= (published if current else last_published)
                else ""
            ),
            "writerSerialized": True,
            "currentRoot": current_root,
            "unitUpdatedAt": updated,
            "watcherCheckedAt": state.get("checkedAt", ""),
        }
        if not current and handoff_rows and target > 0:
            mode_detail.update({
                "gateProgressLabel": (
                    "Staging upload" if stage == "staging-upload"
                    else "Protected staging handoff"
                ),
                "gateProgressCurrent": phase_count,
                "gateProgressTarget": target,
                "substageProgressLabel": (
                    "Staging upload" if stage == "staging-upload"
                    else "Protected staging handoff"
                ),
                "substageProgressCurrent": phase_count,
                "substageProgressTarget": target,
            })
        elif current and target > 0:
            gate_label = {
                "storage-upload": "Storage upload",
                "publication-complete": "Publishing",
                "live-verification": "Live verification",
                "complete": "Live verification",
            }.get(stage)
            if gate_label:
                mode_detail.update({
                    "gateProgressLabel": gate_label,
                    "gateProgressCurrent": phase_count,
                    "gateProgressTarget": target,
                    "substageProgressLabel": gate_label,
                    "substageProgressCurrent": phase_count,
                    "substageProgressTarget": target,
                })
        elif not current and not handoff_rows:
            mode_detail.update({
                "substageProgressLabel": "Publisher idle completion",
                "substageProgressCurrent": 1,
                "substageProgressTarget": 1,
            })
        return {
            "id": definition.get("id", "publisher"),
            "name": definition.get("name", "Stage-to-live publisher"),
            "stage": stage,
            "accepted": phase_count,
            "target": target,
            "uploaded": uploaded,
            "published": published,
            "liveVerified": verified,
            "health": health,
            "detail": (
                f"{accepted} prepared · {duplicate_removed} duplicates removed · "
                f"{uploaded} uploaded · {published} published · {verified} live-verified · "
                f"{queued_behind_current} queued behind current · "
                f"{parked_units} parked · {preflight_units} preflight"
                if current else
                f"Last completed: {last_published} published · {last_verified} live-verified · "
                f"{ready_units} ready · {parked_units} parked · {preflight_units} preflight"
            ),
            "queueReady": ready_units,
            "queueParked": parked_units,
            "queuePreflight": preflight_units,
            "batchQueue": batch_queue,
            "updatedAt": updated,
            "currentRoot": current_root,
            "mode": mode,
            "modeDetail": mode_detail,
            "batchNumber": batch_number,
            "successHistory": publisher_history,
            "progressSince": latest.get("completedAt") if not current else None,
            "codexLive": _count(
                _read_json(Path(current_root) / "promotion_validation.json").get(
                    "publishedLiveTotal"
                )
            )
            if current_root
            else 0,
            "progressEvidence": {
                "stage": stage,
                "currentRoot": current_root,
                "prepared": accepted,
                "duplicatesRemoved": duplicate_removed,
                "uploaded": uploaded,
                "published": published,
                "liveVerified": verified,
                "pendingUnits": pending_units,
                "parkedUnits": parked_units,
                "preflightUnits": preflight_units,
                "watcherCheckedAt": state.get("checkedAt", ""),
                "unitUpdatedAt": updated,
            },
        }

    def _metrics(self) -> Dict[str, Any]:
        value = self.config.get("metricsPath")
        if not value:
            return {}
        return _read_json(self._path(str(value)))

    def _receipt_live_total(self) -> int:
        """Return the monotonic total proven by permanent promotion receipts."""
        imports = self.project_root / "work/judah_library/imports"
        total = 0
        for path in imports.glob("*/promotion_validation.json"):
            receipt = _read_json(path)
            if receipt.get("status") in {
                    "published-and-five-gate-verified",
                    "already-live-and-five-gate-accounted"}:
                total = max(total, _count(receipt.get("publishedLiveTotal")))
        return total

    def _archived_source_history(self, active_ids: set[str]) -> List[Dict[str, Any]]:
        """Keep retired source receipts visible without reactivating their lanes."""
        definitions = (
            ("open-library", "Open Library", "open_library_"),
            ("library-of-congress", "Library of Congress", "library_of_congress_"),
            ("internet-archive", "Internet Archive", "internet_archive_"),
            ("global-grey-christianity", "Global Grey Ebooks", "global_grey_"),
        )
        archived: List[Dict[str, Any]] = []
        imports = self.project_root / "work/judah_library/imports"
        for lane_id, name, prefix in definitions:
            if lane_id in active_ids:
                continue
            history = self._source_success_history(
                lane_id, {"historyPrefix": prefix}
            )
            if not history:
                continue
            permanent_receipts = []
            for path in imports.glob(f"{prefix}*/promotion_validation.json"):
                receipt = _read_json(path)
                if receipt.get("status") in {
                        "published-and-five-gate-verified",
                        "already-live-and-five-gate-accounted"}:
                    permanent_receipts.append(receipt)
            archived.append({
                "id": lane_id,
                "name": name,
                "liveVerified": sum(
                    _count(item.get("liveVerified"))
                    for item in permanent_receipts
                ),
                "receiptCount": len(permanent_receipts),
                "successHistory": history,
            })
        return archived

    def status(self) -> Dict[str, Any]:
        # Configuration is operational state: lane visibility and batch
        # targets must change on the next authoritative UI refresh without a
        # controller restart. `_read_json` remains inode/mtime cached when the
        # file is unchanged, so the normal polling path stays inexpensive.
        refreshed_config = _read_json(self.config_path)
        if refreshed_config:
            self.config = refreshed_config
        lanes = [self._lane(item) for item in self.config.get("lanes", []) if isinstance(item, dict)]
        checked_at = datetime.now(timezone.utc)
        metrics = self._metrics()
        active_ids = {str(lane["id"]) for lane in lanes}
        archived_sources = self._archived_source_history(active_ids)
        self._progress_observations = {
            lane_id: observation
            for lane_id, observation in self._progress_observations.items()
            if lane_id in active_ids
        }
        self._accepted_growth_observations = {
            lane_id: observation
            for lane_id, observation in self._accepted_growth_observations.items()
            if lane_id in active_ids
        }
        for lane in lanes:
            evidence = lane.pop("progressEvidence", {})
            key = _progress_key(evidence)
            lane_id = str(lane["id"])
            observation = self._progress_observations.get(lane_id)
            if observation is None or observation["key"] != key:
                observation = {"key": key, "since": checked_at}
                self._progress_observations[lane_id] = observation
            lane["progressSince"] = observation["since"].isoformat().replace("+00:00", "Z")
            stage = str(lane.get("stage", "unknown"))
            stage_observation = self._stage_observations.get(lane_id)
            if stage_observation is None or stage_observation["stage"] != stage:
                supplied = _timestamp(lane.get("updatedAt"))
                stage_observation = {"stage": stage, "since": supplied or checked_at}
                self._stage_observations[lane_id] = stage_observation
            lane["stageSince"] = stage_observation["since"].isoformat().replace("+00:00", "Z")
            # Every configured source lane is judged by accepted growth, even
            # while it is handing survivors to staging. Upload activity is an
            # adjustment toward health; it is not acquisition health itself.
            if lane_id != "publisher":
                terminal_lane = (
                    str(lane.get("mode", "")).casefold() == "complete"
                    or any(marker in str(lane.get("stage", "")).casefold() for marker in (
                        "campaign-complete", "batch-complete", "source-exhausted",
                    ))
                )
                # Current-unit counters reset at every successful handoff. Lane
                # health must observe the monotonic authoritative acceptance
                # total or that rollover falsely looks like a regression to 0.
                accepted = _count(lane.get("authoritativeGrowthCount",
                                           lane.get("acceptedCumulative", lane.get("accepted"))))
                growth = self._accepted_growth_observations.get(lane_id)
                current_root = str(lane.get("currentRoot") or "")
                if growth is None:
                    active_empty_root = (
                        str(lane.get("mode", "")).casefold() in {
                            "acquisition", "discovery", "uploading",
                        }
                        and _count(lane.get("accepted")) == 0
                        and bool(current_root)
                    )
                    growth = {
                        "accepted": accepted,
                        "currentRoot": current_root,
                        "lastGrowth": (
                            _timestamp(lane.get("acceptedUpdatedAt"))
                            if accepted > 0 and not active_empty_root else None
                        ),
                        "observedSince": _timestamp(lane.get("updatedAt")) or checked_at,
                    }
                elif str(growth.get("currentRoot") or "") != current_root:
                    growth = {
                        "accepted": accepted,
                        "currentRoot": current_root,
                        "lastGrowth": None,
                        "observedSince": checked_at,
                    }
                elif accepted < _count(growth.get("accepted")):
                    # Quarantine/revocation is an integrity correction, not
                    # accepted-book growth. Preserve the last real increase so
                    # a freshly written rejection cannot turn health green.
                    growth = {"accepted": accepted,
                              "currentRoot": current_root,
                              "lastGrowth": growth.get("lastGrowth"),
                              "observedSince": growth.get("observedSince", checked_at)}
                elif accepted > _count(growth.get("accepted")):
                    growth = {"accepted": accepted, "currentRoot": current_root,
                              "lastGrowth": checked_at,
                              "observedSince": growth.get("observedSince", checked_at)}
                self._accepted_growth_observations[lane_id] = growth
                last_growth = growth.get("lastGrowth")
                lane["acceptedGrowthSince"] = (
                    last_growth.isoformat().replace("+00:00", "Z")
                    if isinstance(last_growth, datetime) else None
                )
                if terminal_lane or str(lane.get("mode", "")).casefold() == "queued":
                    lane["health"] = "healthy"
                elif lane.get("health") != "failed":
                    if not isinstance(last_growth, datetime):
                        observed_since = growth.get("observedSince")
                        no_growth_age = (
                            (checked_at - observed_since).total_seconds()
                            if isinstance(observed_since, datetime) else 0
                        )
                        lane["health"] = "watch" if no_growth_age <= 300 else "stuck"
                    else:
                        growth_age = (checked_at - last_growth).total_seconds()
                        lane["health"] = (
                            "healthy" if growth_age <= 300
                            else "watch" if growth_age <= 900
                            else "stuck"
                        )
        codex_live = _count(metrics.get("codexLive", self.config.get("codexLive", 0)))
        publisher_live = max((_count(lane.get("codexLive")) for lane in lanes), default=0)
        codex_live = max(codex_live, publisher_live, self._receipt_live_total())
        workspace = str(self.config.get("workspace", "")).strip() or "web_sweeper"
        return {
            "schemaVersion": 1,
            "workspace": workspace,
            "checkedAt": checked_at.isoformat().replace("+00:00", "Z"),
            "codexLive": codex_live,
            "confirmedStaged": _count(metrics.get("confirmedStaged")),
            "metricsCheckedAt": metrics.get("checkedAt", ""),
            "lanes": lanes,
            "archivedSources": archived_sources,
            "allHealthy": bool(lanes) and all(item["health"] == "healthy" for item in lanes),
            "productionWriterLimit": 1,
            "optimizationStandard": {
                "points": OPTIMIZATION_POINT_COUNT,
                "stages": len(OPTIMIZATION_STAGES),
                "controlsPerStage": len(OPTIMIZATION_CONTROLS),
                "integrityFirst": True,
            },
        }

    def action(self, action: str, lane_id: str) -> Dict[str, Any]:
        """Run only a command explicitly provided by the trusted host config."""
        if action == "push":
            return self._request_push(lane_id)
        actions = self.config.get("actions", {})
        definition = actions.get(action) if isinstance(actions, dict) else None
        if not isinstance(definition, dict):
            raise ValueError(f"action is disabled: {action}")
        allowed_lanes = definition.get("lanes", [])
        if allowed_lanes and lane_id not in allowed_lanes:
            raise ValueError(f"action {action} is not allowed for lane {lane_id}")
        command = definition.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
            raise ValueError(f"action has no trusted command: {action}")
        rendered = [part.replace("{lane}", lane_id) for part in command]
        process = subprocess.Popen(
            rendered,
            cwd=self.project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"accepted": True, "action": action, "lane": lane_id, "pid": process.pid}

    def _request_push(self, lane_id: str) -> Dict[str, Any]:
        """Freeze a positive survivor remainder for the normal staging path."""
        lane = next((item for item in self.config.get("lanes", [])
                     if isinstance(item, dict) and item.get("id") == lane_id), None)
        if not isinstance(lane, dict) or lane.get("kind") == "publisher":
            raise ValueError(f"push is not allowed for lane {lane_id}")
        state_path = self._path(str(lane.get("statePath", "missing.json")))
        state = _read_json(state_path)
        root_value = str(state.get("currentRoot") or state.get("root") or "")
        if not root_value:
            raise ValueError(f"lane {lane_id} has no protected current root")
        root = Path(root_value).resolve()
        imports = (self.project_root / "work/judah_library/imports").resolve()
        if imports not in root.parents:
            raise ValueError("push root is outside the authoritative imports directory")
        catalog = _read_json(root / "catalog.json").get("books", [])
        books = len(catalog) if isinstance(catalog, list) else 0
        if books < 1:
            books, _ = self._accepted_journal_count(root / "progress.jsonl")
        if books < 1:
            raise ValueError("push requires at least one authoritative accepted survivor")
        requested_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        request = {
            "schemaVersion": 1,
            "lane": lane_id,
            "root": str(root),
            "books": books,
            "requestedAt": requested_at,
            "action": "freeze-validate-and-stage-positive-remainder",
        }
        request_path = root / "operator_switch_request.json"
        temporary = request_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
        temporary.replace(request_path)

        handoff_path = self._path(str(self.config.get(
            "pushHandoffsPath",
            "work/judah_library/cache/web_sweeper_push_handoffs.json",
        )))
        ledger = _read_json(handoff_path)
        handoffs = ledger.get("handoffs", [])
        if not isinstance(handoffs, list):
            handoffs = []
        handoffs = [row for row in handoffs
                    if isinstance(row, dict) and str(row.get("root")) != str(root)]
        handoffs.append(request)
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_temporary = handoff_path.with_suffix(".json.tmp")
        ledger_temporary.write_text(json.dumps({
            "schemaVersion": 1,
            "updatedAt": requested_at,
            "handoffs": handoffs[-100:],
        }, indent=2) + "\n", encoding="utf-8")
        ledger_temporary.replace(handoff_path)

        pid = None
        if lane_id == "internet-archive":
            command = [
                "/usr/bin/python3", "-u",
                "tool/run_internet_archive_staging_campaign.py",
                "--start-unit", str(_count(state.get("currentUnit")) or _batch_number(root.name)),
                "--units", "1",
                "--target", str(_count(state.get("target")) or _count(lane.get("target")) or 2000),
                "--workers", "2",
                "--cache", str(state_path.parent),
                "--state", str(state_path),
                "--root-prefix", root.name.rsplit("_unit_", 1)[0],
                "--source-profile", str(state.get("sourceProfile") or "americana"),
                "--finalize-current-remainder",
            ]
            process = subprocess.Popen(
                command, cwd=self.project_root, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            pid = process.pid
        return {
            "accepted": True,
            "action": "push",
            "lane": lane_id,
            "books": books,
            "root": str(root),
            "status": "approved-handoff-preparing-for-staging",
            "pid": pid,
        }

    def save_preferences(self, preferences: Dict[str, Any]) -> None:
        """Atomically save harmless UI settings separately from host commands."""
        slot_count = max(1, min(10, int(preferences.get("sourceSlots", 2))))
        raw_models = preferences.get("models", [])
        models: List[Dict[str, Any]] = []
        if isinstance(raw_models, list):
            for index, raw in enumerate(raw_models[:slot_count], start=1):
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name", "")).strip()[:120]
                connector = str(raw.get("connector", "")).strip()[:1000]
                models.append({
                    "slot": index,
                    "name": name,
                    "connector": connector,
                    "batchTarget": max(1, min(100000, int(raw.get("batchTarget", 2000)))),
                    "uploadTarget": max(1, min(100000, int(raw.get("uploadTarget", 100)))),
                    "navigationQueries": [
                        str(value).strip()[:120]
                        for value in raw.get("navigationQueries", [])[:10]
                        if str(value).strip()
                    ] if isinstance(raw.get("navigationQueries", []), list) else [],
                })
        allowed = {
            "sourceSlots": slot_count,
            "batchTarget": max(1, min(100000, int(preferences.get("batchTarget", 2000)))),
            "uploadTarget": max(1, min(100000, int(preferences.get("uploadTarget", 100)))),
            "tertiaryEnabled": bool(preferences.get("tertiaryEnabled", False)),
            "models": models,
        }
        destination = self.config_path.with_name("controller.preferences.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=destination.name, dir=destination.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(allowed, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
