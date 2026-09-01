from __future__ import annotations

from dataclasses import dataclass, field
from typing import AbstractSet


@dataclass
class CandidateWindow:
    """Count newly screened identities without charging remembered duplicates."""

    target: int
    remembered: AbstractSet[str] = field(default_factory=frozenset)
    raw_records: int = 0
    remembered_skips: int = 0
    screened: int = 0
    _admitted: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.target < 1:
            raise ValueError("candidate target must be positive")

    @property
    def full(self) -> bool:
        return self.screened >= self.target

    def admit(self, identity: str) -> bool:
        self.raw_records += 1
        normalized = str(identity).strip()
        if not normalized or normalized in self.remembered or normalized in self._admitted:
            self.remembered_skips += 1
            return False
        if self.full:
            return False
        self._admitted.add(normalized)
        self.screened += 1
        return True
