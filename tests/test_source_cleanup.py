import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sweeper.source_cleanup import cleanup_staged_source_cache


class SourceCleanupTest(unittest.TestCase):
    def test_requires_exact_staging_receipt_and_exact_cleaner_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "dock-staging.json"):
                cleanup_staged_source_cache(root, ["cleaner"])
            (root / "dock-staging.json").write_text(json.dumps({
                "passed": True, "production_mutated": False,
                "items": {"source:one": "a" * 64, "source:two": "b" * 64},
            }))
            with patch("sweeper.source_cleanup.command_json", return_value={
                    "deleted": ["source:one"], "bytes_reclaimed": 10}):
                with self.assertRaisesRegex(ValueError, "exact staged source-cache membership"):
                    cleanup_staged_source_cache(root, ["cleaner"])
            with patch("sweeper.source_cleanup.command_json", return_value={
                    "deleted": ["source:one", "source:two"], "bytes_reclaimed": 2048}):
                result = cleanup_staged_source_cache(root, ["cleaner"])
            self.assertTrue(result["passed"])
            self.assertEqual(2048, result["bytes_reclaimed"])
            self.assertTrue((root / "dock-source-cleanup.json").exists())


if __name__ == "__main__":
    unittest.main()
