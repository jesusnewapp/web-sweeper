import json
import tempfile
import unittest
from pathlib import Path

from sweeper.frontier import FrontierRetirement


class FrontierRetirementTest(unittest.TestCase):
    def test_exact_exhausted_file_is_skipped_until_its_bytes_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "items.jsonl"
            manifest.write_text(json.dumps({"id": "one"}) + "\n", encoding="utf-8")
            memory = FrontierRetirement(root / "workspace")
            self.assertFalse(memory.is_retired("source", str(manifest)))
            receipt = memory.retire("source", str(manifest))
            self.assertFalse(receipt["acceptedArtifactsChanged"])
            self.assertTrue(memory.is_retired("source", str(manifest)))
            manifest.write_text(json.dumps({"id": "two"}) + "\n", encoding="utf-8")
            self.assertFalse(memory.is_retired("source", str(manifest)))

    def test_rotation_moves_to_next_set_and_only_then_marks_source_exhausted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); workspace = root / "workspace"
            first = root / "first.jsonl"; second = root / "second.jsonl"
            first.write_text("{}\n"); second.write_text("{}\n")
            memory = FrontierRetirement(workspace)
            memory.retire("source", str(first))
            status = memory.rotation_status("source", [str(first), str(second)])
            self.assertEqual(1, status["nextFrontierIndex"])
            self.assertFalse(status["sourceFrontierExhausted"])
            memory.retire("source", str(second))
            status = memory.rotation_status("source", [str(first), str(second)])
            self.assertIsNone(status["nextFrontier"])
            self.assertTrue(status["sourceFrontierExhausted"])


if __name__ == "__main__":
    unittest.main()
