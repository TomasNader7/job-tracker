import ast
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import embed_golden_fixture as embed  # noqa: E402


class NotebookFixtureTests(unittest.TestCase):
    def test_the_notebook_carries_the_same_golden_fixture_as_the_json_file(self):
        fixture = json.loads(embed.FIXTURE.read_text(encoding="utf-8"))
        embedded = json.loads(embed.embedded_json(embed.NOTEBOOK.read_text(encoding="utf-8")))

        self.assertEqual(fixture, embedded, "run: python scripts/embed_golden_fixture.py")

    def test_the_fixture_covers_every_transition_pair_and_the_key_edge_cases(self):
        fixture = json.loads(embed.FIXTURE.read_text(encoding="utf-8"))
        names = {c["name"] for c in fixture["cases"]}

        for needed in ["same_timestamp_numeric_sequence_order", "correction_of_a_correction", "no_reply_exactly_30_days_is_not_yet",
                       "no_reply_31_days", "correction_target_in_other_application_is_ignored", "transition_matrix_every_from_to_pair",
                       "two_corrections_supersede_each_other_both_dropped", "reopen_after_rejection"]:
            self.assertIn(needed, names)
        matrix = next(c for c in fixture["cases"] if c["name"] == "transition_matrix_every_from_to_pair")
        self.assertEqual(49, len(matrix["expected"]))

    def test_the_notebook_is_valid_python(self):
        ast.parse(embed.NOTEBOOK.read_text(encoding="utf-8"))

    def test_the_notebook_has_the_run_marker_and_the_databricks_header(self):
        text = embed.NOTEBOOK.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("# Databricks notebook source"))
        self.assertIn("# ==== RUN ====", text)

    def test_the_notebook_never_reads_a_latest_folder_or_uses_tokens(self):
        lines = embed.NOTEBOOK.read_text(encoding="utf-8").lower().splitlines()
        code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))  # comments may explain the rule

        self.assertNotIn("latest", code)
        for forbidden in ["dapi", "personal access token", "databricks_token", "sparkcontext", ".rdd"]:
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
