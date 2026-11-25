import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot_fixtures as fx  # noqa: E402

import upload_snapshot as up  # noqa: E402
import verify_databricks_run as vr  # noqa: E402

g = fx.g
SID = "snap-0123456789ab"


def status_rows_for(apps_rows):
    """What the notebook would store: one row per application, status from the CSV's expected columns."""
    return [[r[0], r[9] or None, r[10], "0", None] for r in apps_rows]


# Stored rows as text, deliberately NOT in canonical order, so the verifier must sort them itself.
DIMENSION_ROWS = [
    ["priority", "Unspecified", "1", "0", "0", "0", "0", "1", "0.0000", "0.0000", "0.0000", "0.0000", "1.0000"],
    ["job_type", "DataEngineer", "1", "0", "0", "0", "0", "1", "0.0000", "0.0000", "0.0000", "0.0000", "1.0000"],
    ["job_type", "DataAnalyst", "1", "1", "0", "0", "1", "0", "1.0000", "0.0000", "0.0000", "1.0000", "0.0000"],
]


def manifest_with_real_hashes(folder):
    """Rewrite the fixture manifest's gold hashes and measures so they match status_rows_for(default_apps)."""
    path = Path(folder) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = status_rows_for(fx.default_apps())
    manifest["gold_hash"]["gold_application_status"] = g.sha256_hex(vr.canonical_status_text(rows).encode("utf-8"))
    funnel = ["3", "1", "0", "0", "1", "1", "0", None, "0.3333", "0.0000", "0.0000", "0.3333", "0.3333"]
    manifest["gold_hash"]["gold_funnel_summary"] = g.sha256_hex(vr.canonical_funnel_text(funnel).encode("utf-8"))
    manifest["gold_hash"]["gold_funnel_by_dimension"] = g.sha256_hex(vr.canonical_dimension_text(DIMENSION_ROWS).encode("utf-8"))
    manifest["gold_hash"]["gold_funnel_by_dimension_rows"] = len(DIMENSION_ROWS)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest, funnel


class FakeWarehouse:
    """Answers the SQL the verifier sends, from canned data. Edit .data to simulate problems."""

    def __init__(self, folder):
        _, funnel = manifest_with_real_hashes(folder)
        names = vr.REQUIRED_CHECKS + [f"extra.check.{i}" for i in range(5)]
        self.data = {
            "recon": [[n, "gold", "1", "1", "true"] for n in names],
            "counts": {"bronze_applications": 3, "bronze_status_events": 5, "bronze_manifest": 1, "silver_applications": 3,
                       "silver_status_events": 5, "silver_rejected_rows": 0, "gold_application_status": 3,
                       "gold_funnel_summary": 1, "gold_funnel_by_dimension": len(DIMENSION_ROWS),
                       "gold_reconciliation": len(names)},
            "status": status_rows_for(fx.default_apps()),
            "funnel": [funnel],
            "dimension": [list(r) for r in DIMENSION_ROWS],
        }
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        rest = args[1:]
        if rest[:2] == ["warehouses", "list"]:
            return 0, json.dumps([{"id": "wh1", "enable_serverless_compute": False}, {"id": "wh2", "enable_serverless_compute": True}]), ""
        if rest[:2] == ["api", "post"]:
            body = json.loads(args[args.index("--json") + 1])
            statement = body["statement"]
            if "gold_reconciliation WHERE" in statement and "check_name" in statement:
                rows = self.data["recon"]
            elif "UNION ALL" in statement:
                rows = [[t, str(n)] for t, n in self.data["counts"].items()]
            elif "gold_funnel_by_dimension WHERE" in statement:
                rows = self.data["dimension"]
            elif "gold_application_status" in statement:
                rows = self.data["status"]
            else:
                rows = self.data["funnel"]
            return 0, json.dumps({"statement_id": "s1", "status": {"state": "SUCCEEDED"}, "result": {"data_array": rows}}), ""
        return 0, "{}", ""


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = fx.make_snapshot(Path(self._tmp.name), snapshot_id=SID)
        self.warehouse = FakeWarehouse(self.folder)

    def run_verify(self):
        reader = vr.Reader(self.warehouse, "databricks-test", sleep=lambda s: None)
        return vr.verify(self.folder, reader, fx.GENERATOR)

    def failed(self):
        return {name for name, ok, _ in self.run_verify() if not ok}

    def test_a_correct_run_verifies_every_check(self):
        results = self.run_verify()

        self.assertEqual([], [r for r in results if not r[1]])
        self.assertGreaterEqual(len(results), 14)

    def test_a_changed_dimension_row_fails_the_dimension_hash(self):
        self.warehouse.data["dimension"][0][2] = "2"

        self.assertIn("hash.gold_funnel_by_dimension", self.failed())

    def test_a_wrong_dimension_row_count_fails(self):
        self.warehouse.data["counts"]["gold_funnel_by_dimension"] = 99

        self.assertIn("rows.gold_funnel_by_dimension", self.failed())

    def test_it_prefers_the_serverless_warehouse(self):
        self.run_verify()

        post = next(c for c in self.warehouse.calls if c[1:3] == ["api", "post"])
        self.assertEqual("wh2", json.loads(post[post.index("--json") + 1])["warehouse_id"])

    def test_it_only_ever_runs_select_statements(self):
        self.run_verify()

        for call in self.warehouse.calls:
            if call[1:3] == ["api", "post"]:
                statement = json.loads(call[call.index("--json") + 1])["statement"].lstrip().upper()
                self.assertTrue(statement.startswith("SELECT"), statement[:60])
                for word in ["INSERT ", "DELETE ", "DROP ", "UPDATE ", "CREATE ", "ALTER ", "MERGE "]:
                    self.assertNotIn(word, statement)

    def test_every_call_uses_the_oauth_profile(self):
        self.run_verify()

        for call in self.warehouse.calls:
            self.assertEqual("jobtracker-demo", call[call.index("-p") + 1])

    def test_a_failed_reconciliation_row_is_caught(self):
        self.warehouse.data["recon"][2][4] = "false"

        self.assertIn("reconciliation.all_passed", self.failed())

    def test_a_reconciliation_row_whose_expected_and_actual_differ_is_caught_even_if_marked_passed(self):
        self.warehouse.data["recon"][3][3] = "999"

        self.assertIn("reconciliation.all_passed", self.failed())

    def test_missing_required_checks_are_caught(self):
        self.warehouse.data["recon"] = [r for r in self.warehouse.data["recon"] if r[0] != "gold.hash.application_status"] + \
                                       [["pad", "gold", "1", "1", "true"]] * 3

        self.assertIn("reconciliation.required_checks_present", self.failed())

    def test_duplicate_rows_after_a_rerun_are_caught(self):
        self.warehouse.data["counts"]["gold_application_status"] = 6
        self.warehouse.data["counts"]["bronze_applications"] = 6

        failed = self.failed()
        self.assertIn("rows.gold_application_status", failed)
        self.assertIn("rows.bronze_applications", failed)

    def test_rejected_rows_in_silver_are_caught(self):
        self.warehouse.data["counts"]["silver_rejected_rows"] = 1

        self.assertIn("rows.silver_rejected_rows", self.failed())

    def test_a_changed_status_changes_the_hash_and_the_projection_comparison(self):
        self.warehouse.data["status"][0][1] = "Accepted"

        failed = self.failed()
        self.assertIn("hash.gold_application_status", failed)
        self.assertIn("status.equals_the_apps_projection", failed)

    def test_a_changed_funnel_value_is_caught(self):
        self.warehouse.data["funnel"][0][8] = "0.3334"

        failed = self.failed()
        self.assertIn("funnel.equals_manifest_measures", failed)
        self.assertIn("hash.gold_funnel_summary", failed)

    def test_a_missing_funnel_row_is_caught(self):
        self.warehouse.data["funnel"] = []

        self.assertIn("funnel.equals_manifest_measures", self.failed())

    def test_a_tampered_local_snapshot_is_refused_before_any_query(self):
        path = self.folder / "applications.csv"
        path.write_bytes(path.read_bytes().replace(b"Company 001", b"Company 009", 1))

        with self.assertRaises(g.GuardError):
            self.run_verify()
        self.assertEqual([], self.warehouse.calls)

    def test_a_failing_statement_raises_a_clear_error(self):
        def bad(args):
            if args[1:3] == ["api", "post"]:
                return 0, json.dumps({"statement_id": "s", "status": {"state": "FAILED", "error": {"message": "table not found"}}}), ""
            return self.warehouse(args)

        reader = vr.Reader(bad, "databricks-test", sleep=lambda s: None)
        with self.assertRaises(up.CliFailure) as ctx:
            vr.verify(self.folder, reader, fx.GENERATOR)
        self.assertIn("table not found", str(ctx.exception))

    def test_a_pending_statement_is_polled_until_it_finishes(self):
        polls = []

        def slow(args):
            if args[1:3] == ["api", "post"]:
                return 0, json.dumps({"statement_id": "s9", "status": {"state": "PENDING"}}), ""
            if args[1:3] == ["api", "get"]:
                polls.append(args[3])
                return 0, json.dumps({"statement_id": "s9", "status": {"state": "SUCCEEDED"}, "result": {"data_array": [["1"]]}}), ""
            return self.warehouse(args)

        reader = vr.Reader(slow, "databricks-test", sleep=lambda s: None)

        self.assertEqual([["1"]], reader.sql("SELECT 1"))
        self.assertEqual(["/api/2.0/sql/statements/s9"], polls)


class CanonicalTextTests(unittest.TestCase):
    def test_status_text_matches_the_contract_example(self):
        rows = [["2", "Interviewed", "0", "0", "2026-10-20"], ["1", None, "0", "1", None], ["3", "Applied", "1", "0", None]]

        self.assertEqual("1\t\\N\t0\t1\t\\N\n2\tInterviewed\t0\t0\t2026-10-20\n3\tApplied\t1\t0\t\\N\n", vr.canonical_status_text(rows))

    def test_rows_sort_by_number_not_by_text(self):
        rows = [["10", "Applied", "0", "0", None], ["9", "Applied", "0", "0", None]]

        self.assertTrue(vr.canonical_status_text(rows).startswith("9\t"))

    def test_funnel_text_matches_the_contract_example(self):
        values = ["2", "1", "1", "0", "0", "0", "1", "21.00", "0.5000", "0.5000", "0.0000", "0.0000", "0.0000"]

        self.assertEqual("2\t1\t1\t0\t0\t0\t1\t21.00\t0.5000\t0.5000\t0.0000\t0.0000\t0.0000\n", vr.canonical_funnel_text(values))
        self.assertEqual("0\t\\N\n", vr.canonical_funnel_text(["0", None]))


if __name__ == "__main__":
    unittest.main()
