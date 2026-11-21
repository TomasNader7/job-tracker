"""Guarded upload of one synthetic snapshot to Databricks Free Edition, then run the pipeline notebook (Phase 3).

Order of events, and why it is safe:
  1. Refuse if tokens are in play (browser OAuth only).
  2. Validate the snapshot folder completely (lane, schema, counts, hashes, synthetic-only text).
     Any failure stops here: nothing has left the machine, and the Databricks CLI has not even been started.
  3. Only then call the Databricks CLI, explicitly, with profile jobtracker-demo, against a path that contains
     the exact snapshot id. There is no "latest" folder anywhere.

Usage:
    python scripts/upload_snapshot.py snapshots/<snapshot_id> [--dry-run] [--no-wait]

Exit codes: 0 ok, 2 refused by a safety check, 3 environment or upload failure, 4 the Databricks job failed.
Standard library only.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import snapshot_guard as guard

REPO = Path(__file__).resolve().parent.parent


def default_cli() -> str:
    """Where the Databricks CLI is: DATABRICKS_CLI_PATH, else the PATH, else ~/tools/databricks-cli/databricks.exe."""
    configured = os.environ.get("DATABRICKS_CLI_PATH")
    if configured:
        return configured
    return shutil.which("databricks") or str(Path.home() / "tools" / "databricks-cli" / "databricks.exe")


DEFAULT_CLI = default_cli()
PROFILE = "jobtracker-demo"
LANDING_BASE = "/Volumes/workspace/job_tracker_demo/landing/snapshots"   # one subfolder per snapshot id
WORKSPACE_DIR = "/Shared/job_tracker_p3"
NOTEBOOK_NAME = "02_snapshot_pipeline"
NOTEBOOK_LOCAL = REPO / "databricks" / "notebooks" / "02_snapshot_pipeline.py"
GENERATOR = REPO / "scripts" / "generate_synthetic.py"
DATABRICKS_CFG = Path.home() / ".databrickscfg"
TERMINAL_STATES = {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}


class CliFailure(Exception):
    """A Databricks CLI call failed."""


def real_runner(args):
    """Run a command without a shell. Returns (return code, stdout, stderr)."""
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1500)
    return done.returncode, done.stdout, done.stderr


def parse_json_output(text: str):
    """The CLI may print a hint line before the JSON. Parse from the first brace or bracket."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise CliFailure(f"No JSON in CLI output: {text[:200]!r}")
    return json.loads(text[min(starts):])


class Uploader:
    def __init__(self, runner, cli, say=print, sleep=time.sleep):
        self.runner, self.cli, self.say, self.sleep = runner, cli, say, sleep

    def call(self, *args, profile=True):
        command = [self.cli, *args] + (["-p", PROFILE] if profile else [])
        code, out, err = self.runner(command)
        if code != 0:
            raise CliFailure(f"`databricks {' '.join(args[:2])}` failed (exit {code}): {(err or out).strip()[:400]}")
        return out

    def check_cli(self):
        code, out, err = self.runner([self.cli, "--version"])
        if code != 0 or not re.search(r"Databricks CLI v\d+\.\d+\.\d+", out):
            raise CliFailure(f"The Databricks CLI was not found or did not answer at {self.cli}")
        self.say(f"CLI: {out.strip()}")

    def upload(self, folder: Path, snapshot_id: str):
        remote = f"dbfs:{LANDING_BASE}/{snapshot_id}"
        self.call("fs", "mkdir", remote)
        for name in sorted(guard.ALLOWED_FILES):          # exactly the allowlisted files, nothing else
            self.call("fs", "cp", "--overwrite", str(folder / name), f"{remote}/{name}")
            self.say(f"  uploaded {name}")

    def import_notebook(self, notebook: Path):
        self.call("workspace", "mkdirs", WORKSPACE_DIR)
        self.call("workspace", "import", f"{WORKSPACE_DIR}/{NOTEBOOK_NAME}", "--file", str(notebook),
                  "--format", "SOURCE", "--language", "PYTHON", "--overwrite")

    def submit(self, snapshot_id: str, as_of: str) -> int:
        request = {
            "run_name": f"snapshot-pipeline-{snapshot_id}",
            "tasks": [{
                "task_key": "snapshot_pipeline",
                "notebook_task": {
                    "notebook_path": f"{WORKSPACE_DIR}/{NOTEBOOK_NAME}",
                    # Explicit parameters. The notebook reads only this snapshot, from this path.
                    "base_parameters": {
                        "snapshot_id": snapshot_id,
                        "as_of": as_of,
                        "landing_path": f"{LANDING_BASE}/{snapshot_id}",
                    },
                },
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "submit.json"
            body.write_text(json.dumps(request), encoding="utf-8")
            out = self.call("jobs", "submit", "--json", f"@{body}", "--no-wait", "-o", "json")
        return int(parse_json_output(out)["run_id"])

    def wait(self, run_id: int, poll_seconds=15, max_polls=120) -> dict:
        for _ in range(max_polls):
            run = parse_json_output(self.call("jobs", "get-run", str(run_id), "-o", "json"))
            state = run.get("state", {})
            if state.get("life_cycle_state") in TERMINAL_STATES:
                return run
            self.sleep(poll_seconds)
        raise CliFailure(f"Run {run_id} did not finish in time")


def run_upload(folder, *, runner=real_runner, cli=DEFAULT_CLI, env=None, cfg_path=DATABRICKS_CFG, generator=GENERATOR,
               notebook=NOTEBOOK_LOCAL, dry_run=False, wait=True, say=print, sleep=time.sleep) -> int:
    env = os.environ if env is None else env
    folder = Path(folder)

    try:
        guard.ensure_oauth_only(env, cfg_path, PROFILE)
        manifest = guard.validate_snapshot(folder, generator)
    except guard.GuardError as exc:
        say(f"REFUSED: {exc}")
        say("Nothing was uploaded. The Databricks CLI was not called.")
        return 2

    snapshot_id, as_of = manifest["snapshot_id"], manifest["as_of"]
    say(f"Snapshot {snapshot_id} passed every safety check ({manifest['applications']['accepted']} applications, "
        f"{manifest['files'][1]['rows']} events, lane {manifest['lane']}).")
    if dry_run:
        say("Dry run: validated only. Nothing was uploaded.")
        return 0

    uploader = Uploader(runner, cli, say, sleep)
    try:
        if not Path(notebook).is_file():
            raise CliFailure(f"The pipeline notebook is missing: {notebook}")
        uploader.check_cli()
        say(f"Uploading to {LANDING_BASE}/{snapshot_id}")
        uploader.upload(folder, snapshot_id)
        uploader.import_notebook(Path(notebook))
        run_id = uploader.submit(snapshot_id, as_of)
        say(f"Submitted run {run_id}")
        if not wait:
            say("Not waiting (--no-wait).")
            return 0
        run = uploader.wait(run_id)
    except CliFailure as exc:
        say(f"FAILED: {exc}")
        return 3

    state = run.get("state", {})
    say(f"Run {run_id}: {state.get('life_cycle_state')} / {state.get('result_state')}")
    if run.get("run_page_url"):
        say(f"  {run['run_page_url']}")
    if state.get("result_state") != "SUCCESS":
        say(f"The job did not succeed: {state.get('state_message', '')}")
        return 4
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Guarded upload of one synthetic snapshot to Databricks.")
    parser.add_argument("snapshot_dir", help="snapshots/<snapshot_id>")
    parser.add_argument("--dry-run", action="store_true", help="validate only; call nothing")
    parser.add_argument("--no-wait", action="store_true", help="submit the run but do not wait for it")
    parser.add_argument("--cli", default=DEFAULT_CLI, help="path to the Databricks CLI executable")
    args = parser.parse_args(argv)
    return run_upload(args.snapshot_dir, cli=args.cli, dry_run=args.dry_run, wait=not args.no_wait)


if __name__ == "__main__":
    sys.exit(main())
