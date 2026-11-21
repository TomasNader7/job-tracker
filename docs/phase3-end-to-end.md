# Phase 3: first synthetic end-to-end slice

Date: 2026-11-21. Synthetic data only. Real tracker data never enters this path: the tools refuse any database but `JobTracker_Demo`, and the uploader refuses anything that is not a clean synthetic snapshot.

## Question

Can the app's append-only status history be seeded, exported as a versioned snapshot, uploaded safely, **recomputed independently in Databricks**, reconciled at every layer, and rerun with identical results?

## What was built

```text
JobTracker_Demo (LocalDB, synthetic only)
  | seed-demo         refuses any database but JobTracker_Demo
  | export-snapshot   deterministic: pinned as-of, content-derived snapshot id
  v
snapshots/<snapshot_id>/   applications.csv, status_events.csv, manifest.json   (git-ignored)
  | scripts/upload_snapshot.py   every safety check runs BEFORE the Databricks CLI is called
  v
landing volume  .../snapshots/<snapshot_id>/        (never a "latest" folder)
  | databricks/notebooks/02_snapshot_pipeline.py
  v
workspace.job_tracker_p3:  bronze_* -> silver_* (+ rejected rows) -> gold_application_status, gold_funnel_summary, gold_reconciliation
  | scripts/verify_databricks_run.py   independent read-back through the SQL warehouse
```

The contract both sides implement is in [snapshot-contract.md](snapshot-contract.md): one ordering rule, the full correction semantics, problem codes, rounding, formats, and the canonical gold-content hash.

## Run it

```text
dotnet run --project src/JobTracker.Tools -- seed-demo --reset
dotnet run --project src/JobTracker.Tools -- export-snapshot
python scripts/upload_snapshot.py snapshots/<snapshot_id>          # add --dry-run to validate only
python scripts/verify_databricks_run.py snapshots/<snapshot_id>
```

Needs the Databricks CLI signed in by browser OAuth under the profile `jobtracker-demo`. The CLI is found through `DATABRICKS_CLI_PATH`, then the PATH, then `~/tools/databricks-cli/`. No tokens: the scripts refuse if one is set.

## Results

| Check | Result |
|---|---|
| Demo database | 182 applications, 248 events (7 corrections, one of them a correction of a correction), 0 projection problems |
| Measures vs Phase 0 `expected_measures.json` | equal digit for digit: 182 / 56 / 2 / 0 / 54 / 72, rates 0.3077 / 0.0110 / 0.0000 / 0.2967 / 0.3956, average 21.00 days |
| Two pinned exports | byte-identical `applications.csv`, `status_events.csv` and `manifest.json`; same snapshot id `snap-c5d6823fb2ea` |
| Real `JobTracker` database | refused by both commands, exit code 2 |
| Tampered snapshot | refused before the CLI is called, exit code 2 (tests: changed byte, edited manifest, wrong lane, bad id, extra file, extra column, bad company, bad title, email/URL/phone in a note, token in the environment) |
| Spark on a local machine | reproduces the C# result exactly: all 13 measures, every application's status and no-reply flag, both canonical gold hashes |
| Databricks job (serverless) | first run SUCCESS (run 853302819432721) |
| Reconciliation stored by the job | 36 of 36 checks passed (source 2, bronze 2, silver 5, gold 26, self-test 1) |
| Independent read-back | 16 of 16 checks verified by `verify_databricks_run.py`, which re-hashes the stored rows in Python |
| Rerun of the same snapshot | SUCCESS (run 412045970948906); every table keeps exactly one copy of the snapshot; **same canonical gold-content hashes** |
| Canonical gold hashes | status table `8a0349c26a2161328fa825fb13e5fb7970a7193726abadd4230f26ffd7680336`, funnel `e7baa2af9f99c330a4f066cc398a124070e2e9ee19de50d0f726ec1d5a73e1e5` |
| Phase 1 objects | untouched: its three tables and its gold values are unchanged |

Tests: 201 .NET tests, 99 Python tests (9 of them run the notebook's functions on a local Spark). All run in CI, which also checks that a snapshot written by the C# exporter passes the Python guard.

## How the cross-language agreement is held

- **Golden fixture** (`tests/contract/projection_cases_v1.json`): 20 cases, including a 49-pair transition matrix whose expected answers come from an independent hand-written copy of the transition table. The C# tests run it, the notebook runs it as a self-test before touching any data, and a test checks the notebook's embedded copy has not drifted.
- **Numeric tie-break:** events at the same time are ordered by `event_sequence` compared as a number. A case with sequences 9 and 10 at one timestamp fails if anything sorts them as text, and `recorded_at` is deliberately reversed in that case to prove it is not used.
- **Exact decimals:** rates and the average use decimal arithmetic with HALF UP rounding in both runtimes. Tie cases (1/32 and 1/8) are tested.
- **Hashes over content, not files:** both gold hashes are SHA-256 over a canonical text (sorted keys, fixed formats, `\N` for null), never over Delta files or row order.

## Deviations from the plan, and why

- **Gold column types:** the Phase 3 `gold_funnel_summary` keeps the Phase 1 column names but stores rates and the average as exact decimals instead of doubles, so values reproduce exactly. It adds `snapshot_id`.
- **Separate schema:** outputs go to `workspace.job_tracker_p3` so the Phase 1 schema, tables and notebook stay untouched.
- **182 applications, not 183:** the one Not Started row of the Phase 0 file has no applied date, which the app requires, so it is skipped.
- **A bug the seeder found:** a status change with the same timestamp as an earlier event was refused with a confusing message, because the dry run sorted the new event first. Fixed in the service with regression tests. It affected the real app as well (for example applied and rejected on the same date).

## Limitations

- **Funnel only.** The app has no job type, resume variant, priority or referral fields, so the Phase 0 slicers on those cannot be fed from the app yet. Not added in Phase 3.
- **Seed history is fabricated.** The Phase 0 file has only a current status, so the dates of the events leading to it are made up, deterministically from the application id, and labelled `synthetic seed`. Time-to-response figures from this data mean nothing. Real imported rows will keep null response timestamps.
- **No bad-snapshot run on Databricks.** The notebook fails the job on any failed check, and tests show the reconciliation flags wrong values, but a deliberately wrong snapshot was not run on the platform. The uploader would refuse an invalid one anyway.
- **Power BI refresh against the new gold table is a manual step** that has not been confirmed yet. Point the Databricks connector at `workspace.job_tracker_p3.gold_funnel_summary`.
- **Free Edition**, unverified account baseline, serverless compute only. Import mode needs a manual Refresh.
- The notebook itself cannot be unit-tested inside Databricks; its functions are tested on a local Spark, and the job runs the self-test and reconciliation every time.

## Thin/full gate input

Hours for Phases 0 to 3 have not been measured. A rough floor from commit timestamps is about 4 hours, but commits are sparse and that undercounts: the real figure has to come from the person doing the work. Budget: low 23, high 33.
