import json
import tempfile
import unittest
from pathlib import Path

from inquiry import Catalog, Connection, _search


class InquiryTest(unittest.TestCase):
    def test_scope_is_connection_authoritative_and_custom_fields_search(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "records.jsonl"
            source.write_text(json.dumps({"id": "one", "title": "Signal", "scope": "live", "author": "A. Writer", "metadata": {"shelf": "North"}}) + "\n", encoding="utf-8")
            records, errors = Catalog([Connection("Stage", "staged", "path", str(source))]).records()
            self.assertFalse(errors)
            self.assertEqual("staged", records[0]["scope"])
            self.assertEqual(1, len(_search(records, {"field": ["shelf"], "value": ["north"]})))

    def test_missing_identity_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "records.json"
            source.write_text(json.dumps({"records": [{"title": "No ID"}, {"id": "two", "title": "Kept"}]}), encoding="utf-8")
            records, _ = Catalog([Connection("Live", "live", "path", str(source))]).records()
            self.assertEqual(["two"], [row["id"] for row in records])


if __name__ == "__main__": unittest.main()
