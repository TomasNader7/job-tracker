# Databricks notebook source
# MAGIC %md
# MAGIC # 02 Snapshot pipeline (Phase 3)
# MAGIC
# MAGIC Reads ONE synthetic snapshot (`snapshot_id`) from the landing volume and builds bronze, silver and gold tables in
# MAGIC `workspace.job_tracker_p3`. It recomputes every application's status from the event history on its own, using the
# MAGIC snapshot contract (`docs/snapshot-contract.md`), then reconciles every layer against the manifest.
# MAGIC The job FAILS if any mandatory reconciliation check fails.
# MAGIC
# MAGIC * Synthetic data only. Parameters are explicit: `snapshot_id`, `as_of`, `landing_path`. There is no "latest" folder.
# MAGIC * The `expected_*` columns in `applications.csv` are used ONLY to reconcile, never to compute anything.
# MAGIC * Rerun rule: every table carries `snapshot_id` and a rerun REPLACES that snapshot's rows only (`replaceWhere`).
# MAGIC * Everything above the `==== RUN ====` marker is pure functions, tested locally with the golden fixture.

# COMMAND ----------

import hashlib
import json
import re
from decimal import Decimal

from pyspark.sql import Window as W
from pyspark.sql import functions as F

CONTRACT_VERSION = "1"
CATALOG, SCHEMA = "workspace", "job_tracker_p3"
LANDING_BASE = "/Volumes/workspace/job_tracker_demo/landing/snapshots"
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
TS_FORMAT = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'"
TS_REGEX = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
NO_REPLY_MICROS = 30 * 24 * 60 * 60 * 1_000_000   # strictly more than 30 days
NULL_TEXT = "\\N"

STATUSES = ["NotStarted", "Applied", "InterviewScheduled", "Interviewed", "Rejected", "Withdrawn", "Accepted"]
SOURCES = ["Manual", "Import", "Correction"]
TRANSITIONS = [
    ("NotStarted", "Applied"), ("NotStarted", "Withdrawn"),
    ("Applied", "InterviewScheduled"), ("Applied", "Rejected"), ("Applied", "Withdrawn"),
    ("InterviewScheduled", "Interviewed"), ("InterviewScheduled", "Rejected"), ("InterviewScheduled", "Withdrawn"),
    ("Interviewed", "InterviewScheduled"), ("Interviewed", "Rejected"), ("Interviewed", "Accepted"), ("Interviewed", "Withdrawn"),
    ("Rejected", "InterviewScheduled"),
]
APPLICATION_COLUMNS = ["application_id", "company_name", "job_title", "date_applied", "expected_projected_status", "expected_is_no_reply"]
EVENT_COLUMNS = ["event_sequence", "application_id", "previous_status", "new_status", "effective_at", "recorded_at",
                 "source", "supersedes_event_sequence", "note"]
PROBLEM_CODES = ["ILLEGAL_FIRST", "FIRST_PREVIOUS_NOT_NULL", "ILLEGAL_TRANSITION", "PREVIOUS_MISMATCH", "CORRECTION_TARGET_INVALID"]
MEASURE_COUNTS = ["Applications", "Responses", "Interviews", "Offers", "Rejections", "No_Replies", "Rows_with_interview_date"]
MEASURE_RATES = ["Responses_Rate", "Interviews_Rate", "Offers_Rate", "Rejections_Rate", "No_Replies_Rate"]
FUNNEL_ORDER = MEASURE_COUNTS + ["Avg_Days_to_Interview"] + MEASURE_RATES

# COMMAND ----------

# ---------- small helpers ----------

def _ok(condition):
    """A check that is null (unknown) counts as NOT ok."""
    return F.coalesce(condition, F.lit(False))


def _sql_string(value):
    if value is None:
        return "CAST(NULL AS STRING)"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def rows_to_df(spark, columns, rows):
    """All-string DataFrame from Python rows, built with SQL VALUES (no Python workers needed)."""
    if not rows:
        empty = ", ".join(f"CAST(NULL AS STRING) AS {c}" for c in columns)
        return spark.sql(f"SELECT {empty} WHERE 1 = 0")
    values = ",\n".join("(" + ", ".join(_sql_string(v) for v in row) + ")" for row in rows)
    return spark.sql(f"SELECT * FROM VALUES\n{values}\nAS t({', '.join(columns)})")


def nullify_empty(df, columns):
    """CSV empty fields mean null."""
    return df.select(*[F.when(F.trim(F.col(c)) == "", F.lit(None).cast("string")).otherwise(F.col(c)).alias(c) for c in columns],
                     *[F.col(c) for c in df.columns if c not in columns])


def _ts(column):
    return F.expr(f"try_to_timestamp({column}, \"{TS_FORMAT}\")")


# COMMAND ----------

# ---------- silver: typing and validation (bad rows go to a rejected table with a reason code) ----------

def split_applications(bronze):
    """Returns (valid typed applications, rejected rows)."""
    d = nullify_empty(bronze, APPLICATION_COLUMNS)
    key_ok = _ok(F.col("application_id").rlike(r"^\d{1,18}$"))
    date_ok = F.expr("try_to_timestamp(date_applied, 'yyyy-MM-dd')").isNotNull()
    status_ok = F.col("expected_projected_status").isNull() | _ok(F.col("expected_projected_status").isin(STATUSES))
    flag_ok = _ok(F.col("expected_is_no_reply").isin("0", "1"))
    reason = F.when(~key_ok, "BAD_KEY").when(~date_ok, "BAD_DATE").when(~status_ok, "BAD_STATUS").when(~flag_ok, "BAD_FLAG")
    d = d.withColumn("_reason", reason)
    duplicate = F.count(F.lit(1)).over(W.partitionBy("application_id")) > 1
    d = d.withColumn("_reason", F.when(F.col("_reason").isNull() & duplicate, "DUPLICATE_KEY").otherwise(F.col("_reason")))

    valid = d.where(F.col("_reason").isNull()).select(
        F.expr("try_cast(application_id AS BIGINT)").alias("application_id"),
        "company_name", "job_title",
        F.to_date(F.expr("try_to_timestamp(date_applied, 'yyyy-MM-dd')")).alias("date_applied"),
        F.col("expected_projected_status"),
        F.expr("try_cast(expected_is_no_reply AS INT)").alias("expected_is_no_reply"))
    rejected = d.where(F.col("_reason").isNotNull()).select(
        F.lit("applications.csv").alias("source_file"), F.col("_row_number").alias("source_row"),
        F.col("_reason").alias("reason_code"), F.concat_ws("|", *[F.coalesce(F.col(c), F.lit("")) for c in APPLICATION_COLUMNS]).alias("raw"))
    return valid, rejected


def split_events(bronze, valid_applications):
    """Returns (valid typed events, rejected rows)."""
    d = nullify_empty(bronze, EVENT_COLUMNS)
    seq_ok = _ok(F.col("event_sequence").rlike(r"^\d{1,18}$"))
    app_ok = _ok(F.col("application_id").rlike(r"^\d{1,18}$"))
    new_ok = _ok(F.col("new_status").isin(STATUSES))
    prev_ok = F.col("previous_status").isNull() | _ok(F.col("previous_status").isin(STATUSES))
    ts_ok = _ok(F.col("effective_at").rlike(TS_REGEX) & _ts("effective_at").isNotNull()
                & F.col("recorded_at").rlike(TS_REGEX) & _ts("recorded_at").isNotNull())
    source_ok = _ok(F.col("source").isin(SOURCES))
    sup_ok = F.col("supersedes_event_sequence").isNull() | _ok(F.col("supersedes_event_sequence").rlike(r"^\d{1,18}$"))
    reason = (F.when(~seq_ok, "BAD_KEY").when(~app_ok, "BAD_KEY").when(~new_ok, "BAD_STATUS").when(~prev_ok, "BAD_STATUS")
              .when(~ts_ok, "BAD_TIMESTAMP").when(~source_ok, "BAD_SOURCE").when(~sup_ok, "BAD_SUPERSEDES"))
    d = d.withColumn("_reason", reason)
    duplicate = F.count(F.lit(1)).over(W.partitionBy("event_sequence")) > 1
    d = d.withColumn("_reason", F.when(F.col("_reason").isNull() & duplicate, "DUPLICATE_KEY").otherwise(F.col("_reason")))

    known = valid_applications.select(F.col("application_id").alias("_app_id")).distinct()
    d = d.join(known, F.expr("try_cast(application_id AS BIGINT)") == F.col("_app_id"), "left")
    d = d.withColumn("_reason", F.when(F.col("_reason").isNull() & F.col("_app_id").isNull(), "ORPHAN_EVENT").otherwise(F.col("_reason")))

    valid = d.where(F.col("_reason").isNull()).select(
        F.expr("try_cast(event_sequence AS BIGINT)").alias("event_sequence"),
        F.expr("try_cast(application_id AS BIGINT)").alias("application_id"),
        "previous_status", "new_status",
        _ts("effective_at").alias("effective_at"), _ts("recorded_at").alias("recorded_at"),
        "source", F.expr("try_cast(supersedes_event_sequence AS BIGINT)").alias("supersedes_event_sequence"), "note")
    rejected = d.where(F.col("_reason").isNotNull()).select(
        F.lit("status_events.csv").alias("source_file"), F.col("_row_number").alias("source_row"),
        F.col("_reason").alias("reason_code"), F.concat_ws("|", *[F.coalesce(F.col(c), F.lit("")) for c in EVENT_COLUMNS]).alias("raw"))
    return valid, rejected


# COMMAND ----------

# ---------- the status rules, recomputed from the event history (snapshot contract v1) ----------

def compute_projection(spark, events, as_of):
    """events: typed event DataFrame. as_of: text like 2026-11-16T00:00:00.000000Z.

    Returns (results, problems):
      results  one row per application that has at least one accepted event:
               application_id, status, is_no_reply, interview_date
      problems one row per problem: application_id, event_sequence, code
    Ordering: effective_at, then event_sequence as a NUMBER. recorded_at is never used.
    """
    as_of_micros = F.unix_micros(F.to_timestamp(F.lit(as_of), TS_FORMAT))
    transitions = rows_to_df(spark, ["from_status", "to_status"], TRANSITIONS)

    # 1. Corrections. Valid only if the target exists AND belongs to the same application.
    targets = events.select(F.col("event_sequence").alias("_t_seq"), F.col("application_id").alias("_t_app"))
    linked = events.where(F.col("supersedes_event_sequence").isNotNull()).alias("c").join(
        targets.alias("t"), F.col("c.supersedes_event_sequence") == F.col("t._t_seq"), "left")
    is_valid = F.col("t._t_app").isNotNull() & (F.col("t._t_app") == F.col("c.application_id"))
    superseded = linked.where(is_valid).select(F.col("c.supersedes_event_sequence").alias("_seq"))
    invalid = linked.where(~is_valid).select(F.col("c.application_id").alias("application_id"),
                                             F.col("c.event_sequence").alias("event_sequence"))
    # Superseded events and ignored (invalid) corrections leave the chain. A correction that is itself superseded
    # still counts as a valid correction for its own target (so A <- C1 <- C2 drops A and C1, keeps C2).
    excluded = superseded.union(invalid.select(F.col("event_sequence").alias("_seq"))).distinct()
    chain = events.join(excluded, events["event_sequence"] == excluded["_seq"], "left_anti")

    # 2. Walk the chain in order. Everything up to the first illegal step is accepted.
    ordered = W.partitionBy("application_id").orderBy("effective_at", "event_sequence")
    chain = chain.withColumn("_rn", F.row_number().over(ordered)).withColumn("_prev_new", F.lag("new_status").over(ordered))
    chain = chain.join(transitions, (chain["_prev_new"] == transitions["from_status"]) & (chain["new_status"] == transitions["to_status"]), "left")
    illegal = F.when(F.col("_rn") == 1, ~F.col("new_status").isin("NotStarted", "Applied")).otherwise(F.col("to_status").isNull())
    chain = chain.withColumn("_illegal", illegal)
    chain = chain.withColumn("_stop_rn", F.min(F.when(F.col("_illegal"), F.col("_rn"))).over(W.partitionBy("application_id")))
    accepted = chain.where(F.col("_stop_rn").isNull() | (F.col("_rn") < F.col("_stop_rn")))

    # 3. Problems, with stable codes.
    def problem(df, code):
        return df.select("application_id", "event_sequence", code.alias("code"))

    stop = chain.where(F.col("_illegal") & (F.col("_rn") == F.col("_stop_rn")))
    problems = (
        problem(stop, F.when(F.col("_rn") == 1, "ILLEGAL_FIRST").otherwise("ILLEGAL_TRANSITION"))
        .unionByName(problem(accepted.where((F.col("_rn") == 1) & F.col("previous_status").isNotNull()), F.lit("FIRST_PREVIOUS_NOT_NULL")))
        .unionByName(problem(accepted.where((F.col("_rn") > 1) & ~F.col("previous_status").eqNullSafe(F.col("_prev_new"))), F.lit("PREVIOUS_MISMATCH")))
        .unionByName(invalid.select("application_id", "event_sequence", F.lit("CORRECTION_TARGET_INVALID").alias("code"))))

    # 4. Current status, no-reply and interview date from the accepted part of the chain.
    per_app = accepted.groupBy("application_id").agg(
        F.max_by("new_status", "_rn").alias("status"),
        F.max_by("effective_at", "_rn").alias("since"),
        F.min(F.when(F.col("new_status") == "Interviewed", F.col("effective_at"))).alias("first_interviewed_at"))
    results = per_app.select(
        "application_id", "status",
        F.when((F.col("status") == "Applied") & (as_of_micros - F.unix_micros("since") > NO_REPLY_MICROS), 1).otherwise(0).alias("is_no_reply"),
        F.to_date("first_interviewed_at").alias("interview_date"))
    return results, problems


def build_gold_status(silver_applications, results, problems):
    counts = problems.groupBy("application_id").agg(F.count(F.lit(1)).cast("int").alias("problem_count"))
    return (silver_applications.select("application_id")
            .join(results, "application_id", "left").join(counts, "application_id", "left")
            .select("application_id", "status",
                    F.coalesce("is_no_reply", F.lit(0)).cast("int").alias("is_no_reply"),
                    F.coalesce("problem_count", F.lit(0)).cast("int").alias("problem_count"),
                    "interview_date"))


def build_funnel(gold_status, silver_applications):
    """One row with the Phase 1 column names. Rates and the average are exact decimals, rounded HALF UP."""
    d = gold_status.join(silver_applications.select("application_id", "date_applied"), "application_id")
    is_app = F.col("status").isNotNull() & (F.col("status") != "NotStarted")

    def count_if(cond):
        return F.coalesce(F.sum(F.when(cond, 1).otherwise(0)), F.lit(0)).cast("long")

    base = d.agg(
        count_if(is_app).alias("Applications"),
        count_if(F.col("status").isin("Rejected", "InterviewScheduled", "Interviewed", "Accepted")).alias("Responses"),
        count_if(F.col("status").isin("InterviewScheduled", "Interviewed", "Accepted")).alias("Interviews"),
        count_if(F.col("status") == "Accepted").alias("Offers"),
        count_if(F.col("status") == "Rejected").alias("Rejections"),
        count_if(is_app & (F.col("is_no_reply") == 1)).alias("No_Replies"),
        count_if(is_app & F.col("interview_date").isNotNull()).alias("Rows_with_interview_date"),
        F.sum(F.when(is_app & F.col("interview_date").isNotNull(), F.datediff("interview_date", "date_applied"))).alias("_sum_days"))

    def rate(name):
        return F.when(F.col("Applications") > 0,
                      F.round(F.col(name).cast("decimal(20,6)") / F.col("Applications").cast("decimal(20,6)"), 4)
                      ).cast("decimal(9,4)")

    return base.select(
        *MEASURE_COUNTS,
        F.when(F.col("Rows_with_interview_date") > 0,
               F.round(F.col("_sum_days").cast("decimal(20,6)") / F.col("Rows_with_interview_date").cast("decimal(20,6)"), 2)
               ).cast("decimal(9,2)").alias("Avg_Days_to_Interview"),
        rate("Responses").alias("Responses_Rate"), rate("Interviews").alias("Interviews_Rate"), rate("Offers").alias("Offers_Rate"),
        rate("Rejections").alias("Rejections_Rate"), rate("No_Replies").alias("No_Replies_Rate"))


# COMMAND ----------

# ---------- canonical content hashes (never Delta files, metadata or unspecified row order) ----------

def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_status_text(rows):
    """rows: dicts with application_id, status, is_no_reply, problem_count, interview_date. Sorted by application_id."""
    lines = []
    for r in sorted(rows, key=lambda r: r["application_id"]):
        lines.append("\t".join([
            str(r["application_id"]),
            r["status"] if r["status"] is not None else NULL_TEXT,
            str(int(r["is_no_reply"])),
            str(int(r["problem_count"])),
            r["interview_date"].isoformat() if r["interview_date"] is not None else NULL_TEXT]) + "\n")
    return "".join(lines)


def _fixed(value, places):
    if value is None:
        return NULL_TEXT
    return format(Decimal(value).quantize(Decimal(1).scaleb(-places)), "f")


def funnel_strings(row):
    """The funnel row as the contract's text values: counts base-10, rates 4 decimals, average 2 decimals, null as \\N."""
    out = {k: str(int(row[k])) for k in MEASURE_COUNTS}
    out["Avg_Days_to_Interview"] = _fixed(row["Avg_Days_to_Interview"], 2)
    for k in MEASURE_RATES:
        out[k] = _fixed(row[k], 4)
    return out


def canonical_funnel_text(row):
    s = funnel_strings(row)
    return "\t".join(s[k] for k in FUNNEL_ORDER) + "\n"


# COMMAND ----------

# ---------- self-test: the golden fixture and the validators, run BEFORE any real data is processed ----------

# BEGIN GOLDEN FIXTURE (generated by scripts/embed_golden_fixture.py from tests/contract/projection_cases_v1.json)
GOLDEN_FIXTURE_JSON = r'''{"contract_version":"1","as_of":"2026-11-16T00:00:00.000000Z","cases":[
{"name":"simple_chain","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":3,"application_id":1,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-08T00:00:00.000000Z","recorded_at":"2026-11-08T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"Interviewed","is_no_reply":0,"problem_codes":[],"interview_date":"2026-11-08"}}},
{"name":"same_timestamp_numeric_sequence_order","events":[{"event_sequence":10,"application_id":1,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":9,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"illegal_transition_stops_at_last_valid","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"Accepted","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":3,"application_id":1,"previous_status":"Accepted","new_status":"Withdrawn","effective_at":"2026-11-06T00:00:00.000000Z","recorded_at":"2026-11-06T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"Applied","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null}}},
{"name":"illegal_first_event","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Interviewed","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":null,"is_no_reply":0,"problem_codes":["ILLEGAL_FIRST"],"interview_date":null}}},
{"name":"first_event_with_previous_status","events":[{"event_sequence":1,"application_id":1,"previous_status":"InterviewScheduled","new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"Applied","is_no_reply":0,"problem_codes":["FIRST_PREVIOUS_NOT_NULL"],"interview_date":null}}},
{"name":"previous_status_mismatch_chain_wins","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"InterviewScheduled","new_status":"Rejected","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"Rejected","is_no_reply":0,"problem_codes":["PREVIOUS_MISMATCH"],"interview_date":null}}},
{"name":"correction_replaces_wrong_event","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":3,"application_id":1,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-06T00:00:00.000000Z","source":"Correction","supersedes_event_sequence":2,"note":"synthetic correction"}],"expected":{"1":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"correction_of_a_correction","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":3,"application_id":1,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-06T00:00:00.000000Z","source":"Correction","supersedes_event_sequence":2,"note":"synthetic correction"},{"event_sequence":4,"application_id":1,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-07T00:00:00.000000Z","source":"Correction","supersedes_event_sequence":3,"note":"synthetic correction"}],"expected":{"1":{"status":"Withdrawn","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"correction_target_missing_is_ignored","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Correction","supersedes_event_sequence":99,"note":"synthetic correction"}],"expected":{"1":{"status":"Applied","is_no_reply":0,"problem_codes":["CORRECTION_TARGET_INVALID"],"interview_date":null}}},
{"name":"correction_target_in_other_application_is_ignored","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Correction","supersedes_event_sequence":3,"note":"synthetic correction"},{"event_sequence":3,"application_id":2,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"Applied","is_no_reply":0,"problem_codes":["CORRECTION_TARGET_INVALID"],"interview_date":null},"2":{"status":"Applied","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"two_corrections_supersede_each_other_both_dropped","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":3,"application_id":1,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-06T00:00:00.000000Z","recorded_at":"2026-11-06T00:00:00.000000Z","source":"Correction","supersedes_event_sequence":4,"note":"synthetic correction"},{"event_sequence":4,"application_id":1,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-07T00:00:00.000000Z","recorded_at":"2026-11-07T00:00:00.000000Z","source":"Correction","supersedes_event_sequence":3,"note":"synthetic correction"}],"expected":{"1":{"status":"Rejected","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"reopen_after_rejection","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":3,"application_id":1,"previous_status":"Rejected","new_status":"InterviewScheduled","effective_at":"2026-11-10T00:00:00.000000Z","recorded_at":"2026-11-10T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"no_reply_exactly_30_days_is_not_yet","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-10-17T00:00:00.000000Z","recorded_at":"2026-10-17T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"Applied","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"no_reply_31_days","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-10-16T00:00:00.000000Z","recorded_at":"2026-10-16T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"Applied","is_no_reply":1,"problem_codes":[],"interview_date":null}}},
{"name":"late_reply_clears_no_reply","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-09-01T00:00:00.000000Z","recorded_at":"2026-09-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-10-20T00:00:00.000000Z","recorded_at":"2026-10-20T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"Rejected","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"not_started_is_never_no_reply","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"NotStarted","effective_at":"2026-08-01T00:00:00.000000Z","recorded_at":"2026-08-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"NotStarted","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"interview_date_is_first_interviewed_event","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":3,"application_id":1,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-08T00:00:00.000000Z","recorded_at":"2026-11-08T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":4,"application_id":1,"previous_status":"Interviewed","new_status":"InterviewScheduled","effective_at":"2026-11-10T00:00:00.000000Z","recorded_at":"2026-11-10T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":5,"application_id":1,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-12T00:00:00.000000Z","recorded_at":"2026-11-12T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"Interviewed","is_no_reply":0,"problem_codes":[],"interview_date":"2026-11-08"}}},
{"name":"superseded_interview_is_not_counted","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":3,"application_id":1,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-08T00:00:00.000000Z","recorded_at":"2026-11-08T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":4,"application_id":1,"previous_status":"InterviewScheduled","new_status":"Rejected","effective_at":"2026-11-08T00:00:00.000000Z","recorded_at":"2026-11-09T00:00:00.000000Z","source":"Correction","supersedes_event_sequence":3,"note":"synthetic correction"}],"expected":{"1":{"status":"Rejected","is_no_reply":0,"problem_codes":[],"interview_date":null}}},
{"name":"no_reply_uses_the_active_applied_event_time","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-10T00:00:00.000000Z","recorded_at":"2026-11-10T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":null,"new_status":"Applied","effective_at":"2026-10-01T00:00:00.000000Z","recorded_at":"2026-11-11T00:00:00.000000Z","source":"Correction","supersedes_event_sequence":1,"note":"synthetic correction"}],"expected":{"1":{"status":"Applied","is_no_reply":1,"problem_codes":[],"interview_date":null}}},
{"name":"transition_matrix_every_from_to_pair","events":[{"event_sequence":1,"application_id":1,"previous_status":null,"new_status":"NotStarted","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":2,"application_id":1,"previous_status":"NotStarted","new_status":"NotStarted","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":3,"application_id":2,"previous_status":null,"new_status":"NotStarted","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":4,"application_id":2,"previous_status":"NotStarted","new_status":"Applied","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":5,"application_id":3,"previous_status":null,"new_status":"NotStarted","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":6,"application_id":3,"previous_status":"NotStarted","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":7,"application_id":4,"previous_status":null,"new_status":"NotStarted","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":8,"application_id":4,"previous_status":"NotStarted","new_status":"Interviewed","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":9,"application_id":5,"previous_status":null,"new_status":"NotStarted","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":10,"application_id":5,"previous_status":"NotStarted","new_status":"Rejected","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":11,"application_id":6,"previous_status":null,"new_status":"NotStarted","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":12,"application_id":6,"previous_status":"NotStarted","new_status":"Withdrawn","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":13,"application_id":7,"previous_status":null,"new_status":"NotStarted","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":14,"application_id":7,"previous_status":"NotStarted","new_status":"Accepted","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":15,"application_id":8,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":16,"application_id":8,"previous_status":"Applied","new_status":"NotStarted","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":17,"application_id":9,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":18,"application_id":9,"previous_status":"Applied","new_status":"Applied","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":19,"application_id":10,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":20,"application_id":10,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":21,"application_id":11,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":22,"application_id":11,"previous_status":"Applied","new_status":"Interviewed","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":23,"application_id":12,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":24,"application_id":12,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":25,"application_id":13,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":26,"application_id":13,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":27,"application_id":14,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":28,"application_id":14,"previous_status":"Applied","new_status":"Accepted","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":29,"application_id":15,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":30,"application_id":15,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":31,"application_id":15,"previous_status":"InterviewScheduled","new_status":"NotStarted","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":32,"application_id":16,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":33,"application_id":16,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":34,"application_id":16,"previous_status":"InterviewScheduled","new_status":"Applied","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":35,"application_id":17,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":36,"application_id":17,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":37,"application_id":17,"previous_status":"InterviewScheduled","new_status":"InterviewScheduled","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":38,"application_id":18,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":39,"application_id":18,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":40,"application_id":18,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":41,"application_id":19,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":42,"application_id":19,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":43,"application_id":19,"previous_status":"InterviewScheduled","new_status":"Rejected","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":44,"application_id":20,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":45,"application_id":20,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":46,"application_id":20,"previous_status":"InterviewScheduled","new_status":"Withdrawn","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":47,"application_id":21,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":48,"application_id":21,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":49,"application_id":21,"previous_status":"InterviewScheduled","new_status":"Accepted","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":50,"application_id":22,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":51,"application_id":22,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":52,"application_id":22,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":53,"application_id":22,"previous_status":"Interviewed","new_status":"NotStarted","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":54,"application_id":23,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":55,"application_id":23,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":56,"application_id":23,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":57,"application_id":23,"previous_status":"Interviewed","new_status":"Applied","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":58,"application_id":24,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":59,"application_id":24,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":60,"application_id":24,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":61,"application_id":24,"previous_status":"Interviewed","new_status":"InterviewScheduled","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":62,"application_id":25,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":63,"application_id":25,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":64,"application_id":25,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":65,"application_id":25,"previous_status":"Interviewed","new_status":"Interviewed","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":66,"application_id":26,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":67,"application_id":26,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":68,"application_id":26,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":69,"application_id":26,"previous_status":"Interviewed","new_status":"Rejected","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":70,"application_id":27,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":71,"application_id":27,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":72,"application_id":27,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":73,"application_id":27,"previous_status":"Interviewed","new_status":"Withdrawn","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":74,"application_id":28,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":75,"application_id":28,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":76,"application_id":28,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":77,"application_id":28,"previous_status":"Interviewed","new_status":"Accepted","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":78,"application_id":29,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":79,"application_id":29,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":80,"application_id":29,"previous_status":"Rejected","new_status":"NotStarted","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":81,"application_id":30,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":82,"application_id":30,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":83,"application_id":30,"previous_status":"Rejected","new_status":"Applied","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":84,"application_id":31,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":85,"application_id":31,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":86,"application_id":31,"previous_status":"Rejected","new_status":"InterviewScheduled","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":87,"application_id":32,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":88,"application_id":32,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":89,"application_id":32,"previous_status":"Rejected","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":90,"application_id":33,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":91,"application_id":33,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":92,"application_id":33,"previous_status":"Rejected","new_status":"Rejected","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":93,"application_id":34,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":94,"application_id":34,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":95,"application_id":34,"previous_status":"Rejected","new_status":"Withdrawn","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":96,"application_id":35,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":97,"application_id":35,"previous_status":"Applied","new_status":"Rejected","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":98,"application_id":35,"previous_status":"Rejected","new_status":"Accepted","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":99,"application_id":36,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":100,"application_id":36,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":101,"application_id":36,"previous_status":"Withdrawn","new_status":"NotStarted","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":102,"application_id":37,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":103,"application_id":37,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":104,"application_id":37,"previous_status":"Withdrawn","new_status":"Applied","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":105,"application_id":38,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":106,"application_id":38,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":107,"application_id":38,"previous_status":"Withdrawn","new_status":"InterviewScheduled","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":108,"application_id":39,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":109,"application_id":39,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":110,"application_id":39,"previous_status":"Withdrawn","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":111,"application_id":40,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":112,"application_id":40,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":113,"application_id":40,"previous_status":"Withdrawn","new_status":"Rejected","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":114,"application_id":41,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":115,"application_id":41,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":116,"application_id":41,"previous_status":"Withdrawn","new_status":"Withdrawn","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":117,"application_id":42,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":118,"application_id":42,"previous_status":"Applied","new_status":"Withdrawn","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":119,"application_id":42,"previous_status":"Withdrawn","new_status":"Accepted","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":120,"application_id":43,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":121,"application_id":43,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":122,"application_id":43,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":123,"application_id":43,"previous_status":"Interviewed","new_status":"Accepted","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":124,"application_id":43,"previous_status":"Accepted","new_status":"NotStarted","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":125,"application_id":44,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":126,"application_id":44,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":127,"application_id":44,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":128,"application_id":44,"previous_status":"Interviewed","new_status":"Accepted","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":129,"application_id":44,"previous_status":"Accepted","new_status":"Applied","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":130,"application_id":45,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":131,"application_id":45,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":132,"application_id":45,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":133,"application_id":45,"previous_status":"Interviewed","new_status":"Accepted","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":134,"application_id":45,"previous_status":"Accepted","new_status":"InterviewScheduled","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":135,"application_id":46,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":136,"application_id":46,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":137,"application_id":46,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":138,"application_id":46,"previous_status":"Interviewed","new_status":"Accepted","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":139,"application_id":46,"previous_status":"Accepted","new_status":"Interviewed","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":140,"application_id":47,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":141,"application_id":47,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":142,"application_id":47,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":143,"application_id":47,"previous_status":"Interviewed","new_status":"Accepted","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":144,"application_id":47,"previous_status":"Accepted","new_status":"Rejected","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":145,"application_id":48,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":146,"application_id":48,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":147,"application_id":48,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":148,"application_id":48,"previous_status":"Interviewed","new_status":"Accepted","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":149,"application_id":48,"previous_status":"Accepted","new_status":"Withdrawn","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":150,"application_id":49,"previous_status":null,"new_status":"Applied","effective_at":"2026-11-01T00:00:00.000000Z","recorded_at":"2026-11-01T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":151,"application_id":49,"previous_status":"Applied","new_status":"InterviewScheduled","effective_at":"2026-11-02T00:00:00.000000Z","recorded_at":"2026-11-02T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":152,"application_id":49,"previous_status":"InterviewScheduled","new_status":"Interviewed","effective_at":"2026-11-03T00:00:00.000000Z","recorded_at":"2026-11-03T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":153,"application_id":49,"previous_status":"Interviewed","new_status":"Accepted","effective_at":"2026-11-04T00:00:00.000000Z","recorded_at":"2026-11-04T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null},{"event_sequence":154,"application_id":49,"previous_status":"Accepted","new_status":"Accepted","effective_at":"2026-11-05T00:00:00.000000Z","recorded_at":"2026-11-05T00:00:00.000000Z","source":"Manual","supersedes_event_sequence":null,"note":null}],"expected":{"1":{"status":"NotStarted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"2":{"status":"Applied","is_no_reply":0,"problem_codes":[],"interview_date":null},"3":{"status":"NotStarted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"4":{"status":"NotStarted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"5":{"status":"NotStarted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"6":{"status":"Withdrawn","is_no_reply":0,"problem_codes":[],"interview_date":null},"7":{"status":"NotStarted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"8":{"status":"Applied","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"9":{"status":"Applied","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"10":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":[],"interview_date":null},"11":{"status":"Applied","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"12":{"status":"Rejected","is_no_reply":0,"problem_codes":[],"interview_date":null},"13":{"status":"Withdrawn","is_no_reply":0,"problem_codes":[],"interview_date":null},"14":{"status":"Applied","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"15":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"16":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"17":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"18":{"status":"Interviewed","is_no_reply":0,"problem_codes":[],"interview_date":"2026-11-03"},"19":{"status":"Rejected","is_no_reply":0,"problem_codes":[],"interview_date":null},"20":{"status":"Withdrawn","is_no_reply":0,"problem_codes":[],"interview_date":null},"21":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"22":{"status":"Interviewed","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"},"23":{"status":"Interviewed","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"},"24":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":[],"interview_date":"2026-11-03"},"25":{"status":"Interviewed","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"},"26":{"status":"Rejected","is_no_reply":0,"problem_codes":[],"interview_date":"2026-11-03"},"27":{"status":"Withdrawn","is_no_reply":0,"problem_codes":[],"interview_date":"2026-11-03"},"28":{"status":"Accepted","is_no_reply":0,"problem_codes":[],"interview_date":"2026-11-03"},"29":{"status":"Rejected","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"30":{"status":"Rejected","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"31":{"status":"InterviewScheduled","is_no_reply":0,"problem_codes":[],"interview_date":null},"32":{"status":"Rejected","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"33":{"status":"Rejected","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"34":{"status":"Rejected","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"35":{"status":"Rejected","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"36":{"status":"Withdrawn","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"37":{"status":"Withdrawn","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"38":{"status":"Withdrawn","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"39":{"status":"Withdrawn","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"40":{"status":"Withdrawn","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"41":{"status":"Withdrawn","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"42":{"status":"Withdrawn","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":null},"43":{"status":"Accepted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"},"44":{"status":"Accepted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"},"45":{"status":"Accepted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"},"46":{"status":"Accepted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"},"47":{"status":"Accepted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"},"48":{"status":"Accepted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"},"49":{"status":"Accepted","is_no_reply":0,"problem_codes":["ILLEGAL_TRANSITION"],"interview_date":"2026-11-03"}}}
]}'''
# END GOLDEN FIXTURE


def _golden_events_df(spark, golden):
    """All cases in one DataFrame. Ids are offset per case so cases cannot touch each other."""
    rows = []
    for index, case in enumerate(golden["cases"], start=1):
        offset = index * 1_000_000
        for e in case["events"]:
            sup = e["supersedes_event_sequence"]
            rows.append([str(e["event_sequence"] + offset), str(e["application_id"] + offset), e["previous_status"], e["new_status"],
                         e["effective_at"], e["recorded_at"], e["source"],
                         None if sup is None else str(sup + offset), e["note"]])
    bronze = rows_to_df(spark, EVENT_COLUMNS, rows).withColumn("_row_number", F.monotonically_increasing_id())
    apps = rows_to_df(spark, ["application_id"], sorted({(r[1],) for r in rows}))
    valid_apps = apps.select(F.expr("try_cast(application_id AS BIGINT)").alias("application_id"))
    return split_events(bronze, valid_apps)


def run_selftest(spark):
    """Returns a list of (check name, passed, detail). Never raises for a failed check; the caller decides."""
    golden = json.loads(GOLDEN_FIXTURE_JSON)
    results_out = []

    events, rejected = _golden_events_df(spark, golden)
    results_out.append(("selftest.fixture_events_all_valid", rejected.count() == 0, f"{rejected.count()} rejected"))

    results, problems = compute_projection(spark, events, golden["as_of"])
    got = {r["application_id"]: r for r in results.collect()}
    found = {}
    for r in problems.collect():
        found.setdefault(r["application_id"], []).append(r["code"])

    passed_cases = 0
    for index, case in enumerate(golden["cases"], start=1):
        offset = index * 1_000_000
        problems_in_case = []
        for app_text, want in case["expected"].items():
            app_id = int(app_text) + offset
            row = got.get(app_id)
            status = row["status"] if row is not None else None
            no_reply = int(row["is_no_reply"]) if row is not None else 0
            idate = row["interview_date"].isoformat() if row is not None and row["interview_date"] is not None else None
            codes = sorted(found.get(app_id, []))
            if (status, no_reply, codes, idate) != (want["status"], want["is_no_reply"], sorted(want["problem_codes"]), want["interview_date"]):
                problems_in_case.append(f"app {app_text}: got {(status, no_reply, codes, idate)}, want "
                                        f"{(want['status'], want['is_no_reply'], sorted(want['problem_codes']), want['interview_date'])}")
        ok = not problems_in_case
        passed_cases += ok
        results_out.append((f"selftest.case.{case['name']}", ok, "; ".join(problems_in_case[:3])))
    results_out.append(("selftest.cases_passed", passed_cases == len(golden["cases"]), f"{passed_cases} of {len(golden['cases'])}"))

    # validators: each bad row must be rejected with the right reason code
    bad_apps = rows_to_df(spark, APPLICATION_COLUMNS + ["_row_number"], [
        ["1", "Company 001", "Data Analyst", "2026-10-01", "Applied", "0", "1"],      # fine
        ["x", "Company 002", "Data Analyst", "2026-10-01", "Applied", "0", "2"],      # BAD_KEY
        ["3", "Company 003", "Data Analyst", "2026-02-30", "Applied", "0", "3"],      # BAD_DATE
        ["4", "Company 004", "Data Analyst", "2026-10-01", "Hired", "0", "4"],        # BAD_STATUS
        ["5", "Company 005", "Data Analyst", "2026-10-01", "Applied", "2", "5"],      # BAD_FLAG
        ["6", "Company 006", "Data Analyst", "2026-10-01", "", "0", "6"],             # fine (empty status = null)
        ["7", "Company 007", "Data Analyst", "2026-10-01", "Applied", "0", "7"],      # DUPLICATE_KEY
        ["7", "Company 007", "Data Analyst", "2026-10-02", "Applied", "0", "8"]])     # DUPLICATE_KEY
    valid_apps, rejected_apps = split_applications(bad_apps)
    reasons = {r["source_row"]: r["reason_code"] for r in rejected_apps.collect()}
    want_reasons = {"2": "BAD_KEY", "3": "BAD_DATE", "4": "BAD_STATUS", "5": "BAD_FLAG", "7": "DUPLICATE_KEY", "8": "DUPLICATE_KEY"}
    results_out.append(("selftest.application_validation", {str(k): v for k, v in reasons.items()} == want_reasons
                        and valid_apps.count() == 2, f"reasons {reasons}"))

    ok_ts = "2026-10-01T00:00:00.000000Z"
    bad_events = rows_to_df(spark, EVENT_COLUMNS + ["_row_number"], [
        ["1", "1", None, "Applied", ok_ts, ok_ts, "Manual", None, None, "1"],         # fine
        ["x", "1", None, "Applied", ok_ts, ok_ts, "Manual", None, None, "2"],         # BAD_KEY
        ["3", "1", None, "Hired", ok_ts, ok_ts, "Manual", None, None, "3"],           # BAD_STATUS
        ["4", "1", None, "Applied", "2026-10-01", ok_ts, "Manual", None, None, "4"],   # BAD_TIMESTAMP
        ["5", "1", None, "Applied", ok_ts, ok_ts, "Scraped", None, None, "5"],         # BAD_SOURCE
        ["6", "1", None, "Applied", ok_ts, ok_ts, "Correction", "z", None, "6"],       # BAD_SUPERSEDES
        ["7", "99", None, "Applied", ok_ts, ok_ts, "Manual", None, None, "7"],         # ORPHAN_EVENT
        ["8", "1", None, "Applied", ok_ts, ok_ts, "Manual", None, None, "8"],          # DUPLICATE_KEY
        ["8", "1", None, "Applied", ok_ts, ok_ts, "Manual", None, None, "9"]])         # DUPLICATE_KEY
    known = rows_to_df(spark, ["application_id"], [["1"]]).select(F.expr("try_cast(application_id AS BIGINT)").alias("application_id"))
    valid_events, rejected_events = split_events(bad_events, known)
    reasons = {r["source_row"]: r["reason_code"] for r in rejected_events.collect()}
    want_reasons = {"2": "BAD_KEY", "3": "BAD_STATUS", "4": "BAD_TIMESTAMP", "5": "BAD_SOURCE", "6": "BAD_SUPERSEDES",
                    "7": "ORPHAN_EVENT", "8": "DUPLICATE_KEY", "9": "DUPLICATE_KEY"}
    results_out.append(("selftest.event_validation", {str(k): v for k, v in reasons.items()} == want_reasons
                        and valid_events.count() == 1, f"reasons {reasons}"))
    return results_out


# COMMAND ----------

# ---------- reconciliation ----------

def build_reconciliation(facts, manifest):
    """facts: values measured from the stored tables. Returns a list of (check, layer, expected, actual) rows."""
    files = {f["name"]: f for f in manifest["files"]}
    exp = manifest["expected_measures"]
    problems = manifest["expected_problems"]
    rows = []

    def check(name, layer, expected, actual):
        rows.append((name, layer, str(expected), str(actual)))

    check("source.applications.sha256", "source", files["applications.csv"]["sha256"], facts["sha256_applications"])
    check("source.status_events.sha256", "source", files["status_events.csv"]["sha256"], facts["sha256_events"])
    check("bronze.applications.rows", "bronze", files["applications.csv"]["rows"], facts["bronze_applications"])
    check("bronze.status_events.rows", "bronze", files["status_events.csv"]["rows"], facts["bronze_events"])
    check("silver.applications.accepted_plus_rejected_equals_bronze", "silver", facts["bronze_applications"],
          facts["silver_applications"] + facts["rejected_applications"])
    check("silver.status_events.accepted_plus_rejected_equals_bronze", "silver", facts["bronze_events"],
          facts["silver_events"] + facts["rejected_events"])
    check("silver.applications.accepted_equals_manifest", "silver", manifest["applications"]["accepted"], facts["silver_applications"])
    check("silver.status_events.accepted_equals_manifest", "silver", files["status_events.csv"]["rows"], facts["silver_events"])
    check("silver.rejected_rows_total", "silver", 0, facts["rejected_applications"] + facts["rejected_events"])
    check("gold.application_status.rows_equal_silver_applications", "gold", facts["silver_applications"], facts["gold_status_rows"])
    check("gold.application_status.distinct_ids_equal_rows", "gold", facts["gold_status_rows"], facts["gold_status_distinct"])
    check("gold.funnel_summary.rows", "gold", 1, facts["gold_funnel_rows"])
    for name in FUNNEL_ORDER:
        want = exp[name] if exp[name] is not None else NULL_TEXT
        check(f"gold.measure.{name}", "gold", want, facts["funnel"][name])
    check("gold.status_mismatches_vs_expected_projected_status", "gold", 0, facts["status_mismatches"])
    check("gold.no_reply_mismatches_vs_expected_is_no_reply", "gold", 0, facts["no_reply_mismatches"])
    check("gold.problems.total", "gold", problems["total"], facts["problem_total"])
    for code in PROBLEM_CODES:
        check(f"gold.problems.{code}", "gold", problems["by_code"][code], facts["problem_by_code"].get(code, 0))
    check("gold.hash.application_status", "gold", manifest["gold_hash"]["gold_application_status"], facts["hash_status"])
    check("gold.hash.funnel_summary", "gold", manifest["gold_hash"]["gold_funnel_summary"], facts["hash_funnel"])
    check("selftest.all_checks_passed", "selftest", "True", facts["selftest_ok"])
    return rows


# COMMAND ----------

# ---------- writing (Delta, one snapshot at a time) ----------

def table_name(name):
    return f"{CATALOG}.{SCHEMA}.{name}"


def write_snapshot_table(spark, df, name, snapshot_id):
    """Replace this snapshot's rows only. A rerun never appends duplicates and never touches other snapshots."""
    full = table_name(name)
    out = df.withColumn("snapshot_id", F.lit(snapshot_id))
    out = out.select("snapshot_id", *[c for c in out.columns if c != "snapshot_id"])
    if spark.catalog.tableExists(full):
        out.write.mode("overwrite").option("replaceWhere", f"snapshot_id = '{snapshot_id}'").saveAsTable(full)
    else:
        out.write.mode("overwrite").saveAsTable(full)


# COMMAND ----------

# ==== RUN ==== (everything above is pure functions; everything below needs the Databricks job context)

dbutils.widgets.text("snapshot_id", "")
dbutils.widgets.text("as_of", "")
dbutils.widgets.text("landing_path", "")
snapshot_id = dbutils.widgets.get("snapshot_id").strip()
as_of = dbutils.widgets.get("as_of").strip()
landing_path = dbutils.widgets.get("landing_path").strip()

assert SAFE_ID.fullmatch(snapshot_id), f"Unsafe snapshot_id {snapshot_id!r}"
assert landing_path == f"{LANDING_BASE}/{snapshot_id}", f"landing_path must be exactly {LANDING_BASE}/{snapshot_id}, got {landing_path!r}"
assert re.fullmatch(TS_REGEX, as_of), f"as_of must look like 2026-11-16T00:00:00.000000Z, got {as_of!r}"

spark.conf.set("spark.sql.session.timeZone", "UTC")
assert spark.conf.get("spark.sql.session.timeZone") == "UTC"
print(f"Snapshot {snapshot_id}, as of {as_of}, from {landing_path}")

# COMMAND ----------

# The manifest and the files' hashes are checked again here, inside Databricks, before anything is read.
with open(f"{landing_path}/manifest.json", encoding="utf-8") as handle:
    manifest = json.load(handle)

assert manifest["contract_version"] == CONTRACT_VERSION, manifest["contract_version"]
assert manifest["lane"] == "synthetic", "lane must be synthetic"
assert manifest["snapshot_id"] == snapshot_id, "manifest snapshot_id does not match the parameter"
assert manifest["as_of"] == as_of, "manifest as_of does not match the parameter"

source_hashes = {}
for name in ("applications.csv", "status_events.csv"):
    with open(f"{landing_path}/{name}", "rb") as handle:
        source_hashes[name] = hashlib.sha256(handle.read()).hexdigest()
    wanted = next(f["sha256"] for f in manifest["files"] if f["name"] == name)
    assert source_hashes[name] == wanted, f"{name} does not match the manifest SHA-256"

# COMMAND ----------

selftest = run_selftest(spark)
selftest_ok = all(passed for _, passed, _ in selftest)
for name, passed, detail in selftest:
    if not passed:
        print(f"SELFTEST FAILED {name}: {detail}")
assert selftest_ok, "The golden-fixture self-test failed. Nothing was written."
print(f"Self-test passed ({len(selftest)} checks)")

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")


def read_bronze(file_name, expected_columns):
    df = (spark.read.option("header", True).option("inferSchema", False).option("mode", "FAILFAST")
          .option("quote", '"').option("escape", '"').csv(f"{landing_path}/{file_name}"))
    if df.columns != expected_columns:
        raise ValueError(f"{file_name} has the wrong columns: {df.columns}")
    return (df.withColumn("_source_file", F.lit(file_name))
              .withColumn("_row_number", F.row_number().over(W.orderBy(F.monotonically_increasing_id())) + 1))


bronze_applications = read_bronze("applications.csv", APPLICATION_COLUMNS)
bronze_events = read_bronze("status_events.csv", EVENT_COLUMNS)

write_snapshot_table(spark, bronze_applications, "bronze_applications", snapshot_id)
write_snapshot_table(spark, bronze_events, "bronze_status_events", snapshot_id)
manifest_row = rows_to_df(spark, ["contract_version", "lane", "as_of", "exported_at", "manifest_json"],
                          [[manifest["contract_version"], manifest["lane"], manifest["as_of"], manifest["exported_at"],
                            json.dumps(manifest, sort_keys=True)]])
write_snapshot_table(spark, manifest_row, "bronze_manifest", snapshot_id)

# Work from what was stored, so the layers really are what the tables hold.
bronze_applications = spark.table(table_name("bronze_applications")).where(F.col("snapshot_id") == snapshot_id)
bronze_events = spark.table(table_name("bronze_status_events")).where(F.col("snapshot_id") == snapshot_id)

# COMMAND ----------

silver_applications, rejected_applications = split_applications(bronze_applications)
silver_events, rejected_events = split_events(bronze_events, silver_applications)
write_snapshot_table(spark, silver_applications, "silver_applications", snapshot_id)
write_snapshot_table(spark, silver_events, "silver_status_events", snapshot_id)
write_snapshot_table(spark, rejected_applications.unionByName(rejected_events), "silver_rejected_rows", snapshot_id)

silver_applications = spark.table(table_name("silver_applications")).where(F.col("snapshot_id") == snapshot_id)
silver_events = spark.table(table_name("silver_status_events")).where(F.col("snapshot_id") == snapshot_id)

# COMMAND ----------

# Status computed from events only. The expected_* columns are not touched here.
results, problems = compute_projection(spark, silver_events.drop("snapshot_id"), as_of)
gold_status = build_gold_status(silver_applications.drop("snapshot_id"), results, problems)
write_snapshot_table(spark, gold_status, "gold_application_status", snapshot_id)

stored_status = spark.table(table_name("gold_application_status")).where(F.col("snapshot_id") == snapshot_id)
funnel = build_funnel(stored_status.drop("snapshot_id"), silver_applications.drop("snapshot_id"))
write_snapshot_table(spark, funnel.withColumn("_gold_at", F.current_timestamp()), "gold_funnel_summary", snapshot_id)

# COMMAND ----------

# Measure everything from the STORED tables, then reconcile.
stored_status = spark.table(table_name("gold_application_status")).where(F.col("snapshot_id") == snapshot_id)
stored_funnel = spark.table(table_name("gold_funnel_summary")).where(F.col("snapshot_id") == snapshot_id)
status_rows = [r.asDict() for r in stored_status.collect()]
funnel_rows = stored_funnel.collect()

compare = silver_applications.select("application_id", "expected_projected_status", "expected_is_no_reply").join(
    stored_status.select("application_id", "status", "is_no_reply"), "application_id", "left")
problem_by_code = {r["code"]: r["n"] for r in problems.groupBy("code").agg(F.count(F.lit(1)).alias("n")).collect()}

facts = {
    "sha256_applications": source_hashes["applications.csv"],
    "sha256_events": source_hashes["status_events.csv"],
    "bronze_applications": bronze_applications.count(),
    "bronze_events": bronze_events.count(),
    "silver_applications": silver_applications.count(),
    "silver_events": silver_events.count(),
    "rejected_applications": rejected_applications.count(),
    "rejected_events": rejected_events.count(),
    "gold_status_rows": stored_status.count(),
    "gold_status_distinct": stored_status.select("application_id").distinct().count(),
    "gold_funnel_rows": len(funnel_rows),
    "funnel": funnel_strings(funnel_rows[0].asDict()) if funnel_rows else {k: "missing" for k in FUNNEL_ORDER},
    "status_mismatches": compare.where(~F.col("expected_projected_status").eqNullSafe(F.col("status"))).count(),
    "no_reply_mismatches": compare.where(F.col("expected_is_no_reply") != F.col("is_no_reply")).count(),
    "problem_total": sum(problem_by_code.values()),
    "problem_by_code": problem_by_code,
    "hash_status": sha256_text(canonical_status_text(status_rows)),
    "hash_funnel": sha256_text(canonical_funnel_text(funnel_rows[0].asDict())) if funnel_rows else "missing",
    "selftest_ok": selftest_ok,
}

reconciliation = build_reconciliation(facts, manifest)
recon_df = rows_to_df(spark, ["check_name", "layer", "expected", "actual"], [list(r) for r in reconciliation]).withColumn(
    "passed", F.col("expected") == F.col("actual"))
write_snapshot_table(spark, recon_df, "gold_reconciliation", snapshot_id)

failed = [r for r in reconciliation if r[2] != r[3]]
for name, layer, expected, actual in failed:
    print(f"RECONCILIATION FAILED {layer}/{name}: expected {expected}, got {actual}")
print(f"{len(reconciliation) - len(failed)} of {len(reconciliation)} reconciliation checks passed")
assert not failed, f"{len(failed)} mandatory reconciliation check(s) failed: " + ", ".join(r[0] for r in failed)

# COMMAND ----------

dbutils.notebook.exit(json.dumps({
    "snapshot_id": snapshot_id,
    "checks": len(reconciliation),
    "failed": 0,
    "gold_hash_application_status": facts["hash_status"],
    "gold_hash_funnel_summary": facts["hash_funnel"],
}))
