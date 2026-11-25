# Snapshot contract v2

The agreement between the app (C#) and the Databricks pipeline (Spark). Both sides implement the same rules independently, then reconcile. Synthetic data only: the real lane never uses this path.

If this document and the code disagree, the golden fixture (`tests/contract/projection_cases_v1.json`) decides, and the code is fixed.

## What changed from v1

v2 adds the five optional fields to `applications.csv` (after `date_applied`). Event rules, ordering and every measure are unchanged, so the golden fixture stays `projection_cases_v1.json` and the existing gold hashes keep their values. The snapshot id formula now hashes version `2`, so ids differ from v1 ids.

## Files

A snapshot is a folder `snapshots/<snapshot_id>/` with exactly three files. Nothing else is allowed.

`applications.csv`

| Column | Type | Notes |
|---|---|---|
| application_id | int64 | |
| company_name | string | synthetic names only (`Company 001`) |
| job_title | string | |
| date_applied | date | `yyyy-MM-dd` |
| job_type | string or empty | enum name: `DataAnalyst`, `BiAnalyst`, `BusinessAnalyst`, `DataScientist`, `MlAiEngineer`, `DataEngineer`, `SoftwareEngineer`, `NewGradProgram`, `Other` |
| resume_variant | string or empty | `DataAnalytics`, `BackendSwe`, `BusinessIntelligence`, `MlDataScience`, `DataEngineering`, `Automation`, `FrontendSwe` |
| priority | string or empty | `TopPriority`, `MediumPriority`, `LowPriority` |
| referral_type | string or empty | `NetworkingContact`, `ColdApply` |
| location | string or empty | free text. Synthetic city names only (`Austin, TX`, `Remote (US)`) |
| expected_projected_status | string or empty | app result, **reconciliation only** |
| expected_is_no_reply | int (0/1) | app result, **reconciliation only** |

`status_events.csv`

| Column | Type | Notes |
|---|---|---|
| event_sequence | int64 | dense 1..N, see ordering |
| application_id | int64 | |
| previous_status | string or empty | |
| new_status | string | one of the 7 stored statuses |
| effective_at | timestamp | UTC, microseconds |
| recorded_at | timestamp | UTC, microseconds, **not used for ordering** |
| source | string | `Manual`, `Import` or `Correction` |
| supersedes_event_sequence | int64 or empty | set only on corrections |
| note | string or empty | |

`manifest.json`: see Manifest below.

The five optional fields (job type to location) are empty when the app stores nothing; "Unspecified" in the source data is stored as empty, never as a word. They are descriptive only: they do not change status, ordering or any measure.

The `expected_*` columns exist only so the pipeline can prove it reached the same answer. The pipeline must never use them as input to its own computation.

## Formats

- UTF-8, no BOM, LF line endings, header row, fixed column order, invariant culture.
- Rows sorted by key: `application_id` for applications, `event_sequence` for events.
- Null is an empty field. A field containing a comma, a double quote or a newline is wrapped in double quotes, with inner quotes doubled (RFC 4180). Nothing else is quoted.
- Dates: `yyyy-MM-dd`. Timestamps: UTC, `yyyy-MM-ddTHH:mm:ss.ffffffZ` (6 fractional digits). The exporter fails if a source value cannot be written exactly at microsecond precision.
- Statuses: `NotStarted`, `Applied`, `InterviewScheduled`, `Interviewed`, `Rejected`, `Withdrawn`, `Accepted`.

## Ordering (one rule for both runtimes)

Events of an application are processed in this order:

1. `effective_at` ascending
2. `event_sequence` ascending, **compared as a number** (so 9 comes before 10; as text it would not)

`event_sequence` is assigned by the exporter, dense from 1, in the same order as the app's event Id. That keeps the app's existing tie-break while making the export independent of gaps in database identity values.

`recorded_at` is **not** part of the ordering. (An earlier plan listed it. The implemented projection never used it, and the contract follows the implementation.)

## Status rules (must match `StatusProjection` exactly)

1. A correction is valid only if its target event exists **and belongs to the same application**. A valid correction marks its target superseded.
2. A correction whose target is missing or belongs to another application is **ignored**: removed from the chain, problem `CORRECTION_TARGET_INVALID`.
3. Corrections can be corrected. The superseded set comes from **all** valid corrections, including corrections that are themselves superseded. If C2 supersedes C1 which supersedes A, then A and C1 are dropped and C2 stays. Two corrections that supersede each other are both dropped.
4. The chain is every event except superseded and ignored ones, in the ordering above. Walk it:
   - First event must be `NotStarted` or `Applied`. Otherwise problem `ILLEGAL_FIRST`, stop, status is null.
   - A first event with a non-empty `previous_status` adds `FIRST_PREVIOUS_NOT_NULL`. The chain continues.
   - A later step not allowed by the transition table adds `ILLEGAL_TRANSITION` and stops at the last valid status.
   - A later step whose `previous_status` differs from the chain's current status adds `PREVIOUS_MISMATCH`. The chain wins and continues.
5. `is_no_reply` is true when the status is `Applied` and `as_of - effective_at` of the event that set that status is **strictly more than 30 days** (30 x 24 hours).

Transition table (from `StatusTransitions`):

| From | Allowed next |
|---|---|
| NotStarted | Applied, Withdrawn |
| Applied | InterviewScheduled, Rejected, Withdrawn |
| InterviewScheduled | Interviewed, Rejected, Withdrawn |
| Interviewed | InterviewScheduled, Rejected, Accepted, Withdrawn |
| Rejected | InterviewScheduled (reopen) |
| Withdrawn, Accepted | none |

Problem codes: `ILLEGAL_FIRST`, `FIRST_PREVIOUS_NOT_NULL`, `ILLEGAL_TRANSITION`, `PREVIOUS_MISMATCH`, `CORRECTION_TARGET_INVALID`.

`interview_date` is the UTC date of the first `Interviewed` event in the active chain (events processed before any stop), or null.

## Measures

An application counts as an **application** when its status is not null and not `NotStarted` (Withdrawn counts).

| Measure | Rule |
|---|---|
| Applications | status in Applied, InterviewScheduled, Interviewed, Rejected, Withdrawn, Accepted |
| Responses | status in Rejected, InterviewScheduled, Interviewed, Accepted |
| Interviews | status in InterviewScheduled, Interviewed, Accepted |
| Offers | status Accepted |
| Rejections | status Rejected |
| No_Replies | `is_no_reply` |
| Rows_with_interview_date | applications with an `interview_date` |
| Avg_Days_to_Interview | mean of (`interview_date` - `date_applied`) in whole days over those applications |
| *_Rate | count / Applications |

Rounding: rates are an integer numerator over an integer denominator computed as an exact decimal, **ROUND_HALF_UP to 4 places**. Avg_Days_to_Interview is HALF_UP to 2 places. Never binary floating point. A rate with zero applications, or an average with zero interview rows, is null.

## Gold tables (Databricks, schema `workspace.job_tracker_p3`)

Phase 3 writes to its own schema so the Phase 1 objects stay untouched. Every table carries `snapshot_id`.

`gold_funnel_summary` keeps the Phase 1 column names:
`Applications, Responses, Interviews, Offers, Rejections, No_Replies, Rows_with_interview_date, Avg_Days_to_Interview, Responses_Rate, Interviews_Rate, Offers_Rate, Rejections_Rate, No_Replies_Rate, _gold_at`, plus `snapshot_id`.

Deliberate difference from Phase 1: rates are `DECIMAL(9,4)` and the average is `DECIMAL(9,2)` instead of `DOUBLE`, so values are exact and reproducible. `_gold_at` is a provenance timestamp and is excluded from every hash.

`gold_application_status`: `snapshot_id, application_id, status, is_no_reply, problem_count, interview_date`.

`gold_funnel_by_dimension` (new in v2): `snapshot_id, dimension, dimension_value, Applications, Responses, Interviews, Offers, Rejections, No_Replies, Responses_Rate, Interviews_Rate, Offers_Rate, Rejections_Rate, No_Replies_Rate`. One row per value of each of four dimensions: `job_type`, `resume_variant`, `priority`, `referral_type`. A missing value is the word `Unspecified`. Each row uses exactly the overall measure rules, computed over the applications that share that value (no interview-date measures). A value with no applications has no row. For every dimension the `Applications` column adds up to the overall `Applications`. Rates are `DECIMAL(9,4)`.

`gold_reconciliation`: `snapshot_id, check_name, layer, expected, actual, passed`.

## Manifest

`manifest.json` (UTF-8, LF, keys in the order below):

| Field | Meaning |
|---|---|
| contract_version | `"2"` |
| lane | must be `"synthetic"` |
| snapshot_id | see Identity |
| as_of | UTC timestamp the statuses were computed at |
| exported_at | when the files were written. Operational only |
| files | per file: `name`, `columns` (names and types), `rows`, `sha256` |
| applications | `accepted` (exported) and `rejected` (not exported, with reason counts) |
| expected_measures | every measure above, computed by the C# projection |
| expected_problems | total and per-code counts |
| gold_hash | definition text, the expected `gold_application_status`, `gold_funnel_summary` and `gold_funnel_by_dimension` hashes, and `gold_funnel_by_dimension_rows` (the row count) |

The exporter rejects (does not export) an application that has no events or a timestamp it cannot write exactly. Projection problems do **not** cause rejection: they are exported and reconciled.

## Determinism and identity

- Same source state and same `as_of` give byte-identical `applications.csv` and `status_events.csv`, therefore identical SHA-256s.
- `snapshot_id` is content-derived unless supplied: `snap-` + the first 12 hex characters of SHA-256 over the UTF-8 text `contract_version`, LF, `as_of`, LF, sha256(applications.csv), LF, sha256(status_events.csv). A supplied id must match `^[a-z0-9][a-z0-9-]{2,63}$`, as must a derived one.
- `exported_at` is outside content identity and outside every hash. Tests pin it.

## Canonical gold-content hash

SHA-256 (lowercase hex) of UTF-8 text built like this. It never depends on Delta files, table metadata or unspecified row order.

- `gold_application_status`: columns `application_id, status, is_no_reply, problem_count, interview_date`; rows sorted by `application_id`; fields joined by TAB; each row ends with LF; null is `\N`; integers base-10; `is_no_reply` is `0` or `1`; dates ISO.
- `gold_funnel_summary`: one row; fields in the order `Applications, Responses, Interviews, Offers, Rejections, No_Replies, Rows_with_interview_date, Avg_Days_to_Interview, Responses_Rate, Interviews_Rate, Offers_Rate, Rejections_Rate, No_Replies_Rate`; counts base-10; rates as fixed 4-decimal text; the average as fixed 2-decimal text; null is `\N`; TAB-joined; ends with LF.

- `gold_funnel_by_dimension`: columns `dimension, dimension_value, Applications, Responses, Interviews, Offers, Rejections, No_Replies, Responses_Rate, Interviews_Rate, Offers_Rate, Rejections_Rate, No_Replies_Rate`; rows in the fixed dimension order above, then by `dimension_value` as plain text (ordinal, code-point order); counts base-10; rates as fixed 4-decimal text; fields TAB-joined; each row ends with LF.

The C# exporter computes the expected hashes into the manifest. The pipeline must reproduce them, and a rerun must reproduce the same value.

## Safety

- Lane `synthetic` only. Company names match `Company ###`; job titles come from the generator's title set; notes are exactly `synthetic seed` or `synthetic correction`. The upload guard enforces this before anything leaves the machine.
- Only allowlisted files, exact schemas, verified row counts and SHA-256s.

## Golden fixture

`tests/contract/projection_cases_v1.json` holds small event sets with the expected status, `is_no_reply`, problem codes and `interview_date`. It is run by the .NET tests, embedded in the notebook as a self-test that runs before any data is processed, and a Python test checks the notebook's copy has not drifted.
