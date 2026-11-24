"""Build a deterministic, entirely fake job-application tracker fixture.

The workbook is meant for testing an Excel reader.  It contains 180 ordinary
rows followed by 19 deliberately faulty rows at fixed positions.  No source
data is read, and every name, company, city, and job is invented.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook


DEFAULT_SEED = 20261124
AS_OF = date(2026, 11, 24)
NORMAL_ROW_COUNT = 180

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

STATUSES = [
    "Not Started",
    "Applied",
    "Withdrawn",
    "Rejected",
    "Interview Scheduled",
    "Interviewed",
    "Accepted",
]
RESUME_VARIANTS = [
    "Data Analytics",
    "Backend SWE",
    "Business Intelligence",
    "ML/Data Science",
    "Data Engineering",
    "Automation",
    "Frontend SWE",
    "",  # blank means Unspecified
]
PRIORITIES = ["Top Priority", "Medium Priority", "Low Priority", ""]

# The order matters: these are the reader's first-match-wins rules.
JOB_TYPE_RULES = [
    (
        "ML/AI Engineer",
        re.compile(
            r"machine learning engineer|\bml engineer|ai/ml|applied ai|"
            r"\bai engineer|ai (product|security) engineer|"
            r"ai and machine learning|research engineer|machine learning analyst"
        ),
    ),
    ("Data Scientist", re.compile(r"data scien|demand science")),
    (
        "Data Engineer",
        re.compile(
            r"data engineer|data developer|data platform|analytics engineer|"
            r"data integration"
        ),
    ),
    ("BI Analyst", re.compile(r"business intelligence|\bbi\b|dashboard|reporting")),
    (
        "Data Analyst",
        re.compile(r"data analy|analytics|insights|quantitative|predictive"),
    ),
    (
        "Software Engineer",
        re.compile(r"software|developer|backend|forward deploy|\bswe\b|engineer"),
    ),
    ("Business Analyst", re.compile(r"analyst")),
    (
        "New Grad Program",
        re.compile(r"program|rotation|graduate|early career|talent|2027"),
    ),
]

TITLES_BY_TYPE = {
    "ML/AI Engineer": [
        "Machine Learning Engineer",
        "Applied AI Specialist",
        "AI Security Engineer",
        "ML Engineer I",
    ],
    "Data Scientist": [
        "Data Scientist I",
        "Junior Data Science Associate",
        "Demand Science Specialist",
    ],
    "Data Engineer": [
        "Data Engineer I",
        "Data Platform Associate",
        "Data Integration Specialist",
        "Junior Data Developer",
    ],
    "BI Analyst": [
        "Business Intelligence Specialist",
        "BI Associate",
        "Dashboard Specialist",
        "Reporting Specialist",
    ],
    "Data Analyst": [
        "Data Analyst I",
        "Insights Associate",
        "Quantitative Specialist",
        "Predictive Analytics Associate",
    ],
    "Software Engineer": [
        "Software Engineer I",
        "Backend Developer",
        "Junior SWE",
        "Forward Deploy Specialist",
    ],
    "Business Analyst": [
        "Operations Analyst",
        "Junior Business Analyst",
        "Process Analyst",
    ],
    "New Grad Program": [
        "2027 Technology Associate",
        "Early Career Technology Associate",
        "Graduate Technology Associate",
        "Technology Rotation Program",
    ],
    "Other": [
        "Technical Project Coordinator",
        "Cloud Support Specialist",
        "Product Operations Associate",
        "Implementation Consultant",
    ],
}


def derive_job_type(title: str) -> str:
    """Return the first job type whose regular expression matches *title*."""

    lowered = title.lower()
    for job_type, pattern in JOB_TYPE_RULES:
        if pattern.search(lowered):
            return job_type
    return "Other"


def _fake_companies() -> list[str]:
    """Create more unique fake names than this fixture needs."""

    first_words = [
        "Northwind",
        "Bluepeak",
        "Cedar",
        "Brightpath",
        "Silverline",
        "Redwood",
        "Clearwater",
        "Sunstone",
        "Pinecrest",
        "Westbridge",
        "Cloudberry",
        "Greenfield",
    ]
    second_words = [
        "Analytics",
        "Systems",
        "Labs",
        "Works",
        "Digital",
        "Solutions",
        "Technology",
        "Partners",
    ]
    return [
        f"{first} {second} {number}"
        for number in range(1, 3)
        for first in first_words
        for second in second_words
    ]


def _normal_rows(rng: random.Random) -> list[list[Any]]:
    companies = _fake_companies()
    rng.shuffle(companies)

    all_titles = [title for titles in TITLES_BY_TYPE.values() for title in titles]
    locations = [
        "Maple Junction, FS",
        "Lakeview, FS",
        "Cedar Point, FS",
        "North Harbor, FS",
        "Pine Valley, FS",
        "Silver Creek, FS",
    ]
    job_labels = ["Full-time", "FT", "new grad", ""]
    contacts = [
        "Pat Example",
        "Jordan Sample",
        "Casey Fiction",
        "Riley Demo",
    ]
    start = date(2026, 6, 1)
    date_span = (date(2026, 11, 20) - start).days

    rows: list[list[Any]] = []
    for index in range(NORMAL_ROW_COUNT):
        status = rng.choice(STATUSES)
        applied = None
        if status != "Not Started" or rng.random() < 0.55:
            applied = start + timedelta(days=rng.randint(0, date_span))

        interview = None
        if status in {"Interview Scheduled", "Interviewed"}:
            # Interview rows need an application date so that the normal data
            # does not accidentally contain a missing-date fault.
            if applied is None:
                applied = start + timedelta(days=rng.randint(0, date_span))
            interview = applied + timedelta(days=rng.randint(3, 21))

        contact = rng.choice(contacts) if rng.random() < 0.15 else ""
        rows.append(
            [
                companies[index],
                rng.choice(all_titles),
                rng.choice(job_labels),
                applied,
                rng.choice(locations),
                rng.choice(RESUME_VARIANTS),
                contact,
                status,
                interview,
                rng.choice(PRIORITIES),
            ]
        )
    return rows


def _base_fault_row(number: int, title: str = "Technical Project Coordinator") -> list[Any]:
    """Return a valid invented row that one caller can alter with one fault."""

    return [
        f"Fixture Fault Company {number}",
        title,
        "Full-time",
        date(2026, 10, 1) + timedelta(days=number),
        "Testville, FS",
        "Data Analytics",
        "",
        "Applied",
        None,
        "Medium Priority",
    ]


def _fault_rows(normal_rows: list[list[Any]]) -> list[list[Any]]:
    """Build the 19 faults placed at Excel rows 182 through 200.

    Each list entry is one distinct fault row.  Keeping this order fixed makes
    row-level reader failures easy to reproduce and document.
    """

    rows: list[list[Any]] = []

    for number in (1, 2):  # Excel rows 182-183: blank company -> skipped
        row = _base_fault_row(number)
        row[0] = ""
        rows.append(row)

    for number in (3, 4):  # rows 184-185: outreach title -> skipped
        rows.append(_base_fault_row(number, f"Outreach (no posting) Contact {number}"))
    row = _base_fault_row(5)  # row 186: outreach type -> skipped
    row[2] = "Referral Outreach"
    rows.append(row)

    for number in (6, 7):  # rows 187-188: unknown status -> rejected
        row = _base_fault_row(number)
        row[7] = "Maybe"
        rows.append(row)

    row = _base_fault_row(8)  # row 189: unknown resume variant -> rejected
    row[5] = "Quantum Resume"
    rows.append(row)

    row = _base_fault_row(9)  # row 190: unknown priority -> rejected
    row[9] = "Urgent-ish"
    rows.append(row)

    for number in (10, 11):  # rows 191-192: bad Date Applied -> rejected
        row = _base_fault_row(number)
        row[3] = "31/31/2026"
        rows.append(row)

    row = _base_fault_row(12)  # row 193: bad Interview Date -> rejected
    row[8] = "not-a-date"
    rows.append(row)

    for number, future_day in ((13, 15), (14, 20)):  # rows 194-195: warning
        row = _base_fault_row(number)
        row[3] = date(2026, 12, future_day)
        rows.append(row)

    row = _base_fault_row(15)  # row 196: interview before application warning
    row[7] = "Interviewed"
    row[3] = date(2026, 10, 20)
    row[8] = date(2026, 10, 10)
    rows.append(row)

    # Rows 197-198 duplicate earlier kept normal rows.  Case changes prove the
    # duplicate key is case-insensitive for company and title.
    for source in normal_rows[:2]:
        duplicate = list(source)
        duplicate[0] = str(duplicate[0]).upper()
        duplicate[1] = str(duplicate[1]).lower()
        rows.append(duplicate)

    for number in (18, 19):  # rows 199-200: missing application date warning
        row = _base_fault_row(number)
        row[3] = None
        rows.append(row)

    return rows


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # A reader may accept ISO date strings, but the injected strings are
    # intentionally not ISO dates.
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("unparseable date")


def _expected(rows: Iterable[list[Any]], seed: int) -> dict[str, Any]:
    """Apply the specified reader semantics to build expected.json."""

    issue_counts = {
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
    counts: dict[str, int] = {
        "skipped_blank": 0,
        "skipped_outreach": 0,
        "kept": 0,
        "rejected": 0,
        "warnings": 0,
        **issue_counts,
    }
    issues: list[dict[str, Any]] = []
    kept_job_types: list[str] = []
    seen: set[tuple[str, str, date | None]] = set()
    warning_codes = {
        "FUTURE_DATE",
        "INTERVIEW_BEFORE_APPLIED",
        "DUPLICATE",
        "MISSING_DATE_APPLIED",
    }

    for row_number, row in enumerate(rows, start=2):
        company, title, type_of_job = row[0], row[1], row[2]
        if company in (None, ""):
            counts["skipped_blank"] += 1
            continue
        if "outreach (no posting)" in str(title).lower() or str(type_of_job).lower() == "referral outreach":
            counts["skipped_outreach"] += 1
            continue

        row_issues: list[tuple[str, str]] = []
        status = row[7] or "Not Started"
        if status not in STATUSES:
            row_issues.append(("application_status", "UNKNOWN_STATUS"))
        if (row[5] or "") not in RESUME_VARIANTS:
            row_issues.append(("resume_variant", "UNKNOWN_RESUME_VARIANT"))
        if (row[9] or "") not in PRIORITIES:
            row_issues.append(("priority", "UNKNOWN_PRIORITY"))

        parsed_dates: dict[int, date | None] = {}
        for column_index, field in ((3, "date_applied"), (8, "interview_date")):
            try:
                parsed_dates[column_index] = _as_date(row[column_index])
            except ValueError:
                row_issues.append((field, "BAD_DATE"))

        blocking = any(code.startswith("UNKNOWN_") or code == "BAD_DATE" for _, code in row_issues)
        if blocking:
            counts["rejected"] += 1
        else:
            applied = parsed_dates[3]
            interview = parsed_dates[8]
            if applied is not None and applied > AS_OF:
                row_issues.append(("date_applied", "FUTURE_DATE"))
            if applied is not None and interview is not None and interview < applied:
                row_issues.append(("interview_date", "INTERVIEW_BEFORE_APPLIED"))

            duplicate_key = (str(company).lower(), str(title).lower(), applied)
            if duplicate_key in seen:
                row_issues.append(("row", "DUPLICATE"))
            if applied is None and status != "Not Started":
                row_issues.append(("date_applied", "MISSING_DATE_APPLIED"))

            seen.add(duplicate_key)
            counts["kept"] += 1
            kept_job_types.append(derive_job_type(str(title)))

        for field, code in row_issues:
            counts[code] += 1
            if code in warning_codes:
                counts["warnings"] += 1
            issues.append({"row_number": row_number, "field": field, "code": code})

    issues.sort(key=lambda issue: (issue["row_number"], issue["code"]))
    return {
        "as_of": AS_OF.isoformat(),
        "seed": seed,
        "counts": counts,
        "issues": issues,
        "kept_job_types": kept_job_types,
    }


def build(out_dir: str | Path, seed: int = DEFAULT_SEED) -> tuple[Path, Path]:
    """Write tracker_synthetic.xlsx and expected.json into *out_dir*."""

    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    normal_rows = _normal_rows(rng)
    rows = normal_rows + _fault_rows(normal_rows)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Applications"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)

    workbook_path = destination / "tracker_synthetic.xlsx"
    workbook.save(workbook_path)

    expected_path = destination / "expected.json"
    # Write bytes explicitly so Windows does not translate LF to CRLF.  This
    # keeps the fixture byte-for-byte deterministic across operating systems.
    expected_bytes = (
        json.dumps(_expected(rows, seed), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    expected_path.write_bytes(expected_bytes)
    return workbook_path, expected_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="directory for the two output files")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    build(args.out_dir, args.seed)


if __name__ == "__main__":
    main()
