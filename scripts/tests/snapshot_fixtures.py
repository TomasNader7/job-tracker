"""Builds tiny valid snapshots for the guard tests, so each test can break exactly one thing."""
import csv
import hashlib
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import snapshot_guard as g  # noqa: E402

GENERATOR = SCRIPTS / "generate_synthetic.py"
TS = "2026-10-{day:02d}T{hour:02d}:00:00.000000Z"


def ts(day, hour=0):
    return TS.format(day=day, hour=hour)


def default_apps():
    return [
        ["1", "Company 001", "Data Analyst", "2026-10-01", "Rejected", "0"],
        ["2", "Company 002", "Data Engineer", "2026-10-02", "Applied", "1"],
        ["3", "Company 003", "Data Scientist, New Grad", "2026-10-03", "Applied", "0"],
    ]


def default_events():
    return [
        ["1", "1", "", "Applied", ts(1), ts(1), "Manual", "", ""],
        ["2", "1", "Applied", "Withdrawn", ts(5), ts(5), "Manual", "", "synthetic seed"],
        ["3", "1", "Applied", "Rejected", ts(5), ts(5, 12), "Correction", "2", "synthetic correction"],
        ["4", "2", "", "Applied", ts(2), ts(2), "Manual", "", ""],
        ["5", "3", "", "Applied", ts(3), ts(3), "Manual", "", ""],
    ]


def csv_bytes(header, rows):
    import io
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def default_manifest(snapshot_id, apps_bytes, events_bytes, n_apps, n_events):
    return {
        "contract_version": "1",
        "lane": "synthetic",
        "snapshot_id": snapshot_id,
        "as_of": "2026-11-16T00:00:00.000000Z",
        "exported_at": "2026-11-17T09:30:00.000000Z",
        "files": [
            {"name": "applications.csv",
             "columns": [{"name": n, "type": t} for n, t in zip(g.APPLICATION_COLUMNS, g.APPLICATION_TYPES)],
             "rows": n_apps, "sha256": hashlib.sha256(apps_bytes).hexdigest()},
            {"name": "status_events.csv",
             "columns": [{"name": n, "type": t} for n, t in zip(g.EVENT_COLUMNS, g.EVENT_TYPES)],
             "rows": n_events, "sha256": hashlib.sha256(events_bytes).hexdigest()},
        ],
        "applications": {"accepted": n_apps, "rejected": 0, "rejected_reasons": {"NO_EVENTS": 0, "TIMESTAMP_PRECISION": 0}},
        "expected_measures": {
            "Applications": 3, "Responses": 1, "Interviews": 0, "Offers": 0, "Rejections": 1, "No_Replies": 1,
            "Rows_with_interview_date": 0, "Avg_Days_to_Interview": None,
            "Responses_Rate": "0.3333", "Interviews_Rate": "0.0000", "Offers_Rate": "0.0000",
            "Rejections_Rate": "0.3333", "No_Replies_Rate": "0.3333",
        },
        "expected_problems": {"total": 0, "by_code": {c: 0 for c in g.PROBLEM_CODES}},
        "gold_hash": {"algorithm": "sha256", "definition": "docs/snapshot-contract.md, section Canonical gold-content hash",
                      "gold_application_status": "a" * 64, "gold_funnel_summary": "b" * 64},
    }


def make_snapshot(root, snapshot_id="snap-0123456789ab", apps=None, events=None, edit_manifest=None,
                  apps_bytes=None, events_bytes=None, folder_name=None):
    """Write a valid snapshot under root and return its folder.

    apps_bytes / events_bytes replace the CSV file content (hashes in the manifest are recomputed to match),
    edit_manifest(manifest) can change the manifest after the hashes are filled in.
    """
    apps = default_apps() if apps is None else apps
    events = default_events() if events is None else events
    a_bytes = apps_bytes if apps_bytes is not None else csv_bytes(g.APPLICATION_COLUMNS, apps)
    e_bytes = events_bytes if events_bytes is not None else csv_bytes(g.EVENT_COLUMNS, events)

    manifest = default_manifest(snapshot_id, a_bytes, e_bytes, len(apps), len(events))
    if edit_manifest:
        edit_manifest(manifest)

    folder = Path(root) / (folder_name or snapshot_id)
    folder.mkdir(parents=True)
    (folder / "applications.csv").write_bytes(a_bytes)
    (folder / "status_events.csv").write_bytes(e_bytes)
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return folder
