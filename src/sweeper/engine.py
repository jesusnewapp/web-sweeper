from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import urllib.parse
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .manifest import candidates
from .continuation import build_plan, prioritized_sources
from .model import Candidate, Config, Policy, Source
from .state import State
from .nurture import preserve
from .activity import record as activity_record
from .frontier import FrontierRetirement


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def policy_reason(item: Candidate, policy: Policy) -> str:
    if policy.require_language and not item.language:
        return "missing-language"
    if policy.languages and item.language.lower() not in {value.lower() for value in policy.languages}:
        return "language-not-allowed"
    if policy.require_license and not item.license:
        return "missing-license"
    if policy.licenses and item.license.lower() not in {value.lower() for value in policy.licenses}:
        return "license-not-allowed"
    if policy.require_rights_evidence and not item.rights_evidence_url:
        return "missing-rights-evidence"
    allowed_media = {value.lower() for value in policy.media_types}
    media_type = item.media_type.lower()
    if allowed_media and not any(
            rule == media_type or (rule.endswith("/*") and media_type.startswith(rule[:-1]))
            for rule in allowed_media):
        return "media-type-not-allowed"
    if policy.artifact_classes and item.artifact_class.lower() not in {value.lower() for value in policy.artifact_classes}:
        return "artifact-class-not-allowed"
    if policy.data_classes and item.data_class.lower() not in {value.lower() for value in policy.data_classes}:
        return "data-class-not-allowed"
    missing = [field for field in policy.required_metadata_fields
               if not str(item.metadata.get(field, "")).strip()]
    if missing:
        return "missing-metadata:" + ",".join(sorted(missing))
    if policy.require_expected_sha256 and not re_full_sha256(str(item.metadata.get("expected_sha256", ""))):
        return "missing-or-invalid-expected-sha256"
    if policy.allowed_file_extensions:
        extension = Path(urllib.request.url2pathname(urllib.parse.urlparse(item.url).path)).suffix.casefold()
        if extension not in {value.casefold() if value.startswith(".") else "."+value.casefold()
                             for value in policy.allowed_file_extensions}:
            return "file-extension-not-allowed"
    return ""


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)


def verify_download(path: Path, item: Candidate, policy: Policy, digest: str) -> str:
    expected = str(item.metadata.get("expected_sha256", "")).casefold()
    if expected and (not re_full_sha256(expected) or expected != digest.casefold()):
        return "expected-sha256-mismatch"
    if policy.verify_zip_integrity and zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if not names: return "empty-zip-archive"
                if any(name.startswith(("/", "\\")) or ".." in Path(name).parts for name in names):
                    return "unsafe-zip-member-path"
                if archive.testzip() is not None: return "zip-crc-failure"
        except (OSError, zipfile.BadZipFile):
            return "invalid-zip-archive"
    return ""


def review(item: Candidate, path: Path, policy: Policy) -> tuple[bool, str]:
    if not policy.reviewer_command:
        return True, ""
    payload = {"candidate": item.__dict__, "local_path": str(path)}
    result = subprocess.run(policy.reviewer_command, input=json.dumps(payload), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if result.returncode:
        return False, f"reviewer-exit-{result.returncode}"
    try:
        decision = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "reviewer-invalid-json"
    return decision.get("accepted") is True, str(decision.get("reason", "reviewer-rejected"))


def retrieve(item: Candidate, source: Source, config: Config, state: State) -> None:
    early = policy_reason(item, config.policy)
    stamp = now()
    common = dict(source_id=item.source_id, item_id=item.item_id, url=item.url, title=item.title, updated_at=stamp)
    if early:
        state.record(**common, status="rejected", reason=early)
        return
    request = urllib.request.Request(item.url, headers={"User-Agent": config.user_agent, **source.headers})
    store = config.workspace / "objects"
    store.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    fd, temporary_name = tempfile.mkstemp(prefix="sweeper-", dir=str(config.workspace))
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(request, timeout=90) as response:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if config.policy.maximum_bytes is not None and size > config.policy.maximum_bytes:
                    state.record(**common, status="rejected", reason="maximum-bytes-exceeded", size=size)
                    return
                digest.update(block)
                output.write(block)
        if size < config.policy.minimum_bytes:
            state.record(**common, status="rejected", reason="below-minimum-bytes", size=size)
            return
        hexdigest = digest.hexdigest()
        verification_error = verify_download(Path(temporary_name), item, config.policy, hexdigest)
        if verification_error:
            state.record(**common, status="rejected", reason=verification_error,
                         digest=hexdigest, size=size)
            return
        owner = state.hash_owner(hexdigest)
        if owner:
            state.record(**common, status="duplicate", reason=f"content-match:{owner}", digest=hexdigest, size=size)
            return
        destination = store / hexdigest[:2] / hexdigest
        destination.parent.mkdir(parents=True, exist_ok=True)
        Path(temporary_name).replace(destination)
        accepted, reason = review(item, destination, config.policy)
        if not accepted:
            state.record(**common, status="rejected", reason=reason, digest=hexdigest,
                         size=size, local_path=str(destination))
            return
        state.record(**common, status="accepted", digest=hexdigest, size=size, local_path=str(destination))
    except urllib.error.HTTPError as error:
        if error.code in {401, 403, 407}:
            state.record(**common, status="rejected", reason=f"access-required-http-{error.code}")
        else:
            state.record(**common, status="failed", reason=f"HTTPError:{error.code}")
    except Exception as error:
        state.record(**common, status="failed", reason=f"{type(error).__name__}:{error}")
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()


def run(config: Config, progress: Optional[Callable[[dict], None]] = None) -> dict:
    config.workspace.mkdir(parents=True, exist_ok=True)
    activity_record(config.workspace,"cycle-start",detail={"project":config.project_name})
    state = State(config.workspace / "state.sqlite3")
    frontiers = FrontierRetirement(config.workspace)
    source_errors = []
    continuation = []
    batch_transitions = []
    frontier_advances = []
    breathing = []
    try:
        # The planner reorders only whole source turns. It never changes item policy,
        # accepted membership, or an in-progress manifest checkpoint.
        ordered = prioritized_sources(config, state)
        for source in ordered:
            accepted_at_cycle_start = state.accepted_count(source.id)
            batch_complete = False
            activity_record(config.workspace, "source-batch-start", lane=source.id,
                status="working", detail={"batchSize": source.batch_size,
                    "acceptedAtStart": accepted_at_cycle_start,
                    "productionWriterTouched": False})
            base_delay = 1.0 / source.requests_per_second
            active_delay = base_delay
            if progress:
                progress({"phase": "source", "source": source.id})
            manifests = [source.manifest, *source.continuation_manifests]
            for manifest_index, manifest in enumerate(manifests):
                if frontiers.is_retired(source.id, manifest):
                    activity_record(config.workspace, "source-frontier-retired-skip",
                        lane=source.id, status="rotate-next-frontier", detail={
                            "manifest": manifest, "continuationManifest": manifest_index,
                            "acceptedArtifactsChanged": False})
                    continue
                active_source = replace(source, manifest=manifest)
                frontier_exhausted = False
                try:
                    for item in candidates(active_source, config.user_agent):
                        if state.status(item.source_id, item.item_id) in {"accepted", "rejected", "duplicate"}:
                            continue
                        if progress:
                            progress({"phase": "item", "source": source.id, "item": item.item_id,
                                      "continuationManifest": manifest_index})
                        retrieve(item, active_source, config, state)
                        outcome = state.status(item.source_id, item.item_id)
                        accepted_this_batch = (
                            state.accepted_count(source.id) - accepted_at_cycle_start
                        )
                        if outcome == "failed":
                            active_delay = min(base_delay * 8, active_delay * 1.5)
                            mode = "exhale-reduce-pressure"
                        else:
                            active_delay = max(base_delay, active_delay * 0.9)
                            mode = "inhale-normal-pressure"
                        breathing.append({"source": source.id, "mode": mode,
                            "delaySeconds": round(active_delay, 3), "outcome": outcome,
                            "integrityGatesChanged": False})
                        if len(breathing) > 100: del breathing[:-100]
                        time.sleep(active_delay)
                        if accepted_this_batch >= source.batch_size:
                            batch_complete = True
                            break
                    else:
                        frontier_exhausted = True
                except Exception as error:
                    source_errors.append({"source": source.id, "manifest": manifest,
                        "error": f"{type(error).__name__}: {error}"})
                    activity_record(config.workspace,"source-failure-bookkept",lane=source.id,
                        status="continuing",detail=source_errors[-1])
                    if progress:
                        progress({"phase": "source-error", "source": source.id,
                                  "manifest": manifest, "error": source_errors[-1]["error"]})
                    continue
                if frontier_exhausted:
                    retired = frontiers.retire(source.id, manifest)
                    rotation = frontiers.rotation_status(source.id, manifests)
                    frontier_advances.append({
                        "source": source.id, "exhaustedFrontierIndex": manifest_index,
                        "exhaustedFrontier": manifest,
                        "acceptedSurvivors": state.accepted_count(source.id) - accepted_at_cycle_start,
                        "action": ("advance-next-configured-frontier"
                                   if not rotation["sourceFrontierExhausted"]
                                   else "handoff-survivors-and-discover-next-frontier"),
                        **rotation,
                    })
                    activity_record(config.workspace, "source-frontier-retired",
                        lane=source.id, status="rotate-next-frontier", detail={
                            **retired, "continuationManifest": manifest_index,
                            "nextAction": frontier_advances[-1]["action"],
                            "sourceFrontierExhausted": rotation["sourceFrontierExhausted"]})
                if batch_complete:
                    break
                if source.target_items and state.accepted_count(source.id) >= source.target_items:
                    break
            accepted = state.accepted_count(source.id)
            accepted_this_batch = accepted - accepted_at_cycle_start
            rotation = frontiers.rotation_status(source.id, manifests)
            target_reached = bool(source.target_items and accepted >= source.target_items)
            close_reason = ("accepted-target-filled" if batch_complete else
                            "configured-source-frontier-exhausted"
                            if rotation["sourceFrontierExhausted"] else
                            "operator-target-reached" if target_reached else
                            "continuation-frontier-available")
            batch_transitions.append({
                "source": source.id, "requested": source.batch_size,
                "acceptedSurvivors": accepted_this_batch,
                "closeReason": close_reason,
                "handoffAction": "stage-exact-valid-survivors" if accepted_this_batch else "record-zero-survivor-unit",
                "nextAction": ("immediate-next-batch-from-checkpoint" if batch_complete else
                               "advance-next-configured-frontier" if rotation["nextFrontier"] else
                               "discover-or-configure-next-frontier"),
                "automaticAdvance": True,
                **rotation,
            })
            activity_record(config.workspace, "source-batch-complete", lane=source.id,
                status="advance-next-batch" if batch_complete else "source-frontier-reached",
                detail={"batchSize": source.batch_size,
                    "acceptedThisBatch": accepted_this_batch,
                    "acceptedTotal": accepted,
                    "batchFilled": batch_complete,
                    "closeReason": close_reason,
                    "nextAction": batch_transitions[-1]["nextAction"],
                    "productionWriterTouched": False})
            if source.target_items and accepted < source.target_items:
                continuation.append({"source": source.id, "accepted": accepted,
                    "target": source.target_items, "deficit": source.target_items - accepted,
                    "nextAction": "add-or-discover-continuation-manifest"})
        plan = build_plan(config, state)
        plan_path = config.workspace / "continuation-plan.json"
        temporary = plan_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        temporary.replace(plan_path)
        history_path = config.workspace / "forecast-history.json"
        try:
            history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
            if not isinstance(history, list):
                history = []
        except (OSError, ValueError):
            history = []
        forecast = plan["project"]["forecast"]
        snapshot = {
            "calculatedAt": forecast["calculatedAt"],
            "status": forecast["status"], "maturity": forecast["maturity"],
            "observationDays": forecast["observationDays"],
            "estimatedHighQualityEligibleItems": forecast["estimatedHighQualityEligibleItems"],
            "estimatedDailyHighQualityItems": forecast["estimatedDailyHighQualityItems"],
            "estimatedOverallGoalCoveragePercent": forecast["estimatedOverallGoalCoveragePercent"],
            "estimatedDailyGoalCoveragePercent": forecast["estimatedDailyGoalCoveragePercent"],
            "depletion": plan["sourceIntelligence"]["depletion"]["assessment"],
        }
        comparison = {key: value for key, value in snapshot.items() if key != "calculatedAt"}
        previous = ({key: value for key, value in history[-1].items() if key != "calculatedAt"}
                    if history else None)
        if comparison != previous:
            history.append(snapshot)
            history = history[-365:]
            history_tmp = history_path.with_suffix(".tmp")
            history_tmp.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
            history_tmp.replace(history_path)
        accepted_items=state.accepted_items()
        nurture=preserve(config.workspace,
            {f"{item['source_id']}:{item['item_id']}":str(item['sha256']) for item in accepted_items},
            "accepted",config.nurture_threshold) if accepted_items else {"active":False,"members":0,
                "threshold":config.nurture_threshold}
        result={"completedAt": now(), "counts": state.counts(), "workspace": str(config.workspace),
                "batchCeilingItems": 1_000,
                "sweeperBlocked": False,
                "failureDisposition": "bookkeep-item-or-source-and-continue",
                "sourceErrors": source_errors, "continuation": continuation,
                "continuationRequired": bool(continuation), "breathing": breathing,
                "batchTransitions": batch_transitions,
                "frontierAdvances": frontier_advances,
                "continuationPlan": str(plan_path), "forecastHistory": str(history_path),
                "nurture":nurture}
        activity_record(config.workspace,"cycle-complete",status="continuing",detail={
            "counts":result["counts"],"sourceErrors":len(source_errors),"nurture":nurture})
        return result
    finally:
        state.close()
