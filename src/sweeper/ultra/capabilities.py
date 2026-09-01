from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


class CapabilityUnavailable(RuntimeError):
    """The engine does not implement a capability; this permits safe fallback."""


class IntegrityFailure(RuntimeError):
    """A gate failed. This must never be converted into an engine fallback."""


CAPABILITIES = (
    "discovery", "acquisition", "gate0", "rights", "completeness", "relevance",
    "deduplication", "review", "validation", "staging", "translation",
    "publication", "live-verification", "cleanup",
)


@dataclass(frozen=True)
class CapabilityResult:
    capability: str
    executor: str
    fallback_used: bool
    value: Any


class CapabilityRouter:
    """Ultra-first execution with explicit V2 compatibility fallback."""

    def execute(self, capability: str,
                ultra: Optional[Callable[[], Any]],
                v2: Optional[Callable[[], Any]]) -> CapabilityResult:
        if capability not in CAPABILITIES:
            raise ValueError("unknown Sweeper capability: %s" % capability)
        if ultra is not None:
            try:
                return CapabilityResult(capability, "ultra", False, ultra())
            except CapabilityUnavailable:
                pass
        if v2 is None:
            raise CapabilityUnavailable("%s is unavailable in Ultra and V2" % capability)
        # Only explicit absence permits fallback. Integrity failures and ordinary
        # defects from Ultra propagate and cannot be hidden by switching engines.
        return CapabilityResult(capability, "v2", True, v2())

    def matrix(self, ultra_native: Dict[str, bool], v2_available: Dict[str, bool]) -> dict:
        rows = []
        for capability in CAPABILITIES:
            native = bool(ultra_native.get(capability))
            fallback = bool(v2_available.get(capability))
            rows.append({"capability": capability,
                "preferred": "ultra", "ultraNative": native,
                "v2FallbackAvailable": fallback,
                "effectiveExecutor": "ultra" if native else "v2" if fallback else "unavailable"})
        return {"priority": ["ultra", "v2"], "capabilities": rows,
                "fallbackOn": "explicit-capability-unavailable-only",
                "integrityFailuresNeverFallBack": True}
