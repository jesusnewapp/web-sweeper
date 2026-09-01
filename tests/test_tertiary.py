import json
import tempfile
import unittest
from pathlib import Path

from sweeper.config import load_config
from sweeper.tertiary import (adapter_view, inquisitive_read, measure_blocker,
                              nurture_intensity, observe)


class TertiaryTest(unittest.TestCase):
    def test_initial_nurture_scale_anchors(self):
        self.assertEqual(10, nurture_intensity(50))
        self.assertEqual(20, nurture_intensity(100))
        self.assertEqual(50, nurture_intensity(1000))
        self.assertEqual(75, nurture_intensity(2000))
        self.assertEqual(100, nurture_intensity(10000))

    def test_nurture_can_outmeasure_friction_but_not_integrity(self):
        shortfall = measure_blocker(
            "exact-target-shortfall-with-positive-survivors", 75)
        self.assertTrue(shortfall["nurtureMeetsMeasuredStrength"])
        self.assertTrue(shortfall["continuityOvercomeEligible"])
        duplicate = measure_blocker("duplicate-evidence", 100)
        self.assertTrue(duplicate["nurtureMeetsMeasuredStrength"])
        self.assertFalse(duplicate["continuityOvercomeEligible"])
        self.assertTrue(duplicate["integrityBoundary"])

    def test_unknown_blocker_fails_closed_as_measurement(self):
        unknown = measure_blocker("future-unknown", 100)
        self.assertEqual("unknown-fail-closed", unknown["class"])
        self.assertFalse(unknown["continuityOvercomeEligible"])
    def config(self, root: Path, enabled=False, inquisitive=False, adapter=False):
        payload = {
            "workspace": "./data",
            "user_agent": "Test Sweeper (test@example.org)",
            "project": {"name": "test"},
            "layout": {"major_slots": 2, "minor_slots": 2},
            "policy": {}, "translation": {}, "sources": [],
            "tertiary": {"enabled": enabled, "inquisitive_enabled": inquisitive,
                         "adapter_enabled": adapter,
                         "signals": ["nurture", "pivot", "continuation"]},
        }
        path = root / "sweeper.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_config(path)

    def test_default_off_preserves_legacy_execution_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary))
            result = observe(config)
            self.assertFalse(result["enabled"])
            self.assertFalse((config.workspace / "tertiary-observations.json").exists())
            self.assertFalse(adapter_view(config)["attached"])

    def test_enabled_field_is_powerless_and_optional(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary), True, True, True)
            field = observe(config)
            self.assertEqual(field["authority"], "none")
            self.assertFalse(field["advisory"])
            self.assertFalse(field["executionCoupling"])
            self.assertFalse(field["canOpenGate"])
            self.assertFalse(field["canCloseGate"])
            self.assertFalse(field["canSelectRoute"])
            self.assertFalse(field["canStartOrStopProcess"])
            read = inquisitive_read(config)
            self.assertTrue(read["available"])
            self.assertTrue(read["optional"])
            adapter = adapter_view(config)
            self.assertTrue(adapter["attached"])
            self.assertFalse(adapter["adapterExecutesActions"])
            self.assertTrue(adapter["hostRetainsDecisionAuthority"])

    def test_adapter_exposes_bounded_permission_without_executing(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.config(Path(temporary), True, True, True)
            config.workspace.mkdir(parents=True)
            (config.workspace / "tertiary-blockers.json").write_text(json.dumps([{
                "kind": "exact-target-shortfall-with-positive-survivors",
                "nurturePercent": 75,
                "evidence": {"accepted": 1988, "target": 2000},
            }]))
            observe(config)
            adapter = adapter_view(config)
            self.assertEqual(1, len(adapter["continuityPermissions"]))
            permission = adapter["continuityPermissions"][0]
            self.assertTrue(permission["hostMayChoose"])
            self.assertFalse(permission["adapterExecuted"])

    def test_adapter_cannot_be_enabled_without_field(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "adapter cannot be enabled"):
                self.config(Path(temporary), False, True, True)


if __name__ == "__main__":
    unittest.main()
