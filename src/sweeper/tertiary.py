"""Powerless observations and an explicitly separate optional adapter boundary.

The observation field cannot execute, route, gate, restart, stage, or publish.
An adapter may read the field only when separately enabled; authority and action
remain properties of the host coordinator that consumes the adapter output.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .model import Config
from .state import State


# Strength is descriptive, not executable. Integrity boundaries deliberately
# remain non-overcomable regardless of the measured Nurture intensity.
BLOCKER_SCALE = {
    "poll-wait": {"strength": 5, "class": "continuity-friction"},
    "retry-delay": {"strength": 10, "class": "continuity-friction"},
    "rollover-wait": {"strength": 20, "class": "continuity-friction"},
    "expired-gate0-snapshot-mid-unit": {"strength": 25, "class": "continuity-friction"},
    "exact-target-shortfall-with-positive-survivors": {"strength": 40,
                                                        "class": "continuity-friction"},
    "configured-frontier-depleted-with-positive-survivors": {
        "strength": 50, "class": "continuity-friction"},
    "redundant-validation-of-unchanged-passing-membership": {
        "strength": 60, "class": "continuity-friction"},
    "capacity-gate": {"strength": 85, "class": "integrity-boundary"},
    "validation-failure": {"strength": 100, "class": "integrity-boundary"},
    "duplicate-evidence": {"strength": 100, "class": "integrity-boundary"},
    "rights-failure": {"strength": 100, "class": "integrity-boundary"},
    "source-hash-mismatch": {"strength": 100, "class": "integrity-boundary"},
    "content-incomplete": {"strength": 100, "class": "integrity-boundary"},
    "writer-lease-conflict": {"strength": 100, "class": "integrity-boundary"},
    "live-verification-failure": {"strength": 100, "class": "integrity-boundary"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def nurture_intensity(members: int) -> float:
    """Return a neutral, bounded observation using the studio's first anchors."""
    anchors = ((0, 0.0), (50, 10.0), (100, 20.0), (1000, 50.0),
               (2000, 75.0), (10000, 100.0))
    members = max(0, int(members))
    for (left_n, left_p), (right_n, right_p) in zip(anchors, anchors[1:]):
        if members <= right_n:
            span = right_n - left_n
            return round(left_p + (members - left_n) * (right_p - left_p) / span, 2)
    return 100.0


def measure_blocker(kind: str, nurture_percent: float) -> dict:
    """Compare two measurements without recommending or executing an action."""
    definition = BLOCKER_SCALE.get(kind, {"strength": 100,
                                          "class": "unknown-fail-closed"})
    strength = int(definition["strength"])
    boundary = definition["class"] != "continuity-friction"
    nurture_percent = max(0.0, min(100.0, float(nurture_percent)))
    return {"kind": kind, "class": definition["class"],
            "strengthPercent": strength, "nurturePercent": nurture_percent,
            "nurtureMeetsMeasuredStrength": nurture_percent >= strength,
            "continuityOvercomeEligible": not boundary,
            "integrityBoundary": boundary, "measurementOnly": True,
            "tertiaryAuthority": "none"}


def blocker_field(config: Config) -> list[dict]:
    """Read optional blocker observations supplied by the host, without acting."""
    path = config.workspace / "tertiary-blockers.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    measured = []
    for row in raw:
        if not isinstance(row, dict) or not str(row.get("kind", "")).strip():
            continue
        measured.append({**measure_blocker(str(row["kind"]), row.get("nurturePercent", 0)),
                         "evidence": row.get("evidence")})
    return measured


def observe(config: Config) -> dict:
    """Measure lane context without returning decisions or actions."""
    if not config.tertiary.enabled:
        return {"schemaVersion": 1, "mode": "tertiary", "enabled": False,
                "authority": "none", "executionCoupling": False,
                "observations": []}
    state = State(config.workspace / "state.sqlite3")
    try:
        rows = []
        for source in config.sources:
            if not source.enabled:
                continue
            counts = state.source_counts(source.id)
            accepted = int(counts.get("accepted", 0))
            target = int(source.target_items)
            ratio = accepted / target if target > 0 else None
            signals = {}
            if "nurture" in config.tertiary.signals:
                signals["nurture"] = {
                    "acceptedMembership": accepted,
                    "targetMembership": target,
                    "completionRatio": ratio,
                    "thresholdObserved": accepted >= config.nurture_threshold,
                    "intensityPercent": nurture_intensity(accepted),
                    "measurementOnly": True,
                }
            if "pivot" in config.tertiary.signals:
                signals["pivot"] = {
                    "failedCount": int(counts.get("failed", 0)),
                    "duplicateCount": int(counts.get("duplicate", 0)),
                    "unresolvedTargetCount": max(0, target - accepted) if target else None,
                }
            if "continuation" in config.tertiary.signals:
                signals["continuation"] = {
                    "acceptedCount": accepted,
                    "terminalCount": sum(int(counts.get(key, 0))
                                         for key in ("accepted", "rejected", "duplicate")),
                    "failedCount": int(counts.get("failed", 0)),
                    "targetRemainder": max(0, target - accepted) if target else None,
                }
            rows.append({"lane": source.id, "counts": counts, "target": target,
                         "completionRatio": ratio, "signals": signals})
    finally:
        state.close()
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    payload = {
        "schemaVersion": 1, "mode": "tertiary", "enabled": True,
        "observedAt": _now(), "authority": "none", "advisory": False,
        "executionCoupling": False, "optionalRead": True,
        "canOpenGate": False, "canCloseGate": False,
        "canSelectRoute": False, "canStartOrStopProcess": False,
        "blockerScale": BLOCKER_SCALE,
        "blockers": blocker_field(config),
        "observationSha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "observations": rows,
    }
    _atomic_json(config.workspace / "tertiary-observations.json", payload)
    return payload


def inquisitive_read(config: Config) -> dict:
    """Expose the field to an optional reader without affecting execution."""
    if not config.tertiary.enabled or not config.tertiary.inquisitive_enabled:
        return {"schemaVersion": 1, "mode": "inquisitive", "available": False,
                "reason": "tertiary-or-inquisitive-toggle-off",
                "authority": "none", "executionCoupling": False}
    path = config.workspace / "tertiary-observations.json"
    try:
        field = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        field = observe(config)
    return {"schemaVersion": 1, "mode": "inquisitive", "available": True,
            "readAt": _now(), "authority": "none", "executionCoupling": False,
            "optional": True, "field": field}


def adapter_view(config: Config) -> dict:
    """Return neutral inputs to a host controller; never execute the controller."""
    if not config.tertiary.adapter_enabled:
        return {"schemaVersion": 1, "mode": "tertiary-adapter",
                "attached": False, "reason": "adapter-toggle-off",
                "adapterExecutesActions": False}
    reading = inquisitive_read(config)
    field = reading.get("field", {}) if isinstance(reading, dict) else {}
    permissions = []
    for blocker in field.get("blockers", []) if isinstance(field, dict) else []:
        if (blocker.get("continuityOvercomeEligible") is True and
                blocker.get("nurtureMeetsMeasuredStrength") is True):
            permissions.append({
                "blocker": blocker.get("kind"),
                "hostMayChoose": True,
                "boundedMoves": ["preserve-passing-survivors",
                                 "quarantine-individual-failures",
                                 "stage-positive-remainder",
                                 "resume-or-rollover-from-checkpoint"],
                "adapterExecuted": False,
            })
    return {"schemaVersion": 1, "mode": "tertiary-adapter",
            "attached": bool(reading.get("available")),
            "adapterExecutesActions": False,
            "hostRetainsDecisionAuthority": True,
            "continuityPermissions": permissions,
            "continuityScope": ["preserve-passing-survivors", "quarantine-failing-items",
                                "stage-positive-remainder", "resume-from-checkpoint"],
            "stagingToLiveBoundary": {
                "reuse": ["hash-bound-acquisition-attestation"],
                "freshChecks": ["live-duplicate-delta", "deployment-live-verification"],
                "redundantChecksAreFriction": [
                    "repeat-rights-validation-on-unchanged-membership",
                    "repeat-content-validation-on-unchanged-membership"],
            },
            "neverOverrides": ["rights", "source-hash", "content-completeness",
                               "duplicate-isolation", "writer-serialization",
                               "live-verification"],
            "input": reading}
