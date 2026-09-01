# Web Sweeper 200-point optimization standard

Web Sweeper evaluates twenty preservation-safe controls at each of ten pipeline
stages. The resulting 200 points form one auditable optimization surface:

- stages: discovery, Gate 0, metadata, retrieval, conversion, deduplication,
  checkpoint, staging, publication, and live verification;
- controls: early exit, immutable caching, hash reuse, bounded batching,
  respectful concurrency, backpressure, bounded retry, timeout, resumability,
  append-only decisions, memory bounds, capacity gates, identity projection,
  source pacing, deterministic ordering, exclusive ownership, authoritative
  observability, recovery receipts, stale-state invalidation, and fail-closed
  integrity.

The controller computes the exact count from these two declared dimensions and
returns it as `optimizationStandard.points`. The developer interface displays
that value, and regression tests require it to remain exactly 200.

This is an optimization standard, not permission to bypass a gate. A point is
applicable only when it preserves higher-priority rights, completeness,
language, relevance, duplicate, staging, and live-verification requirements.
Process activity never substitutes for accepted-book growth when measuring an
acquisition lane's health.

Opti source adapters may keep reviewing a new candidate frontier while their
`currentRoot` still names the preceding receipt-bound unit. In that state, the
controller treats the adapter's active accepted membership and `lastGrowthAt`
as authoritative. The operating card subtracts the receipt-bound prior-unit
baseline and labels only the difference as newly accepted; cumulative review
membership and protected prior custody remain available in mode details. A
historical receipt cannot reset the active card, replace the current stage, or
make accepted growth look stale. Likewise, an older staging-progress file cannot
override a newer acquisition checkpoint.

Public Opti configurations should keep source boundaries isolated, retain
append-only decisions and duplicate memory, require more than 1 GiB free, and
stage accepted manuscripts without granting production authority. UI refreshes
read the newest state automatically; recovery resumes one checkpoint owner and
never replaces a healthy active model.

The current implementation also avoids reparsing unchanged JSON state by using
a bounded, inode/size/mtime-nanosecond cache. Atomic replacements invalidate the
entry immediately, deleted or malformed files fail closed, and the cache evicts
old entries after 2,048 paths.

Each controller response declares its `web_sweeper` workspace identity. Clients
validate that identity before rendering cards and repair a stale saved endpoint
once against the canonical local controller.
