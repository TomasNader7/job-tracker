# Phase 1 spike: Databricks Free Edition

Date: 2026-11-19 to 2026-11-20. Synthetic data only. Real tracker data never leaves the local machine.

## Question

Can the synthetic job-tracker data run through a bronze, silver, gold pipeline on Databricks Free Edition, be uploaded and triggered from the terminal, and feed Power BI?

## Results

| Check | Result |
|---|---|
| Account | Free Edition, unverified (no LinkedIn verification). Used as the baseline. |
| Storage | Schema `workspace.job_tracker_demo`, managed volume `landing`. |
| Bronze | `bronze_application`, 183 rows from `fact_application.csv`. |
| Silver | `silver_application`, 183 in, 183 out, dimension joins clean. |
| Gold | `gold_funnel_summary` matches `data/synthetic/expected_measures.json` on every measure (182 applications, 56 responses, 2 interviews, 0 offers, 54 rejections). |
| CLI | Databricks CLI v1.27.0, browser OAuth login, no token. |
| Upload | `databricks fs cp` copied a CSV to the volume (17,039 bytes, matched). |
| Trigger | `databricks jobs submit` ran the notebook on serverless compute, no cluster, SUCCESS in about 1 minute. |
| Power BI | Databricks connector, OAuth (OIDC), Import mode, serverless starter warehouse. Bronze, silver and gold are listed. Gold preview matches the expected values and a visual renders 182 applications. |

## Conclusion

The CLI upload plus job trigger path works, and Power BI can read the gold table over the SQL warehouse. This path is viable for the MVP demo lane.

## Not tested

- Whether LinkedIn verification of the account changes any limits. The MVP works on the unverified baseline.
- Scheduled refresh. Import mode needs a manual Refresh.

## Rules that stay in force

- Only seeded synthetic data goes to Databricks Free Edition. It may be used to train Databricks services.
- Real data stays local and private.
- The app is local-only in the MVP. Do not call it "deployed".
- No tokens in the repo. Authentication is OAuth.
