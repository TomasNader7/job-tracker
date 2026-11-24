"""Tests for tracker_reader using only small, invented workbooks."""

import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from tracker_reader import OUTPUT_COLUMNS, main, read_tracker  # noqa: E402


HEADERS = [
    "Company Name",
    "Job Title",
    "Type of Job",
    "Date Applied",
    "Location",
    "Resume Variant Used",
    "Referral / Networking Contact",
    "Application Status",
    "Interview Date",
    "Priority",
]


def make_workbook(tmp_path, rows, headers=HEADERS, name="tracker.xlsx"):
    path = tmp_path / name
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Applications"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    workbook.save(path)
    workbook.close()
    return path


def valid_row(**changes):
    row = {
        "Company Name": "Acme Corp",
        "Job Title": "Data Engineer",
        "Type of Job": "Inconsistent raw value",
        "Date Applied": "2026-01-10",
        "Location": "Faketown, ZZ",
        "Resume Variant Used": "Data Engineering",
        "Referral / Networking Contact": "",
        "Application Status": "Applied",
        "Interview Date": "",
        "Priority": "Medium Priority",
    }
    row.update(changes)
    return row


class TrackerReaderTests(unittest.TestCase):
    """Each test gets its own throwaway folder (the pytest tmp_path idea, done with unittest)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def test_normalizes_rows_and_ignores_header_order_and_extra_columns(self):
        tmp_path = self.tmp_path
        headers = ["Extra Column", *reversed(HEADERS)]
        rows = [
            valid_row(
                **{
                    "Company Name": "  Acme Corp  ",
                    "Job Title": "Applied AI and Software Engineer",
                    "Date Applied": datetime(2026, 1, 10, 9, 30),
                    "Interview Date": date(2026, 1, 20),
                    "Referral / Networking Contact": "Pat Example",
                    "Application Status": "",
                    "Resume Variant Used": "",
                    "Priority": "",
                    "Extra Column": "ignored",
                }
            )
        ]
        result = read_tracker(make_workbook(tmp_path, rows, headers), date(2026, 2, 1))

        assert result.counts["kept"] == 1
        assert result.issues == []
        assert result.clean_rows == [
            {
                "application_id": 1,
                "company_name": "Acme Corp",
                "job_title": "Applied AI and Software Engineer",
                "date_applied": "2026-01-10",
                "job_type": "ML/AI Engineer",  # First matching pattern wins.
                "location": "Faketown, ZZ",
                "resume_variant": "Unspecified",
                "referral_type": "Networking Contact",
                "application_status": "Not Started",
                "interview_date": "2026-01-20",
                "priority": "Unspecified",
            }
        ]


    def test_skips_blank_companies_and_both_kinds_of_outreach(self):
        tmp_path = self.tmp_path
        rows = [
            valid_row(**{"Company Name": "  "}),
            valid_row(**{"Job Title": "OUTREACH (NO POSTING) to a team"}),
            valid_row(**{"Type of Job": "Referral Outreach"}),
            valid_row(**{"Company Name": "Beta Labs"}),
        ]
        result = read_tracker(make_workbook(tmp_path, rows), date(2026, 2, 1))

        assert result.counts["skipped_blank"] == 1
        assert result.counts["skipped_outreach"] == 2
        assert result.counts["kept"] == 1
        assert result.clean_rows[0]["application_id"] == 1


    def test_job_type_patterns_and_first_match_order(self):
        tmp_path = self.tmp_path
        titles_and_expected = [
            ("Machine Learning Engineer", "ML/AI Engineer"),
            ("Data Scientist", "Data Scientist"),
            ("Analytics Engineer", "Data Engineer"),
            ("BI Dashboard Specialist", "BI Analyst"),
            ("Quantitative Analytics Associate", "Data Analyst"),
            ("Backend Developer", "Software Engineer"),
            ("Operations Analyst", "Business Analyst"),
            ("2027 Graduate Rotation", "New Grad Program"),
            ("Office Coordinator", "Other"),
            # Both patterns match, but Data Scientist appears first.
            ("Data Science Analytics", "Data Scientist"),
        ]
        rows = [
            valid_row(**{"Company Name": f"Fake Company {index}", "Job Title": title})
            for index, (title, _) in enumerate(titles_and_expected)
        ]
        result = read_tracker(make_workbook(tmp_path, rows), date(2026, 2, 1))

        assert [row["job_type"] for row in result.clean_rows] == [
            expected for _, expected in titles_and_expected
        ]


    def test_unknown_values_and_bad_dates_are_blocking(self):
        tmp_path = self.tmp_path
        rows = [
            valid_row(
                **{
                    "Application Status": "Maybe",
                    "Resume Variant Used": "Paper Resume",
                    "Priority": "Urgent",
                    "Date Applied": "01/35/2026",
                    "Interview Date": 12345,
                }
            ),
            valid_row(**{"Company Name": "Beta Labs"}),
        ]
        result = read_tracker(make_workbook(tmp_path, rows), date(2026, 2, 1))

        assert result.counts["rejected"] == 1
        assert result.counts["kept"] == 1
        assert [row["application_id"] for row in result.clean_rows] == [1]
        assert result.counts["UNKNOWN_STATUS"] == 1
        assert result.counts["UNKNOWN_RESUME_VARIANT"] == 1
        assert result.counts["UNKNOWN_PRIORITY"] == 1
        assert result.counts["BAD_DATE"] == 2
        assert set(result.rejected_rows[0]["issue_codes"]) == {
            "UNKNOWN_STATUS",
            "UNKNOWN_RESUME_VARIANT",
            "UNKNOWN_PRIORITY",
            "BAD_DATE",
        }


    def test_all_warning_codes_keep_rows(self):
        tmp_path = self.tmp_path
        rows = [
            valid_row(
                **{
                    "Date Applied": "2026-03-01",
                    "Interview Date": "2026-02-20",
                }
            ),
            valid_row(
                **{
                    "Company Name": "acme corp",
                    "Job Title": "data engineer",
                    "Date Applied": "2026-03-01",
                }
            ),
            valid_row(
                **{
                    "Company Name": "Beta Labs",
                    "Date Applied": "",
                    "Application Status": "Interviewed",
                }
            ),
            valid_row(
                **{
                    "Company Name": "Gamma Works",
                    "Date Applied": "",
                    "Application Status": "Not Started",
                }
            ),
        ]
        result = read_tracker(make_workbook(tmp_path, rows), date(2026, 2, 1))

        assert result.counts["kept"] == 4
        assert result.counts["rejected"] == 0
        assert result.counts["FUTURE_DATE"] == 2
        assert result.counts["INTERVIEW_BEFORE_APPLIED"] == 1
        assert result.counts["DUPLICATE"] == 1
        assert result.counts["MISSING_DATE_APPLIED"] == 1
        assert result.counts["warnings"] == 5
        assert result.clean_rows[2]["date_applied"] == ""
        assert result.clean_rows[3]["date_applied"] == ""


    def test_missing_columns_returns_no_rows(self):
        tmp_path = self.tmp_path
        headers = [header for header in HEADERS if header != "Priority"]
        result = read_tracker(
            make_workbook(tmp_path, [valid_row()], headers), date(2026, 2, 1)
        )

        assert result.clean_rows == []
        assert result.rejected_rows == []
        assert result.counts["MISSING_COLUMN"] == 1
        assert result.issues[0]["field"] == "Priority"


    def test_cli_is_deterministic_and_quality_json_contains_no_row_text(self):
        tmp_path = self.tmp_path
        rows = [
            valid_row(
                **{
                    "Company Name": "Secret Fake Company",
                    "Job Title": "Secret Fake Title",
                    "Application Status": "Unknown Fake Status",
                }
            ),
            valid_row(**{"Company Name": "Beta Labs", "Job Title": "Data Analyst"}),
        ]
        workbook_path = make_workbook(tmp_path, rows)
        first = tmp_path / "first"
        second = tmp_path / "second"

        assert main([str(workbook_path), "--as-of", "2026-02-01", "--out-dir", str(first)]) == 0
        assert main([str(workbook_path), "--as-of", "2026-02-01", "--out-dir", str(second)]) == 0

        first_csv = (first / "clean_rows.csv").read_bytes()
        assert first_csv == (second / "clean_rows.csv").read_bytes()
        assert not first_csv.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in first_csv
        assert first_csv.decode("utf-8").splitlines()[0] == ",".join(OUTPUT_COLUMNS)

        report_text = (first / "quality_report.json").read_text(encoding="utf-8")
        assert "Secret Fake Company" not in report_text
        assert "Secret Fake Title" not in report_text
        assert "Unknown Fake Status" not in report_text
        report = json.loads(report_text)
        assert report["issues"] == [
            {"row_number": 2, "field": "application_status", "code": "UNKNOWN_STATUS"}
        ]
        assert "message" not in report["issues"][0]


if __name__ == "__main__":
    unittest.main()
