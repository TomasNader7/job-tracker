"""Tests for the deterministic synthetic tracker fixture."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import make_synthetic_tracker as generator  # noqa: E402


class SyntheticTrackerTests(unittest.TestCase):
    def build_fixture(self, directory: str, seed: int = generator.DEFAULT_SEED):
        workbook_path, expected_path = generator.build(directory, seed)
        workbook = load_workbook(workbook_path, data_only=True)
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        return workbook, expected, expected_path

    @staticmethod
    def cell_snapshot(workbook) -> tuple[tuple[object, ...], ...]:
        return tuple(
            tuple(cell.value for cell in row)
            for row in workbook["Applications"].iter_rows()
        )

    @staticmethod
    def as_date(value):
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        raise ValueError

    def independently_scan(self, sheet):
        """Recount faults without using the generator's expected-data helper."""

        codes = {
            "UNKNOWN_STATUS": 0,
            "UNKNOWN_RESUME_VARIANT": 0,
            "UNKNOWN_PRIORITY": 0,
            "BAD_DATE": 0,
            "FUTURE_DATE": 0,
            "INTERVIEW_BEFORE_APPLIED": 0,
            "DUPLICATE": 0,
            "MISSING_DATE_APPLIED": 0,
            "MISSING_COLUMN": 0,
        }
        totals = {
            "skipped_blank": 0,
            "skipped_outreach": 0,
            "kept": 0,
            "rejected": 0,
            "warnings": 0,
        }
        seen = set()

        for values in sheet.iter_rows(min_row=2, values_only=True):
            company, title, type_of_job = values[:3]
            if company in (None, ""):
                totals["skipped_blank"] += 1
                continue
            if "outreach (no posting)" in str(title).lower() or str(type_of_job).lower() == "referral outreach":
                totals["skipped_outreach"] += 1
                continue

            blocking_codes = []
            if (values[7] or "Not Started") not in generator.STATUSES:
                blocking_codes.append("UNKNOWN_STATUS")
            if (values[5] or "") not in generator.RESUME_VARIANTS:
                blocking_codes.append("UNKNOWN_RESUME_VARIANT")
            if (values[9] or "") not in generator.PRIORITIES:
                blocking_codes.append("UNKNOWN_PRIORITY")

            parsed = {}
            for index in (3, 8):
                try:
                    parsed[index] = self.as_date(values[index])
                except ValueError:
                    blocking_codes.append("BAD_DATE")

            if blocking_codes:
                totals["rejected"] += 1
                for code in blocking_codes:
                    codes[code] += 1
                continue

            totals["kept"] += 1
            warnings = []
            applied, interview = parsed[3], parsed[8]
            if applied is not None and applied > generator.AS_OF:
                warnings.append("FUTURE_DATE")
            if applied is not None and interview is not None and interview < applied:
                warnings.append("INTERVIEW_BEFORE_APPLIED")
            key = (str(company).lower(), str(title).lower(), applied)
            if key in seen:
                warnings.append("DUPLICATE")
            if applied is None and (values[7] or "Not Started") != "Not Started":
                warnings.append("MISSING_DATE_APPLIED")
            seen.add(key)

            for code in warnings:
                codes[code] += 1
                totals["warnings"] += 1

        return {**totals, **codes}

    def test_layout_and_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            workbook, expected, _ = self.build_fixture(temporary)
            self.assertEqual(workbook.sheetnames, ["Applications"])
            sheet = workbook["Applications"]
            self.assertEqual(
                [cell.value for cell in sheet[1]],
                generator.HEADERS,
            )
            self.assertEqual(sheet.max_row, 1 + generator.NORMAL_ROW_COUNT + 19)
            self.assertEqual(sheet.max_column, len(generator.HEADERS))
            self.assertEqual(len(expected["kept_job_types"]), expected["counts"]["kept"])

    def test_fault_counts_match_an_independent_workbook_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            workbook, expected, _ = self.build_fixture(temporary)
            actual_counts = self.independently_scan(workbook["Applications"])
            self.assertEqual(actual_counts, expected["counts"])
            self.assertEqual(
                actual_counts,
                {
                    "skipped_blank": 2,
                    "skipped_outreach": 3,
                    "kept": 187,
                    "rejected": 7,
                    "warnings": 7,
                    "UNKNOWN_STATUS": 2,
                    "UNKNOWN_RESUME_VARIANT": 1,
                    "UNKNOWN_PRIORITY": 1,
                    "BAD_DATE": 3,
                    "FUTURE_DATE": 2,
                    "INTERVIEW_BEFORE_APPLIED": 1,
                    "DUPLICATE": 2,
                    "MISSING_DATE_APPLIED": 2,
                    "MISSING_COLUMN": 0,
                },
            )

    def test_same_seed_is_identical_and_different_seed_changes_company_order(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second, tempfile.TemporaryDirectory() as third:
            workbook_a, _, expected_a = self.build_fixture(first, 12345)
            workbook_b, _, expected_b = self.build_fixture(second, 12345)
            workbook_c, _, _ = self.build_fixture(third, 54321)

            self.assertEqual(self.cell_snapshot(workbook_a), self.cell_snapshot(workbook_b))
            self.assertEqual(expected_a.read_bytes(), expected_b.read_bytes())

            companies_a = [
                workbook_a["Applications"].cell(row=row, column=1).value
                for row in range(2, 2 + generator.NORMAL_ROW_COUNT)
            ]
            companies_c = [
                workbook_c["Applications"].cell(row=row, column=1).value
                for row in range(2, 2 + generator.NORMAL_ROW_COUNT)
            ]
            self.assertNotEqual(companies_a, companies_c)

    def test_expected_json_contains_no_company_names_or_job_titles(self):
        with tempfile.TemporaryDirectory() as temporary:
            workbook, _, expected_path = self.build_fixture(temporary)
            expected_text = expected_path.read_text(encoding="utf-8")
            sheet = workbook["Applications"]
            for company, title in sheet.iter_rows(
                min_row=2,
                min_col=1,
                max_col=2,
                values_only=True,
            ):
                if company:
                    self.assertNotIn(company, expected_text)
                if title:
                    self.assertNotIn(title, expected_text)

    def test_every_normal_row_uses_allowed_values_and_real_dates(self):
        with tempfile.TemporaryDirectory() as temporary:
            workbook, _, _ = self.build_fixture(temporary)
            rows = workbook["Applications"].iter_rows(
                min_row=2,
                max_row=1 + generator.NORMAL_ROW_COUNT,
                values_only=True,
            )
            seen_companies = set()
            for values in rows:
                company, title = values[0], values[1]
                self.assertNotIn(company, seen_companies)
                seen_companies.add(company)
                self.assertIn(values[7], generator.STATUSES)
                self.assertIn(values[5] or "", generator.RESUME_VARIANTS)
                self.assertIn(values[9] or "", generator.PRIORITIES)
                self.assertIn(values[2] or "", {"Full-time", "FT", "new grad", ""})
                self.assertTrue(values[6] in (None, "") or re.fullmatch(r"[A-Za-z]+ (Example|Sample|Fiction|Demo)", values[6]))
                for date_value in (values[3], values[8]):
                    self.assertTrue(date_value is None or isinstance(date_value, (date, datetime)))
                self.assertIn(generator.derive_job_type(title), generator.TITLES_BY_TYPE)

    def test_issue_rows_and_fields_are_exact_and_sorted(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, expected, expected_path = self.build_fixture(temporary)
            issue_pairs = [(item["row_number"], item["code"]) for item in expected["issues"]]
            self.assertEqual(issue_pairs, sorted(issue_pairs))
            self.assertEqual(
                [(item["row_number"], item["field"], item["code"]) for item in expected["issues"]],
                [
                    (187, "application_status", "UNKNOWN_STATUS"),
                    (188, "application_status", "UNKNOWN_STATUS"),
                    (189, "resume_variant", "UNKNOWN_RESUME_VARIANT"),
                    (190, "priority", "UNKNOWN_PRIORITY"),
                    (191, "date_applied", "BAD_DATE"),
                    (192, "date_applied", "BAD_DATE"),
                    (193, "interview_date", "BAD_DATE"),
                    (194, "date_applied", "FUTURE_DATE"),
                    (195, "date_applied", "FUTURE_DATE"),
                    (196, "interview_date", "INTERVIEW_BEFORE_APPLIED"),
                    (197, "row", "DUPLICATE"),
                    (198, "row", "DUPLICATE"),
                    (199, "date_applied", "MISSING_DATE_APPLIED"),
                    (200, "date_applied", "MISSING_DATE_APPLIED"),
                ],
            )
            raw = expected_path.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertEqual(raw, (json.dumps(expected, indent=2, sort_keys=True) + "\n").encode())


if __name__ == "__main__":
    unittest.main()
