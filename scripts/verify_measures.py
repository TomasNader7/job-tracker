"""Recompute every measure from the star-schema CSVs (joins done by hand, no shortcuts)
and compare to expected_measures.json written by the generator. Exit 1 on any mismatch.

Use the printed numbers as the reference when checking Power BI cards and Excel PivotTables.
"""
import csv
import json
import sys
from datetime import date
from pathlib import Path

DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "synthetic"


def read(name):
    with open(DATA / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    fact = read("fact_application.csv")
    status = {r["status_key"]: r for r in read("dim_status.csv")}
    keys = {
        "resume_variant_key": {r["resume_variant_key"] for r in read("dim_resume_variant.csv")},
        "job_type_key": {r["job_type_key"] for r in read("dim_job_type.csv")},
        "priority_key": {r["priority_key"] for r in read("dim_priority.csv")},
        "status_key": set(status),
    }
    dates = {r["date_key"] for r in read("dim_date.csv")}

    problems = []
    ids = [r["application_id"] for r in fact]
    if len(ids) != len(set(ids)):
        problems.append("duplicate application_id")
    for r in fact:
        for k, valid in keys.items():
            if r[k] not in valid:
                problems.append(f"{r['application_id']}: bad {k}={r[k]}")
        for k in ("date_key", "interview_date_key"):
            if r[k] and r[k] not in dates:
                problems.append(f"{r['application_id']}: {k}={r[k]} not in dim_date")

    apps = [r for r in fact if status[r["status_key"]]["is_application"] == "1"]
    n = len(apps)

    def cnt(flag):
        return sum(1 for r in apps if status[r["status_key"]][flag] == "1")

    gaps = [
        (date.fromisoformat(r["interview_date"]) - date.fromisoformat(r["date_applied"])).days
        for r in apps if r["interview_date"]
    ]
    got = {
        "Applications": n, "Responses": cnt("is_response"), "Interviews": cnt("is_interview"),
        "Offers": cnt("is_offer"), "Rejections": cnt("is_rejection"), "No Replies": cnt("is_no_reply"),
        "Response Rate": round(cnt("is_response") / n, 4), "Interview Rate": round(cnt("is_interview") / n, 4),
        "Offer Rate": round(cnt("is_offer") / n, 4), "Rejection Rate": round(cnt("is_rejection") / n, 4),
        "No-Reply Rate": round(cnt("is_no_reply") / n, 4),
        "Avg Days to Interview": round(sum(gaps) / len(gaps), 2) if gaps else None,
        "Rows with interview date": len(gaps),
    }
    exp = json.loads((DATA / "expected_measures.json").read_text(encoding="utf-8"))
    for k, v in exp.items():
        if got.get(k) != v:
            problems.append(f"{k}: expected {v}, recomputed {got.get(k)}")

    print(f"rows={len(fact)}  FK/date checks + measure recompute")
    for k, v in got.items():
        print(f"  {k}: {v}")
    if problems:
        print("FAIL")
        print("\n".join(problems[:20]))
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
