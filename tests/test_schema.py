"""The schemas (contracts/schema.py) against their fixtures (contracts/fixtures/<type>.json): every valid item passes
clean() unchanged, every invalid one fails for the reason it names, and a field the contract does not name is cut.
python3 -m unittest tests.test_schema"""
import json
from pathlib import Path
import unittest

import contracts
from contracts.schema import TYPES, clean, schema_for

FIXTURES = Path(contracts.__file__).resolve().parent / "fixtures"


class SchemaTests(unittest.TestCase):
    def test_every_type_has_a_schema_and_fixtures(self):
        self.assertEqual(TYPES, ("metadata", "availability", "content", "reminders", "storage", "tvcontrol", "widgets"))
        for t in TYPES:
            self.assertEqual(schema_for(t)["$id"], t + ".schema.json")
            self.assertTrue((FIXTURES / (t + ".json")).is_file(), t)

    def test_valid_fixtures_pass_unchanged(self):
        for t in TYPES:
            for item in json.loads((FIXTURES / (t + ".json")).read_text())["valid"]:
                cleaned, errors = clean(t, item)
                self.assertEqual(errors, [], (t, item.get("id") or item.get("key")))
                self.assertEqual(cleaned, item, t)

    def test_invalid_fixtures_fail(self):
        for t in TYPES:
            for case in json.loads((FIXTURES / (t + ".json")).read_text())["invalid"]:
                self.assertNotEqual(clean(t, case["item"])[1], [], (t, case["why"]))

    def test_anything_the_contract_does_not_name_is_cut(self):
        item, errors = clean("metadata", {
            "id": "p:1", "kind": "movie", "title": "T", "year": 2000, "updated_at": "2026-10-14T07:00:00Z",
            "play_url": "https://p/play", "trailer": {"url": "https://p/t.mp4"},
            "art": {"poster": {"url": "https://p/a.jpg", "license": "pd", "source": "x"}}})
        self.assertEqual(errors, [])
        self.assertEqual(sorted(item), ["art", "id", "kind", "title", "updated_at", "year"])
        self.assertEqual(item["art"], {"poster": {"url": "https://p/a.jpg", "license": "pd"}})


if __name__ == "__main__":
    unittest.main()
