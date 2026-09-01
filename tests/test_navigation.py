import unittest
from datetime import datetime, timedelta, timezone

from sweeper.navigation import (StagedUnit, navigation_index, normalize_queries,
                                staged_pool_plan)


class NavigationTest(unittest.TestCase):
    def test_query_pool_is_ordered_unique_and_bounded(self):
        values = [" Sermons,  English ", "Christian stories", "christian STORIES"]
        self.assertEqual(("Sermons, English", "Christian stories"), normalize_queries(values))

    def test_query_advances_only_after_exhaustion_or_one_hour_without_growth(self):
        checked = datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
        queries = ("one", "two")
        self.assertEqual((0, "candidate-growth-active"), navigation_index(
            queries, 0, checked - timedelta(minutes=59), checked_at=checked))
        self.assertEqual((1, "candidate-growth-stalled-one-hour"), navigation_index(
            queries, 0, checked - timedelta(hours=1), checked_at=checked))
        self.assertEqual((1, "configured-pages-exhausted"), navigation_index(
            queries, 0, checked, checked_at=checked, pages_exhausted=True))

    def test_staged_pool_uses_largest_exact_unit_first_and_half_target(self):
        plan = staged_pool_plan([
            StagedUnit("legacy-42", 42), StagedUnit("current-1981", 1981),
            StagedUnit("current-159", 159),
        ])
        self.assertEqual(["current-1981", "current-159", "legacy-42"],
                         [unit.unit_id for unit in plan["units"]])
        self.assertEqual(2182, plan["pendingBooks"])
        self.assertEqual(1091, plan["minimumCycleBooks"])
        self.assertEqual(["current-1981"],
                         [unit.unit_id for unit in plan["selectedUnits"]])
        self.assertTrue(plan["serializedWriterRequired"])

    def test_staged_pool_does_not_choose_a_tiny_root_before_large_backlog(self):
        plan = staged_pool_plan([
            StagedUnit("tiny-5", 5), StagedUnit("large-499", 499),
            StagedUnit("remainder-102", 102),
        ])
        self.assertEqual(500, plan["minimumCycleBooks"])
        self.assertEqual(["large-499", "remainder-102"],
                         [unit.unit_id for unit in plan["selectedUnits"]])
        self.assertEqual(601, plan["selectedBooks"])


if __name__ == "__main__":
    unittest.main()
