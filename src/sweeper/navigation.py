"""Source-neutral navigation pools and staged-pool scheduling policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Iterable


@dataclass(frozen=True)
class StagedUnit:
    unit_id: str
    books: int


def normalize_queries(values: Iterable[str], limit: int = 10) -> tuple[str, ...]:
    """Return at most ten unique, non-empty navigation queries in order."""
    queries: list[str] = []
    seen: set[str] = set()
    for value in values:
        query = " ".join(str(value).strip().split())
        key = query.casefold()
        if not query or key in seen:
            continue
        if len(query) > 120:
            raise ValueError("navigation query exceeds 120 characters")
        queries.append(query); seen.add(key)
        if len(queries) == limit:
            break
    if not queries:
        raise ValueError("at least one navigation query is required")
    return tuple(queries)


def navigation_index(queries: tuple[str, ...], current_index: int,
                     last_candidate_growth_at: datetime, *,
                     pages_exhausted: bool = False,
                     checked_at: datetime | None = None,
                     stall_after: timedelta = timedelta(hours=1)) -> tuple[int, str]:
    """Advance only after page exhaustion or one hour without candidate growth."""
    if not queries:
        raise ValueError("navigation queries are required")
    checked_at = checked_at or datetime.now(timezone.utc)
    index = max(0, min(current_index, len(queries) - 1))
    if pages_exhausted:
        return (index + 1) % len(queries), "configured-pages-exhausted"
    if checked_at - last_candidate_growth_at >= stall_after:
        return (index + 1) % len(queries), "candidate-growth-stalled-one-hour"
    return index, "candidate-growth-active"


def staged_pool_plan(units: Iterable[StagedUnit], minimum_share: float = 0.5,
                     minimum_publication_books: int = 500) -> dict:
    """Schedule largest exact units first and record a minimum book target.

    The plan never merges units. Consumers retain each unit's hashes and
    receipts while processing serially through one production writer.
    """
    if not 0 < minimum_share <= 1:
        raise ValueError("minimum share must be greater than zero and at most one")
    if minimum_publication_books < 1:
        raise ValueError("minimum publication books must be positive")
    ordered = sorted((unit for unit in units if unit.books > 0),
                     key=lambda unit: (-unit.books, unit.unit_id))
    total = sum(unit.books for unit in ordered)
    minimum_cycle = min(total, max(ceil(total * minimum_share),
                                   minimum_publication_books))
    selected: list[StagedUnit] = []
    selected_books = 0
    for unit in ordered:
        if selected_books >= minimum_cycle:
            break
        selected.append(unit)
        selected_books += unit.books
    return {
        "units": ordered,
        "selectedUnits": selected,
        "selectedBooks": selected_books,
        "pendingBooks": total,
        "minimumCycleBooks": minimum_cycle,
        "minimumAutomaticPublicationBooks": minimum_publication_books,
        "minimumPoolSharePercent": round(minimum_share * 100, 2),
        "serializedWriterRequired": True,
    }
