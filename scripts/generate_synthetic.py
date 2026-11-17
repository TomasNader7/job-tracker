"""Generate a seeded synthetic job-search tracker (star-schema CSVs), calibrated to the SHAPE
of a real tracker: same row count, status mix, resume-variant / job-type / priority mix,
networking share and weekly application pace. Every company, title and date is fabricated.

Calibration constants below are aggregates only (no names, links or contacts).
Deterministic: same seed -> same files.

  python scripts/generate_synthetic.py [--xlsx]
"""
import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import starschema as ss

SEED = 20261116
ASOF = date(2026, 11, 16)  # fixed reference date so output is reproducible

# --- calibration (aggregates from the real tracker as of 2026-11-16) ---
N_STARTED = 182
N_NOT_STARTED = 1
N_REJECTED = 54
N_INTERVIEWED = 2
N_WITHDRAWN = 1
NETWORKING_SHARE = 22 / 183
VARIANT_WEIGHTS = {"Data Analytics": 52, "Business Intelligence": 36, "ML/Data Science": 23,
                   "Backend SWE": 45, "Data Engineering": 13, "Automation": 11,
                   "Frontend SWE": 2, "Unspecified": 1}
JOB_TYPE_WEIGHTS = {"Data Analyst": 39, "Data Scientist": 16, "New Grad Program": 10,
                    "Software Engineer": 43, "BI Analyst": 20, "Data Engineer": 13,
                    "Business Analyst": 29, "ML/AI Engineer": 12, "Other": 1}
PRIORITY_WEIGHTS = {"Medium Priority": 137, "Low Priority": 6, "Top Priority": 28, "Unspecified": 12}
APPLIED_PER_WEEK = {  # week start (Monday) -> applications that week
    "2026-09-14": 17, "2026-09-21": 16, "2026-09-28": 21, "2026-10-05": 27, "2026-10-12": 23,
    "2026-10-19": 16, "2026-10-26": 19, "2026-11-02": 18, "2026-11-09": 19, "2026-11-16": 6,
}
# --- end calibration ---

TITLE_BY_TYPE = {
    "Data Analyst": "Data Analyst", "BI Analyst": "Business Intelligence Analyst",
    "Business Analyst": "Business Analyst", "Data Scientist": "Data Scientist, New Grad",
    "ML/AI Engineer": "Machine Learning Engineer", "Data Engineer": "Data Engineer",
    "Software Engineer": "Software Engineer I", "New Grad Program": "2027 Graduate Program",
    "Other": "Technical Specialist",
}
CITIES = ["Chicago, IL", "Austin, TX", "Remote (US)", "Atlanta, GA", "Seattle, WA", "Denver, CO",
          "Boston, MA", "Dallas, TX", "Charlotte, NC", "New York, NY", "Raleigh, NC", "Greenville, SC"]


def choose(rng, weights):
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def build_rows(rng):
    rows = []
    n = 0
    for wk, count in APPLIED_PER_WEEK.items():
        wk0 = date.fromisoformat(wk)
        for _ in range(count):
            applied = min(wk0 + timedelta(days=rng.randint(0, 6)), ASOF)
            n += 1
            jtype = choose(rng, JOB_TYPE_WEIGHTS)
            rows.append({
                "application_id": n, "company_name": f"Company {n:03d}", "job_title": TITLE_BY_TYPE[jtype],
                "date_applied": applied, "job_type": jtype, "location": rng.choice(CITIES),
                "resume_variant": choose(rng, VARIANT_WEIGHTS),
                "referral_type": "Networking Contact" if rng.random() < NETWORKING_SHARE else "Cold Apply",
                "application_status": "Applied", "interview_date": "",
                "priority": choose(rng, PRIORITY_WEIGHTS),
            })
    assert n == N_STARTED, (n, N_STARTED)

    # exact status counts, spread randomly over rows
    order = list(range(len(rows)))
    rng.shuffle(order)
    old = [i for i in order if (ASOF - rows[i]["date_applied"]).days >= 50]
    interviewed = old[:N_INTERVIEWED]
    for i in interviewed:
        gap = rng.randint(5, 45)
        rows[i]["application_status"] = "Interviewed"
        rows[i]["interview_date"] = min(rows[i]["date_applied"] + timedelta(days=gap), ASOF)
    rest = [i for i in order if i not in interviewed]
    for i in rest[:N_REJECTED]:
        rows[i]["application_status"] = "Rejected"
    for i in rest[N_REJECTED:N_REJECTED + N_WITHDRAWN]:
        rows[i]["application_status"] = "Withdrawn"

    for k in range(N_NOT_STARTED):
        n += 1
        jtype = choose(rng, JOB_TYPE_WEIGHTS)
        rows.append({
            "application_id": n, "company_name": f"Company {n:03d}", "job_title": TITLE_BY_TYPE[jtype],
            "date_applied": "", "job_type": jtype, "location": rng.choice(CITIES),
            "resume_variant": choose(rng, VARIANT_WEIGHTS), "referral_type": "Cold Apply",
            "application_status": "Not Started", "interview_date": "", "priority": "Medium Priority",
        })
    return ss.apply_no_reply_rule(rows, ASOF)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "synthetic"))
    ap.add_argument("--xlsx", action="store_true", help="also write a git-ignored flat Excel copy for PivotTable checks")
    args = ap.parse_args()
    rows = build_rows(random.Random(SEED))
    exp = ss.write_star(args.out, rows, ASOF, xlsx=args.xlsx)
    print(f"Wrote {len(rows)} rows to {args.out}")
    for k, v in exp.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
