from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .model import Config, Source
from .state import State
from .intelligence import source_intelligence


DEFAULT_POOL = (
    "continue-current-manifest",
    "advance-continuation-manifest",
    "retry-transient-failures",
    "switch-to-next-ready-source",
    "reduce-request-pressure",
    "restore-normal-request-pressure",
    "narrow-next-cycle-scope",
    "broaden-next-cycle-scope",
    "checkpoint-and-yield-slot",
    "resume-from-checkpoint",
    "refresh-source-manifest",
    "advance-source-cursor",
    "discover-replacement-source",
    "rotate-to-compatible-source",
    "reuse-verified-object-cache",
    "retry-review-helper",
    "quarantine-failed-members",
    "rebind-survivor-membership",
    "revalidate-changed-survivors",
    "resume-verification-only",
    "refresh-live-delta",
    "recover-stale-writer-lease",
    "advance-next-ready-unit",
    "cleanup-live-verified-staging",
    "switch-compatible-media-representation",
    "advance-media-collection-cursor",
    "refresh-expired-download-location",
    "resume-range-capable-transfer",
    "reduce-large-object-concurrency",
    "verify-container-and-codec-metadata",
    "verify-comic-page-order-and-completeness",
    "verify-audio-duration-and-container",
    "verify-video-duration-and-container",
    "quarantine-unsupported-media-variant",
    "recheck-item-rights-evidence",
    "rotate-to-rights-compatible-collection",
    "skip-authentication-required-source",
    "advance-next-public-download-manifest",
)


def mandatory_reload(last_receipted_unit: int, requested_unit: int = 1,
                     *, live_owner: bool = False,
                     shared_integrity_ok: bool = True,
                     capacity_available: bool = True) -> dict:
    """Choose the receipt-safe next unit without trusting disposable files.

    Cache snapshots, candidate slices, cursors, and download memory are never
    inputs to this decision. Their absence selects dynamic discovery after the
    launch; it cannot prevent the launch itself.
    """
    if last_receipted_unit < 0 or requested_unit < 1:
        raise ValueError("unit numbers are out of range")
    next_unit = max(requested_unit, last_receipted_unit + 1)
    safety_holds = []
    if live_owner:
        safety_holds.append("live-owner")
    if not shared_integrity_ok:
        safety_holds.append("shared-integrity")
    if not capacity_available:
        safety_holds.append("capacity-floor")
    return {
        "action": "adopt-live-owner" if live_owner else
                  "preserve-and-retry" if safety_holds else
                  "mandatory-reload",
        "nextUnit": next_unit,
        "discoveryFallback": "dynamic-discovery",
        "disposableFilesMayVetoReload": False,
        "safetyHolds": safety_holds,
    }


def _score(action: str, source: Source, counts: dict, peer_stages: dict) -> tuple[int, list[str]]:
    accepted = int(counts.get("accepted", 0)); failed = int(counts.get("failed", 0))
    deficit = max(0, source.target_items - accepted) if source.target_items else 0
    score = 30; reasons: list[str] = []
    if action == "continue-current-manifest":
        score += 20; reasons.append("preserves the active source checkpoint")
    if action == "advance-continuation-manifest" and deficit:
        score += 28; reasons.append(f"target deficit is {deficit}")
    if action == "retry-transient-failures" and failed:
        score += min(30, failed * 3); reasons.append(f"{failed} retryable failures")
    if action == "switch-to-next-ready-source" and failed > accepted:
        score += 24; reasons.append("another source can progress while this source recovers")
    if action == "reduce-request-pressure" and failed:
        score += 18; reasons.append("failure pressure detected")
    if action == "restore-normal-request-pressure" and accepted and not failed:
        score += 18; reasons.append("healthy yield supports normal pressure")
    if action == "checkpoint-and-yield-slot" and peer_stages.get(source.lane, 0) > 1:
        score += 12; reasons.append("reduces same-lane contention")
    if action == "broaden-next-cycle-scope" and deficit and not failed:
        score += 14; reasons.append("healthy source still has useful target headroom")
    if action == "narrow-next-cycle-scope" and failed:
        score += 12; reasons.append("smaller scope limits repeated failure cost")
    if action == "resume-from-checkpoint":
        score += 15; reasons.append("restart-safe default")
    if action in {"retry-review-helper", "quarantine-failed-members"} and failed:
        score += 20; reasons.append("isolates item failures without discarding successful work")
    if action in {"rebind-survivor-membership", "revalidate-changed-survivors"} and failed:
        score += 16; reasons.append("rebuilds an exact survivor checkpoint")
    if action in {"refresh-source-manifest", "advance-source-cursor",
                  "discover-replacement-source", "rotate-to-compatible-source"} and deficit:
        score += 15; reasons.append("opens another authorized path toward the target")
    if action == "reuse-verified-object-cache" and accepted:
        score += 14; reasons.append("avoids repeating unchanged retrieval work")
    if action in {"resume-verification-only", "refresh-live-delta",
                  "advance-next-ready-unit"} and accepted:
        score += 12; reasons.append("advances completed acquisition work")
    if action == "recover-stale-writer-lease" and failed:
        score += 8; reasons.append("available only after proving the prior owner is absent")
    if action == "cleanup-live-verified-staging":
        score += 5; reasons.append("eligible only after exact live verification")
    if action in {"resume-range-capable-transfer", "reduce-large-object-concurrency"} and failed:
        score += 18; reasons.append("large media transfer pressure can be reduced without losing the checkpoint")
    if action in {"switch-compatible-media-representation", "quarantine-unsupported-media-variant"} and failed:
        score += 17; reasons.append("keeps the work while pivoting away from a failed representation")
    if action in {"verify-container-and-codec-metadata", "verify-comic-page-order-and-completeness",
                  "verify-audio-duration-and-container", "verify-video-duration-and-container"} and accepted:
        score += 10; reasons.append("adds media-specific validation before promotion")
    if action in {"recheck-item-rights-evidence", "rotate-to-rights-compatible-collection"} and failed:
        score += 22; reasons.append("rights uncertainty must be resolved or safely bypassed")
    if action in {"skip-authentication-required-source", "advance-next-public-download-manifest"} and failed:
        score += 24; reasons.append("access-controlled paths are skipped instead of retried or bypassed")
    return score, reasons


def build_plan(config: Config, state: State) -> dict:
    enabled = sorted((source for source in config.sources if source.enabled),
                     key=lambda source: (source.lane != "major", source.slot, source.id))
    lane_load = {lane: sum(1 for source in enabled if source.lane == lane)
                 for lane in {source.lane for source in enabled}}
    decisions = []
    intelligence = source_intelligence(config, state)
    aggregate_suggestions = [
        {"domain": row.get("domain"), "url": row.get("url"),
         "confidence": row.get("confidence"), "nextEvaluation": row.get("nextEvaluation")}
        for row in intelligence["potentialSites"][:5]
    ]
    total_accepted = int(state.counts().get("accepted", 0))
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    accepted_today = state.accepted_since(today.isoformat())
    first_observed, last_observed = state.observation_bounds()
    observation_days = 0.0
    try:
        first = datetime.fromisoformat(first_observed.replace("Z", "+00:00"))
        observation_days = round(max(0.0, (datetime.now(timezone.utc) - first).total_seconds() / 86400), 2)
    except (AttributeError, TypeError, ValueError):
        pass
    enabled_sources = [source for source in config.sources if source.enabled]
    estimated_capacity = sum(source.estimated_eligible_items for source in enabled_sources)
    estimated_daily = sum(source.estimated_daily_items for source in enabled_sources)
    unknown_capacity_sources = sum(1 for source in enabled_sources
                                   if not source.estimated_eligible_items)
    unknown_daily_sources = sum(1 for source in enabled_sources if not source.estimated_daily_items)
    known_estimates = len(enabled_sources) - unknown_capacity_sources
    def percent(numerator: int, denominator: int):
        return round(min(100.0, numerator * 100.0 / denominator), 2) if denominator else None
    for source in enabled:
        counts = state.source_counts(source.id)
        accepted = int(counts.get("accepted", 0))
        failed = int(counts.get("failed", 0))
        deficit = max(0, source.target_items - accepted) if source.target_items else 0
        candidates = []
        for action in DEFAULT_POOL:
            score, reasons = _score(action, source, counts, lane_load)
            candidates.append({"action": action, "score": score, "reasons": reasons})
        candidates.sort(key=lambda row: (-row["score"], row["action"]))
        pressure = min(100, int(counts.get("failed", 0)) * 12)
        mode = "exhale" if pressure >= 48 else "inhale" if pressure == 0 else "steady"
        decisions.append({
            "source": source.id,
            "lane": source.lane,
            "slot": source.slot,
            "counts": counts,
            "recommendedAction": candidates[0]["action"],
            "recommendationScore": candidates[0]["score"],
            "alternatives": candidates[1:5],
            "breathing": {"mode": mode, "pressureScore": pressure},
            "autonomy": {
                "advisoryOnly": True,
                "specificActionNeverForced": True,
                "mayChooseAlternative": True,
                "mayCreateLocalCandidate": True,
                "mustRecordReason": True,
            },
            "operatorAssistance": {
                "mode": source.assistance_mode,
                "decisionOwner": "sweeper",
                "operatorCanObserve": source.assistance_mode != "disabled",
                "operatorCanOfferGuidance": source.assistance_mode != "disabled",
                "operatorCanChangeSourceOrMode": False,
                "sweeperMayRequest": source.assistance_mode != "disabled",
                "sweeperMayAcceptDeclineDeferOrReplace": True,
                "offerReason": ("pivot-or-exhaustion-risk" if deficit or failed else "standby"),
                "suggestedNextAggregates": aggregate_suggestions,
            },
        })
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "model": "fleet-aware-continuation-advisor",
        "purpose": "Continuously pursue the largest, best-organized collection possible while preserving quality.",
        "project": {
            "name": config.project_name,
            "overallTargetItems": config.overall_target_items,
            "overallAcceptedItems": total_accepted,
            "overallRemainingItems": max(0, config.overall_target_items - total_accepted)
                if config.overall_target_items else None,
            "dailyTargetItems": config.daily_target_items,
            "acceptedTodayUtc": accepted_today,
            "dailyRemainingItems": max(0, config.daily_target_items - accepted_today)
                if config.daily_target_items else None,
            "overallProgressPercent": percent(total_accepted, config.overall_target_items),
            "dailyProgressPercent": percent(accepted_today, config.daily_target_items),
            "forecast": {
                "operator": "background-forecast-operator",
                "calculatedAt": datetime.now(timezone.utc).isoformat(),
                "updatesEveryDaemonCycle": True,
                "revisionPolicy": "revise when inventory, yield, overlap, depletion, or source evidence changes",
                "estimatedHighQualityEligibleItems": estimated_capacity,
                "estimatedDailyHighQualityItems": estimated_daily,
                "estimatedOverallGoalCoveragePercent": percent(
                    total_accepted + estimated_capacity, config.overall_target_items),
                "estimatedDailyGoalCoveragePercent": percent(
                    estimated_daily, config.daily_target_items),
                "unknownCapacitySourceCount": unknown_capacity_sources,
                "unknownDailyRateSourceCount": unknown_daily_sources,
                "completeEstimate": unknown_capacity_sources == 0,
                "advisoryOnly": True,
                "observationDays": observation_days,
                "firstLooseEstimateDays": 7,
                "recommendedMaturityDays": 14,
                "status": ("estimate-available" if observation_days >= 7 and known_estimates
                           else "still-calculating"),
                "approxDaysUntilFirstNumber": round(max(0.0, 7 - observation_days), 2)
                    if not (observation_days >= 7 and known_estimates) else 0,
                "approxDaysUntilMatureEstimate": round(max(0.0, 14 - observation_days), 2),
                "maturity": ("mature" if observation_days >= 14 and unknown_capacity_sources == 0
                             else "preliminary-loose-estimate" if observation_days >= 7 and known_estimates
                             else "still-calculating"),
                "percentageIsApproximate": True,
                "basis": "operator-provided or adapter-learned high-quality source estimates",
            },
            "targetsArePlanningGoals": True,
            "qualityGatesRemainBinding": True,
        },
        "pool": list(DEFAULT_POOL),
        "sourceIntelligence": intelligence,
        "decisions": decisions,
        "mandatoryInvariants": {
            "quality": ["authorization", "rights", "policy", "identity", "hashing",
                        "deduplication", "review", "single-live-writer"],
            "continuation": "seek another safe useful action until explicitly deactivated or temporarily impossible",
            "neverBlocked": "bookkeep and remove the failed item or source, then choose the next safe action",
        },
        "blockingStateAllowed": False,
        "failureLifecycle": ["bookkeep", "quarantine-or-defer", "preserve-survivors", "continue-or-pivot"],
        "invariants": ["quality", "continuation"],
    }


def prioritized_sources(config: Config, state: State) -> Iterable[Source]:
    plan = build_plan(config, state)
    scores = {row["source"]: row["recommendationScore"] for row in plan["decisions"]}
    return sorted((source for source in config.sources if source.enabled),
                  key=lambda source: (-scores.get(source.id, 0), source.lane != "major",
                                      source.slot, source.id))
