"""Build the Power BI star schema from the REAL tracker (private).

Reads Full-Time Job Tracker.xlsx (read-only) and writes the same six tables as the synthetic
generator into data/real/, which is git-ignored. Never commit that folder.

  python scripts/build_real.py                       # uses default tracker path
  python scripts/build_real.py --xlsx                # also writes data/real/tracker_flat.xlsx for PivotTable checks
  python scripts/build_real.py --stats               # print aggregates only (used to calibrate the synthetic data)
  python scripts/build_real.py --asof 2026-11-16     # reference date for the No Reply rule (default: today)

Cleaning rules (documented in README):
  - Job Type is derived from Job Title keywords (the raw "Type of Job" column is inconsistent).
  - Blank priority / resume variant -> "Unspecified".
  - Referral / Networking Contact filled -> "Networking Contact", else "Cold Apply".
  - Status Applied older than 30 days -> "No Reply".
  - Rows that are outreach with no posting are excluded.
"""
import argparse
import collections
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import starschema as ss  # noqa: E402

DEFAULT_TRACKER = os.environ.get("TRACKER_PATH", "")  # set TRACKER_PATH or pass --tracker
OUT = Path(__file__).resolve().parents[1] / "data" / "real"

# first match wins
JOB_TYPE_RULES = [
    ("ML/AI Engineer", r"machine learning engineer|\bml engineer|ai/ml|applied ai|\bai engineer|ai (product|security) engineer|ai and machine learning|research engineer|machine learning analyst"),
    ("Data Scientist", r"data scien|demand science"),
    ("Data Engineer", r"data engineer|data developer|data platform|analytics engineer|data integration"),
    ("BI Analyst", r"business intelligence|\bbi\b|dashboard|reporting"),
    ("Data Analyst", r"data analy|analytics|insights|quantitative|predictive"),
    ("Software Engineer", r"software|developer|backend|forward deploy|\bswe\b|engineer"),
    ("Business Analyst", r"analyst"),
    ("New Grad Program", r"program|rotation|graduate|early career|talent|2027"),
]


def job_type(title):
    t = (title or "").lower()
    for name, pat in JOB_TYPE_RULES:
        if re.search(pat, t):
            return name
    return "Other"


def to_date(v):
    if v in (None, ""):
        return ""
    if hasattr(v, "date"):
        return v.date()
    return date.fromisoformat(str(v)[:10])


def load_rows(path, asof):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Applications"]
    data = list(ws.iter_rows(values_only=True))
    h = list(data[0])
    ix = {c: h.index(c) for c in h if c}
    rows, skipped = [], 0
    for r in data[1:]:
        if not r[ix["Company Name"]]:
            continue
        title = r[ix["Job Title"]] or ""
        if "outreach (no posting)" in title.lower() or (r[ix["Type of Job"]] or "").lower() == "referral outreach":
            skipped += 1
            continue
        status = r[ix["Application Status"]] or "Not Started"
        if status not in ss.STATUS_FLAGS:
            raise SystemExit(f"Unknown status {status!r}; add it to starschema.STATUSES")
        variant = r[ix["Resume Variant Used"]] or "Unspecified"
        prio = r[ix["Priority"]] or "Unspecified"
        for val, allowed, label in ((variant, ss.RESUME_VARIANTS, "resume variant"), (prio, ss.PRIORITIES, "priority")):
            if val not in allowed:
                raise SystemExit(f"Unknown {label} {val!r}; add it to starschema")
        rows.append({
            "application_id": len(rows) + 1, "company_name": r[ix["Company Name"]], "job_title": title,
            "date_applied": to_date(r[ix["Date Applied"]]), "job_type": job_type(title),
            "location": r[ix["Location"]] or "", "resume_variant": variant,
            "referral_type": "Networking Contact" if r[ix["Referral / Networking Contact"]] else "Cold Apply",
            "application_status": status, "interview_date": to_date(r[ix["Interview Date"]]), "priority": prio,
        })
    ss.apply_no_reply_rule(rows, asof)
    return rows, skipped


def stats(rows, asof):
    """Aggregates only, no names. Used to calibrate the public synthetic generator."""
    started = [r for r in rows if r["date_applied"]]
    out = {
        "rows": len(rows), "not_started": len(rows) - len(started),
        "status": dict(collections.Counter(r["application_status"] for r in rows)),
        "resume_variant": dict(collections.Counter(r["resume_variant"] for r in rows)),
        "job_type": dict(collections.Counter(r["job_type"] for r in rows)),
        "priority": dict(collections.Counter(r["priority"] for r in rows)),
        "networking_contact": sum(1 for r in rows if r["referral_type"] == "Networking Contact"),
        "first_applied": min(r["date_applied"] for r in started).isoformat(),
        "last_applied": max(r["date_applied"] for r in started).isoformat(),
        "applied_per_week": dict(sorted(collections.Counter(
            (r["date_applied"] - __import__("datetime").timedelta(days=r["date_applied"].weekday())).isoformat()
            for r in started).items())),
        "measures": ss.expected_measures(rows),
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracker", default=str(DEFAULT_TRACKER))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--asof", default=date.today().isoformat())
    ap.add_argument("--xlsx", action="store_true")
    ap.add_argument("--stats", action="store_true", help="print aggregates only; write nothing")
    args = ap.parse_args()
    asof = date.fromisoformat(args.asof)
    if not args.tracker:
        raise SystemExit("Set the TRACKER_PATH environment variable or pass --tracker <path to the tracker .xlsx>")

    rows, skipped = load_rows(args.tracker, asof)
    if args.stats:
        print(json.dumps(stats(rows, asof), indent=2, default=str))
        return
    exp = ss.write_star(args.out, rows, asof, xlsx=args.xlsx)
    print(f"Wrote {len(rows)} rows ({skipped} outreach-only rows excluded) to {args.out}  [as of {asof}]")
    print(json.dumps(exp, indent=2))
    others = [r["job_title"] for r in rows if r["job_type"] == "Other"]
    print(f"\nJob types: {dict(collections.Counter(r['job_type'] for r in rows))}")
    if others:
        print("Titles mapped to 'Other' (review):")
        for t in others:
            print("  -", t)


if __name__ == "__main__":
    main()
