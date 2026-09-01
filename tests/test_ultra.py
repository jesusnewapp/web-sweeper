import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sweeper.ultra import (IdempotencyConflict, LeaseBusy, LeaseLost, ProgressRejected,
                           UltraRuntime, CapabilityRouter, CapabilityUnavailable,
                           IntegrityFailure)
from sweeper.ultra import production


class Clock:
    def __init__(self):
        self.value = 1_000_000

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds * 1000


class SweeperUltraTest(unittest.TestCase):
    def runtime(self, directory, clock):
        return UltraRuntime(Path(directory) / "ultra.sqlite3", clock_ms=clock)

    def writer_binding(self, unit_id="unit", member_ids=None):
        members = sorted(member_ids or ["item-a", "item-b"])
        return {"taskId":"task","laneId":"lane","unitId":unit_id,
            "catalogSha256":"a"*64,"manuscriptSetSha256":"b"*64,
            "validationAttestationSha256":"c"*64,
            "stagingVerificationSha256":"d"*64,
            "membershipSha256":"e"*64,"itemCount":len(members),
            "memberIds":members,"startingLiveRevision":"revision"}

    def verification_receipt(self, binding):
        return {key: binding[key] for key in (
            "itemCount", "memberIds", "membershipSha256", "catalogSha256",
            "manuscriptSetSha256", "validationAttestationSha256",
            "stagingVerificationSha256",
        )}

    def test_ultra_first_then_v2_fallback_without_hiding_integrity_failure(self):
        router = CapabilityRouter()
        routed = router.execute("acquisition",
            ultra=lambda: (_ for _ in ()).throw(CapabilityUnavailable()),
            v2=lambda: "v2-acquired")
        self.assertEqual("v2", routed.executor)
        self.assertTrue(routed.fallback_used)
        with self.assertRaises(IntegrityFailure):
            router.execute("rights",
                ultra=lambda: (_ for _ in ()).throw(IntegrityFailure("rights failed")),
                v2=lambda: "must-not-run")

    def test_event_commands_are_idempotent_and_hash_chained(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock(); runtime = self.runtime(directory, clock)
            first = runtime.enqueue(work_id="one", lane_id="lane", kind="source",
                priority=1, payload={"cursor": 0}, command_key="enqueue-one")
            second = runtime.enqueue(work_id="one", lane_id="lane", kind="source",
                priority=1, payload={"cursor": 0}, command_key="enqueue-one")
            self.assertEqual(first, second)
            self.assertEqual(1, runtime.snapshot()["throughEventSeq"])
            with self.assertRaises(IdempotencyConflict):
                runtime.enqueue(work_id="one", lane_id="lane", kind="different",
                    priority=1, payload={"cursor": 0}, command_key="enqueue-one")
            self.assertEqual(runtime.snapshot()["stateSha256"], runtime.snapshot()["stateSha256"])
            runtime.close()

    def test_one_runner_lease_and_stale_fencing(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock(); runtime = self.runtime(directory, clock)
            first = runtime.acquire_lease(resource="runner:lane", owner_id="alpha",
                ttl_seconds=10, binding={"lane": "lane"}, command_key="claim-alpha")
            with self.assertRaises(LeaseBusy):
                runtime.acquire_lease(resource="runner:lane", owner_id="beta",
                    ttl_seconds=10, binding={"lane": "lane"}, command_key="claim-beta-early")
            clock.advance(11)
            second = runtime.acquire_lease(resource="runner:lane", owner_id="beta",
                ttl_seconds=10, binding={"lane": "lane"}, command_key="claim-beta-late")
            self.assertGreater(second["fence"], first["fence"])
            runtime.close()

    def test_old_acquisition_retry_never_aliases_same_owner_new_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock(); runtime = self.runtime(directory, clock)
            first = runtime.acquire_lease(resource="runner:lane", owner_id="alpha",
                ttl_seconds=10, binding={"unit": "a"}, command_key="claim-a")
            repeated = runtime.acquire_lease(resource="runner:lane", owner_id="alpha",
                ttl_seconds=10, binding={"unit": "a"}, command_key="claim-a")
            self.assertEqual(first, repeated)
            clock.advance(11)
            second = runtime.acquire_lease(resource="runner:lane", owner_id="alpha",
                ttl_seconds=10, binding={"unit": "b"}, command_key="claim-b")
            self.assertGreater(second["fence"], first["fence"])
            with self.assertRaises(LeaseLost):
                runtime.acquire_lease(resource="runner:lane", owner_id="alpha",
                    ttl_seconds=10, binding={"unit": "a"}, command_key="claim-a")
            runtime.close()

    def test_expired_or_released_acquisition_retry_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock(); runtime = self.runtime(directory, clock)
            runtime.acquire_lease(resource="runner:expired", owner_id="alpha",
                ttl_seconds=10, binding={"lane": "expired"},
                command_key="claim-expired")
            clock.advance(11)
            with self.assertRaises(LeaseLost):
                runtime.acquire_lease(resource="runner:expired", owner_id="alpha",
                    ttl_seconds=10, binding={"lane": "expired"},
                    command_key="claim-expired")

            released = runtime.acquire_lease(resource="runner:released",
                owner_id="alpha", ttl_seconds=10, binding={"lane": "released"},
                command_key="claim-released")
            runtime.release_lease(resource="runner:released", owner_id="alpha",
                fence=int(released["fence"]), command_key="release-released")
            with self.assertRaises(LeaseLost):
                runtime.acquire_lease(resource="runner:released", owner_id="alpha",
                    ttl_seconds=10, binding={"lane": "released"},
                    command_key="claim-released")
            runtime.close()

    def test_heartbeat_does_not_prevent_pivot_but_proof_completes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock(); runtime = self.runtime(directory, clock)
            runtime.enqueue(work_id="one", lane_id="lane", kind="source",
                priority=1, payload={}, command_key="enqueue")
            clock.advance(599)
            runtime.record_heartbeat(work_id="one", actor_id="runner",
                telemetry={"pid": 1}, command_key="heartbeat")
            self.assertEqual([], runtime.evaluate_pivots()["requested"])
            clock.advance(1)
            pivot_id = runtime.evaluate_pivots()["requested"][0]
            runtime.acknowledge_pivot(pivot_id=pivot_id, actor_id="runner",
                action_family="advance-cursor", checkpoint_sha256="a" * 64,
                command_key="ack")
            with self.assertRaises(ProgressRejected):
                runtime.record_progress(work_id="one", actor_id="runner",
                    proof_kind="heartbeat", evidence={"pid": 2}, command_key="bad-proof",
                    pivot_id=pivot_id)
            runtime.record_progress(work_id="one", actor_id="runner",
                proof_kind="cursor-advanced", evidence={"ordinal": 2, "pageSha256": "b" * 64},
                command_key="good-proof", pivot_id=pivot_id)
            pivot = runtime.snapshot()["state"]["pivots"][0]
            self.assertEqual("succeeded", pivot["status"])
            runtime.close()

    def test_failed_pivot_requires_different_action_family(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = Clock(); runtime = self.runtime(directory, clock)
            runtime.enqueue(work_id="one", lane_id="lane", kind="source",
                priority=1, payload={}, command_key="enqueue")
            clock.advance(600); first = runtime.evaluate_pivots()["requested"][0]
            runtime.acknowledge_pivot(pivot_id=first, actor_id="runner",
                action_family="advance-cursor", checkpoint_sha256="a" * 64,
                command_key="ack-one")
            clock.advance(600); self.assertEqual([first], runtime.evaluate_pivots()["escalated"])
            second = runtime.evaluate_pivots()["requested"][0]
            with self.assertRaises(ValueError):
                runtime.acknowledge_pivot(pivot_id=second, actor_id="runner",
                    action_family="advance-cursor", checkpoint_sha256="a" * 64,
                    command_key="ack-two-same")
            runtime.acknowledge_pivot(pivot_id=second, actor_id="runner",
                action_family="rotate-source", checkpoint_sha256="a" * 64,
                command_key="ack-two-new")
            runtime.close()

    def test_production_writer_requires_complete_binding_and_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory, Clock())
            binding = self.writer_binding()
            with self.assertRaises(ValueError):
                runtime.acquire_writer(owner_id="writer",binding=binding,
                    free_bytes=5*1024**3,largest_working_set_bytes=1,
                    ttl_seconds=60,command_key="low-capacity")
            lease=runtime.acquire_writer(owner_id="writer",binding=binding,
                free_bytes=7*1024**3,largest_working_set_bytes=1024**3,
                ttl_seconds=60,command_key="writer-ready")
            self.assertEqual("production-writer",lease["resource"])
            with self.assertRaises(LeaseBusy):
                runtime.acquire_writer(owner_id="other",binding=binding,
                    free_bytes=7*1024**3,largest_working_set_bytes=1024**3,
                    ttl_seconds=60,command_key="writer-race")
            runtime.close()

    def test_writer_rejects_other_unit_subset_and_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.runtime(directory, Clock())
            binding = self.writer_binding()
            lease = runtime.acquire_writer(owner_id="writer", binding=binding,
                free_bytes=7*1024**3, largest_working_set_bytes=1024**3,
                ttl_seconds=60, command_key="writer-ready")
            fence = int(lease["fence"])
            with self.assertRaises(ValueError):
                runtime.begin_publish(owner_id="writer", fence=fence,
                    unit_id="unit-b", command_key="begin-wrong-unit")
            runtime.begin_publish(owner_id="writer", fence=fence,
                unit_id="unit", command_key="begin-unit")
            receipt = self.verification_receipt(binding)
            with self.assertRaises(ValueError):
                runtime.record_live_verification(owner_id="writer", fence=fence,
                    unit_id="unit", verified_items=["item-a"], receipt=receipt,
                    command_key="verify-subset")
            bad_receipt = dict(receipt); bad_receipt["catalogSha256"] = "f" * 64
            with self.assertRaises(ValueError):
                runtime.record_live_verification(owner_id="writer", fence=fence,
                    unit_id="unit", verified_items=binding["memberIds"],
                    receipt=bad_receipt, command_key="verify-wrong-hash")
            runtime.record_live_verification(owner_id="writer", fence=fence,
                unit_id="unit", verified_items=binding["memberIds"],
                receipt=receipt, command_key="verify-exact")
            runtime.release_writer(owner_id="writer", fence=fence,
                command_key="release-exact")
            runtime.close()

    def test_production_adapter_requires_one_global_authority_across_workspaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_workspace = root / "workspace-a"; first_workspace.mkdir()
            second_workspace = root / "workspace-b"; second_workspace.mkdir()
            authority = root / "global-authority" / "production.sqlite3"
            entered = threading.Event(); finish = threading.Event()
            failures = []

            def fake_unit(workspace, owner_id, starting_live_revision):
                member = "%s-item" % Path(workspace).name
                binding = self.writer_binding(
                    unit_id="unit-%s" % Path(workspace).name,
                    member_ids=[member],
                )
                binding["taskId"] = owner_id
                binding["startingLiveRevision"] = starting_live_revision
                return {"unitId": binding["unitId"], "binding": binding,
                    "approved": {member: "9" * 64}, "workingSetBytes": 1,
                    "stagingReceipt": {"passed": True}}

            def blocking_promote(workspace, publisher, verifier):
                entered.set()
                if not finish.wait(5):
                    raise RuntimeError("test publisher wait timed out")
                member = "%s-item" % Path(workspace).name
                return {"published": [member], "verified": [member],
                        "items": {member: "9" * 64}, "passed": True}

            def first_publish():
                try:
                    production.promote_with_v2(
                        first_workspace, ["publisher"], ["verifier"],
                        "writer-a", "revision-a", lease_authority=authority,
                    )
                except Exception as error:  # pragma: no cover - assertion below
                    failures.append(error)

            with patch.object(production, "_validated_unit", side_effect=fake_unit), \
                    patch.object(production, "v2_promote", side_effect=blocking_promote), \
                    patch.object(production.shutil, "disk_usage",
                                 return_value=SimpleNamespace(free=7*1024**3)):
                worker = threading.Thread(target=first_publish)
                worker.start()
                self.assertTrue(entered.wait(5))
                with self.assertRaises(LeaseBusy):
                    production.promote_with_v2(
                        second_workspace, ["publisher"], ["verifier"],
                        "writer-b", "revision-b", lease_authority=authority,
                    )
                finish.set(); worker.join(5)
                self.assertFalse(worker.is_alive())
            self.assertEqual([], failures)

    def test_production_adapter_fails_closed_without_global_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"; workspace.mkdir()
            with patch.dict(production.os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "global production lease"):
                    production.promote_with_v2(
                        workspace, ["publisher"], ["verifier"],
                        "writer", "revision",
                    )
                with self.assertRaisesRegex(ValueError, "workspace-local"):
                    production.promote_with_v2(
                        workspace, ["publisher"], ["verifier"],
                        "writer", "revision",
                        lease_authority=workspace / "ultra/production.sqlite3",
                    )


if __name__ == "__main__":
    unittest.main()
