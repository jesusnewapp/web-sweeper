"""Receipt-bound, restart-safe transitions between configured source slots."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


MAX_SOURCE_SLOTS = 64


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_source_slots(config: dict) -> list[dict]:
    """Validate an ordered, configurable source pool.

    Slots describe policy and commands; this module never executes arbitrary
    commands. A deployment controller consumes the returned continuation plan
    only after recording the exact receipts required by ``evaluate_route``.
    """
    slots = config.get("source_slots")
    if slots is None:
        return []
    count = config.get("source_slot_count")
    if not isinstance(count, int) or not 1 <= count <= MAX_SOURCE_SLOTS:
        raise ValueError(f"source_slot_count must be an integer from 1 to {MAX_SOURCE_SLOTS}")
    if not isinstance(slots, list) or len(slots) != count:
        raise ValueError("source_slots length must exactly match source_slot_count")
    expected = list(range(1, count + 1))
    numbers = [slot.get("slot") for slot in slots]
    if numbers != expected:
        raise ValueError("source slots must be ordered consecutively from 1")
    identities = [str(slot.get("id", "")).strip() for slot in slots]
    if any(not identity for identity in identities) or len(set(identities)) != len(identities):
        raise ValueError("every source slot requires a unique non-empty id")
    for slot in slots:
        target = slot.get("acquisition_target")
        if not isinstance(target, int) or not 1 <= target <= 100_000:
            raise ValueError(f"{slot['id']}: acquisition_target must be from 1 to 100000")
        command = slot.get("launch_command") or slot.get("adapter_command")
        if not isinstance(command, list) or not command or not all(
                isinstance(part, str) and part.strip() for part in command):
            raise ValueError(f"{slot['id']}: a non-empty command array is required")
    return slots


def plan_slot_continuation(config: dict, current_slot: int, staged: int,
                           source_exhausted: bool) -> dict:
    """Return the deterministic next action after an exact staging receipt."""
    slots = validate_source_slots(config)
    if not slots:
        raise ValueError("source slot continuation requires source_slots")
    if not 1 <= current_slot <= len(slots):
        raise ValueError("current_slot is outside the configured source pool")
    if staged < 0:
        raise ValueError("staged cannot be negative")
    slot = slots[current_slot - 1]
    target = slot["acquisition_target"]
    if staged > target:
        raise ValueError("staged count exceeds the active slot target")
    if not source_exhausted:
        if staged != target:
            raise ValueError("a non-exhausted source may continue only after a full unit")
        return {"action": "restart-current-source", "slot": current_slot,
                "source": slot["id"], "stageRemainder": False}
    if staged and slot.get("allow_partial_on_exhaustion") is not True:
        raise ValueError("positive exhausted remainder is not allowed by this slot")
    if current_slot == len(slots):
        return {"action": "source-pool-complete", "slot": current_slot,
                "source": slot["id"], "stageRemainder": staged > 0}
    successor = slots[current_slot]
    return {"action": "advance-to-next-source", "slot": current_slot + 1,
            "source": successor["id"], "retiredSource": slot["id"],
            "stageRemainder": staged > 0}


def evaluate_throughput(policy: dict, accepted: int, elapsed_seconds: float) -> dict:
    """Normalize an observed window to 100 accepts and flag safe failover."""
    baseline = policy.get("baseline_seconds_per_100")
    multiplier = policy.get("slow_source_multiplier", 2.0)
    minimum = policy.get("minimum_accepted_sample", 100)
    if not isinstance(baseline, (int, float)) or baseline <= 0:
        raise ValueError("baseline_seconds_per_100 must be positive")
    if not isinstance(multiplier, (int, float)) or multiplier < 1:
        raise ValueError("slow_source_multiplier must be at least 1")
    if not isinstance(minimum, int) or minimum < 1:
        raise ValueError("minimum_accepted_sample must be a positive integer")
    if accepted < 0 or elapsed_seconds < 0:
        raise ValueError("throughput observations cannot be negative")
    threshold = float(baseline) * float(multiplier)
    if accepted < minimum:
        return {"status": "collecting-sample", "accepted": accepted,
                "minimumAcceptedSample": minimum,
                "thresholdSecondsPer100": threshold,
                "nextAction": "continue-current-source"}
    observed = float(elapsed_seconds) * 100.0 / accepted
    slow = observed > threshold
    return {"status": "slow-right-now" if slow else "within-marker",
            "accepted": accepted, "observedSecondsPer100": observed,
            "thresholdSecondsPer100": threshold,
            "nextAction": ("transition-at-next-safe-receipt-boundary" if slow
                           else "continue-current-source")}


def evaluate_positive_remainder_idle(accepted: int, target: int,
                                     idle_seconds: float,
                                     idle_limit_seconds: float = 900.0,
                                     operator_switch: bool = False) -> dict:
    """Join stalled discovery to the normal receipt-bound staging route.

    This decision is deliberately a stickman: it may stop discovery and
    preserve an atomic positive remainder, but it cannot validate, stage,
    publish, or bypass any integrity boundary by itself.
    """
    if accepted < 0 or target < 1 or accepted > target:
        raise ValueError("accepted and target are out of range")
    if idle_seconds < 0 or idle_limit_seconds < 1:
        raise ValueError("idle durations are out of range")
    positive = accepted > 0
    automatic = positive and idle_seconds >= idle_limit_seconds
    freeze = positive and (operator_switch or automatic)
    return {
        "accepted": accepted,
        "target": target,
        "nurtureScorePercent": round(min(100.0, accepted * 100.0 / target), 2),
        "idleSeconds": float(idle_seconds),
        "idleLimitSeconds": float(idle_limit_seconds),
        "operatorSwitch": bool(operator_switch),
        "freezePositiveRemainder": freeze,
        "reason": ("operator-switch" if operator_switch and positive else
                   "idle-positive-remainder" if automatic else
                   "continue-discovery"),
        "nextAction": ("stop-child-preserve-checkpoint-run-normal-validation-staging"
                       if freeze else "continue-discovery"),
        "neverBypasses": ["exact-staging-membership", "duplicate-screening",
                          "single-production-writer", "live-verification"],
    }


def evaluate_route(route: dict, base: Path) -> dict:
    """Evaluate one transition without starting or stopping a process."""
    required = ("from", "to", "completion_receipt", "cleanup_receipt", "checkpoint")
    missing = [key for key in required if not str(route.get(key, "")).strip()]
    if missing:
        raise ValueError(f"transition route is missing {', '.join(missing)}")
    paths = {key: (base / str(route[key])).resolve()
             for key in ("completion_receipt", "cleanup_receipt", "checkpoint")}
    absent = [key for key, path in paths.items() if not path.is_file()]
    status = "waiting-for-current-unit" if absent else "ready-to-transition"
    row = {"from": route["from"], "to": route["to"], "status": status,
           "missingEvidence": absent, "evaluatedAt": now(),
           "commandsArePlaceholders": bool(route.get("commands_are_placeholders", True))}
    if absent:
        return row
    completion = json.loads(paths["completion_receipt"].read_text(encoding="utf-8"))
    cleanup = json.loads(paths["cleanup_receipt"].read_text(encoding="utf-8"))
    staged = int(completion.get("staged", 0) or 0)
    target = int(route.get("target", 1000) or 1000)
    source_exhausted = completion.get("sourceExhausted") is True
    if staged < 1 or completion.get("productionMutated") is not False:
        raise ValueError(f"{route['from']}: completion receipt is not exact isolated staging")
    if staged > target:
        raise ValueError(f"{route['from']}: staged count exceeds the configured target")
    if staged < target and not (route.get("allow_partial_on_exhaustion") is True and
                                source_exhausted):
        raise ValueError(
            f"{route['from']}: partial unit requires an exact source-exhausted receipt"
        )
    if cleanup.get("status") not in {"complete", "completed", "source-cache-cleanup-complete"}:
        # Public adapters may use different receipt schemas. Exact deleted keys
        # or a non-negative reclaimed-byte count is also acceptable evidence.
        deleted = cleanup.get("deleted") or cleanup.get("deletedFiles")
        reclaimed = cleanup.get("bytesReclaimed", cleanup.get("deletedBytes"))
        if not isinstance(deleted, list) and not isinstance(reclaimed, int):
            raise ValueError(f"{route['from']}: cleanup receipt is incomplete")
    row.update({"staged": staged, "target": target,
                "sourceExhausted": source_exhausted,
                "partialUnit": staged < target,
                "evidence": {key: {"path": str(path), "sha256": sha256(path)}
                             for key, path in paths.items()},
                "nextAction": ("replace-placeholder-commands" if row["commandsArePlaceholders"]
                               else "stop-old-confirm-dead-start-successor")})
    return row


def evaluate(config_path: Path) -> dict:
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    slots = validate_source_slots(config)
    routes = config.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValueError("transition config requires at least one route")
    identities = [str(route.get("from", "")) for route in routes]
    successors = [str(route.get("to", "")) for route in routes]
    if len(identities) != len(set(identities)) or len(successors) != len(set(successors)):
        raise ValueError("each source and successor may appear in only one transition route")
    rows = [evaluate_route(route, config_path.parent) for route in routes]
    return {"schemaVersion": 2, "model": "receipt-bound-source-transition",
            "evaluatedAt": now(), "practiceMode": bool(config.get("practice_mode", True)),
            "sourceSlotCount": len(slots),
            "sourceSlots": [{"slot": slot["slot"], "id": slot["id"],
                             "acquisitionTarget": slot["acquisition_target"]}
                            for slot in slots],
            "routes": rows,
            "ready": sum(row["status"] == "ready-to-transition" for row in rows),
            "waiting": sum(row["status"] != "ready-to-transition" for row in rows),
            "invariants": ["finish-current-unit", "exact-staging-receipt",
                           "cleanup-receipt", "checkpoint-preserved",
                           "old-source-confirmed-inactive", "one-successor-only",
                           "durable-transition-receipt"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.config)
    if args.output:
        atomic_json(args.output.resolve(), result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
