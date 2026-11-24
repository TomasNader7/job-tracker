"""Read and validate the job-application tracker workbook.

This module is deliberately limited to file conversion.  It reads one Excel
workbook and can write CSV/JSON files; it never connects to a database.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


REQUIRED_COLUMNS = (
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
)

OUTPUT_COLUMNS = (
    "application_id",
    "company_name",
    "job_title",
    "date_applied",
    "job_type",
    "location",
    "resume_variant",
    "referral_type",
    "application_status",
    "interview_date",
    "priority",
)

ALLOWED_STATUSES = {
    "Not Started",
    "Applied",
    "No Reply",
    "Withdrawn",
    "Rejected",
    "Interview Scheduled",
    "Interviewed",
    "Accepted",
}
ALLOWED_RESUME_VARIANTS = {
    "Data Analytics",
    "Backend SWE",
    "Business Intelligence",
    "ML/Data Science",
    "Data Engineering",
    "Automation",
    "Frontend SWE",
    "Unspecified",
}
ALLOWED_PRIORITIES = {
    "Top Priority",
    "Medium Priority",
    "Low Priority",
    "Unspecified",
}

ISSUE_CODES = (
    "UNKNOWN_STATUS",
    "UNKNOWN_RESUME_VARIANT",
    "UNKNOWN_PRIORITY",
    "BAD_DATE",
    "FUTURE_DATE",
    "INTERVIEW_BEFORE_APPLIED",
    "DUPLICATE",
    "MISSING_DATE_APPLIED",
    "MISSING_COLUMN",
)
WARNING_CODES = {
    "FUTURE_DATE",
    "INTERVIEW_BEFORE_APPLIED",
    "DUPLICATE",
    "MISSING_DATE_APPLIED",
}

# Order matters: the first matching category wins.
JOB_TYPE_PATTERNS = (
    (
        "ML/AI Engineer",
        r"machine learning engineer|\bml engineer|ai/ml|applied ai|\bai engineer|"
        r"ai (product|security) engineer|ai and machine learning|research engineer|"
        r"machine learning analyst",
    ),
    ("Data Scientist", r"data scien|demand science"),
    (
        "Data Engineer",
        r"data engineer|data developer|data platform|analytics engineer|data integration",
    ),
    ("BI Analyst", r"business intelligence|\bbi\b|dashboard|reporting"),
    ("Data Analyst", r"data analy|analytics|insights|quantitative|predictive"),
    (
        "Software Engineer",
        r"software|developer|backend|forward deploy|\bswe\b|engineer",
    ),
    ("Business Analyst", r"analyst"),
    ("New Grad Program", r"program|rotation|graduate|early career|talent|2027"),
)


@dataclass
class TrackerResult:
    """The usable rows and the complete quality result for one workbook."""

    clean_rows: list[dict[str, Any]]
    rejected_rows: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    counts: dict[str, int]


def _text(value: Any) -> str:
    """Turn an Excel cell into stripped text without turning None into 'None'."""

    return "" if value is None else str(value).strip()


def _parse_date(value: Any) -> tuple[date | None, bool]:
    """Return (parsed date, is_bad). Blank cells are valid missing dates."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return None, False
    if isinstance(value, datetime):
        return value.date(), False
    if isinstance(value, date):
        return value, False
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text), False
        except ValueError:
            # ISO datetime strings are also accepted; only the date is exported.
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).date(), False
            except ValueError:
                return None, True
    return None, True


def _job_type(job_title: str) -> str:
    lowered_title = job_title.lower()
    for name, pattern in JOB_TYPE_PATTERNS:
        if re.search(pattern, lowered_title):
            return name
    return "Other"


def _new_counts() -> dict[str, int]:
    counts = {
        "skipped_blank": 0,
        "skipped_outreach": 0,
        "kept": 0,
        "rejected": 0,
        "warnings": 0,
    }
    counts.update({code: 0 for code in ISSUE_CODES})
    return counts


def _add_issue(
    issues: list[dict[str, Any]],
    counts: dict[str, int],
    row_number: int,
    field: str,
    code: str,
    message: str,
) -> None:
    issues.append(
        {
            "row_number": row_number,
            "field": field,
            "code": code,
            "message": message,
        }
    )
    counts[code] += 1
    if code in WARNING_CODES:
        counts["warnings"] += 1


def read_tracker(path: str | Path, as_of: date) -> TrackerResult:
    """Read an Applications sheet and return normalized rows plus quality data."""

    clean_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    counts = _new_counts()

    # These options ensure formulas are read as cached values and memory stays low.
    workbook = load_workbook(filename=path, read_only=True, data_only=True)
    try:
        if "Applications" not in workbook.sheetnames:
            _add_issue(
                issues,
                counts,
                1,
                "Applications",
                "MISSING_COLUMN",
                "Required sheet 'Applications' is missing.",
            )
            return TrackerResult(clean_rows, rejected_rows, issues, counts)

        sheet = workbook["Applications"]
        header_values = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
        header_map = {_text(value): index for index, value in enumerate(header_values)}
        missing = [name for name in REQUIRED_COLUMNS if name not in header_map]
        if missing:
            for name in missing:
                _add_issue(
                    issues,
                    counts,
                    1,
                    name,
                    "MISSING_COLUMN",
                    f"Required column '{name}' is missing.",
                )
            return TrackerResult(clean_rows, rejected_rows, issues, counts)

        seen_duplicates: set[tuple[str, str, str]] = set()

        for row_number, values in enumerate(
            sheet.iter_rows(min_row=2, values_only=True), start=2
        ):
            def cell(column: str) -> Any:
                index = header_map[column]
                return values[index] if index < len(values) else None

            company = _text(cell("Company Name"))
            if not company:
                counts["skipped_blank"] += 1
                continue

            title = _text(cell("Job Title"))
            raw_job_type = _text(cell("Type of Job"))
            if (
                "outreach (no posting)" in title.lower()
                or raw_job_type.lower() == "referral outreach"
            ):
                counts["skipped_outreach"] += 1
                continue

            status = _text(cell("Application Status")) or "Not Started"
            resume_variant = _text(cell("Resume Variant Used")) or "Unspecified"
            priority = _text(cell("Priority")) or "Unspecified"
            applied, bad_applied = _parse_date(cell("Date Applied"))
            interview, bad_interview = _parse_date(cell("Interview Date"))

            row_issues_before = len(issues)
            blocking = False
            if status not in ALLOWED_STATUSES:
                blocking = True
                _add_issue(
                    issues, counts, row_number, "application_status", "UNKNOWN_STATUS",
                    f"Application status '{status}' is not allowed.",
                )
            if resume_variant not in ALLOWED_RESUME_VARIANTS:
                blocking = True
                _add_issue(
                    issues, counts, row_number, "resume_variant",
                    "UNKNOWN_RESUME_VARIANT",
                    f"Resume variant '{resume_variant}' is not allowed.",
                )
            if priority not in ALLOWED_PRIORITIES:
                blocking = True
                _add_issue(
                    issues, counts, row_number, "priority", "UNKNOWN_PRIORITY",
                    f"Priority '{priority}' is not allowed.",
                )
            if bad_applied:
                blocking = True
                _add_issue(
                    issues, counts, row_number, "date_applied", "BAD_DATE",
                    "Date applied is not a valid ISO or Excel date.",
                )
            if bad_interview:
                blocking = True
                _add_issue(
                    issues, counts, row_number, "interview_date", "BAD_DATE",
                    "Interview date is not a valid ISO or Excel date.",
                )

            date_applied = applied.isoformat() if applied else ""
            interview_date = interview.isoformat() if interview else ""
            candidate = {
                "company_name": company,
                "job_title": title,
                "date_applied": date_applied,
                "job_type": _job_type(title),
                "location": _text(cell("Location")),
                "resume_variant": resume_variant,
                "referral_type": (
                    "Networking Contact"
                    if _text(cell("Referral / Networking Contact"))
                    else "Cold Apply"
                ),
                "application_status": status,
                "interview_date": interview_date,
                "priority": priority,
            }

            # Record non-blocking date warnings even if another field causes the
            # row to be rejected.  An invalid date is not also called "missing."
            if applied and applied > as_of:
                _add_issue(
                    issues, counts, row_number, "date_applied", "FUTURE_DATE",
                    "Date applied is after the caller's as-of date.",
                )
            if applied and interview and interview < applied:
                _add_issue(
                    issues, counts, row_number, "interview_date",
                    "INTERVIEW_BEFORE_APPLIED",
                    "Interview date is before date applied.",
                )
            if not applied and not bad_applied and status != "Not Started":
                _add_issue(
                    issues, counts, row_number, "date_applied",
                    "MISSING_DATE_APPLIED",
                    "Date applied is missing for a started application.",
                )

            if blocking:
                rejected = {"row_number": row_number, **candidate}
                rejected["issue_codes"] = [
                    issue["code"] for issue in issues[row_issues_before:]
                ]
                rejected_rows.append(rejected)
                counts["rejected"] += 1
                continue

            duplicate_key = (company.casefold(), title.casefold(), date_applied)
            if duplicate_key in seen_duplicates:
                _add_issue(
                    issues, counts, row_number, "row", "DUPLICATE",
                    "Company, title, and date applied duplicate an earlier kept row.",
                )
            seen_duplicates.add(duplicate_key)

            clean_rows.append(
                {"application_id": len(clean_rows) + 1, **candidate}
            )
            counts["kept"] += 1

        return TrackerResult(clean_rows, rejected_rows, issues, counts)
    finally:
        workbook.close()


def _write_outputs(result: TrackerResult, out_dir: str | Path) -> None:
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # newline="" plus lineterminator="\n" makes line endings deterministic.
    with (output_path / "clean_rows.csv").open(
        "w", encoding="utf-8", newline=""
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file, fieldnames=OUTPUT_COLUMNS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(result.clean_rows)

    # The CLI report intentionally excludes messages and all row content.
    safe_issues = [
        {
            "row_number": issue["row_number"],
            "field": issue["field"],
            "code": issue["code"],
        }
        for issue in result.issues
    ]
    report = {"counts": result.counts, "issues": safe_issues}
    with (output_path / "quality_report.json").open(
        "w", encoding="utf-8", newline="\n"
    ) as json_file:
        json.dump(report, json_file, indent=2, ensure_ascii=False, sort_keys=True)
        json_file.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", help="Path to the tracker .xlsx workbook")
    parser.add_argument("--as-of", required=True, help="Quality cutoff (YYYY-MM-DD)")
    parser.add_argument("--out-dir", required=True, help="Directory for CSV and JSON")
    args = parser.parse_args(argv)

    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        parser.error("--as-of must be an ISO date in YYYY-MM-DD form")

    result = read_tracker(args.workbook, as_of)
    _write_outputs(result, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
