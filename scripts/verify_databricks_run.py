"""Independent read-back of a Databricks pipeline run (Phase 3).

The notebook reconciles itself and fails the job if anything is off. This script does NOT take its word for it:
it reads the stored tables through the SQL warehouse and re-checks them against the LOCAL snapshot and manifest,
recomputing both canonical gold-content hashes in Python from the stored rows.

Usage:  python scripts/verify_databricks_run.py snapshots/<snapshot_id>
Exit codes: 0 everything verified, 1 a check failed, 2 refused (unsafe snapshot or token in play), 3 CLI problem.
Standard library only. Read-only: it only runs SELECT statements.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

import snapshot_guard as guard
import upload_snapshot as up

SCHEMA = "workspace.job_tracker_p3"
TABLES = ["bronze_applications", "bronze_status_events", "bronze_manifest", "silver_applications", "silver_status_events",
          "silver_rejected_rows", "gold_application_status", "gold_funnel_summary", "gold_funnel_by_dimension", "gold_reconciliation"]
FUNNEL_ORDER = ["Applications", "Responses", "Interviews", "Offers", "Rejections", "No_Replies", "Rows_with_interview_date",
                "Avg_Days_to_Interview", "Responses_Rate", "Interviews_Rate", "Offers_Rate", "Rejections_Rate", "No_Replies_Rate"]
REQUIRED_CHECKS = ["source.applications.sha256", "source.status_events.sha256", "silver.rejected_rows_total",
                   "gold.status_mismatches_vs_expected_projected_status", "gold.problems.total", "gold.hash.application_status",
                   "gold.hash.funnel_summary", "gold.hash.funnel_by_dimension", "selftest.all_checks_passed", "gold.measure.Applications", "gold.measure.No_Replies_Rate"]
NULL_TEXT = "\\N"


def canonical_status_text(rows) -> str:
    """rows: (application_id, status, is_no_reply, problem_count, interview_date) as text/None. Contract format."""
    lines = []
    for app_id, status, no_reply, problems, idate in sorted(rows, key=lambda r: int(r[0])):
        lines.append("\t".join([str(int(app_id)), status if status is not None else NULL_TEXT, str(int(no_reply)),
                                str(int(problems)), idate if idate is not None else NULL_TEXT]) + "\n")
    return "".join(lines)


DIMENSION_ORDER = ["job_type", "resume_variant", "priority", "referral_type"]
DIMENSION_COLUMNS = ["dimension", "dimension_value", "Applications", "Responses", "Interviews", "Offers", "Rejections",
                     "No_Replies", "Responses_Rate", "Interviews_Rate", "Offers_Rate", "Rejections_Rate", "No_Replies_Rate"]


def canonical_dimension_text(rows) -> str:
    """rows: the 13 DIMENSION_COLUMNS as text. Fixed dimension order, then value as plain text. Contract format."""
    lines = []
    for row in sorted(rows, key=lambda r: (DIMENSION_ORDER.index(r[0]), r[1])):
        lines.append("\t".join(v if v is not None else NULL_TEXT for v in row) + "\n")
    return "".join(lines)


def canonical_funnel_text(values) -> str:
    return "\t".join(v if v is not None else NULL_TEXT for v in values) + "\n"


class Reader:
    def __init__(self, runner, cli, sleep=time.sleep):
        self.up = up.Uploader(runner, cli, say=lambda *_: None, sleep=sleep)
        self.sleep = sleep
        self._warehouse = None

    def warehouse_id(self) -> str:
        if self._warehouse is None:
            warehouses = up.parse_json_output(self.up.call("warehouses", "list", "-o", "json"))
            if not warehouses:
                raise up.CliFailure("No SQL warehouse found")
            serverless = [w for w in warehouses if w.get("enable_serverless_compute")]
            self._warehouse = (serverless or warehouses)[0]["id"]
        return self._warehouse

    def sql(self, statement: str, polls=40) -> list:
        """Run one SELECT and return its rows as lists of text (or None)."""
        body = json.dumps({"warehouse_id": self.warehouse_id(), "statement": statement,
                           "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
        reply = up.parse_json_output(self.up.call("api", "post", "/api/2.0/sql/statements", "--json", body))
        for _ in range(polls):
            state = reply.get("status", {}).get("state")
            if state == "SUCCEEDED":
                return reply.get("result", {}).get("data_array", []) or []
            if state in ("FAILED", "CANCELED", "CLOSED"):
                raise up.CliFailure(f"SQL {state}: {reply.get('status', {}).get('error', {}).get('message', '')[:300]}")
            self.sleep(5)
            reply = up.parse_json_output(self.up.call("api", "get", f"/api/2.0/sql/statements/{reply['statement_id']}"))
        raise up.CliFailure("SQL statement did not finish in time")


def verify(folder, reader, generator=up.GENERATOR) -> list:
    """Returns a list of (check, ok, detail)."""
    manifest = guard.validate_snapshot(folder, generator)
    sid = manifest["snapshot_id"]
    if not guard.SAFE_ID.fullmatch(sid):
        raise guard.GuardError("unsafe snapshot id")
    where = f"snapshot_id = '{sid}'"
    files = {f["name"]: f for f in manifest["files"]}
    out = []

    def add(name, ok, detail=""):
        out.append((name, bool(ok), detail))

    # 1. the notebook's own reconciliation, read back
    recon = reader.sql(f"SELECT check_name, layer, expected, actual, passed FROM {SCHEMA}.gold_reconciliation WHERE {where} ORDER BY check_name")
    names = {r[0] for r in recon}
    failed = [r[0] for r in recon if str(r[4]).lower() != "true" or r[2] != r[3]]
    add("reconciliation.rows_present", len(recon) >= len(REQUIRED_CHECKS), f"{len(recon)} checks stored")
    add("reconciliation.required_checks_present", set(REQUIRED_CHECKS) <= names, f"missing {sorted(set(REQUIRED_CHECKS) - names)}")
    add("reconciliation.all_passed", not failed, f"failed: {failed}")

    # 2. row counts per table for THIS snapshot (a rerun must not duplicate anything)
    union = " UNION ALL ".join(f"SELECT '{t}' AS t, COUNT(*) AS n FROM {SCHEMA}.{t} WHERE {where}" for t in TABLES)
    counts = {r[0]: int(r[1]) for r in reader.sql(union)}
    accepted = manifest["applications"]["accepted"]
    expected_counts = {"bronze_applications": files["applications.csv"]["rows"], "bronze_status_events": files["status_events.csv"]["rows"],
                       "bronze_manifest": 1, "silver_applications": accepted, "silver_status_events": files["status_events.csv"]["rows"],
                       "silver_rejected_rows": 0, "gold_application_status": accepted, "gold_funnel_summary": 1,
                       "gold_funnel_by_dimension": manifest["gold_hash"]["gold_funnel_by_dimension_rows"],
                       "gold_reconciliation": len(recon)}
    for table, want in expected_counts.items():
        add(f"rows.{table}", counts.get(table) == want, f"expected {want}, found {counts.get(table)}")

    # 3. the stored gold status table, re-hashed here
    status_rows = reader.sql(f"SELECT application_id, status, is_no_reply, problem_count, CAST(interview_date AS STRING) "
                             f"FROM {SCHEMA}.gold_application_status WHERE {where} ORDER BY application_id")
    status_hash = guard.sha256_hex(canonical_status_text(status_rows).encode("utf-8"))
    add("hash.gold_application_status", status_hash == manifest["gold_hash"]["gold_application_status"], f"sha256 {status_hash}")

    with open(Path(folder) / "applications.csv", encoding="utf-8", newline="") as handle:
        expected = {int(r["application_id"]): (r["expected_projected_status"] or None, int(r["expected_is_no_reply"]))
                    for r in csv.DictReader(handle)}
    stored = {int(r[0]): (r[1], int(r[2])) for r in status_rows}
    add("status.equals_the_apps_projection", stored == expected, f"{sum(1 for k in expected if stored.get(k) != expected[k])} differ")

    # 4. the stored funnel row
    funnel_sql = ", ".join(f"CAST({c} AS STRING)" for c in FUNNEL_ORDER)
    funnel = reader.sql(f"SELECT {funnel_sql} FROM {SCHEMA}.gold_funnel_summary WHERE {where}")
    row = funnel[0] if funnel else [None] * len(FUNNEL_ORDER)
    measures = manifest["expected_measures"]
    want = [None if measures[c] is None else str(measures[c]) for c in FUNNEL_ORDER]
    add("funnel.equals_manifest_measures", list(row) == want, f"stored {list(row)}")
    funnel_hash = guard.sha256_hex(canonical_funnel_text(row).encode("utf-8"))
    add("hash.gold_funnel_summary", funnel_hash == manifest["gold_hash"]["gold_funnel_summary"], f"sha256 {funnel_hash}")

    # 5. the stored by-dimension table, re-hashed here
    dimension_sql = ", ".join(f"CAST({c} AS STRING)" for c in DIMENSION_COLUMNS)
    dimension = reader.sql(f"SELECT {dimension_sql} FROM {SCHEMA}.gold_funnel_by_dimension WHERE {where} "
                           f"ORDER BY dimension, dimension_value")
    dimension_hash = guard.sha256_hex(canonical_dimension_text(dimension).encode("utf-8"))
    add("hash.gold_funnel_by_dimension", dimension_hash == manifest["gold_hash"]["gold_funnel_by_dimension"], f"sha256 {dimension_hash}")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Independent read-back of a Databricks pipeline run.")
    parser.add_argument("snapshot_dir")
    parser.add_argument("--cli", default=up.DEFAULT_CLI)
    args = parser.parse_args(argv)

    try:
        guard.ensure_oauth_only(__import__("os").environ, up.DATABRICKS_CFG, up.PROFILE)
        reader = Reader(up.real_runner, args.cli)
        results = verify(args.snapshot_dir, reader)
    except guard.GuardError as exc:
        print(f"REFUSED: {exc}")
        return 2
    except up.CliFailure as exc:
        print(f"FAILED: {exc}")
        return 3

    for name, ok, detail in results:
        show = detail if (not ok or name.startswith("hash.")) else ""
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  ({show})" if show else ""))
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)} of {len(results)} checks verified independently." + ("" if not bad else f" {len(bad)} FAILED."))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
