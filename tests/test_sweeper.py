import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
if str(PUBLIC_ROOT) not in sys.path:
    sys.path.insert(0, str(PUBLIC_ROOT))

from sweeper.config import load_config
from sweeper.cli import initialize, load_project, save_project
from sweeper.cli import daemon
from sweeper.engine import policy_reason
from sweeper.engine import verify_download
from sweeper.engine import run
from sweeper.dock import cleanup_verified_staging, membership, validate_attestation
from sweeper.model import Candidate, Policy
from sweeper.state import State
from sweeper.translation import LANGUAGES, capabilities, engine_variable
from sweeper.translation_fleet import TranslationFleet
from sweeper.continuation import build_plan
from sweeper.nurture import preserve as nurture_preserve
from sweeper.activity import report as activity_report
from sweeper.enforcer import evaluate
from goodies.indexer import export_json as export_goodies_json, index_jsonl as index_goodies_jsonl


class SweeperV2Test(unittest.TestCase):
    def test_activity_log_preserves_current_and_historical_dispositions(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(Path(directory)/"state.sqlite3")
            state.record(source_id="lane",item_id="one",url="u",title="One",status="failed",
                         reason="temporary",updated_at="now")
            state.record(source_id="lane",item_id="one",url="u",title="One",status="accepted",
                         updated_at="later",digest="abc",size=3,local_path="p")
            state.close(); report=activity_report(Path(directory),10)
            self.assertEqual(2,report["totalEvents"])
            self.assertEqual(["failed","accepted"],[row["status"] for row in report["recent"]])

    def test_nurture_threshold_preserves_membership_and_raises_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); items={f"source:{n}":hashlib.sha256(str(n).encode()).hexdigest() for n in range(30)}
            result=nurture_preserve(root,items,"validated",30)
            self.assertTrue(result["active"]); self.assertEqual(30,result["members"])
            self.assertGreaterEqual(result["nurtureIntensityPercent"],80)
            self.assertEqual("none",result["tertiaryAuthority"])
            self.assertFalse(result["advisory"])
            self.assertTrue(Path(result["snapshot"]).exists())
            self.assertTrue(result["singleItemNeverBlocksContinuation"])

    def test_pivot_enforcer_holds_source_and_translator_to_ten_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lanes = [{"lane": "source-one", "kind": "source", "required": True,
                      "counts": {"failed": 1}, "target": 50},
                     {"lane": "translator", "kind": "translation", "required": True,
                      "counts": {"queued": 2}}]
            first = evaluate(root, lanes, current_epoch=1000)
            self.assertFalse(first["enforcementRequired"])
            almost = evaluate(root, lanes, current_epoch=1599)
            self.assertFalse(almost["enforcementRequired"])
            overdue = evaluate(root, lanes, current_epoch=1600)
            self.assertTrue(overdue["enforcementRequired"])
            self.assertEqual(["source-one", "translator"], overdue["overdue"])
            self.assertTrue(overdue["doesNotChoosePivot"])

    def test_goodies_indexer_is_incremental_and_exports_ui_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "records.jsonl"; database = root / "index.sqlite3"
            source.write_text(json.dumps({"id": "record-1", "title": "Neutral Example",
                "category": "Operator Category", "text": "searchable material"}) + "\n")
            self.assertEqual(1, index_goodies_jsonl(database, source, "staged")["indexed"])
            self.assertEqual(1, index_goodies_jsonl(database, source, "staged")["unchanged"])
            self.assertEqual(1, export_goodies_json(database, root / "index.json")["recordCount"])

    def test_default_two_plus_two_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            example = Path(__file__).parents[1] / "examples/sweeper.example.json"
            data = json.loads(example.read_text())
            data["workspace"] = "data"
            path = root / "sweeper.json"
            path.write_text(json.dumps(data))
            config = load_config(path)
            self.assertEqual(2, config.major_slots)
            self.assertEqual(2, config.minor_slots)
            self.assertEqual(4, len(config.sources))
            self.assertEqual([50, 100, 50, 50], [source.batch_size for source in config.sources])

    def test_source_and_translation_batch_sizes_are_operator_selected_but_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = {"workspace": "data", "user_agent": "Test Institute (test@example.org)",
                "layout": {"major_slots": 2, "minor_slots": 1}, "policy": {},
                "sources": [{"id": "one", "slot": 1, "lane": "major",
                    "manifest": "items.jsonl", "batch_size": 1000}]}
            path = root / "sweeper.json"; path.write_text(json.dumps(base))
            self.assertEqual(1000, load_config(path).sources[0].batch_size)
            base["sources"][0]["batch_size"] = 1001
            path.write_text(json.dumps(base))
            with self.assertRaisesRegex(ValueError, "between 1 and 1,000"):
                load_config(path)

    def test_light_layout_can_expand_to_six(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); example = Path(__file__).parents[1] / "examples/sweeper.example.json"
            data = json.loads(example.read_text()); data["workspace"] = "data"
            data["layout"]["minor_slots"] = 6
            for slot in range(3, 7):
                data["sources"].append({"id": f"minor-{slot}", "slot": slot, "lane": "minor",
                    "manifest": "./manifests/minor-one.jsonl", "workers": 1,
                    "requests_per_second": 0.5})
            path = root / "sweeper.json"; path.write_text(json.dumps(data))
            config = load_config(path)
            self.assertEqual(6, config.minor_slots); self.assertEqual(8, len(config.sources))

    def test_policy_fails_closed_on_missing_rights(self):
        item = Candidate("s", "i", "https://example.test/i", language="en", license="")
        reason = policy_reason(item, Policy(languages=["en"], licenses=["CC0-1.0"]))
        self.assertEqual("missing-license", reason)

    def test_media_family_policy_and_rights_evidence(self):
        policy = Policy(languages=["en"], licenses=["CC0-1.0"],
                        media_types=["audio/*", "video/*"],
                        artifact_classes=["music", "video"],
                        require_rights_evidence=True)
        missing = Candidate("s", "a", "https://example.test/a.flac", language="en",
                            license="CC0-1.0", media_type="audio/flac",
                            artifact_class="music")
        self.assertEqual("missing-rights-evidence", policy_reason(missing, policy))
        audio = Candidate("s", "a", "https://example.test/a.flac", language="en",
                          license="CC0-1.0", rights_evidence_url="https://example.test/rights",
                          media_type="audio/flac", artifact_class="music")
        self.assertEqual("", policy_reason(audio, policy))
        comic = Candidate("s", "c", "https://example.test/c.cbz", language="en",
                          license="CC0-1.0", rights_evidence_url="https://example.test/rights",
                          media_type="application/vnd.comicbook+zip", artifact_class="comic")
        self.assertEqual("media-type-not-allowed", policy_reason(comic, policy))

    def test_rights_free_rom_policy_requires_rights_metadata_checksum_and_valid_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive = root / "game.zip"
            with zipfile.ZipFile(archive, "w") as output: output.writestr("game.nes", b"homebrew")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            policy = Policy(languages=["en"], licenses=["HOMEBREW-REDISTRIBUTION-GRANTED"],
                media_types=["application/zip"], artifact_classes=["game-rom"],
                require_rights_evidence=True, required_metadata_fields=["platform",
                    "redistribution_scope", "expected_sha256"], allowed_file_extensions=[".zip"],
                require_expected_sha256=True, verify_zip_integrity=True)
            item = Candidate("games", "one", archive.as_uri(), "Family Homebrew", "en",
                "HOMEBREW-REDISTRIBUTION-GRANTED", "https://example.test/license",
                "application/zip", "game-rom", "open-public", {"platform":"NES",
                    "redistribution_scope":"redistribute",
                    "expected_sha256":digest})
            self.assertEqual("", policy_reason(item, policy))
            self.assertEqual("", verify_download(archive, item, policy, digest))
            commercial = Candidate("games", "two", archive.as_uri(), "Commercial", "en",
                "UNKNOWN", "", "application/zip", "game-rom", "open-public", {})
            self.assertEqual("license-not-allowed", policy_reason(commercial, policy))

    def test_content_hash_dedup_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(Path(directory) / "state.sqlite3")
            state.record(source_id="a", item_id="1", url="u", title="t", status="accepted",
                         updated_at="now", digest="abc", size=3, local_path="p")
            self.assertEqual("a:1", state.hash_owner("abc"))
            state.close()

    def test_reviewer_is_disabled_by_default(self):
        self.assertEqual([], Policy().reviewer_command)

    def test_init_creates_editable_config_and_manifest_example(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sweeper.json"
            initialize(path)
            raw = json.loads(path.read_text())
            self.assertEqual(0, raw["project"]["overall_target_items"])
            self.assertEqual(0, raw["project"]["daily_target_items"])
            self.assertTrue(path.exists())
            self.assertTrue((path.parent / "manifests/source.example.jsonl").exists())
            self.assertEqual([], json.loads(path.read_text())["sources"])

    def test_end_to_end_local_manifest_is_resumable_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload.txt"
            payload.write_text("solid institutional record", encoding="utf-8")
            manifest = root / "items.jsonl"
            records = [
                {"id": "one", "url": payload.as_uri(), "title": "One", "language": "en",
                 "license": "CC0-1.0", "media_type": "text/plain"},
                {"id": "two", "url": payload.as_uri(), "title": "Two", "language": "en",
                 "license": "CC0-1.0", "media_type": "text/plain"},
            ]
            manifest.write_text("\n".join(json.dumps(value) for value in records) + "\n")
            config_path = root / "sweeper.json"
            config_path.write_text(json.dumps({
                "workspace": "data", "user_agent": "Test Institute (test@example.org)",
                "layout": {"major_slots": 2, "minor_slots": 6},
                "policy": {"languages": ["en"], "licenses": ["CC0-1.0"],
                           "media_types": ["text/plain"], "minimum_bytes": 1},
                "sources": [{"id": "local", "slot": 1, "lane": "minor",
                             "manifest": "items.jsonl", "requests_per_second": 10.0}],
            }))
            config = load_config(config_path)
            first = run(config)
            second = run(config)
            self.assertEqual({"accepted": 1, "duplicate": 1}, first["counts"])
            self.assertEqual(first["counts"], second["counts"])

    def test_source_batch_size_caps_new_acceptances_and_records_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest = root / "items.jsonl"; rows = []
            for number in range(3):
                payload = root / f"payload-{number}.txt"
                payload.write_text(f"distinct record {number}")
                rows.append({"id": str(number), "url": payload.as_uri(), "language": "en",
                    "license": "CC0-1.0", "media_type": "text/plain"})
            manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            config_path = root / "sweeper.json"
            config_path.write_text(json.dumps({"workspace": "data",
                "user_agent": "Test Institute (test@example.org)",
                "layout": {"major_slots": 2, "minor_slots": 1},
                "policy": {"languages": ["en"], "licenses": ["CC0-1.0"],
                    "media_types": ["text/plain"]},
                "sources": [{"id": "local", "slot": 1, "lane": "major",
                    "manifest": "items.jsonl", "batch_size": 2,
                    "requests_per_second": 10.0}]}))
            first = run(load_config(config_path)); second = run(load_config(config_path))
            self.assertEqual(2, first["counts"]["accepted"])
            self.assertEqual(3, second["counts"]["accepted"])
            events = activity_report(root / "data", 20)["recent"]
            completed = [row for row in events if row["event"] == "source-batch-complete"]
            self.assertEqual([2, 1], [row["detail"]["acceptedThisBatch"] for row in completed])

    def test_daemon_once_records_health(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "sweeper.json"
            config_path.write_text(json.dumps({
                "workspace": "data", "user_agent": "Test Institute (test@example.org)",
                "layout": {"major_slots": 2, "minor_slots": 6},
                "policy": {}, "sources": [],
            }))
            self.assertEqual(0, daemon(config_path, 5, once=True))
            health = json.loads((root / "data/daemon-state.json").read_text())
            self.assertEqual("healthy", health["status"])
            self.assertEqual(0, health["consecutiveFailures"])
            self.assertEqual(5, health["nextCheckSeconds"])

    def test_broken_source_does_not_stop_later_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good_object = root / "good.txt"; good_object.write_text("good public record")
            good_manifest = root / "good.jsonl"
            good_manifest.write_text(json.dumps({"id": "good", "url": good_object.as_uri(),
                "language": "en", "license": "CC0-1.0", "media_type": "text/plain"}) + "\n")
            config_path = root / "sweeper.json"
            config_path.write_text(json.dumps({"workspace": "data",
                "user_agent": "Test Institute (test@example.org)",
                "layout": {"major_slots": 2, "minor_slots": 6},
                "policy": {"languages": ["en"], "licenses": ["CC0-1.0"],
                           "media_types": ["text/plain"]},
                "sources": [
                    {"id": "broken", "slot": 1, "lane": "major", "manifest": "missing.jsonl"},
                    {"id": "good", "slot": 2, "lane": "major", "manifest": "good.jsonl"}]}))
            result = run(load_config(config_path))
            self.assertEqual(1, result["counts"]["accepted"])
            self.assertFalse(result["sweeperBlocked"])
            self.assertEqual("bookkeep-item-or-source-and-continue", result["failureDisposition"])
            self.assertEqual("broken", result["sourceErrors"][0]["source"])
            self.assertTrue(Path(result["forecastHistory"]).exists())

    def test_continuation_pool_retains_partial_catch_and_reaches_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifests = []
            for number in (1, 2):
                obj = root / f"object-{number}.txt"; obj.write_text(f"public record {number}")
                manifest = root / f"manifest-{number}.jsonl"
                manifest.write_text(json.dumps({"id": str(number), "url": obj.as_uri(),
                    "language": "en", "license": "CC0-1.0", "media_type": "text/plain"}) + "\n")
                manifests.append(manifest.name)
            config_path = root / "sweeper.json"
            config_path.write_text(json.dumps({"workspace": "data",
                "user_agent": "Test Institute (test@example.org)",
                "layout": {"major_slots": 2, "minor_slots": 1},
                "policy": {"languages": ["en"], "licenses": ["CC0-1.0"],
                           "media_types": ["text/plain"]},
                "sources": [{"id": "light", "slot": 1, "lane": "minor",
                    "manifest": manifests[0], "continuation_manifests": [manifests[1]],
                    "target_items": 2, "requests_per_second": 10.0}]}))
            result = run(load_config(config_path))
            self.assertEqual(2, result["counts"]["accepted"])
            self.assertFalse(result["continuationRequired"])

    def test_translation_bridge_has_supported_languages_and_fails_closed(self):
        self.assertGreaterEqual(len(LANGUAGES), 25)
        for language in ("fr", "pl", "nl", "de", "es", "ja", "zh", "la"):
            self.assertIn(language, LANGUAGES)
        self.assertEqual("SWEEPER_TRANSLATOR_IT_EN", engine_variable("it", "en"))
        status = capabilities()
        self.assertEqual(len(LANGUAGES) * (len(LANGUAGES) - 1), len(status["pairs"]))

    def test_translation_fleet_validates_stages_hands_off_and_queues_next(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.txt"
            source.write_text("A complete source book for translation.")
            translator = root / "translator.py"
            translator.write_text("import json,sys\nr=json.load(sys.stdin)\n"
                "print(json.dumps({'translation':'Texto traducido completo.'}))\n")
            validator = root / "validator.py"
            validator.write_text("import json,sys\nr=json.load(sys.stdin)\ne=r['translation']\n"
                "print(json.dumps({'approved':True,'language':r['target_language'],"
                "'sha256':e['translation_sha256']}))\n")
            stager = root / "stager.py"
            stager.write_text("import json,sys\nr=json.load(sys.stdin)\n"
                "print(json.dumps({'staged':sorted(r['items'])}))\n")
            notifier = root / "notifier.py"
            notifier.write_text("import json,sys\njson.load(sys.stdin)\n"
                "print(json.dumps({'acknowledged':True}))\n")
            config_path = root / "sweeper.json"
            config_path.write_text(json.dumps({"workspace": "data",
                "user_agent": "Test Institute (test@example.org)",
                "layout": {"major_slots": 2, "minor_slots": 1}, "policy": {}, "sources": [],
                "translation": {"enabled": True, "batch_size": 1,
                    "staging_collection": "translation_stage", "target_languages": ["es"],
                    "notifier_command": [sys.executable, str(notifier)],
                    "validator_command": [sys.executable, str(validator)],
                    "stager_command": [sys.executable, str(stager)]}}))
            config = load_config(config_path); state = State(config.workspace / "state.sqlite3")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            state.record(source_id="books", item_id="one", url="u", title="Book",
                status="accepted", updated_at=datetime.now(timezone.utc).isoformat(),
                digest=digest, size=source.stat().st_size, local_path=str(source)); state.close()
            variable = engine_variable("en", "es"); previous = os.environ.get(variable)
            os.environ[variable] = f"{sys.executable} {translator}"
            try:
                fleet = TranslationFleet(config)
                queued = fleet.queue("es")
                result = fleet.run_batch("es")
                status = fleet.status(); fleet.close()
            finally:
                if previous is None: os.environ.pop(variable, None)
                else: os.environ[variable] = previous
            self.assertEqual(1, queued["count"])
            self.assertEqual(1, result["validated"]); self.assertEqual(1, result["staged"])
            self.assertEqual(1, status["counts"]["staged"])
            self.assertTrue(Path(result["handoff"]).exists())
            self.assertEqual(0, result["nextBatch"]["count"])

    def test_staging_dock_requires_exact_hash_bound_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); obj = root / "object"; obj.write_bytes(b"staged")
            digest = hashlib.sha256(obj.read_bytes()).hexdigest()
            state = State(root / "state.sqlite3")
            state.record(source_id="s", item_id="1", url="u", title="t", status="accepted",
                         updated_at="now", digest=digest,
                         size=6, local_path=str(obj))
            items = state.accepted_items(); state.close()
            attestation = root / "approval.json"
            attestation.write_text(json.dumps({"approved": True, "reviewed_by": "Reviewer",
                "reviewed_at": "now", "items": membership(items)}))
            result = validate_attestation(root, attestation)
            self.assertTrue(result["passed"]); self.assertEqual(1, result["item_count"])

    def test_continuation_plan_is_fleet_aware_and_advisory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config_path = root / "sweeper.json"
            config_path.write_text(json.dumps({"workspace": "data",
                "user_agent": "Test Institute (test@example.org)",
                "layout": {"major_slots": 2, "minor_slots": 2}, "policy": {},
                "sources": [
                    {"id": "major", "slot": 1, "lane": "major", "manifest": "a.jsonl"},
                    {"id": "light", "slot": 1, "lane": "minor", "manifest": "b.jsonl",
                     "target_items": 10, "estimated_eligible_items": 40,
                     "estimated_daily_items": 8}]}))
            config = load_config(config_path); state = State(config.workspace / "state.sqlite3")
            state.record(source_id="light", item_id="1", url="u", title="t", status="failed",
                         updated_at="now", reason="timeout")
            plan = build_plan(config, state); state.close()
            self.assertEqual("fleet-aware-continuation-advisor", plan["model"])
            self.assertGreaterEqual(len(plan["pool"]), 20)
            self.assertEqual(len(plan["pool"]), len(set(plan["pool"])))
            self.assertEqual(2, len(plan["decisions"]))
            light = next(row for row in plan["decisions"] if row["source"] == "light")
            self.assertTrue(light["autonomy"]["advisoryOnly"])
            self.assertTrue(light["autonomy"]["specificActionNeverForced"])
            self.assertEqual(["quality", "continuation"], plan["invariants"])
            self.assertIn("quality", plan["mandatoryInvariants"])
            self.assertIn("continuation", plan["mandatoryInvariants"])
            self.assertEqual(40, plan["project"]["forecast"]["estimatedHighQualityEligibleItems"])
            self.assertEqual(8, plan["project"]["forecast"]["estimatedDailyHighQualityItems"])
            self.assertTrue(plan["project"]["forecast"]["advisoryOnly"])
            self.assertEqual("still-calculating", plan["project"]["forecast"]["status"])
            self.assertGreater(plan["project"]["forecast"]["approxDaysUntilFirstNumber"], 0)
            intelligence = plan["sourceIntelligence"]
            self.assertEqual(2, intelligence["counts"]["active"])
            self.assertFalse(intelligence["depletion"]["entireInternetExhausted"])
            self.assertEqual("partial", intelligence["depletion"]["confidence"])
            self.assertEqual("sweeper", light["operatorAssistance"]["decisionOwner"])
            self.assertFalse(light["operatorAssistance"]["operatorCanChangeSourceOrMode"])
            self.assertTrue(light["operatorAssistance"]["sweeperMayAcceptDeclineDeferOrReplace"])
            self.assertEqual("steady", light["breathing"]["mode"])
            self.assertIn(light["recommendedAction"], plan["pool"])

    def test_project_goals_and_saved_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config_path = root / "sweeper.json"
            config_path.write_text(json.dumps({"workspace": "data",
                "user_agent": "Test Institute (test@example.org)",
                "project": {"name": "Codex Project", "overall_target_items": 100_000_000_000,
                            "daily_target_items": 3000},
                "layout": {"major_slots": 2, "minor_slots": 2}, "policy": {},
                "sources": []}))
            config = load_config(config_path)
            self.assertEqual(100_000_000_000, config.overall_target_items)
            saved = save_project(config_path, root / "projects", "Codex Project")
            self.assertTrue(saved.exists())
            loaded = load_project(root / "projects", "Codex Project", root / "loaded.json")
            self.assertEqual(3000, load_config(loaded).daily_target_items)
            raw = json.loads(config_path.read_text())
            raw["project"]["overall_target_items"] = 100_000_000_001
            config_path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "100,000,000,000"):
                load_config(config_path)

    def test_mature_forecast_and_ranked_source_intelligence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config_path = root / "sweeper.json"
            config_path.write_text(json.dumps({"workspace": "data",
                "user_agent": "Test Institute (test@example.org)",
                "project": {"name": "Research", "overall_target_items": 100,
                            "daily_target_items": 10},
                "layout": {"major_slots": 2, "minor_slots": 1}, "policy": {},
                "sources": [{"id": "one", "slot": 1, "lane": "minor", "manifest": "m.jsonl",
                    "estimated_eligible_items": 80, "estimated_daily_items": 8}]}))
            config = load_config(config_path); config.workspace.mkdir(parents=True)
            (config.workspace / "discovered-sources.json").write_text(json.dumps({
                "candidate_sites": [
                    {"domain": "small.example", "confidence": "medium",
                     "estimated_eligible_items": 10},
                    {"domain": "large.example", "confidence": "high",
                     "estimated_eligible_items": 1000}], "errors": []}))
            state = State(config.workspace / "state.sqlite3")
            old = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
            state.record(source_id="one", item_id="1", url="u", title="t",
                         status="accepted", updated_at=old)
            plan = build_plan(config, state); state.close()
            self.assertEqual("estimate-available", plan["project"]["forecast"]["status"])
            self.assertEqual("mature", plan["project"]["forecast"]["maturity"])
            potential = plan["sourceIntelligence"]["potentialSites"]
            self.assertEqual("large.example", potential[0]["domain"])
            self.assertEqual(1, potential[0]["advisoryRank"])

    def test_cleanup_requires_passing_exact_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "dock-promotion.json"):
                cleanup_verified_staging(root, ["unused"])
            (root / "dock-promotion.json").write_text(json.dumps({"passed": False,
                "published": ["s:1"], "verified": [], "items": {"s:1": "abc"}}))
            with self.assertRaisesRegex(ValueError, "live verification is incomplete"):
                cleanup_verified_staging(root, ["unused"])


if __name__ == "__main__":
    unittest.main()
