import unittest

from sweeper.continuation import mandatory_reload


class MandatoryReloadTests(unittest.TestCase):
    def test_receipt_advances_and_disposable_files_cannot_veto(self):
        decision = mandatory_reload(10, 1)
        self.assertEqual("mandatory-reload", decision["action"])
        self.assertEqual(11, decision["nextUnit"])
        self.assertFalse(decision["disposableFilesMayVetoReload"])
        self.assertEqual("dynamic-discovery", decision["discoveryFallback"])

    def test_live_owner_is_adopted_without_overlapping_it(self):
        decision = mandatory_reload(10, live_owner=True)
        self.assertEqual("adopt-live-owner", decision["action"])
        self.assertEqual(["live-owner"], decision["safetyHolds"])

    def test_real_safety_hold_preserves_and_retries(self):
        decision = mandatory_reload(3, shared_integrity_ok=False,
                                    capacity_available=False)
        self.assertEqual("preserve-and-retry", decision["action"])
        self.assertEqual(["shared-integrity", "capacity-floor"],
                         decision["safetyHolds"])


if __name__ == "__main__":
    unittest.main()
