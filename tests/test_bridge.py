import unittest

from sweeper.bridge import decision


class BridgeSwitchTests(unittest.TestCase):
    def test_switch_activates_at_fifty_percent(self):
        self.assertFalse(decision(49, 100, True)["active"])
        self.assertTrue(decision(50, 100, True)["active"])
        self.assertTrue(decision(99, 100, True)["active"])

    def test_off_never_activates_and_integrity_boundaries_remain(self):
        result = decision(100, 100, False)
        self.assertFalse(result["active"])
        self.assertIn("live-verification", result["neverBypasses"])


if __name__ == "__main__":
    unittest.main()
