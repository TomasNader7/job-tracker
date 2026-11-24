"""Cross-check: the tracker reader must agree with the synthetic tracker generator.

The generator (make_synthetic_tracker.py) writes a fake workbook with known faults plus an
expected.json computed by its own logic. The reader (tracker_reader.py) was written separately.
If the two agree on counts, flagged rows and job types, both are probably right.
Fake data only: nothing here touches a real tracker.
"""
import csv
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import make_synthetic_tracker as generator  # noqa: E402
import tracker_reader as reader  # noqa: E402


class ReaderAgreesWithGeneratorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        workbook, expected_path = generator.build(self._tmp.name)
        self.expected = json.loads(expected_path.read_text(encoding="utf-8"))
        self.result = reader.read_tracker(workbook, date.fromisoformat(self.expected["as_of"]))
        self.out_dir = Path(self._tmp.name) / "reader_out"
        reader.main([str(workbook), "--as-of", self.expected["as_of"], "--out-dir", str(self.out_dir)])

    def test_counts_match(self):
        self.assertEqual(self.expected["counts"], self.result.counts)

    def test_every_flagged_row_field_and_code_matches(self):
        def key(issue):
            return (issue["row_number"], issue["field"], issue["code"])

        self.assertEqual(
            sorted(key(i) for i in self.expected["issues"]),
            sorted(key(i) for i in self.result.issues),
        )

    def test_job_types_match_for_every_kept_row(self):
        self.assertEqual(self.expected["kept_job_types"], [r["job_type"] for r in self.result.clean_rows])

    def test_the_written_csv_and_report_match_the_expected_counts(self):
        with (self.out_dir / "clean_rows.csv").open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(self.expected["counts"]["kept"], len(rows))
        report = json.loads((self.out_dir / "quality_report.json").read_text(encoding="utf-8"))
        self.assertEqual(self.expected["counts"], report["counts"])


if __name__ == "__main__":
    unittest.main()
