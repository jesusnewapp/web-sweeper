# Internet Archive Codex Sweeper Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a tested, resumable Internet Archive Christian General acquisition lane capped at 20,000 accepted works and 400,000 owned candidates without granting Live publication authority.

**Architecture:** Add a source-neutral Internet Archive discovery adapter to the public Web Sweeper package, then add a PineCone-owned Codex coordinator that consumes immutable discovery checkpoints and applies the existing Codex gates in their mandated order. Persist one locked, receipt-backed campaign state under the authoritative PineCone workspace and launch only after fixture, recovery, zero-download Gate 0, and small live-source pilot gates pass.

**Tech Stack:** Python 3.9+, standard library (`argparse`, `dataclasses`, `hashlib`, `json`, `sqlite3`, `urllib`, `fcntl`, `socket`, `concurrent.futures`), pytest, Web Sweeper V2, Internet Archive Advanced Search/metadata APIs.

**Spec:** `docs/superpowers/specs/2026-08-31-internet-archive-codex-restoration-design.md`

## Global Constraints

- Perform all work in `/Users/jesusnewos/Downloads/PineCone`; do not create a PineCone copy or worktree.
- Preserve the current Codex Import Master Model and Sweeper Model gates without weakening them.
- Internet Archive ownership is `int(SHA256(identifier), 16) % 7 in {0,1,2,3,4}`.
- Count `maxCandidates=400000` only after partition ownership; stop authoritative accepted membership at `maxAccepted=20000`.
- Use deterministic discovery checkpoints of at most 500 source records.
- Retrieve only complete TXT, EPUB, HTML/HTM, XML/TEI, JSON/JSONL, Markdown, RTF, DOCX, ODT, FB2, or qualifying compressed textual artifacts.
- Do not retrieve PDF, DjVu, page images, or run local OCR.
- Run fresh complete Live Gate 0 before full metadata, rights work, source retrieval, conversion, review, or validation.
- Require item-level U.S. public-domain evidence, substantive Christian relevance, substantial English-body evidence, complete text, and at least 1,000 words.
- Keep decisions append-only and correction-aware; serialize membership, deduplication winners, journals, and unit writes.
- Require free space greater than 1 GiB and twice the estimated largest temporary/atomic working set before expensive mutation.
- No command in this plan may publish to Live Codex.
- Use test-driven development: write and observe each focused failing test before its production change.
- Preserve unrelated user changes; commit only files belonging to the current task.

---

## File Structure

### Public Web Sweeper repository

- `src/sweeper/internet_archive.py` — generic Archive query, partition, paging, retry, and immutable checkpoint logic.
- `src/sweeper/model.py` — typed Internet Archive discovery configuration.
- `src/sweeper/config.py` — parse and validate optional Archive discovery configuration.
- `src/sweeper/cli.py` — generic `archive-discover` command.
- `tests/test_internet_archive.py` — adapter unit and fixture-driven API tests.
- `tests/fixtures/internet_archive/` — recorded/synthetic search responses.
- `examples/internet-archive-texts.example.json` — source-neutral configuration example.

### Authoritative PineCone workspace

- `tool/internet_archive_codex.py` — operator CLI and dependency composition only.
- `tool/ia_codex_config.py` — immutable campaign configuration and query-family definitions.
- `tool/ia_codex_state.py` — SQLite schema, advisory lock, append-only journals, atomic receipts, and recovery checks.
- `tool/ia_codex_live.py` — fresh Live snapshot verification and Gate 0 index integration.
- `tool/ia_codex_source.py` — full Archive metadata, derivative selection, bounded source retrieval, and hashing.
- `tool/ia_codex_screen.py` — rights, relevance, English, completeness, structure, and word-floor decisions.
- `tool/ia_codex_convert.py` — format-specific complete-text conversion and provenance manifests.
- `tool/ia_codex_coordinator.py` — deterministic checkpoint processing, serialized decisions, capacity gate, health, continuation, and unit freezing.
- `test/test_ia_codex_*.py` — focused PineCone test modules.
- `test/fixtures/internet_archive/` — Codex metadata, file-list, text, overlap, and failure fixtures.
- `docs/INTERNET_ARCHIVE_CODEX_RUNBOOK.md` — operator commands, reports, recovery, and publication boundary.

---

### Task 1: Generic Archive Partition and Query Contract

**Files:**
- Create: `src/sweeper/internet_archive.py`
- Create: `tests/test_internet_archive.py`
- Create: `tests/fixtures/internet_archive/search_page_1.json`

**Interfaces:**
- Produces: `partition_bucket(identifier: str, modulus: int = 7) -> int`
- Produces: `owns_identifier(identifier: str, modulus: int, buckets: frozenset[int]) -> bool`
- Produces: `ArchiveQuery(query: str, fields: tuple[str, ...], sort: tuple[str, ...], rows: int)`
- Produces: `build_search_url(query: ArchiveQuery, cursor: str | None) -> str`
- Produces: `parse_search_page(payload: bytes) -> ArchiveSearchPage`

- [ ] **Step 1: Write failing partition tests**

```python
def test_partition_is_stable_and_owned_sets_do_not_overlap():
    ids = ["pilgrimsprogress00buny", "cityofgod01augu", "sermons01spur"]
    observed = [partition_bucket(value) for value in ids]
    assert observed == [partition_bucket(value) for value in ids]
    for value in ids:
        assert owns_identifier(value, 7, frozenset(range(5))) != owns_identifier(
            value, 7, frozenset({5, 6})
        )
```

- [ ] **Step 2: Run the partition test and confirm the missing-module failure**

Run: `.venv/bin/pytest tests/test_internet_archive.py::test_partition_is_stable_and_owned_sets_do_not_overlap -v`

Expected: FAIL because `sweeper.internet_archive` does not exist.

- [ ] **Step 3: Implement the minimal partition functions**

Use SHA-256 over the exact UTF-8 Archive identifier, convert the full hexadecimal digest to an integer, and apply the configured modulus. Reject empty identifiers, modulus below 2, empty bucket sets, and buckets outside `[0, modulus)`.

- [ ] **Step 4: Run the focused partition tests**

Run: `.venv/bin/pytest tests/test_internet_archive.py -k partition -v`

Expected: PASS.

- [ ] **Step 5: Write failing URL and response parsing tests**

Assert that the URL contains the exact configured query, identity-only fields, stable `identifier asc` sorting, `rows<=500`, and cursor continuation. Assert that malformed payloads, missing `response.docs`, repeated cursors, and records without identifiers fail closed.

- [ ] **Step 6: Run the query tests and confirm the expected failures**

Run: `.venv/bin/pytest tests/test_internet_archive.py -k 'query or search_page' -v`

Expected: FAIL because query construction and parsing are not implemented.

- [ ] **Step 7: Implement query construction and typed parsing**

Use `urllib.parse.urlencode(..., doseq=True)`. Permit only fields explicitly configured by the caller. Return an immutable `ArchiveSearchPage(records, next_cursor, num_found)` and retain raw identifier/title/creator/alternate-title identity values without enrichment.

- [ ] **Step 8: Run the complete adapter test module**

Run: `.venv/bin/pytest tests/test_internet_archive.py -v`

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

```bash
git add src/sweeper/internet_archive.py tests/test_internet_archive.py tests/fixtures/internet_archive/search_page_1.json
git commit -m "feat: add deterministic Internet Archive discovery contract"
```

### Task 2: Resumable Generic Discovery Checkpoints

**Files:**
- Modify: `src/sweeper/internet_archive.py`
- Modify: `src/sweeper/model.py`
- Modify: `src/sweeper/config.py`
- Modify: `src/sweeper/cli.py`
- Modify: `tests/test_internet_archive.py`
- Create: `examples/internet-archive-texts.example.json`

**Interfaces:**
- Produces: `ArchiveDiscoveryConfig(query, fields, sort, rows, modulus, buckets, max_candidates, requests_per_second, checkpoint_root)`
- Produces: `discover_archive(config: ArchiveDiscoveryConfig, opener=urllib.request.urlopen) -> DiscoveryReport`
- Produces CLI: `sweeper archive-discover --config PATH --source SOURCE_ID`

- [ ] **Step 1: Write failing tests for post-partition candidate accounting**

Use two synthetic pages containing owned and unowned identifiers. Assert that `screenedOwned` increments only for owned records, the candidate ceiling stops exactly on an owned record, and the saved cursor points after the last consumed source record.

- [ ] **Step 2: Run the accounting tests and verify failure**

Run: `.venv/bin/pytest tests/test_internet_archive.py -k 'candidate_ceiling or cursor_resume' -v`

Expected: FAIL because discovery persistence is absent.

- [ ] **Step 3: Implement immutable checkpoints and atomic frontier state**

Write each checkpoint as canonical JSONL plus a SHA-256 receipt. Write the frontier through a temporary file and atomic rename. Refuse to overwrite a checkpoint with different bytes. On resume, verify every prior receipt before requesting the next page.

- [ ] **Step 4: Add failing transient-retry tests**

Inject an opener that raises HTTP 429, HTTP 503, `socket.timeout`, `ConnectionResetError`, and `http.client.IncompleteRead` before succeeding. Assert bounded attempts, exponential delays through an injected sleeper, and a distinct `transient-deferred` result after exhaustion. Assert HTTP 400 and JSON schema errors fail closed without retry.

- [ ] **Step 5: Run retry tests and verify failure**

Run: `.venv/bin/pytest tests/test_internet_archive.py -k retry -v`

Expected: FAIL because retry classification is absent.

- [ ] **Step 6: Implement bounded respectful retries**

Implement four total attempts, source-configured base pacing, capped exponential backoff with deterministic injectable jitter in tests, explicit `socket.timeout` and `IncompleteRead` handling, and no catch-all transient classification.

- [ ] **Step 7: Add configuration and CLI validation**

Reject `rows` outside `1..500`, nonpositive or excessive rates, invalid bucket sets, negative ceilings, mutable sort without stable identifier tie-breaker, and checkpoint roots outside the resolved configured workspace.

- [ ] **Step 8: Run Web Sweeper’s complete suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass with no warnings.

- [ ] **Step 9: Commit Task 2**

```bash
git add src/sweeper/internet_archive.py src/sweeper/model.py src/sweeper/config.py src/sweeper/cli.py tests/test_internet_archive.py examples/internet-archive-texts.example.json
git commit -m "feat: add resumable Internet Archive discovery"
```

### Task 3: Campaign Configuration, Lock, and Durable State

**Files:**
- Create: `/Users/jesusnewos/Downloads/PineCone/tool/ia_codex_config.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/tool/ia_codex_state.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/test_ia_codex_state.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/fixtures/internet_archive/campaign.json`

**Interfaces:**
- Produces: `CampaignConfig.load(path: Path) -> CampaignConfig`
- Produces: `CampaignState(root: Path)`
- Produces: `CampaignState.acquire_lock() -> context manager`
- Produces: `CampaignState.record_decision(decision: ItemDecision) -> None`
- Produces: `CampaignState.authoritative_counts() -> dict[str, int]`
- Produces: `CampaignState.verify_integrity() -> StateVerification`

- [ ] **Step 1: Write failing immutable-configuration tests**

Assert exact campaign ID, authoritative root, 20,000 accepted ceiling, 400,000 post-partition ceiling, buckets `{0..4}`, checkpoint size at most 500, and rejection of attempts to change immutable values after initialization.

- [ ] **Step 2: Run the configuration test and confirm failure**

Run: `python3 -m pytest test/test_ia_codex_state.py -k campaign -v`

Expected: FAIL because the modules do not exist.

- [ ] **Step 3: Implement `CampaignConfig` and canonical configuration receipts**

Resolve every path to an absolute path and require it to remain below `/Users/jesusnewos/Downloads/PineCone`. Hash canonical JSON and preserve the receipt on first initialization.

- [ ] **Step 4: Write failing lock and append-only correction tests**

Assert a second process cannot acquire the state lock, an acceptance followed by a rejection counts as rejected, the growth timestamp changes only on net authoritative accepted growth, and truncated JSONL or SQLite integrity failure blocks recovery.

- [ ] **Step 5: Run state tests and confirm the expected failures**

Run: `python3 -m pytest test/test_ia_codex_state.py -k 'lock or correction or health or integrity' -v`

Expected: FAIL because durable state is absent.

- [ ] **Step 6: Implement SQLite state and append-only journals**

Use explicit transactions, WAL mode, stable source ID primary keys, latest-decision views derived from ordered journal sequence, and an `fcntl.flock(..., LOCK_EX | LOCK_NB)` held for the coordinator lifetime. Flush and `os.fsync` journal writes before committing matching SQLite state.

- [ ] **Step 7: Run all state tests**

Run: `python3 -m pytest test/test_ia_codex_state.py -v`

Expected: PASS.

### Task 4: Receipt-Preserving Clean Initialization and Capacity Gate

**Files:**
- Modify: `/Users/jesusnewos/Downloads/PineCone/tool/ia_codex_state.py`
- Modify: `/Users/jesusnewos/Downloads/PineCone/test/test_ia_codex_state.py`

**Interfaces:**
- Produces: `clean_initialize(root: Path, config: CampaignConfig, dry_run: bool) -> ResetReceipt`
- Produces: `check_capacity(path: Path, estimated_atomic_bytes: int) -> CapacityResult`
- Produces: `restore_reset(receipt_path: Path) -> RestoreResult`

- [ ] **Step 1: Write failing clean-reset preservation tests**

Create a fixture root with disposable cursors/cache and protected accepted artifacts, frozen units, validation attestations, staging receipts, and publication evidence. Assert dry-run changes nothing; real reset moves only disposable paths; the receipt contains pre-move SHA-256 values; and restoration reproduces every moved byte.

- [ ] **Step 2: Run reset tests and confirm failure**

Run: `python3 -m pytest test/test_ia_codex_state.py -k clean -v`

Expected: FAIL because clean initialization is absent.

- [ ] **Step 3: Implement clean initialization and restoration**

Require the lock, verify zero recorded child workers, use a timestamped sibling recovery bundle under the campaign root, and refuse any unclassified path rather than deleting it. Never call `unlink` or recursive deletion for reset content.

- [ ] **Step 4: Write failing capacity tests**

Inject disk usage values and assert failure at exactly 1 GiB, below twice the atomic working set, and success only when both inequalities are satisfied. Assert the failed gate creates no retrieval/output files.

- [ ] **Step 5: Implement and verify the capacity gate**

Run: `python3 -m pytest test/test_ia_codex_state.py -k capacity -v`

Expected: PASS after the minimal implementation.

### Task 5: Fresh Live Snapshot and Gate 0 First

**Files:**
- Create: `/Users/jesusnewos/Downloads/PineCone/tool/ia_codex_live.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/test_ia_codex_live.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/fixtures/internet_archive/live_index.json`

**Interfaces:**
- Consumes: `tool/codex_live_prefilter.py` shared identity normalization and verified Live snapshot format.
- Produces: `load_verified_live_snapshot(index: Path, receipt: Path) -> LiveIdentityIndex`
- Produces: `gate0(candidate: DiscoveryIdentity, live: LiveIdentityIndex) -> Gate0Decision`

- [ ] **Step 1: Write failing snapshot-verification tests**

Assert missing receipts, stale manifests, mismatched byte hashes, mismatched record counts, and incomplete required identity coverage fail closed.

- [ ] **Step 2: Run snapshot tests and confirm failure**

Run: `python3 -m pytest test/test_ia_codex_live.py -k snapshot -v`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement verified snapshot loading using shared identity code**

Do not duplicate normalization logic. Import the shared implementation and bind its version/hash into the lane's Live index manifest.

- [ ] **Step 4: Write failing zero-download overlap tests**

Inject spies for full metadata and source retrieval. Assert source-ID, alternate-title plus author, normalized title-author, work family, edition family, and whole/part matches return `gate0-live-first`, call neither spy, and preserve the matched Live record/key.

- [ ] **Step 5: Implement Gate 0 and run the complete Live module tests**

Run: `python3 -m pytest test/test_ia_codex_live.py -v`

Expected: PASS.

### Task 6: Archive Metadata, Derivative Selection, and Retrieval

**Files:**
- Create: `/Users/jesusnewos/Downloads/PineCone/tool/ia_codex_source.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/test_ia_codex_source.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/fixtures/internet_archive/item_metadata.json`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/fixtures/internet_archive/item_metadata_no_text.json`

**Interfaces:**
- Produces: `fetch_item_metadata(identifier: str, client: ArchiveClient) -> ArchiveItem`
- Produces: `select_text_derivative(item: ArchiveItem, policy: DerivativePolicy) -> DerivativeDecision`
- Produces: `retrieve_derivative(decision: DerivativeDecision, store: Path, client: ArchiveClient) -> SourceArtifact`

- [ ] **Step 1: Write failing derivative allowlist and winner tests**

Assert deterministic preference among qualifying textual files, full considered-file evidence, rejection when only PDF/DjVu/images exist, rejection of partial chapter/page artifacts, and preservation of provider format, size, mtime, and source filename.

- [ ] **Step 2: Run selection tests and confirm failure**

Run: `python3 -m pytest test/test_ia_codex_source.py -k derivative -v`

Expected: FAIL because selection is absent.

- [ ] **Step 3: Implement the versioned fail-closed selector**

Keep the preference table as immutable versioned data. File extensions and Archive format labels must both agree with an approved textual path; ambiguity defers the item.

- [ ] **Step 4: Write failing retrieval integrity and retry tests**

Assert content-addressed storage, SHA-256 receipts, poisoned-cache eviction, four bounded transient attempts, no retry for eligibility/software failures, and removal of incomplete temporary files.

- [ ] **Step 5: Implement retrieval and run source tests**

Run: `python3 -m pytest test/test_ia_codex_source.py -v`

Expected: PASS.

### Task 7: Christian, Rights, English, and Completeness Screening

**Files:**
- Create: `/Users/jesusnewos/Downloads/PineCone/tool/ia_codex_screen.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/test_ia_codex_screen.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/fixtures/internet_archive/screen_cases.jsonl`

**Interfaces:**
- Produces: `screen_metadata(item: ArchiveItem, policy: ScreeningPolicy) -> MetadataDecision`
- Produces: `screen_body(text: str, metadata: ArchiveItem, policy: ScreeningPolicy) -> BodyDecision`

- [ ] **Step 1: Write adversarial relevance tests**

Include Christian/Christmas, Hans Christian Andersen, Baptist/Baptiste, mission/diplomatic mission, saint/place names, Trinity/institution names, Jewish editions with generic Bible wording, technical church-calendar references, Christian Science, sermons, devotion, missions, Christian biography, and institutional administrative records.

- [ ] **Step 2: Run relevance tests and confirm failure**

Run: `python3 -m pytest test/test_ia_codex_screen.py -k relevance -v`

Expected: FAIL because screening is absent.

- [ ] **Step 3: Implement field-aware relevance and counter-signals**

Return exact qualifying fields/phrases and every decisive counter-signal. Popularity and downloads may be retained for later ranking but never enter the eligibility result.

- [ ] **Step 4: Write failing rights tests**

Assert ordinary pre-1931 authoritative evidence passes the date layer, absent/conflicting/later dates defer, a modern revised/corrected transcription defers despite an old underlying work, and item-level rights evidence is retained.

- [ ] **Step 5: Implement the rights layer and verify focused tests**

Run: `python3 -m pytest test/test_ia_codex_screen.py -k rights -v`

Expected: PASS.

- [ ] **Step 6: Write failing body-language, word-floor, and completeness tests**

Cover mislabeled foreign OCR, mixed-language bodies, English parallel editions, fewer than 1,000 words, repeated OCR garbage, missing endings, and complete early-modern English.

- [ ] **Step 7: Implement body screening and run the full module**

Run: `python3 -m pytest test/test_ia_codex_screen.py -v`

Expected: PASS.

### Task 8: Complete-Text Conversion and Provenance

**Files:**
- Create: `/Users/jesusnewos/Downloads/PineCone/tool/ia_codex_convert.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/test_ia_codex_convert.py`
- Create fixture source files under: `/Users/jesusnewos/Downloads/PineCone/test/fixtures/internet_archive/formats/`

**Interfaces:**
- Produces: `convert_source(artifact: SourceArtifact, output_root: Path) -> ManuscriptArtifact`
- Produces manuscript JSON plus `source-manifest.json` containing source bytes hash, selected member hashes, converter version, word count, and normalized content hash.

- [ ] **Step 1: Write failing TXT, HTML, EPUB, and XML conversion tests**

Assert source reading order is preserved, navigation/chrome is excluded only by deterministic structure rules, no source words are summarized or modernized, and malformed/unsafe archives fail closed.

- [ ] **Step 2: Run conversion tests and confirm failure**

Run: `python3 -m pytest test/test_ia_codex_convert.py -v`

Expected: FAIL because converters are absent.

- [ ] **Step 3: Implement minimal format converters and provenance manifests**

Use standard-library parsers and ZIP path traversal protection. Unsupported approved formats remain explicitly deferred until their own tested converter is added; they are never guessed.

- [ ] **Step 4: Add source-to-reader sequence and rebuild-hash tests**

Assert the normalized ordered source-word sequence equals the manuscript sequence and rebuilding from unchanged source bytes produces identical hashes.

- [ ] **Step 5: Run conversion tests**

Run: `python3 -m pytest test/test_ia_codex_convert.py -v`

Expected: PASS.

### Task 9: Deterministic Coordinator and Deduplication

**Files:**
- Create: `/Users/jesusnewos/Downloads/PineCone/tool/ia_codex_coordinator.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/test_ia_codex_coordinator.py`

**Interfaces:**
- Consumes: public adapter checkpoint manifests and Tasks 3–8 interfaces.
- Produces: `run_checkpoint(context: CoordinatorContext, checkpoint: Path) -> CheckpointReport`
- Produces: `run_campaign(context: CoordinatorContext) -> CampaignRunReport`
- Produces: `freeze_unit(context: CoordinatorContext, ceiling: int = 1000) -> FrozenUnitReceipt`

- [ ] **Step 1: Write failing gate-order tests**

Use injected event-recording dependencies and assert exact order: identity freeze, Gate 0, full metadata, relevance/rights, derivative selection, retrieval, conversion/body screen, content dedup, serialized decision. Assert any failed gate prevents all later calls.

- [ ] **Step 2: Run gate-order tests and confirm failure**

Run: `python3 -m pytest test/test_ia_codex_coordinator.py -k gate_order -v`

Expected: FAIL because the coordinator is absent.

- [ ] **Step 3: Implement one deterministic checkpoint loop**

Permit asynchronous immutable retrieval/conversion results, but commit decisions in stable checkpoint order. Individual reject/defer/duplicate outcomes continue to the next candidate.

- [ ] **Step 4: Write failing global deduplication tests**

Cover exact content, source IDs, alternate identifiers, normalized title-author, edition families, canonical Scripture/classic families, broad ambiguous prefix keys, and targeted same-author containment. Assert broad keys generate review candidates but do not automatically merge distinct volumes.

- [ ] **Step 5: Implement shared-index deduplication and run focused tests**

Reuse version-bound prior-root/Live indexes. Persist every winner and collision reason. Do not rely on MinHash alone for whole/part decisions.

- [ ] **Step 6: Write failing interruption and health tests**

Interrupt after discovery, retrieval, conversion, acceptance, and unit-freeze boundaries. Assert recovery repeats no completed expensive step, retains append-only history, restores the exact frontier, and changes the health timestamp only for accepted-count growth.

- [ ] **Step 7: Implement recovery-aware campaign continuation**

Rotate zero-yield query families after 2,000 newly screened candidates, preserve low-yield survivors, and keep the current unfinished unit across rotation.

- [ ] **Step 8: Write and satisfy frozen-unit binding tests**

Assert membership, journal frontier, policy version, query receipts, Live snapshot hash, source hashes, manuscript hashes, and converter version all affect the frozen receipt. Assert no unit larger than 1,000 can be frozen at initial campaign tier.

- [ ] **Step 9: Run coordinator and all PineCone tests**

Run: `python3 -m pytest test/test_ia_codex_*.py -q`

Expected: all tests pass.

### Task 10: Operator CLI and Runbook

**Files:**
- Create: `/Users/jesusnewos/Downloads/PineCone/tool/internet_archive_codex.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/test/test_internet_archive_codex_cli.py`
- Create: `/Users/jesusnewos/Downloads/PineCone/docs/INTERNET_ARCHIVE_CODEX_RUNBOOK.md`

**Interfaces:**
- Produces CLI commands: `init`, `clean-init`, `preflight`, `run`, `status`, `recover`, `freeze-unit`, `verify-state`.

- [ ] **Step 1: Write failing CLI contract tests**

Assert each command's required arguments, machine-readable JSON output, nonzero fail-closed exits, `--dry-run` behavior, immutable limits/partition, and absence of `publish`, `upload`, or Live mutation commands.

- [ ] **Step 2: Run CLI tests and confirm failure**

Run: `python3 -m pytest test/test_internet_archive_codex_cli.py -v`

Expected: FAIL because the CLI is absent.

- [ ] **Step 3: Implement the thin CLI**

Keep logic in the focused modules. Emit exact counts for discovered, owned,
Gate-0-surviving, rights-qualified, retrieved, converted, accepted, frozen,
validated, staged, published, and live-verified; later states remain zero unless
separate evidence exists.

- [ ] **Step 4: Write the operator runbook**

Document exact campaign initialization, preflight, dry run, foreground run,
detached run, status inspection, safe stop, recovery, capacity pause, reset
restoration, unit freezing, report interpretation, and the explicit no-publication
boundary.

- [ ] **Step 5: Run CLI and full PineCone tests**

Run: `python3 -m pytest test/test_ia_codex_*.py test/test_internet_archive_codex_cli.py -q`

Expected: all tests pass.

### Task 11: Full Verification and No-Write Rehearsals

**Files:**
- Modify only tests or implementation files when a rehearsal exposes a defect; every fix begins with a failing regression test.
- Create runtime reports under the campaign root only after `init`.

**Interfaces:**
- Consumes all prior task interfaces.
- Produces exact verification reports and launch/no-launch decision evidence.

- [ ] **Step 1: Verify authoritative location, Git state, and capacity**

Run:

```bash
cd /Users/jesusnewos/Downloads/PineCone
pwd -P
df -Pk .
git -C web-sweeper status --short
```

Expected: exact authoritative path; capacity exceeds both configured gates; only intended changes are present.

- [ ] **Step 2: Run the public Web Sweeper suite**

Run: `cd /Users/jesusnewos/Downloads/PineCone/web-sweeper && .venv/bin/pytest -q`

Expected: all tests pass with zero failures.

- [ ] **Step 3: Run the PineCone Internet Archive suite**

Run: `cd /Users/jesusnewos/Downloads/PineCone && python3 -m pytest test/test_ia_codex_*.py test/test_internet_archive_codex_cli.py -q`

Expected: all tests pass with zero failures.

- [ ] **Step 4: Initialize and verify the campaign in dry-run mode**

Run:

```bash
python3 tool/internet_archive_codex.py clean-init \
  --campaign-config work/internet_archive_christian_general_h0_4_20260831.json \
  --dry-run
```

Expected: exact root, limits, buckets, protected/disposable classification, zero mutations, and `publicationAuthorized=false`.

- [ ] **Step 5: Create the new campaign root**

Run the same `clean-init` command without `--dry-run` only after confirming the resolved root equals the specification. Verify its configuration and creation receipt hashes immediately.

- [ ] **Step 6: Run fresh Live preflight and zero-download Gate 0 rehearsal**

Export a fresh complete Live snapshot using the existing PineCone snapshot tool, bind its receipt, and run:

```bash
python3 tool/internet_archive_codex.py preflight \
  --campaign-config work/internet_archive_christian_general_h0_4_20260831.json \
  --zero-download-rehearsal
```

Expected: verified Live snapshot, partition contract, capacity pass, lock available, Archive query reachable, known Live fixtures caught, and source downloads equal zero.

- [ ] **Step 7: Run fixture interruption/recovery rehearsal**

Terminate the fixture coordinator at the configured fault injection point, verify the former process is dead, run `recover --dry-run`, then resume and compare final journals/hashes to an uninterrupted fixture run.

Expected: byte-identical authoritative membership and no duplicated expensive work.

- [ ] **Step 8: Run a small staging-disabled live Archive pilot**

Use the real query and partition with a separate pilot ceiling of at most 25 owned candidates. Permit source retrieval only after Gate 0 and metadata/rights/relevance pass. Do not freeze, stage, upload, or publish.

Expected: respectful request behavior; exact per-gate counts; preserved source/hash evidence for any eligible artifacts; no Live or staging mutation.

- [ ] **Step 9: Review pilot evidence against launch gates**

Confirm substantive Christian precision, item-level rights evidence, approved derivative selection, English-body quality, completeness, request pacing, recovery, capacity, and accepted-count growth. If any gate fails, add a failing regression test and fix before continuing.

- [ ] **Step 10: Commit the completed restoration**

Commit public Web Sweeper changes in its repository. Record PineCone-local file hashes and runtime receipts in the campaign report; do not initialize a new Git repository at the PineCone root.

### Task 12: Launch and Observe the 20,000/400,000 Lane

**Files:**
- Runtime state only under `/Users/jesusnewos/Downloads/PineCone/work/judah_library/imports/internet_archive_christian_general_hash0_4_20260831`.

**Interfaces:**
- Consumes: verified campaign configuration and all Task 11 launch receipts.
- Produces: locked coordinator process, durable checkpoints, accepted artifacts, exact status reports, and frozen staging-only units.

- [ ] **Step 1: Recheck companion collision boundary**

Verify this lane owns only buckets `{0..4}`, the companion owns only `{5,6}`, the state roots differ, and neither coordinator references the other's root.

- [ ] **Step 2: Start exactly one coordinator**

Launch the documented `run` command from `/Users/jesusnewos/Downloads/PineCone`, record PID/session and configuration hash, and verify a second launch fails on the advisory lock.

- [ ] **Step 3: Observe the first deterministic checkpoint**

Verify at most 500 owned candidates, Gate 0 precedes all expensive work, individual failures continue, journal/SQLite counts agree, and authoritative accepted membership grows before calling the lane healthy.

- [ ] **Step 4: Observe the first frozen unit or truthful partial progress**

If 1,000 accepted works are reached, freeze exactly one staging-only unit and verify its receipt. If the campaign has not reached 1,000, report exact progress and continue; do not lower gates or freeze an accidental remainder.

- [ ] **Step 5: Produce the first campaign report**

Report discovered and owned candidates; Gate 0 overlaps; rights, relevance,
language, derivative, completeness, word-floor, content-duplicate, and transient
outcomes; accepted count and word statistics; query/frontier; throughput by
stage; capacity; recovery events; frozen count; and explicit zeros for published
and live-verified.

---

## Plan Self-Review

- Every design requirement maps to a task: public adapter (Tasks 1–2), state and
  recovery (Tasks 3–4), Gate 0 (Task 5), source policy (Task 6), eligibility
  (Task 7), conversion (Task 8), coordination/deduplication (Task 9), commands
  (Task 10), verification (Task 11), and launch (Task 12).
- Interfaces use consistent campaign, state, source, screening, conversion, and
  coordinator names across tasks.
- The plan contains no Live publication command or implicit publication path.
- The clean reset is recoverable and never deletes unclassified or protected
  evidence.
- The 20,000/400,000 limits and hash buckets are immutable and tested.
