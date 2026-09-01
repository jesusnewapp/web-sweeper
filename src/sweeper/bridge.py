"""Operator-controlled high-nurture staging bridge."""

from __future__ import annotations


def decision(accepted: int, target: int, enabled: bool,
             threshold_percent: float = 50.0) -> dict:
    if accepted < 0 or target < 1 or not 0 <= threshold_percent <= 100:
        raise ValueError("accepted, target, and threshold are out of range")
    score = min(100.0, 100.0 * accepted / target)
    active = bool(enabled) and score >= threshold_percent
    return {
        "enabled": bool(enabled), "active": active,
        "nurtureScorePercent": round(score, 2),
        "thresholdPercent": float(threshold_percent),
        "action": "cross-staging-bridge" if active else "normal-staging-route",
        "authority": "skip-redundant-acquisition-review-only",
        "neverBypasses": ["exact-staging-membership", "live-duplicate-delta",
                          "single-production-writer", "live-verification"],
    }
