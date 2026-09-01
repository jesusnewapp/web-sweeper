import json
import tempfile
import unittest
from pathlib import Path

from sweeper.transition import (evaluate, evaluate_positive_remainder_idle, evaluate_throughput,
                                plan_slot_continuation, validate_source_slots)


class SourceTransitionTest(unittest.TestCase):
    def test_configurable_ordered_source_slots(self):
        config = {"source_slot_count": 2, "source_slots": [
            {"slot": 1, "id": "one", "acquisition_target": 1000,
             "launch_command": ["adapter-one"], "allow_partial_on_exhaustion": True},
            {"slot": 2, "id": "two", "acquisition_target": 1000,
             "launch_command": ["adapter-two"], "allow_partial_on_exhaustion": True},
        ]}
        self.assertEqual(2, len(validate_source_slots(config)))
        self.assertEqual("restart-current-source",
                         plan_slot_continuation(config, 1, 1000, False)["action"])
        transition = plan_slot_continuation(config, 1, 73, True)
        self.assertEqual("advance-to-next-source", transition["action"])
        self.assertEqual("two", transition["source"])
        self.assertTrue(transition["stageRemainder"])

    def test_slot_count_must_match(self):
        with self.assertRaises(ValueError):
            validate_source_slots({"source_slot_count": 2, "source_slots": [
                {"slot": 1, "id": "one", "acquisition_target": 1000,
                 "launch_command": ["adapter-one"]}
            ]})

    def test_exhausted_empty_source_advances_without_staging(self):
        config = {"source_slot_count": 2, "source_slots": [
            {"slot": 1, "id": "one", "acquisition_target": 1000,
             "launch_command": ["adapter-one"], "allow_partial_on_exhaustion": True},
            {"slot": 2, "id": "two", "acquisition_target": 1000,
             "launch_command": ["adapter-two"], "allow_partial_on_exhaustion": True},
        ]}
        transition = plan_slot_continuation(config, 1, 0, True)
        self.assertEqual("advance-to-next-source", transition["action"])
        self.assertFalse(transition["stageRemainder"])

    def test_throughput_marker_uses_normalized_hundred(self):
        policy = {"baseline_seconds_per_100": 466,
                  "slow_source_multiplier": 2.0,
                  "minimum_accepted_sample": 100}
        healthy = evaluate_throughput(policy, 100, 900)
        self.assertEqual("within-marker", healthy["status"])
        slow = evaluate_throughput(policy, 200, 1900)
        self.assertEqual("slow-right-now", slow["status"])
        self.assertEqual("transition-at-next-safe-receipt-boundary", slow["nextAction"])

    def test_throughput_waits_for_full_sample(self):
        result = evaluate_throughput({"baseline_seconds_per_100": 466}, 99, 1200)
        self.assertEqual("collecting-sample", result["status"])

    def test_idle_positive_remainder_uses_normal_staging_route(self):
        waiting = evaluate_positive_remainder_idle(883, 2000, 899)
        self.assertFalse(waiting["freezePositiveRemainder"])
        automatic = evaluate_positive_remainder_idle(883, 2000, 900)
        self.assertTrue(automatic["freezePositiveRemainder"])
        self.assertEqual("idle-positive-remainder", automatic["reason"])
        manual = evaluate_positive_remainder_idle(10, 2000, 1, operator_switch=True)
        self.assertTrue(manual["freezePositiveRemainder"])
        self.assertIn("duplicate-screening", manual["neverBypasses"])

    def test_empty_remainder_never_stages_even_with_switch(self):
        result = evaluate_positive_remainder_idle(0, 2000, 9999, operator_switch=True)
        self.assertFalse(result["freezePositiveRemainder"])

    def test_waits_then_becomes_ready_from_exact_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "transition.json"
            config.write_text(json.dumps({"practice_mode": True, "routes": [{
                "from": "open-library", "to": "plymouth-brethren",
                "target": 1000, "allow_partial_on_exhaustion": True,
                "completion_receipt": "stage.json", "cleanup_receipt": "cleanup.json",
                "checkpoint": "checkpoint.json", "commands_are_placeholders": True,
            }]}))
            self.assertEqual(1, evaluate(config)["waiting"])
            (root / "stage.json").write_text(json.dumps({
                "staged": 50, "productionMutated": False, "sourceExhausted": True,
            }))
            (root / "cleanup.json").write_text(json.dumps({"bytesReclaimed": 0}))
            (root / "checkpoint.json").write_text(json.dumps({"cursor": 10}))
            result = evaluate(config)
            self.assertEqual(1, result["ready"])
            self.assertEqual("replace-placeholder-commands", result["routes"][0]["nextAction"])
            self.assertTrue(result["routes"][0]["partialUnit"])

    def test_partial_unit_requires_exhaustion_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "transition.json"
            config.write_text(json.dumps({"routes": [{"from": "a", "to": "b",
                "target": 1000, "allow_partial_on_exhaustion": True,
                "completion_receipt": "stage.json", "cleanup_receipt": "cleanup.json",
                "checkpoint": "checkpoint.json"}]}))
            (root / "stage.json").write_text(json.dumps({"staged": 999, "productionMutated": False}))
            (root / "cleanup.json").write_text(json.dumps({"bytesReclaimed": 1}))
            (root / "checkpoint.json").write_text("{}")
            with self.assertRaises(ValueError):
                evaluate(config)

    def test_rejects_nonisolated_staging_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "transition.json"
            config.write_text(json.dumps({"routes": [{"from": "a", "to": "b",
                "completion_receipt": "stage.json", "cleanup_receipt": "cleanup.json",
                "checkpoint": "checkpoint.json"}]}))
            (root / "stage.json").write_text(json.dumps({"staged": 1, "productionMutated": True}))
            (root / "cleanup.json").write_text(json.dumps({"bytesReclaimed": 1}))
            (root / "checkpoint.json").write_text("{}")
            with self.assertRaises(ValueError):
                evaluate(config)


if __name__ == "__main__":
    unittest.main()
