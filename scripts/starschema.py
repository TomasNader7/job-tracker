"""Shared star-schema definitions and writer, used by the synthetic generator and the
real-data builder so both feed the identical Power BI model.

A row is a dict with keys: application_id, company_name, job_title, date_applied (date or ""),
job_type, location, resume_variant, referral_type, application_status, interview_date (date or ""),
priority.
"""
import csv
import json
from datetime import date, timedelta
from pathlib import Path

NO_REPLY_DAYS = 30  # Applied with no movement for more than this many days counts as No Reply

RESUME_VARIANTS = [
    "Data Analytics", "Backend SWE", "Business Intelligence", "ML/Data Science",
    "Data Engineering", "Automation", "Frontend SWE", "Unspecified",
]
JOB_TYPES = [
    "Data Analyst", "BI Analyst", "Business Analyst", "Data Scientist", "ML/AI Engineer",
    "Data Engineer", "Software Engineer", "New Grad Program", "Other",
]
PRIORITIES = ["Top Priority", "Medium Priority", "Low Priority", "Unspecified"]
REFERRAL_TYPES = ["Networking Contact", "Cold Apply"]
# status, is_application, is_response, is_interview, is_offer, is_rejection, is_no_reply
STATUSES = [
    ("Not Started", 0, 0, 0, 0, 0, 0),
    ("Applied", 1, 0, 0, 0, 0, 0),
    ("No Reply", 1, 0, 0, 0, 0, 1),
    ("Withdrawn", 1, 0, 0, 0, 0, 0),
    ("Rejected", 1, 1, 0, 0, 1, 0),
    ("Interview Scheduled", 1, 1, 1, 0, 0, 0),
    ("Interviewed", 1, 1, 1, 0, 0, 0),
    ("Accepted", 1, 1, 1, 1, 0, 0),
]
STATUS_FLAGS = {s[0]: s for s in STATUSES}


def apply_no_reply_rule(rows, asof):
    """Applied rows older than NO_REPLY_DAYS become No Reply (data-prep rule, documented in README)."""
    for r in rows:
        if r["application_status"] == "Applied" and r["date_applied"]:
            if (asof - r["date_applied"]).days > NO_REPLY_DAYS:
                r["application_status"] = "No Reply"
    return rows


def expected_measures(rows):
    apps = [r for r in rows if STATUS_FLAGS[r["application_status"]][1]]
    n = len(apps)

    def cnt(i):
        return sum(1 for r in apps if STATUS_FLAGS[r["application_status"]][i])

    days = [(r["interview_date"] - r["date_applied"]).days
            for r in apps if r["interview_date"] and r["date_applied"]]
    return {
        "Applications": n,
        "Responses": cnt(2), "Interviews": cnt(3), "Offers": cnt(4), "Rejections": cnt(5), "No Replies": cnt(6),
        "Response Rate": round(cnt(2) / n, 4), "Interview Rate": round(cnt(3) / n, 4),
        "Offer Rate": round(cnt(4) / n, 4), "Rejection Rate": round(cnt(5) / n, 4),
        "No-Reply Rate": round(cnt(6) / n, 4),
        "Avg Days to Interview": round(sum(days) / len(days), 2) if days else None,
        "Rows with interview date": len(days),
    }


def _write_csv(path, header, data):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(data)


def write_star(out, rows, asof, xlsx=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["date_applied"] == "", r["date_applied"] or date.min, r["application_id"]))

    vkey = {v: i + 1 for i, v in enumerate(RESUME_VARIANTS)}
    tkey = {t: i + 1 for i, t in enumerate(JOB_TYPES)}
    pkey = {p: i + 1 for i, p in enumerate(PRIORITIES)}
    skey = {s[0]: i + 1 for i, s in enumerate(STATUSES)}

    def dkey(d):
        return int(d.strftime("%Y%m%d")) if d else ""

    _write_csv(out / "fact_application.csv",
               ["application_id", "company_name", "job_title", "date_key", "job_type_key", "location",
                "resume_variant_key", "referral_type", "status_key", "interview_date_key", "priority_key",
                "date_applied", "interview_date"],
               [[r["application_id"], r["company_name"], r["job_title"], dkey(r["date_applied"] or None),
                 tkey[r["job_type"]], r["location"], vkey[r["resume_variant"]], r["referral_type"],
                 skey[r["application_status"]], dkey(r["interview_date"] or None), pkey[r["priority"]],
                 r["date_applied"], r["interview_date"]] for r in rows])
    _write_csv(out / "dim_resume_variant.csv", ["resume_variant_key", "resume_variant"],
               [[i + 1, v] for i, v in enumerate(RESUME_VARIANTS)])
    _write_csv(out / "dim_job_type.csv", ["job_type_key", "job_type"], [[i + 1, t] for i, t in enumerate(JOB_TYPES)])
    _write_csv(out / "dim_priority.csv", ["priority_key", "priority"], [[i + 1, p] for i, p in enumerate(PRIORITIES)])
    _write_csv(out / "dim_status.csv",
               ["status_key", "application_status", "is_application", "is_response", "is_interview",
                "is_offer", "is_rejection", "is_no_reply"],
               [[i + 1, *s] for i, s in enumerate(STATUSES)])

    known = [r["date_applied"] for r in rows if r["date_applied"]] + [r["interview_date"] for r in rows if r["interview_date"]]
    first = min(known)
    d = first - timedelta(days=first.weekday())
    last = max(max(known), asof) + timedelta(days=60)
    dates = []
    while d <= last:
        ws = d - timedelta(days=d.weekday())
        dates.append([int(d.strftime("%Y%m%d")), d.isoformat(), d.year, d.month, d.strftime("%B"),
                      f"{d.year}-{d.month:02d}", ws.isoformat(), d.strftime("%A")])
        d += timedelta(days=1)
    _write_csv(out / "dim_date.csv",
               ["date_key", "date", "year", "month_number", "month_name", "year_month", "week_start", "weekday"],
               dates)

    exp = expected_measures(rows)
    (out / "expected_measures.json").write_text(json.dumps(exp, indent=2), encoding="utf-8")

    if xlsx:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tracker"
        cols = ["application_id", "company_name", "job_title", "date_applied", "job_type", "location",
                "resume_variant", "referral_type", "application_status", "interview_date", "priority"]
        ws.append(cols)
        for r in rows:
            ws.append([r[c] if r[c] != "" else None for c in cols])
        wb.save(out / "tracker_flat.xlsx")
    return exp
