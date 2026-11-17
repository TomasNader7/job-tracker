# Job Search Dashboard (Power BI)

A Power BI dashboard over a job-search pipeline: funnel conversion, performance by resume variant, job type, priority and networking vs cold apply, and a weekly timeline. This repo ships **synthetic data only**, calibrated to the shape of a real tracker. The real tracker and its dashboard stay private.

Status: Week A1 (data, model, measures). The model is built and checked on the real data (private). Remaining A1 steps: add `+ 0` to the count measures so zero shows as 0, and run the PivotTable reconciliation. Report pages come in Week A2.

## Two datasets, one model

| | Synthetic (public) | Real (private) |
|---|---|---|
| Script | `scripts/generate_synthetic.py` | `scripts/build_real.py` |
| Output | `data/synthetic/` (committed) | `data/real/` (git-ignored) |
| Content | fabricated companies, titles, dates | the real tracker, read-only |

Both write the identical star schema through `scripts/starschema.py`, so one Power BI model works for either: point the six queries at the other folder and refresh.

| Table | Role |
|---|---|
| `fact_application` | one row per application |
| `dim_status` | status plus 1/0 flags: is_application, is_response, is_interview, is_offer, is_rejection, is_no_reply |
| `dim_resume_variant`, `dim_job_type`, `dim_priority` | slicer dimensions |
| `dim_date` | calendar, joined on applied date |

```
python scripts/generate_synthetic.py --xlsx   # --xlsx adds a git-ignored flat Excel copy (tracker_flat.xlsx) for PivotTable checks
python scripts/build_real.py --xlsx           # real data; needs the real tracker file
python scripts/verify_measures.py data/synthetic   # recomputes every measure from the CSVs; must print PASS
```

## Data-prep rules (both datasets)

- **No Reply:** the tracker keeps silent applications as "Applied". Applied for more than 30 days counts as "No Reply".
- **Job type** (real data): derived from keywords in the job title, because the raw "Type of Job" column is inconsistent.
- **Blank priority or resume variant:** "Unspecified".
- **Networking Contact:** the referral/networking column is filled. Otherwise "Cold Apply". Includes outreach that was drafted or sent, not only confirmed referrals.
- **Excluded:** rows that are outreach with no posting.
- **Withdrawn:** counts as an application, with no response flag.

## Build the model in Power BI Desktop

1. Home > Get data > Text/CSV. Load all six CSVs from `data/synthetic/` (not `expected_measures.json` or the xlsx).
2. In `fact_application`, set `date_applied` and `interview_date` to type **Date**; in `dim_date`, set `date` to Date.
3. Model view. Create relationships (many-to-one, single direction):
   - `fact_application[status_key]` to `dim_status[status_key]`
   - `fact_application[resume_variant_key]` to `dim_resume_variant[resume_variant_key]`
   - `fact_application[job_type_key]` to `dim_job_type[job_type_key]`
   - `fact_application[priority_key]` to `dim_priority[priority_key]`
   - `fact_application[date_key]` to `dim_date[date_key]`
   - Leave `interview_date_key` unrelated (the measures use `interview_date` directly).
4. In `dim_date`, mark as date table on `date`; sort `month_name` by `month_number`.
5. Paste each measure from `powerbi/measures.dax` (Modeling > New measure).
6. Format the rate measures as percentage, Avg Days to Interview as one decimal.

## Measure definitions

- **Applications**: rows whose status counts as submitted (everything except Not Started).
- **Response**: any reply, including rejection (Rejected, Interview Scheduled, Interviewed, Accepted).
- **Interview**: Interview Scheduled, Interviewed, Accepted.
- **No Reply**: status is "No Reply" (see the 30-day rule above).
- Rates divide by Applications. Avg Days to Interview uses only rows with an interview date.

## Reconciliation (expected values, synthetic data)

Cards in Power BI (no slicers) must equal `data/synthetic/expected_measures.json` and the PivotTable on `tracker_flat.xlsx`:

| Measure | Expected |
|---|---|
| Applications | 182 |
| Responses / Rejections / Interviews / Offers / No Replies | 56 / 54 / 2 / 0 / 72 |
| Response / Interview / Offer / Rejection / No-Reply rate | 30.8% / 1.1% / 0.0% / 29.7% / 39.6% |
| Avg Days to Interview | 21.0 |

PivotTable check: Rows = application_status, Values = count of application_id. The counts must add up to the measures above.

## Privacy

`.gitignore` blocks `*.xlsx` and `data/real/`. The real tracker holds company names, contact emails and LinkedIn links and is never committed. The synthetic files contain only fabricated names ("Company 001") and aggregate-level calibration.
