# Internet Archive Codex Sweeper Restoration Design

**Date:** 2026-08-31  
**Status:** Approved design, pending implementation plan  
**Authoritative workspace:** `/Users/jesusnewos/Downloads/PineCone`  
**Public framework repository:** `/Users/jesusnewos/Downloads/PineCone/web-sweeper`

## Purpose

Restore the clean, continuous Internet Archive acquisition behavior formerly used
for Codex without making the public Web Sweeper framework Codex-specific. The
restored system must discover and preserve complete English-language public-domain
Christian works, remain resumable across interruptions, reject individual bad
items without stopping the lane, and keep production publication behind a
separate explicit authorization boundary.

The first configured campaign is a fresh Christian General lane with an accepted
ceiling of 20,000 works and a screened-candidate ceiling of 400,000 records.

## Governing models

The implementation must obey these contracts in descending order of specificity:

1. `docs/CODEX_IMPORT_MASTER_MODEL.md` for Codex item eligibility, preservation,
   deduplication, validation, and publication boundaries.
2. `docs/SWEEPER_MODEL.md` for source selection, checkpointing, lane operation,
   health, coordination, and measured improvement.
3. `web-sweeper/overnight-sweeper/OVERNIGHT_SWEEPER_MODEL.md` for continuous,
   staging-only acquisition and recovery.
4. Michael Preservation for reversible changes, checksums, audit records,
   recoverability, least privilege, and evidence-qualified status claims.
5. The public Web Sweeper V2 contracts for generic acquisition state,
   content-addressing, continuation, and source isolation.

No Shepard model was found in installed skills or the authoritative workspace as
of this design. Its requirements must not be invented. A later verified Shepard
model may add stricter behavior but may not silently weaken any gate above.

## Chosen architecture

Use a two-layer design.

### Public Web Sweeper layer

Add a source-neutral Internet Archive discovery adapter to the public
`web-sweeper` package. It knows how to:

- query the Internet Archive Advanced Search API using a configured query;
- retrieve only lightweight identity fields during discovery;
- advance through a deterministic cursor or sorted page frontier;
- assign each Archive identifier to a deterministic partition;
- emit immutable candidate-manifest records with provenance;
- checkpoint discovery atomically and resume without rescanning completed pages;
- respect configured request pacing and bounded transient retries; and
- report exhaustion, transient deferral, and frontier advancement distinctly.

It does not decide Christian relevance, U.S. public-domain eligibility, Codex
identity, manuscript structure, publication eligibility, or live deployment.

### PineCone Codex layer

Add a PineCone-owned Internet Archive coordinator under `tool/` with focused
modules for configuration, live identity preflight, Archive metadata and file
selection, Christian screening, conversion, persistent state, unit freezing,
and operator status. This layer consumes discovery records from the public
adapter and applies every Codex gate in the required order.

The coordinator may stage validated local units. It must not publish to Live.
Publication remains a separate command and requires explicit user authorization,
a canonical writer lease, a fresh complete live delta, and five-gate deployment
verification.

## Campaign identity and ownership

The canonical campaign ID is:

`internet-archive-christian-general-h0-4-20260831`

Its state root is:

`/Users/jesusnewos/Downloads/PineCone/work/judah_library/imports/internet_archive_christian_general_hash0_4_20260831`

The configured ceilings are:

- `maxAccepted = 20000`
- `maxCandidates = 400000`
- discovery checkpoint size: at most 500 source items
- initial staging unit ceiling: 1,000 accepted works
- standard eventual publication unit: 100 independently validated works

Partition ownership is determined solely from the stable Archive identifier:

```text
bucket = int(SHA256(identifier encoded as UTF-8), 16) mod 7
```

This lane owns buckets `{0, 1, 2, 3, 4}`. The companion 10,000/200,000 lane owns
buckets `{5, 6}`. Bucket membership is calculated before candidate accounting,
so this lane's 400,000 ceiling counts only records inside its owned boundary.
Tests must prove the two sets are exhaustive and non-overlapping.

## Source boundary and query families

The declared source is the Internet Archive texts collection, not the entire
Archive. Candidates must be English-oriented book records with an approved
complete textual derivative available. The coordinator rotates through a
versioned collection of broad Christian subject families rather than relying on
one keyword:

- Scripture and biblical studies;
- theology, doctrine, apologetics, and ethics;
- sermons, preaching, devotion, prayer, and worship;
- church history, denominations, councils, and Christian biography;
- missions, ministry, Christian education, and pastoral care;
- Christian literature and narrative whose substance is Christian.

Every exact Archive query, normalized query hash, sort order, returned count,
cursor, and observation timestamp is preserved. Query matches are only discovery
evidence. They never establish Christian relevance or rights.

A query family is rotated after either proven exhaustion or a measured
negligible-yield window. Zero acceptances after 2,000 newly screened candidates
is a mandatory rotation. A low but nonzero family retains its survivors and
rotates only when its configured window remains below the negligible-yield
ceiling. Rotation never purges the unfinished unit or its rejection memory.

## Required processing order

For every deterministic checkpoint of at most 500 owned identifiers:

1. Freeze lightweight identity metadata and its hash.
2. Use a fresh, complete, receipt-backed Live Codex snapshot for Gate 0.
3. Remove exact source-ID, external-ID, normalized title-author, work-family,
   edition-family, alternate-title, whole/part, and available-content overlaps.
4. Retrieve authoritative full item metadata only for Gate 0 survivors.
5. Establish substantive Christian relevance using field-aware evidence and
   explicit counter-signals.
6. Establish item-level U.S. public-domain status. The ordinary automatic date
   ceiling is publication before 1931; missing, conflicting, or later dates
   defer the item unless stronger item-level evidence is independently reviewed.
7. Select one approved complete textual derivative without downloading PDFs,
   DjVu, page images, or locally generating OCR.
8. Retrieve source bytes with bounded retries, hash them, and preserve the exact
   source filename, Archive file metadata, request URL, and response evidence.
9. Convert without summarizing, modernizing, excerpting, or silently repairing.
10. Verify substantial English body evidence, completeness, structural limits,
    and the 1,000-word preservation floor.
11. Deduplicate by source identifiers, exact normalized content, work identity,
    edition family, and targeted near-text or containment checks.
12. Append the decision, preserve accepted artifacts, and continue after every
    individual reject, defer, duplicate, or transient failure.

Only increased authoritative accepted membership constitutes lane health.
Process liveness, API traffic, and rejection activity are diagnostics.

## Internet Archive derivative policy

The selector is deterministic and fail-closed. It may choose only complete
provider-delivered artifacts in these campaign formats:

- TXT;
- EPUB;
- HTML/HTM;
- XML/TEI;
- structured JSON/JSONL;
- Markdown, RTF, DOCX, ODT, or FB2; or
- a compressed archive whose members are complete books in an approved format
  and whose archive/member hashes and extraction provenance are retained.

Provider-generated OCR is eligible only when delivered as a complete approved
textual artifact and when body-language, completeness, and quality checks pass.
PDF, DjVu, page-image, and local OCR paths are excluded from this campaign.

When multiple derivatives qualify, selection uses a versioned preference table.
The decision record contains every considered filename and the exact rejection
or winning reason so converter changes can be audited and replayed.

## Relevance and rights behavior

Relevance matching is phrase- and field-aware. Christian-looking substrings,
personal names such as Hans Christian Andersen, institutional names, popularity,
and download counts cannot establish relevance. Full item descriptions and
publishers may supply negative evidence that overrides a generic title or
subject match. Administrative records, trials, conventions, indexes, serial
issues, excerpts, and technical works with incidental biblical terms require
independent substantive evidence or are rejected.

Christian traditions, including Christian Science, receive item-level review and
are not excluded by denomination. Tradition-specific interpretation remains
labeled and is not represented as Scripture or the recorded words of Christ.

Rights evidence is stored as a separate immutable record from source text.
Pre-1931 date evidence alone does not validate a modern revised transcription.
Modern editorial, corrected, abridged, or revised expression is deferred unless
permission or an independently verified public-domain source layer resolves it.

## State and audit artifacts

The lane root contains:

- `campaign.json`: immutable campaign identity, limits, partition, query-set
  version, policy versions, and creation receipt;
- `coordinator.lock`: nonblocking OS advisory lock held for the coordinator's
  entire lifetime;
- `state.sqlite3`: candidates, frontiers, latest decisions, attempts, artifact
  bindings, unit membership, health growth events, and recovery history;
- `journal/decisions.jsonl`: append-only correction-aware decisions;
- `journal/activity.jsonl`: append-only operational events;
- `discovery/`: immutable checkpoint manifests and query receipts;
- `live/`: hashed Live snapshot receipts and identity-index manifests;
- `source/objects/`: content-addressed source bytes;
- `manuscripts/`: converted complete works and manifests;
- `units/`: frozen unit manifests and later validation attestations;
- `quarantine/`: revoked or unresolved artifacts with evidence; and
- `reports/`: exact status, screening, capacity, recovery, and unit reports.

Every atomic membership artifact is written to a temporary file, flushed, and
renamed. SQLite uses transactions and an integrity check during recovery.
Relative source paths are forbidden in durable records.

## Fresh reset semantics

The first launch is an explicit fresh campaign. Because the proposed root is new,
the expected reset is normally an empty-root initialization. If that exact root
already exists, the clean command must:

1. acquire the lane lock and prove no coordinator or child worker is alive;
2. inventory and hash the existing root;
3. preserve accepted artifacts, frozen units, staging receipts, validation
   attestations, publication evidence, and shared duplicate indexes;
4. move only disposable lane-local cursors, candidate slices, downloads, caches,
   and unfinished screening memory into a timestamped recovery bundle;
5. write a signed-by-hash reset receipt naming every moved path; and
6. initialize the fresh frontier exactly once.

The reset is recoverable by moving the receipt-bound bundle back while the lane
is stopped. No shared Live or prior-root evidence is deleted. After the first
completed unit, the campaign resumes its own rejection and processed-ID memory;
the fresh marker does not cause repeated amnesia.

## Concurrency and capacity

One coordinator owns this state root. Network retrieval may use up to four
workers only after a measured pilot; journal appends, accepted membership,
duplicate winners, and unit writes remain serialized in stable discovery order.

Before source retrieval, index rebuild, validation report generation, or staging,
free space must exceed both 1 GiB and twice the estimated largest temporary or
atomic working set. Capacity failure records a pause and schedules no new
expensive work. Only receipt-proven, reproducible caches from completed and
live-verified work may be reclaimed automatically.

Archive requests use a truthful configured User-Agent, a conservative initial
rate, bounded attempts with exponential backoff and jitter, and explicit retry
classification for 429, transient 5xx, connection resets, incomplete reads, and
socket timeouts. Rights, identity, eligibility, and software errors are never
reclassified as transient transport failures.

## Unit lifecycle and publication boundary

The campaign begins with staging units of no more than 1,000 accepted books.
Moving to 2,500, 5,000, or 10,000 requires the measured autonomous-continuation
evidence defined by the Overnight Sweeper model. No staging unit may exceed
10,000 complete books.

Freezing a unit binds its ordered membership, decision-journal frontier, source
hashes, manuscript hashes, campaign policy versions, query receipts, Live Gate 0
snapshot, and conversion version. Frozen does not mean validated, published, or
live-verified.

Independent validation consumes the frozen unit and emits a hash-bound
attestation. A future explicitly authorized publisher must construct 100-book
publication units, obtain the canonical production lease, run the fresh Live
delta, publish only survivors, verify Firestore, Storage, search, Living Codex
room assignment, and reader accessibility, then release the lease. The restored
coordinator contains no implicit publish-on-completion path.

## Operator commands

The PineCone command surface will provide:

- `init`: create a new campaign and immutable configuration receipt;
- `clean-init`: perform the receipt-preserving fresh initialization;
- `preflight`: verify workspace, configuration, partition, Live snapshot,
  capacity, lock availability, query reachability, and zero-write readiness;
- `run`: acquire the lock and continuously advance the lane;
- `status`: report exact counts, frontiers, growth timestamp, process ownership,
  capacity, and the next safe action;
- `recover`: verify zero writers, SQLite/journal integrity, accepted-artifact
  coverage, and resume the same checkpoint;
- `freeze-unit`: freeze an exact eligible unit without publication; and
- `verify-state`: perform read-only invariant and artifact checks.

Every mutating command supports `--dry-run` where meaningful. Limits and
partition ownership are immutable after initialization; changing them creates a
new campaign ID and root.

## Testing strategy

Implementation follows test-driven development. Required automated coverage:

- Archive query construction and deterministic cursor advancement;
- partition exhaustiveness, stability, and collision freedom;
- candidate ceiling counted after partition ownership;
- Gate 0 runs before full metadata, rights research, or source download;
- exact and alias identity overlaps produce zero source downloads;
- derivative allowlist and fail-closed winner selection;
- English metadata plus substantive English-body gate;
- pre-1931 rights handling and modern-transcription deferral;
- individual rejection does not stop checkpoint advancement;
- deterministic serialized decisions despite out-of-order fetch completion;
- bounded transport retries including socket timeout and incomplete read;
- interrupted discovery, retrieval, and journal recovery;
- coordinator lock rejects a second owner;
- clean reset preserves protected evidence and writes a restoration receipt;
- capacity gate prevents expensive mutation;
- accepted-count growth is the only health timestamp;
- frozen-unit hashes change whenever membership or policy binding changes; and
- no staging command can mutate Live production.

Network integration tests use recorded or synthetic fixtures. A live-source pilot
is read-only until Gate 0 and retrieves only a deliberately small number of
eligible source artifacts after all automated tests pass.

## Rollout and launch gates

1. Verify the public repository remote and preserve the current commit ID.
2. Implement and test the generic Archive adapter in Web Sweeper.
3. Implement and test the PineCone Codex coordinator.
4. Run the complete automated suite.
5. Run `preflight` and a zero-download Gate 0 rehearsal against a fresh complete
   Live snapshot.
6. Run an interrupted-resume rehearsal on fixtures.
7. Run a small live Archive pilot with no staging or publication.
8. Inspect measured relevance yield, derivative quality, request behavior,
   conversion throughput, and capacity.
9. Start the 20,000/400,000 staging-only lane under its unique lock.
10. Report progress using distinct discovered, owned, Gate-0-surviving,
    rights-qualified, retrieved, converted, accepted, frozen, validated, staged,
    published, and live-verified counts.

The full launch is successful only when authoritative accepted membership grows,
the coordinator advances autonomously from a durable checkpoint, and no invariant
or capacity gate is weakened. Running processes and fresh timestamps alone are
not success evidence.

## Explicit non-goals

- No Live Codex publication without a later explicit authorization.
- No PDF, DjVu, image retrieval, or local OCR in this campaign.
- No loosening of rights, relevance, English, completeness, word-floor, or
  duplicate gates to reach 20,000.
- No reuse of disposable state from an older Internet Archive lane.
- No worktree, alternate PineCone copy, or state root outside the authoritative
  workspace.
- No claim that the entire Internet Archive was swept; reports name the exact
  query version, partition, and explored frontier.
