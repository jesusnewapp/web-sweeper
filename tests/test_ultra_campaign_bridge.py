import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PUBLIC_ROOT.parents[1]
if str(PUBLIC_ROOT) not in sys.path:
    sys.path.insert(0, str(PUBLIC_ROOT))

TOOL = PROJECT_ROOT / "tool/run_codex_ultra_campaign.py"
if not TOOL.exists():
    raise unittest.SkipTest(f"host integration tool unavailable: {TOOL}")
SPEC = importlib.util.spec_from_file_location("run_codex_ultra_campaign", TOOL)
campaign_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(campaign_module)

from sweeper.ultra import LeaseBusy


class Clock:
    def __init__(self):
        self.value = 1_000_000

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds * 1000


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class CodexUltraCampaignBridgeTest(unittest.TestCase):
    def campaign(self, project, owner, clock):
        return campaign_module.CodexUltraCampaign(
            project=project, owner_id=owner,
            runtime_path=project / "work/judah_library/cache/codex_ultra/runtime.sqlite3",
            clock_ms=clock,
        )

    def materialize_fresh_root(self, project, complete=True, requested_size=2,
                               acquisition_receipt=True):
        root = project / "work/judah_library/imports/codex_ultra_open_library_batch_0001"
        books = [
            {"id": "ol-one", "source": "Open Library", "title": "One"},
            {"id": "ol-two", "source": "Open Library", "title": "Two"},
        ]
        write_json(root / "catalog.json", {"books": books})
        write_json(root / "manuscripts/ol-one.json", {"id": "ol-one", "content": "one"})
        if complete:
            write_json(root / "manuscripts/ol-two.json", {"id": "ol-two", "content": "two"})
        if acquisition_receipt:
            live_path = (project / "work/judah_library/cache/codex_ultra/open_library/"
                         "live_batch_0001.json")
            corpus_hash = hashlib.sha256(b"[]").hexdigest()
            write_json(live_path, {
                "schemaVersion": 1, "projectId": "jesus-new-app",
                "collection": "judah_library_books",
                "generatedAt": "1970-01-01T00:16:41Z", "complete": True,
                "publishedBooks": 0, "corpusHashSha256": corpus_hash, "books": [],
            })
            write_json(root / "checkpoint.json", {
                "source": "Open Library", "complete": True,
                "target": requested_size, "accepted": books,
                "liveIndex": str(live_path.resolve()),
                "liveIndexPublishedBooks": 0,
            })
            write_json(root / "import_report.json", {
                "source": "Open Library", "complete": True,
                "target": requested_size, "prepared": len(books),
                "priorRootMode": "finalized-catalog-dedup-only",
                "liveIndexPublishedBooks": 0,
                "liveIndexGeneratedAt": "1970-01-01T00:16:41Z",
                "liveIndexCorpusHashSha256": corpus_hash,
            })
        return root

    def make_ready(self, campaign, root):
        exact = campaign.inspect_fresh_root(root)
        rows = []
        for book_id in exact["bookIds"]:
            row = {"id": book_id}
            row.update({field: True for field in campaign_module.APPROVAL_FIELDS})
            rows.append(row)
        review = {
            "source": "Open Library", "catalogSha256": exact["catalogSha256"],
            "allApproved": True, "booksReviewed": exact["manuscriptCount"],
            "reviewedBy": "test-reviewer", "reviewedAt": "now", "books": rows,
        }
        write_json(root / "metadata_review.json", review)
        write_json(root / "assisted_substantive_review.json", {
            "schemaVersion": 1, "source": "Open Library",
            "reviewMode": "assisted-substantive-text-review",
            "catalogSha256": exact["catalogSha256"],
            "manuscriptSetSha256": exact["manuscriptSetSha256"],
            "membershipSha256": exact["membershipSha256"],
            "manuscriptCount": exact["manuscriptCount"],
            "allReviewed": True, "errors": [],
            "reviewedBy": "test-assisted-reviewer", "reviewedAt": "now",
            "members": [{
                "id": book_id,
                "manuscriptSha256": exact["manuscriptHashes"][book_id],
                "textReviewed": True,
                "christianRelevanceApproved": True,
                "completeWorkApproved": True,
                "rightsEvidenceApproved": True,
            } for book_id in exact["bookIds"]],
        })
        review_sha = hashlib.sha256((root / "metadata_review.json").read_bytes()).hexdigest()
        assisted_sha = hashlib.sha256(
            (root / "assisted_substantive_review.json").read_bytes()
        ).hexdigest()
        write_json(root / "validation_report.json", {
            "source": "Open Library", "validatorVersion": "open-library-validator-6",
            "validationMode": "production-reviewed", "productionEligible": True,
            "passed": True, "publicationReady": True, "errors": [],
            "booksAudited": exact["manuscriptCount"],
            "exactBatchSizeRequired": exact["manuscriptCount"],
            "manualReviewDigestSha256": review_sha,
            "assistedReviewDigestSha256": assisted_sha,
            "validationAttestation": {
                "validatorVersion": "open-library-validator-6",
                "catalogSha256": exact["catalogSha256"],
                "manuscriptSetSha256": exact["manuscriptSetSha256"],
                "manuscriptCount": exact["manuscriptCount"],
            },
        })
        write_json(root / "staging_verification.json", {
            "collection": "judah_library_staging_books", "unit": root.name,
            "prepared": exact["manuscriptCount"], "staged": exact["manuscriptCount"],
            "verified": exact["manuscriptCount"], "productionMutated": False,
            "byteIdenticalToValidatedLocalArtifacts": True,
            "catalogSha256": exact["catalogSha256"],
            "manuscriptSetSha256": exact["manuscriptSetSha256"],
            "manuscriptCount": exact["manuscriptCount"],
            "membershipSha256": exact["membershipSha256"],
            "remoteDocumentIds": exact["bookIds"],
            "remoteDocumentIdsSha256": campaign_module._digest(exact["bookIds"]),
        })

    def test_fresh_namespace_starts_at_one_and_v2_is_only_a_worker_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            with self.campaign(project, "alpha", clock) as campaign:
                plan = campaign.plan(batches=2, batch_size=50)
                names = [Path(row["root"]).name for row in plan["freshBatches"]]
                self.assertEqual([
                    "codex_ultra_open_library_batch_0001",
                    "codex_ultra_open_library_batch_0002",
                ], names)
                self.assertFalse(any(name.startswith("open_library_christian_") for name in names))
                self.assertEqual(0, plan["commandsExecuted"])
                self.assertFalse(plan["productionMutated"])
                for unit in plan["freshBatches"]:
                    self.assertFalse(Path(unit["root"]).exists())
                    self.assertTrue(all(step["executor"] == "v2"
                                        for step in unit["capabilityWorkers"]))
                    self.assertTrue(all(step["fallbackUsed"]
                                        for step in unit["capabilityWorkers"]))
                    self.assertFalse(any("--publish" in step["argv"]
                                         for step in unit["capabilityWorkers"]))
                    acquisition = unit["capabilityWorkers"][1]
                    dedup_index = acquisition["argv"].index("--dedup-db") + 1
                    self.assertEqual(
                        str(Path(unit["root"]) / ".ultra_dedup.sqlite3"),
                        acquisition["argv"][dedup_index],
                    )
                self.assertEqual("production-writer", plan["productionWriter"]["resource"])

    def test_one_runner_per_lane_is_a_durable_ultra_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            first = self.campaign(project, "alpha", clock)
            second = self.campaign(project, "beta", clock)
            try:
                lease = first.acquire_runner()
                self.assertEqual("runner:codex-ultra-open-library", lease["resource"])
                with self.assertRaises(LeaseBusy):
                    second.acquire_runner(command_key="beta-runner")
            finally:
                first.close(); second.close()

    def test_expired_default_runner_attempt_is_reacquired_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            with self.campaign(project, "alpha", clock) as campaign:
                first = campaign.acquire_runner(ttl_seconds=60)
                clock.advance(61)
                recovered = campaign.acquire_runner(ttl_seconds=60)
                self.assertGreater(recovered["expires_at_ms"], first["expires_at_ms"])
                self.assertEqual("active", recovered["phase"])
                campaign._assert_runner(int(recovered["fence"]))
                attempts = campaign.runtime.db.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type='lease.acquired'"
                ).fetchone()[0]
                self.assertEqual(2, attempts)

    def test_pre_schema_assignment_time_comes_from_original_enqueue_event(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            with self.campaign(project, "alpha", clock) as campaign:
                runner = campaign.acquire_runner()
                work_id = campaign_module.CodexUltraCampaign._work_id(1)
                root = campaign._fresh_root(1).resolve()
                legacy_payload = campaign._immutable_payload(
                    root=root, batch_number=1, requested_size=2,
                )
                legacy_payload.pop("assignedAtMs")
                campaign._enqueue(work_id, legacy_payload, 1)
                enqueued = campaign.runtime.db.execute(
                    "SELECT occurred_at_ms FROM events WHERE aggregate_id=? "
                    "AND event_type='work.enqueued'",
                    (work_id,),
                ).fetchone()[0]
                clock.advance(30)
                self.assertEqual(
                    int(enqueued), campaign._assigned_at_ms(work_id, legacy_payload),
                )
                planned = campaign.plan_fresh_batches(
                    1, 2, int(runner["fence"]),
                )[0]
                self.assertEqual(int(enqueued), planned["assignedAtMs"])
                stored = campaign._work_payloads()[work_id]
                self.assertNotIn("assignedAtMs", stored)

    def test_partial_catalog_stays_in_acquisition_until_target_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            with self.campaign(project, "alpha", clock) as campaign:
                runner = campaign.acquire_runner()
                campaign.plan_fresh_batches(1, 50, int(runner["fence"]))
                self.materialize_fresh_root(project, requested_size=50)
                partial = campaign.plan_fresh_batches(1, 50, int(runner["fence"]))[0]
                self.assertFalse(partial["acquisitionComplete"])
                self.assertIsNone(partial["exact"])
                self.assertEqual(
                    ["gate0", "acquisition"],
                    [step["capability"] for step in partial["capabilityWorkers"]],
                )

    def test_fresh_batch_never_skips_validation_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            with self.campaign(project, "alpha", clock) as campaign:
                runner = campaign.acquire_runner()
                initial = campaign.plan_fresh_batches(1, 2, int(runner["fence"]))[0]
                root = self.materialize_fresh_root(project)
                refreshed = campaign.plan_fresh_batches(1, 2, int(runner["fence"]))[0]
                self.assertEqual(initial["workId"], refreshed["workId"])
                self.assertEqual(2, refreshed["exact"]["manuscriptCount"])
                self.assertFalse(refreshed["exact"]["validationReady"])
                validation = next(step for step in refreshed["capabilityWorkers"]
                                  if step["capability"] == "validation")
                self.assertIn("--automated-staging", validation["argv"])
                with self.assertRaises(campaign_module.CampaignIntegrityError):
                    campaign.reserve_production_writer(
                        refreshed["workId"], int(runner["fence"]), "live-revision",
                        free_bytes=7 * 1024 ** 3,
                    )

    def test_ready_fresh_batch_reserves_one_global_writer_without_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            first = self.campaign(project, "alpha", clock)
            try:
                runner = first.acquire_runner()
                unit = first.plan_fresh_batches(1, 2, int(runner["fence"]))[0]
                root = self.materialize_fresh_root(project)
                self.make_ready(first, root)
                refreshed = first.plan_fresh_batches(1, 2, int(runner["fence"]))[0]
                self.assertTrue(refreshed["exact"]["validationReady"])
                self.assertTrue(refreshed["exact"]["stagingReady"])
                reservation = first.reserve_production_writer(
                    unit["workId"], int(runner["fence"]), "revision-1",
                    free_bytes=7 * 1024 ** 3,
                )
                self.assertEqual("production-writer", reservation["resource"])
                self.assertEqual(refreshed["exact"]["bookIds"],
                                 reservation["binding"]["memberIds"])
                self.assertEqual(2, reservation["binding"]["itemCount"])
                self.assertEqual(refreshed["exact"]["membershipSha256"],
                                 reservation["binding"]["membershipSha256"])
                self.assertIsNone(reservation["publicationCommand"])
                self.assertFalse(reservation["publicationStarted"])
                self.assertNotIn("--publish", reservation["preflight"]["argv"])
                events = first.runtime.db.execute(
                    "SELECT event_type FROM events ORDER BY seq"
                ).fetchall()
                self.assertNotIn("publish.started", [row[0] for row in events])
                first.release_runner(int(runner["fence"]))
            finally:
                first.close()

            second = self.campaign(project, "beta", clock)
            try:
                runner = second.acquire_runner(command_key="beta-runner-after-release")
                unit = second.plan_fresh_batches(1, 2, int(runner["fence"]))[0]
                with self.assertRaises(LeaseBusy):
                    second.reserve_production_writer(
                        unit["workId"], int(runner["fence"]), "revision-1",
                        free_bytes=7 * 1024 ** 3, command_key="beta-writer-race",
                    )
            finally:
                second.close()

    def test_fresh_batch_fails_closed_on_catalog_manuscript_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            with self.campaign(project, "alpha", clock) as campaign:
                runner = campaign.acquire_runner()
                campaign.plan_fresh_batches(1, 2, int(runner["fence"]))
                self.materialize_fresh_root(project, complete=False)
                with self.assertRaises(campaign_module.CampaignIntegrityError):
                    campaign.plan_fresh_batches(1, 2, int(runner["fence"]))

    def test_legacy_batch_nineteen_is_not_seeded_into_ultra(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            legacy = project / "work/judah_library/imports/open_library_christian_0050_staging_batch_0019"
            write_json(legacy / "checkpoint.json", {
                "accepted": [{"id": "legacy-secret-member"}], "counter": 19,
                "catalogSha256": "f" * 64,
            })
            with self.campaign(project, "alpha", clock) as campaign:
                plan = campaign.plan(batches=1, batch_size=50)
                serialized_plan = json.dumps(plan, sort_keys=True)
                serialized_events = "\n".join(
                    row[0] for row in campaign.runtime.db.execute(
                        "SELECT payload_json FROM events ORDER BY seq"
                    ).fetchall()
                )
                self.assertFalse(plan["legacyStateImported"])
                self.assertEqual(1, plan["startsAtBatch"])
                self.assertNotIn("legacy-secret-member", serialized_plan + serialized_events)
                self.assertNotIn("batch_0019", serialized_plan + serialized_events)
                worker_cache = plan["freshBatches"][0]["capabilityWorkers"][1]["argv"]
                self.assertIn(str((project / "work/judah_library/cache/codex_ultra/workers/open_library").resolve()),
                              worker_cache)
                self.assertIn("--prior-roots-dedup-only", worker_cache)
                self.assertFalse(hasattr(campaign, "adopt_carryover"))

    def test_membership_freezes_only_after_reviewed_zero_error_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            with self.campaign(project, "alpha", clock) as campaign:
                runner = campaign.acquire_runner()
                unit = campaign.plan_fresh_batches(1, 2, int(runner["fence"]))[0]
                root = self.materialize_fresh_root(project)
                campaign.plan_fresh_batches(1, 2, int(runner["fence"]))
                before = campaign.runtime.db.execute(
                    "SELECT COUNT(*) FROM events WHERE aggregate_id=? "
                    "AND event_type='work.progressed' AND payload_json LIKE ?",
                    (unit["workId"], '%"proofKind":"membership-frozen"%'),
                ).fetchone()[0]
                self.assertEqual(0, before)

                self.make_ready(campaign, root)
                campaign.plan_fresh_batches(1, 2, int(runner["fence"]))
                after = campaign.runtime.db.execute(
                    "SELECT COUNT(*) FROM events WHERE aggregate_id=? "
                    "AND event_type='work.progressed' AND payload_json LIKE ?",
                    (unit["workId"], '%"proofKind":"membership-frozen"%'),
                ).fetchone()[0]
                self.assertEqual(1, after)

    def test_automated_metadata_approval_cannot_replace_assisted_text_review(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); clock = Clock()
            with self.campaign(project, "alpha", clock) as campaign:
                runner = campaign.acquire_runner()
                campaign.plan_fresh_batches(1, 2, int(runner["fence"]))
                root = self.materialize_fresh_root(project)
                self.make_ready(campaign, root)
                (root / "assisted_substantive_review.json").unlink()
                blocked = campaign.plan_fresh_batches(1, 2, int(runner["fence"]))[0]
                self.assertTrue(blocked["exact"]["metadataReviewBound"])
                self.assertFalse(blocked["exact"]["reviewBound"])
                self.assertFalse(blocked["exact"]["validationReady"])
                self.assertEqual(
                    "assisted-review-required",
                    blocked["capabilityWorkers"][1]["executor"],
                )
                frozen = campaign.runtime.db.execute(
                    "SELECT COUNT(*) FROM events WHERE aggregate_id=? "
                    "AND event_type='work.progressed' AND payload_json LIKE ?",
                    (blocked["workId"], '%"proofKind":"membership-frozen"%'),
                ).fetchone()[0]
                self.assertEqual(0, frozen)


if __name__ == "__main__":
    unittest.main()
