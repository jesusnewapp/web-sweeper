from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .dock import atomic_json
from .model import Config
from .state import State
from .translation_fleet import TranslationFleet


PIVOT_SECONDS = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def lane_observations(config: Config) -> list[dict]:
    state = State(config.workspace / "state.sqlite3")
    try:
        rows = []
        for source in config.sources:
            if not source.enabled:
                continue
            counts = state.source_counts(source.id)
            accepted = int(counts.get("accepted", 0))
            required = bool(counts.get("failed", 0) or
                            (source.target_items and accepted < source.target_items))
            rows.append({"lane": source.id, "kind": "source", "required": required,
                         "counts": counts, "target": source.target_items})
    finally:
        state.close()
    if config.translation.enabled:
        fleet = TranslationFleet(config)
        try: translation = fleet.status()
        finally: fleet.close()
        counts = translation.get("counts", {})
        rows.append({"lane": "translator", "kind": "translation",
                     "required": bool(counts.get("queued", 0) or counts.get("failed", 0)),
                     "counts": counts, "targetLanguages": config.translation.target_languages})
    return rows


def evaluate(workspace: Path, observations: list[dict], current_epoch: Optional[float] = None) -> dict:
    current_epoch = time.time() if current_epoch is None else current_epoch
    path = workspace / "pivot-enforcer.json"
    previous = _read(path)
    old = previous.get("lanes", {}) if isinstance(previous.get("lanes"), dict) else {}
    payload = {"schemaVersion": 1, "checkedAt": _now(), "pivotDeadlineSeconds": PIVOT_SECONDS,
               "doesNotChoosePivot": True, "lanes": {}, "overdue": []}
    for observation in observations:
        lane = str(observation["lane"])
        token = hashlib.sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest()
        prior = old.get(lane, {})
        required = bool(observation.get("required"))
        first = (float(prior.get("firstRequiredEpoch", current_epoch))
                 if required and prior.get("observationToken") == token else current_epoch)
        elapsed = max(0.0, current_epoch - first) if required else 0.0
        overdue = required and elapsed >= PIVOT_SECONDS
        row = {"kind": observation.get("kind"), "pivotRequired": required,
               "observationToken": token, "firstRequiredEpoch": first,
               "requiredForSeconds": round(elapsed, 1), "deadlineSeconds": PIVOT_SECONDS,
               "status": "pivot-overdue" if overdue else "awaiting-lane-pivot" if required else "progressing",
               "pivotChoice": "lane-owned", "enforcerChoosesPivot": False}
        if overdue:
            payload["overdue"].append(lane)
        payload["lanes"][lane] = row
    payload["enforcementRequired"] = bool(payload["overdue"])
    atomic_json(path, payload)
    return payload


def enforce(config: Config, current_epoch: Optional[float] = None) -> dict:
    return evaluate(config.workspace, lane_observations(config), current_epoch)
