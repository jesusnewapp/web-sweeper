# Overnight Sweeper Mini-Model

Overnight Sweeper is an optional operating model for unattended, continuous,
staging-only acquisition. It extends Web Sweeper without changing its rights,
quality, provenance, hashing, deduplication, or continuation rules.

## Boundary

- Run source workers with staging authority only.
- Keep publication, live promotion, and live verification in a separate writer.
- Keep one canonical coordinator per source unit. Never run overlapping workers
  against the same unit or checkpoint.
- Continue without a numeric batch limit when configured. Exit only for source
  exhaustion, operator shutdown, or a fail-closed integrity or source error. A
  capacity gate pauses scheduling while the canonical coordinator retains its
  protected root; it does not terminate an unlimited campaign.
- Allow the operator to raise a source unit ceiling when measured acceptance
  throughput and free-space headroom support it; the ceiling changes packaging
  size only and never weakens item gates or the capacity stop.
- Let the operator select the next unit size explicitly. Start at 50 or 100
  while proving the lane, and never exceed 1,000 accepted items in one staging
  unit. This applies across configured artifact classes; the public runtime
  enforces the ceiling.
- After a successful staging upload, record a receipt and immediately begin the
  next source unit.
- A unit may close because it reached its accepted-item target or because its
  current source frontier was genuinely exhausted. In either case, stage every
  eligible survivor already prepared, record the exact survivor count, and
  advance. Never hold a valid remainder merely because it is smaller than the
  configured target.
- Treat exhaustion of one discovery page, cursor window, or partition as an
  internal continuation event, not a successful coordinator exit. Persist the
  next frontier, retain the same incomplete unit and checkpoint, and continue
  immediately. Exit only when the source frontier itself is proven exhausted.
- Use this direct continuation state machine for simple source models. A
  separate pivot advisor is optional and is not required for deterministic
  `checkpoint -> exhaust set -> advance set -> stage survivors -> next unit`
  operation. Design pivot pools at the beginning of future complex systems
  whose workers have several materially different safe recovery routes.
- Bind a proven-exhausted frozen manifest to its exact fingerprint, retire it
  from active rotation, and skip it on restart. If a local manifest's bytes
  change, reactivate it automatically. Source adapters may discard generated
  frozen candidate files after recording retirement, but must never delete
  accepted artifacts, receipts, journals, checkpoints, or deduplication memory.
- Complete substantive rights, relevance, language, completeness, source, and
  quality validation once during acquisition. Bind the accepted membership to
  exact hashes in the staging receipt. Stage-to-live verifies that unchanged
  attestation; it does not re-download the source, repeat those checks, or wait
  for a second legacy validation-report artifact.

## Unit loop

1. Confirm source ownership and the authoritative workspace.
2. Refresh the operator-configured identity/deduplication evidence.
3. Resume the exact checkpoint, candidate frontier, and screening memory.
   When only the current discovery window is exhausted, atomically advance the
   frontier and repeat this step inside the same coordinator and source unit.
4. Apply metadata, rights, language, format, completeness, and policy gates.
5. Acquire, hash, deduplicate, and preserve accepted artifacts.
6. Upload only the acquisition-validated set to the configured isolated staging
   destination. Preserve the exact acceptance policy/version with its hashes.
7. Write a staging receipt containing source, unit, count, timestamp, artifact
   binding, and an explicit declaration that live production was not mutated.
8. Commit the completed-unit checkpoint and next frontier before launching the
   successor. Treat receipt, completion accounting, and successor selection as
   one recoverable transition so a crash can replay safely without double
   staging or skipping a frontier.
9. Immediately begin the next unit. Stage-to-live attestation reuse cannot block
   acquisition continuation.

The source adapter's acquisition gates remain mandatory. Attestation reuse does
not permit missing rights evidence, unsafe formats, incomplete acquisition,
absent hashes, or known duplicates to stage. Before live promotion, the public
V2 guarded dock verifies the unchanged acquisition attestation and performs a
fresh live duplicate delta. It removes only duplicates found since acquisition;
rights, content, and source validation are not repeated.

An individual fresh-delta duplicate never aborts a staging unit or prevents its
coordinator from advancing. Quarantine that member with its matched identity
keys and remote record IDs, rewrite the exact local membership atomically, stage
all remaining survivors, record the survivor count in the receipt, and continue.
If every member is already represented, record a zero-survivor completion and
advance without uploading or retrying the duplicates.

Apply the same isolation rule to malformed generated candidate files and other
item-scoped preparation failures: record and discard the failed generated input,
preserve accepted artifacts and checkpoint evidence, and keep the source lane
moving. A source-wide integrity failure remains fail-closed.

## Autonomous scaling

Do not raise a source toward the 1,000-item ceiling merely because one large unit
fills successfully. Observe at least 5–10 consecutive autonomous unit
continuations before considering a controlled increase, and require a longer
record such as 50 consecutive autonomous continuations before calling a size
established or making it the default. Each qualifying continuation must
stage the exact eligible survivors, quarantine individual duplicates, persist the
checkpoint and accounting, and begin the next unit without an operator, monitor,
or replacement process restarting it.

Track automatic continuations, crash recoveries, monitor-triggered restarts, and
manual restarts separately. A manual or monitor-triggered restart resets the
consecutive-autonomy streak. Reaching an observation threshold is evidence for
evaluation, not an automatic upgrade: disk headroom, source yield, staging
latency, dedup behavior, stage-to-live throughput, and absence of overlapping
workers must also support the larger tier. Discovery frontiers may contain
millions of records, but the 1,000 ceiling applies to items packaged in one
staging unit.

## Stage-to-live drainage

Keep acquisition continuous while one separately authorized writer drains ready
staged units in deterministic order. For each exact unit, validate its bound
artifacts, perform the fresh live-delta check, publish once under a serialized
writer lease, verify the deployment, clean only live-verified staging and local
payloads, and immediately take the next ready unit. Report queue depth and age so
staging throughput cannot silently outrun verified publication capacity.

## Inactivity monitor

- Check coordinator and child-process health every five minutes for the
  operator-requested monitoring window.
- Compare process liveness, stage, checkpoint timestamp, counters, activity log,
  and free space with the prior check.
- Allow long indexing, deduplication, retrieval, and upload phases while the
  process is live and its resource behavior is plausible.
- Treat a lane as inactive after two consecutive checks without meaningful
  progress, or immediately when its process exits unexpectedly or records a
  failure state.
- Before recovery, prove the former coordinator and child are dead and no
  overlapping unit is active.
- Restart the same canonical coordinator from its existing checkpoint, cache,
  candidate frontier, and deduplication state.
- Never reset a source, delete evidence, broaden scope, or create a second
  claimant during automatic recovery.
- Alert the operator when inactivity occurs. Include source, unit, stage,
  counters, timestamps, process state, free space, recovery action, and result.
- Stay quiet while lanes are healthy or legitimately busy.

## Capacity and network recovery

- Configure an explicit free-space stop appropriate to the deployment. Never
  lower it automatically.
- Stop before scheduling new retrieval or atomic output work when the capacity
  gate fails.
- Keep the canonical coordinator attached to its current checkpoint during the
  capacity pause, record the required headroom and bounded retry interval, and
  resume the same protected unit automatically after free space recovers.
- Retry only transient transport failures such as broken pipes, connection
  resets, timeouts, rate limits, and server errors.
- Use low upload concurrency, bounded attempts, and exponential backoff.
- Never reinterpret rights, identity, membership, or deduplication
  failures as network failures.
- Preserve local artifacts until the operator's separate promotion
  workflow establishes its required remote evidence or explicitly authorizes a
  different cleanup boundary.
- If independent promotion validation requires raw source evidence that was
  evicted after staging, deterministically rehydrate only the exact unit from
  its recorded source URLs, verify every recorded source hash, validate and
  live-verify under the serialized writer, then remove only those restored
  source files. Missing or mismatched evidence fails closed.
- After an exact staging receipt and remote object-count/binding check succeed,
  a deployment may evict only re-downloadable source caches belonging to that
  completed unit. Preserve manuscripts, catalogs, hashes, receipts, checkpoints,
  rejection memory, and active-unit caches unless the configured recovery path
  proves those bytes are available elsewhere.
- In an active unit, delete only re-downloadable source-cache directories that
  a fail-closed scan proves are not referenced by any accepted manuscript
  provenance. Preserve accepted source evidence and every checkpoint, journal,
  candidate/cursor, dedup, staged/live protection, and permanent receipt.
  Unreadable or incomplete provenance forbids cleanup. Log the reclaimed count
  and bytes, resume the same root, then prove health through accepted growth.
- Resolve every cleanup target beneath the configured cache root and never
  delete authoritative active-unit artifacts.
- Run that exact source-cache disposal automatically after each passing staging
  receipt when the deployment has configured hash-verified rehydration. After
  exact live verification, also discard the corresponding local manuscript
  payload while retaining its catalog, manuscript-hash manifest, promotion
  receipt, live-verification receipt, and cleanup ledger.
- Evaluate projected headroom before beginning the successor unit. The gate must
  include the configured reserve plus the largest measured temporary/atomic
  working set; accepted-item count alone is not a disk estimate.

## Replacement workers

A stopped source worker may be replaced only after confirming that its former
process is dead. The replacement inherits the same source scope, unit,
checkpoint, journal, candidate frontier, and deduplication database. Parallel
workers for one source require explicitly non-overlapping deterministic shards
and shared global deduplication evidence.

## Accounting

Report these states separately:

- candidates screened;
- accepted, rejected, deferred, and duplicate;
- staging uploads completed;
- units advanced, with automatic advances, crash recoveries, monitor recoveries,
  and manual restarts counted separately;
- recovery events and capacity stops;
- independently validated, staging-verified, published, and live-verified
  counts, which remain deferred in a staging-only run.

Never describe a staging upload as verified or published without evidence from
the separate workflow responsible for those states.

Exhausting every configured query collection proves only discovery-vocabulary
exhaustion, not depletion of a large source. Preserve the unfinished root and
prior-screening memory, extend the registered frontier with bounded precise
Christian query families inside the same source boundary, and resume with one
coordinator. Keep all rights, relevance, completeness, language, identity, and
duplicate gates unchanged. Health is restored only when authoritative accepted
membership increases. When a collection yields survivors and then exhausts,
rotate before partial-unit handoff so those survivors remain in the same
protected root while the next collection continues filling its target.

If the final registered family closes with a valid partial remainder, stage
and publish those survivors before expanding discovery. The next unit can
legitimately inherit that exhausted final cursor. Append new high-precision
families to the registry and resume the same non-fresh unit; allow the old
cursor to return zero and rotate forward. The recovery proof is new accepted
membership from an appended family, not a process restart or UI label.

Do not extend one institution forever when successive bounded additions yield
only negligible final remainders. Publish those survivors, preserve permanent
receipts and shared duplicate protection, retire only the exhausted acquisition
owner, and move the slot to a proven non-overlapping institution. Update the UI
to name the real source. The successor becomes healthy only when its
authoritative accepted membership increases.
