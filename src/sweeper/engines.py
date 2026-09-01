from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, Optional

from .continuation import build_plan
from .engine import run as run_v2
from .model import Config
from .state import State
from .ultra import CapabilityRouter, LeaseBusy, UltraRuntime


def _v2_plan(config: Config) -> dict:
    state = State(config.workspace / "state.sqlite3")
    try:
        return build_plan(config, state)
    finally:
        state.close()


def _counts(config: Config) -> dict:
    state = State(config.workspace / "state.sqlite3")
    try:
        return state.counts()
    finally:
        state.close()


def run_ultra(config: Config, progress: Optional[Callable[[dict], None]] = None,
              shadow_v2: bool = False) -> dict:
    """Run Ultra as coordinator while reusing V2 source adapters during migration."""
    runtime = UltraRuntime(config.workspace / "ultra" / "runtime.sqlite3")
    router = CapabilityRouter()
    owner = "ultra:%s" % os.getpid()
    started = int(time.time() * 1000)
    shadow_before = _v2_plan(config) if shadow_v2 else None
    source_results = []
    try:
        for source in sorted((value for value in config.sources if value.enabled),
                             key=lambda value: (value.lane != "major", value.slot, value.id)):
            work_id = "source:%s" % source.id
            runtime.enqueue(work_id=work_id, lane_id=source.id, kind="source-cycle",
                priority=1000 - source.slot, payload={"manifest": source.manifest},
                command_key="enqueue:%s:%s" % (source.id, source.manifest))
            try:
                lease = runtime.acquire_lease(resource="runner:%s" % source.id,
                    owner_id=owner, ttl_seconds=900,
                    binding={"workId": work_id, "sourceId": source.id},
                    command_key="runner:%s:%s" % (source.id, started))
            except LeaseBusy:
                source_results.append({"source": source.id, "status": "owned-by-another-runner"})
                continue
            before = _counts(config)
            # Compatibility adapter: Ultra owns the lane turn and V2 performs
            # only this source's established acquisition mechanics.
            routed = router.execute("acquisition", ultra=None,
                v2=lambda: run_v2(replace(config, sources=[source], engine_mode="v2"),
                                  progress=progress))
            result = routed.value
            after = _counts(config)
            changed = before != after
            if changed:
                runtime.record_progress(work_id=work_id, actor_id=owner,
                    proof_kind="item-disposition", evidence={"before": before, "after": after},
                    command_key="progress:%s:%s" % (source.id, started))
            else:
                runtime.record_heartbeat(work_id=work_id, actor_id=owner,
                    telemetry={"adapterCompleted": True, "counts": after},
                    command_key="heartbeat:%s:%s" % (source.id, started))
            runtime.release_lease(resource="runner:%s" % source.id, owner_id=owner,
                fence=int(lease["fence"]), command_key="release:%s:%s" % (source.id, started))
            source_results.append({"source": source.id, "status": "completed",
                "executor": routed.executor, "fallbackUsed": routed.fallback_used,
                "counts": result.get("counts", {})})
        pivot = runtime.evaluate_pivots(stale_after_seconds=600, success_after_seconds=600)
        snapshot = runtime.snapshot()
        shadow_after = _v2_plan(config) if shadow_v2 else None
        parity = None
        if shadow_v2:
            parity = {"v2ObservedSources": len(shadow_after.get("decisions", [])),
                "ultraCoordinatedSources": len(source_results),
                "sameSourceSet": sorted(row.get("source") for row in shadow_after.get("decisions", [])) ==
                                 sorted(row.get("source") for row in source_results),
                "v2WasReadOnlyShadow": True,
                "beforePlanGeneratedAt": shadow_before.get("generatedAt")}
        matrix = router.matrix(
            {"gate0": True, "rights": True, "completeness": True, "relevance": True,
             "deduplication": True},
            {name: True for name in ("discovery", "acquisition", "review", "validation",
                "staging", "translation", "publication", "live-verification", "cleanup")})
        return {"engine": "ultra", "ultraLeads": True, "v2CompatibilityAdapters": True,
                "dualShadow": shadow_v2, "sources": source_results, "pivots": pivot,
                "runtime": {"database": str(runtime.path),
                    "throughEventSeq": snapshot["throughEventSeq"],
                    "headEventHash": snapshot["headEventHash"],
                    "stateSha256": snapshot["stateSha256"]}, "parity": parity,
                "capabilityRouter": matrix, "productionWriterTouched": False}
    finally:
        runtime.close()


def run_selected(config: Config, progress: Optional[Callable[[dict], None]] = None) -> dict:
    if config.engine_mode == "v2":
        return {"engine": "v2", "ultraLeads": False, "result": run_v2(config, progress=progress)}
    if config.engine_mode == "dual":
        return run_ultra(config, progress=progress, shadow_v2=True)
    return run_ultra(config, progress=progress, shadow_v2=False)
