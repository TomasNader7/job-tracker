"""Runs the Databricks notebook's pure functions on a LOCAL Spark, with ANSI mode on like serverless.

Skipped when pyspark or Java is missing. To run locally:
    python -m venv .venv-spark && .venv-spark/Scripts/pip install pyspark==3.5.3
    .venv-spark/Scripts/python -m unittest scripts/tests/test_snapshot_pipeline_spark.py
No Python workers are used (DataFrames are built with SQL VALUES), which also keeps this working on Windows.
"""
import csv
import importlib.util
import json
import os
import shutil
import sys
import unittest
from decimal import Decimal
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
REPO = SCRIPTS.parent
NOTEBOOK = REPO / "databricks" / "notebooks" / "02_snapshot_pipeline.py"
HAVE_SPARK = importlib.util.find_spec("pyspark") is not None


def _fix_java_home():
    """Some machines point JAVA_HOME at the bin folder or at a path with spaces. Spark's launcher needs neither."""
    home = os.environ.get("JAVA_HOME", "")
    candidates = [home, str(Path(home).parent)] if home else []
    which = shutil.which("java")
    if which:
        candidates.append(str(Path(which).resolve().parent.parent))
    for candidate in candidates:
        if candidate and (Path(candidate) / "bin" / ("java.exe" if os.name == "nt" else "java")).exists():
            if os.name == "nt" and " " in candidate:
                import ctypes
                buffer = ctypes.create_unicode_buffer(260)
                if ctypes.windll.kernel32.GetShortPathNameW(candidate, buffer, 260):
                    candidate = buffer.value
            os.environ["JAVA_HOME"] = candidate
            return


def _newest_snapshot():
    root = REPO / "snapshots"
    if not root.is_dir():
        return None
    folders = sorted(p for p in root.iterdir() if (p / "manifest.json").is_file())
    return folders[-1] if folders else None


@unittest.skipUnless(HAVE_SPARK, "pyspark is not installed")
class SparkPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _fix_java_home()
        from pyspark.sql import SparkSession
        cls.spark = (SparkSession.builder.master("local[2]").appName("snapshot-pipeline-tests")
                     .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "2")
                     .config("spark.sql.session.timeZone", "UTC").config("spark.sql.ansi.enabled", "true")
                     .config("spark.driver.bindAddress", "127.0.0.1").config("spark.driver.host", "127.0.0.1")
                     .getOrCreate())
        cls.spark.sparkContext.setLogLevel("ERROR")
        head = NOTEBOOK.read_text(encoding="utf-8").split("# ==== RUN ====")[0]
        cls.ns = {}
        exec(compile(head, str(NOTEBOOK), "exec"), cls.ns)

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    # ---- self-test ----

    def test_the_selftest_passes_every_check(self):
        results = self.ns["run_selftest"](self.spark)

        failed = [(name, detail) for name, passed, detail in results if not passed]
        self.assertEqual([], failed)
        self.assertGreaterEqual(len(results), 20 + 3)

    def test_the_selftest_really_fails_when_the_expected_answer_is_wrong(self):
        golden = json.loads(self.ns["GOLDEN_FIXTURE_JSON"])
        golden["cases"][0]["expected"]["1"]["status"] = "Rejected"          # simple_chain really ends at Interviewed
        golden["cases"][1]["expected"]["1"]["problem_codes"] = ["ILLEGAL_FIRST"]
        self.ns["GOLDEN_FIXTURE_JSON"] = json.dumps(golden)
        try:
            results = self.ns["run_selftest"](self.spark)
        finally:
            self.ns["GOLDEN_FIXTURE_JSON"] = json.dumps(json.loads(
                NOTEBOOK.read_text(encoding="utf-8").split("GOLDEN_FIXTURE_JSON = r'''")[1].split("'''")[0]))

        failed = {name for name, passed, _ in results if not passed}
        self.assertIn(f"selftest.case.{golden['cases'][0]['name']}", failed)
        self.assertIn(f"selftest.case.{golden['cases'][1]['name']}", failed)
        self.assertIn("selftest.cases_passed", failed)

    # ---- the real snapshot: Spark must reproduce what the C# exporter computed ----

    def _run_pipeline_on(self, folder):
        ns, spark = self.ns, self.spark
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))

        def bronze(name, columns):
            with open(folder / name, encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(columns, rows[0])
            data = [row + [str(i)] for i, row in enumerate(rows[1:], start=2)]
            return ns["rows_to_df"](spark, columns + ["_row_number"], data)

        apps_bronze = bronze("applications.csv", ns["APPLICATION_COLUMNS"])
        events_bronze = bronze("status_events.csv", ns["EVENT_COLUMNS"])
        apps, rejected_apps = ns["split_applications"](apps_bronze)
        events, rejected_events = ns["split_events"](events_bronze, apps)
        results, problems = ns["compute_projection"](spark, events, manifest["as_of"])
        gold = ns["build_gold_status"](apps, results, problems)
        funnel = ns["build_funnel"](gold, apps)
        return manifest, apps, events, rejected_apps, rejected_events, problems, gold, funnel

    def test_spark_reproduces_the_csharp_snapshot_exactly(self):
        folder = _newest_snapshot()
        if folder is None:
            self.skipTest("no snapshot in snapshots/ (run the Tools export-snapshot command first)")
        ns = self.ns

        manifest, apps, events, rej_apps, rej_events, problems, gold, funnel = self._run_pipeline_on(folder)

        self.assertEqual(manifest["applications"]["accepted"], apps.count())
        self.assertEqual(manifest["files"][1]["rows"], events.count())
        self.assertEqual(0, rej_apps.count() + rej_events.count())

        # every measure, as text, equals the manifest
        want = manifest["expected_measures"]
        got = ns["funnel_strings"](funnel.collect()[0].asDict())
        for name in ns["FUNNEL_ORDER"]:
            self.assertEqual(str(want[name]) if want[name] is not None else "\\N", got[name], name)

        # the two canonical gold-content hashes equal the C# ones
        status_rows = [r.asDict() for r in gold.collect()]
        self.assertEqual(manifest["gold_hash"]["gold_application_status"], ns["sha256_text"](ns["canonical_status_text"](status_rows)))
        self.assertEqual(manifest["gold_hash"]["gold_funnel_summary"], ns["sha256_text"](ns["canonical_funnel_text"](funnel.collect()[0].asDict())))

        # the by-dimension table: same row count and the same canonical hash as the C# exporter computed
        dimension = ns["build_funnel_by_dimension"](gold, apps)
        dimension_rows = [r.asDict() for r in dimension.collect()]
        self.assertEqual(manifest["gold_hash"]["gold_funnel_by_dimension_rows"], len(dimension_rows))
        self.assertEqual(manifest["gold_hash"]["gold_funnel_by_dimension"],
                         ns["sha256_text"](ns["canonical_dimension_text"](dimension_rows)))

        # the status Spark computed from events equals the status the app projected
        with open(folder / "applications.csv", encoding="utf-8", newline="") as handle:
            expected = {int(r["application_id"]): (r["expected_projected_status"] or None, int(r["expected_is_no_reply"]))
                        for r in csv.DictReader(handle)}
        computed = {r["application_id"]: (r["status"], int(r["is_no_reply"])) for r in status_rows}
        self.assertEqual(expected, computed)

        # problem counts
        by_code = {r["code"]: r["n"] for r in problems.groupBy("code").count().withColumnRenamed("count", "n").collect()}
        self.assertEqual(manifest["expected_problems"]["total"], sum(by_code.values()))

    def test_bad_expected_values_are_rejected_by_validation_never_trusted(self):
        folder = _newest_snapshot()
        if folder is None:
            self.skipTest("no snapshot in snapshots/")
        ns, spark = self.ns, self.spark
        # Damage every expected_* value. Validation must reject those rows rather than accept them.
        # (That the projection takes only the events DataFrame is structural: see compute_projection's signature.)
        with open(folder / "applications.csv", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        columns = rows[0]
        damaged = [r[:9] + ["Hired", "7"] if i else r for i, r in enumerate(rows)]
        original = [(r[0], r[9], r[10]) for r in rows[1:]]
        self.assertNotEqual(original[0][1:], ("Hired", "7"))

        data = [row + [str(i)] for i, row in enumerate(damaged[1:], start=2)]
        bronze = ns["rows_to_df"](spark, columns + ["_row_number"], data)
        valid, rejected = ns["split_applications"](bronze)

        # BAD_STATUS and BAD_FLAG reject every row, which is exactly why they are validated, not trusted.
        self.assertEqual(0, valid.count())
        self.assertEqual({"BAD_STATUS"}, {r["reason_code"] for r in rejected.collect()})

    # ---- rounding and edge behaviour of the measures ----

    def _funnel_for(self, statuses, interview_days=None):
        ns, spark = self.ns, self.spark
        apps = ns["rows_to_df"](spark, ["application_id", "date_applied"],
                                [[str(i), "2026-10-01"] for i in range(1, len(statuses) + 1)])
        from pyspark.sql import functions as F
        apps = apps.select(F.col("application_id").cast("long"), F.to_date("date_applied").alias("date_applied"))
        gold_rows = []
        for i, status in enumerate(statuses, start=1):
            days = None if interview_days is None else interview_days.get(i)
            idate = "NULL" if days is None else f"DATE '2026-10-01' + INTERVAL '{days}' DAY"
            gold_rows.append(f"SELECT {i}L AS application_id, {'NULL' if status is None else repr(status)} AS status, "
                             f"0 AS is_no_reply, 0 AS problem_count, CAST({idate} AS DATE) AS interview_date")
        gold = spark.sql(" UNION ALL ".join(gold_rows))
        return ns["build_funnel"](gold, apps).collect()[0].asDict()

    def test_rates_round_half_up(self):
        row = self._funnel_for(["Rejected"] + ["Applied"] * 31)     # 1/32 = 0.03125 exactly -> HALF UP 0.0313
        self.assertEqual(Decimal("0.0313"), row["Rejections_Rate"])
        self.assertEqual("0.0313", self.ns["funnel_strings"](row)["Rejections_Rate"])

    def test_average_days_rounds_half_up(self):
        statuses = ["Interviewed"] * 8
        row = self._funnel_for(statuses, {i: (1 if i == 8 else 0) for i in range(1, 9)})   # 1/8 = 0.125 -> 0.13
        self.assertEqual(Decimal("0.13"), row["Avg_Days_to_Interview"])

    def test_no_applications_gives_nulls_not_errors(self):
        row = self._funnel_for(["NotStarted", None])
        self.assertEqual(0, row["Applications"])
        self.assertIsNone(row["Responses_Rate"])
        self.assertIsNone(row["Avg_Days_to_Interview"])
        text = self.ns["canonical_funnel_text"](row)
        self.assertEqual("0\t0\t0\t0\t0\t0\t0\t\\N\t\\N\t\\N\t\\N\t\\N\t\\N\n", text)

    def test_canonical_texts_match_the_contract_examples(self):
        ns = self.ns
        from datetime import date
        text = ns["canonical_status_text"]([
            {"application_id": 2, "status": "Interviewed", "is_no_reply": 0, "problem_count": 0, "interview_date": date(2026, 10, 20)},
            {"application_id": 1, "status": None, "is_no_reply": 0, "problem_count": 1, "interview_date": None},
            {"application_id": 3, "status": "Applied", "is_no_reply": 1, "problem_count": 0, "interview_date": None}])
        self.assertEqual("1\t\\N\t0\t1\t\\N\n2\tInterviewed\t0\t0\t2026-10-20\n3\tApplied\t1\t0\t\\N\n", text)

        row = {"Applications": 2, "Responses": 1, "Interviews": 1, "Offers": 0, "Rejections": 0, "No_Replies": 0,
               "Rows_with_interview_date": 1, "Avg_Days_to_Interview": Decimal("21.00"), "Responses_Rate": Decimal("0.5000"),
               "Interviews_Rate": Decimal("0.5000"), "Offers_Rate": Decimal("0.0000"), "Rejections_Rate": Decimal("0.0000"),
               "No_Replies_Rate": Decimal("0.0000")}
        self.assertEqual("2\t1\t1\t0\t0\t0\t1\t21.00\t0.5000\t0.5000\t0.0000\t0.0000\t0.0000\n", ns["canonical_funnel_text"](row))

    # ---- additive schema changes: new columns are added, anything else is refused ----

    def test_missing_columns_are_planned_as_additions_in_the_new_order(self):
        plan = self.ns["plan_added_columns"]([("a", "string"), ("b", "int")],
                                             [("a", "string"), ("c", "string"), ("b", "int"), ("d", "date")])

        self.assertEqual([("c", "string"), ("d", "date")], plan)

    def test_no_missing_columns_means_no_change(self):
        pairs = [("a", "string"), ("b", "decimal(9,4)")]

        self.assertEqual([], self.ns["plan_added_columns"](pairs, pairs))

    def test_a_changed_type_is_refused_never_altered(self):
        with self.assertRaises(ValueError) as ctx:
            self.ns["plan_added_columns"]([("a", "string")], [("a", "int")])

        self.assertIn("a", str(ctx.exception))

    def test_a_column_the_new_data_no_longer_has_is_refused(self):
        with self.assertRaises(ValueError):
            self.ns["plan_added_columns"]([("a", "string"), ("old", "string")], [("a", "string")])

    def test_the_alter_statement_quotes_names_and_lists_every_addition(self):
        sql = self.ns["add_columns_sql"]("workspace.s.t", [("job_type", "string"), ("rate", "decimal(9,4)")])

        self.assertEqual("ALTER TABLE workspace.s.t ADD COLUMNS (`job_type` string, `rate` decimal(9,4))", sql)

    def test_dimension_funnel_groups_null_as_unspecified_and_drops_groups_without_applications(self):
        ns, spark = self.ns, self.spark
        from pyspark.sql import functions as F
        apps = ns["rows_to_df"](spark, ["application_id", "date_applied", "job_type", "resume_variant", "priority", "referral_type"], [
            ["1", "2026-10-01", "DataAnalyst", None, "TopPriority", "ColdApply"],
            ["2", "2026-10-01", "DataAnalyst", None, None, "NetworkingContact"],
            ["3", "2026-10-01", "Other", None, None, None]])    # app 3 is NotStarted: no application, so no group
        apps = apps.select(F.col("application_id").cast("long"), F.to_date("date_applied").alias("date_applied"),
                           "job_type", "resume_variant", "priority", "referral_type")
        gold = spark.sql("SELECT 1L AS application_id, 'Rejected' AS status, 0 AS is_no_reply, 0 AS problem_count, CAST(NULL AS DATE) AS interview_date "
                         "UNION ALL SELECT 2L, 'Applied', 1, 0, CAST(NULL AS DATE) "
                         "UNION ALL SELECT 3L, 'NotStarted', 0, 0, CAST(NULL AS DATE)")

        rows = [r.asDict() for r in ns["build_funnel_by_dimension"](gold, apps).collect()]
        text = ns["canonical_dimension_text"](rows)

        lines = text.splitlines()
        self.assertEqual("job_type\tDataAnalyst\t2\t1\t0\t0\t1\t1\t0.5000\t0.0000\t0.0000\t0.5000\t0.5000", lines[0])
        self.assertEqual(
            ["job_type", "resume_variant", "priority", "priority", "referral_type", "referral_type"],
            [line.split("\t")[0] for line in lines])
        self.assertIn("resume_variant\tUnspecified\t2\t", text)
        self.assertNotIn("\tOther\t", text)
        self.assertTrue(text.endswith("\n"))

    def test_reconciliation_flags_a_wrong_value(self):
        ns = self.ns
        folder = _newest_snapshot()
        if folder is None:
            self.skipTest("no snapshot in snapshots/")
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        facts = self._facts_matching(manifest)

        good = ns["build_reconciliation"](facts, manifest)
        self.assertEqual([], [r for r in good if r[2] != r[3]])

        facts["funnel"] = dict(facts["funnel"], Rejections="53")
        facts["problem_total"] = 1
        bad = {r[0] for r in ns["build_reconciliation"](facts, manifest) if r[2] != r[3]}
        self.assertEqual({"gold.measure.Rejections", "gold.problems.total"}, bad)

    def _facts_matching(self, manifest):
        files = {f["name"]: f for f in manifest["files"]}
        measures = manifest["expected_measures"]
        return {
            "sha256_applications": files["applications.csv"]["sha256"], "sha256_events": files["status_events.csv"]["sha256"],
            "bronze_applications": files["applications.csv"]["rows"], "bronze_events": files["status_events.csv"]["rows"],
            "silver_applications": manifest["applications"]["accepted"], "silver_events": files["status_events.csv"]["rows"],
            "rejected_applications": 0, "rejected_events": 0,
            "gold_status_rows": manifest["applications"]["accepted"], "gold_status_distinct": manifest["applications"]["accepted"],
            "gold_funnel_rows": 1,
            "funnel": {k: (str(v) if v is not None else "\\N") for k, v in measures.items()},
            "status_mismatches": 0, "no_reply_mismatches": 0,
            "problem_total": manifest["expected_problems"]["total"],
            "problem_by_code": dict(manifest["expected_problems"]["by_code"]),
            "hash_status": manifest["gold_hash"]["gold_application_status"], "hash_funnel": manifest["gold_hash"]["gold_funnel_summary"],
            "dimension_rows": manifest["gold_hash"]["gold_funnel_by_dimension_rows"],
            "dimension_sums": {name: measures["Applications"] for name in ("job_type", "resume_variant", "priority", "referral_type")},
            "hash_dimension": manifest["gold_hash"]["gold_funnel_by_dimension"],
            "selftest_ok": True,
        }


if __name__ == "__main__":
    unittest.main()
