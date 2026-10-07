"""Guard against an evaluator demanding context its model never receives."""

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
CASE = ROOT / "evals" / "test-writer-behavior"


class SuppliedFixtureTests(unittest.TestCase):
    def test_prompt_supplies_every_versioned_fixture_without_drift(self):
        prompt = (CASE / "prompt.md").read_text()
        sections = re.findall(
            r"### Fixture: (fixtures/[^\n]+)\n\n```[^\n]*\n(.*?)\n```",
            prompt,
            re.DOTALL,
        )
        self.assertEqual(len(sections), len(dict(sections)), "duplicate context file")
        supplied = dict(sections)
        actual = {
            f"fixtures/{file.name}": file.read_text().rstrip()
            for file in (CASE / "fixtures").iterdir()
            if file.is_file()
        }
        self.assertTrue(actual, "the grader cannot demand nonexistent fixtures")
        self.assertEqual(supplied, actual, "regenerate prompt context after changing fixtures")

    def test_persistence_failure_is_after_write_and_has_real_constraint(self):
        source = (CASE / "fixtures" / "OrderService.java").read_text()
        self.assertLess(source.index("orders.markApproved"), source.index("orders.appendEvent"))
        schema = (CASE / "fixtures" / "schema.sql").read_text()
        self.assertIn("CHECK (event_id <> 'FAIL_DB')", schema)
        self.assertIn("VALUES (101, 'alice', 'PENDING')", schema)
        prompt = (CASE / "prompt.md").read_text()
        self.assertIn("separate transaction", prompt)
        self.assertIn("deliberately not supplied", prompt)

    def test_expected_error_is_the_full_existing_contract(self):
        error = json.loads((CASE / "fixtures" / "ownership-error.json").read_text())
        self.assertEqual(error, {
            "status": 403,
            "code": "ORDER_ACCESS_DENIED",
            "message": "You cannot access this order",
            "path": "/orders/101/approve",
        })
        self.assertEqual(len(list((CASE / "graders").glob("*.md"))), 5)


if __name__ == "__main__":
    unittest.main()
