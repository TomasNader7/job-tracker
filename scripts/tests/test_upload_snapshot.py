import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot_fixtures as fx  # noqa: E402

import upload_snapshot as up  # noqa: E402

SNAPSHOT_ID = "snap-0123456789ab"
BANNER = '<claude-code-hint v="1" type="plugin" value="x" />\n'


class FakeRunner:
    """Records every command. Nothing here touches the network."""

    def __init__(self, fail_on=None, run_states=None):
        self.calls = []
        self.submit_bodies = []
        self.fail_on = fail_on
        self.run_states = list(run_states or [("TERMINATED", "SUCCESS")])

    def __call__(self, args):
        self.calls.append(list(args))
        rest = args[1:]
        if rest[:1] == ["--version"]:
            return 0, "Databricks CLI v1.27.0\n", ""
        if self.fail_on and rest[:2] == self.fail_on:
            return 1, "", "boom"
        if rest[:2] == ["jobs", "submit"]:
            body_path = next(a for a in args if a.startswith("@"))[1:]
            self.submit_bodies.append(json.loads(Path(body_path).read_text(encoding="utf-8")))
            return 0, BANNER + '{"run_id": 42}', ""
        if rest[:2] == ["jobs", "get-run"]:
            life, result = self.run_states.pop(0) if len(self.run_states) > 1 else self.run_states[0]
            return 0, BANNER + json.dumps({"state": {"life_cycle_state": life, "result_state": result,
                                                      "state_message": "msg"}, "run_page_url": "https://example/run/42"}), ""
        return 0, "", ""


class UploadTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.notebook = self.root / "nb.py"
        self.notebook.write_text("# Databricks notebook source\n", encoding="utf-8")
        self.messages = []

    def run_upload(self, folder, runner=None, **kwargs):
        runner = runner or FakeRunner()
        defaults = dict(runner=runner, cli="databricks-test", env={}, cfg_path=self.root / "none.cfg",
                        generator=fx.GENERATOR, notebook=self.notebook, say=self.messages.append, sleep=lambda s: None)
        defaults.update(kwargs)
        return up.run_upload(folder, **defaults), runner

    def snapshot(self, **kwargs):
        return fx.make_snapshot(self.root / "snaps", **kwargs)


class RefusalTests(UploadTestCase):
    def test_a_tampered_snapshot_is_refused_and_the_cli_is_never_called(self):
        folder = self.snapshot()
        path = folder / "applications.csv"
        path.write_bytes(path.read_bytes().replace(b"Company 001", b"Company 009", 1))

        code, runner = self.run_upload(folder)

        self.assertEqual(2, code)
        self.assertEqual([], runner.calls)
        self.assertTrue(any("REFUSED" in m for m in self.messages))
        self.assertTrue(any("Nothing was uploaded" in m for m in self.messages))

    def test_a_non_synthetic_lane_is_refused_without_calling_the_cli(self):
        folder = self.snapshot(edit_manifest=lambda m: m.update(lane="real"))

        code, runner = self.run_upload(folder)

        self.assertEqual(2, code)
        self.assertEqual([], runner.calls)

    def test_a_real_looking_company_name_is_refused_without_calling_the_cli(self):
        rows = fx.default_apps()
        rows[0][1] = "Acme Corporation"

        code, runner = self.run_upload(self.snapshot(apps=rows))

        self.assertEqual(2, code)
        self.assertEqual([], runner.calls)

    def test_a_token_in_the_environment_is_refused_before_anything_else(self):
        code, runner = self.run_upload(self.snapshot(), env={"DATABRICKS_TOKEN": "dapi-secret"})

        self.assertEqual(2, code)
        self.assertEqual([], runner.calls)
        self.assertFalse(any("dapi-secret" in m for m in self.messages), "the secret must not be printed")

    def test_a_profile_that_holds_a_token_is_refused(self):
        cfg = self.root / ".databrickscfg"
        cfg.write_text("[jobtracker-demo]\ntoken = dapi123\n", encoding="utf-8")

        code, runner = self.run_upload(self.snapshot(), cfg_path=cfg)

        self.assertEqual(2, code)
        self.assertEqual([], runner.calls)

    def test_an_unsafe_folder_name_is_refused(self):
        folder = fx.make_snapshot(self.root / "snaps", snapshot_id="Snap-ABC", folder_name="Snap-ABC")

        code, runner = self.run_upload(folder)

        self.assertEqual(2, code)
        self.assertEqual([], runner.calls)

    def test_dry_run_validates_and_calls_nothing(self):
        code, runner = self.run_upload(self.snapshot(), dry_run=True)

        self.assertEqual(0, code)
        self.assertEqual([], runner.calls)
        self.assertTrue(any("Dry run" in m for m in self.messages))


class CommandTests(UploadTestCase):
    def test_happy_path_runs_the_expected_commands_in_order(self):
        code, runner = self.run_upload(self.snapshot())

        self.assertEqual(0, code)
        shape = [tuple(c[1:3]) if c[1] != "--version" else ("--version",) for c in runner.calls]
        self.assertEqual(
            [("--version",), ("fs", "mkdir"), ("fs", "cp"), ("fs", "cp"), ("fs", "cp"),
             ("workspace", "mkdirs"), ("workspace", "import"), ("jobs", "submit"), ("jobs", "get-run")],
            shape)

    def test_every_call_uses_the_oauth_profile_and_never_a_token(self):
        _, runner = self.run_upload(self.snapshot())

        for call in runner.calls[1:]:          # calls[0] is the offline `--version` check
            self.assertIn("-p", call, f"profile flag missing on {call}")
            self.assertEqual("jobtracker-demo", call[call.index("-p") + 1])
            self.assertNotIn("--token", " ".join(call))

    def test_uploads_go_only_to_a_folder_named_after_the_exact_snapshot_id(self):
        _, runner = self.run_upload(self.snapshot())

        targets = [c for c in runner.calls if c[1:3] == ["fs", "cp"]]
        self.assertEqual(3, len(targets))
        expected_dir = f"dbfs:/Volumes/workspace/job_tracker_demo/landing/snapshots/{SNAPSHOT_ID}"
        # command shape: databricks fs cp --overwrite <local> <remote> -p jobtracker-demo
        names = sorted(Path(t[4]).name for t in targets)
        self.assertEqual(["applications.csv", "manifest.json", "status_events.csv"], names)
        for t in targets:
            local, remote = t[4], t[5]
            self.assertEqual(f"{expected_dir}/{Path(local).name}", remote)
            self.assertEqual(SNAPSHOT_ID, Path(local).parent.name)

    def test_the_word_latest_never_appears_in_any_command(self):
        _, runner = self.run_upload(self.snapshot())

        self.assertFalse(any("latest" in part.lower() for call in runner.calls for part in call))

    def test_the_notebook_is_submitted_with_explicit_parameters(self):
        _, runner = self.run_upload(self.snapshot())

        body = runner.submit_bodies[0]
        task = body["tasks"][0]["notebook_task"]
        self.assertEqual("/Shared/job_tracker_p3/02_snapshot_pipeline", task["notebook_path"])
        self.assertEqual({
            "snapshot_id": SNAPSHOT_ID,
            "as_of": "2026-11-16T00:00:00.000000Z",
            "landing_path": f"/Volumes/workspace/job_tracker_demo/landing/snapshots/{SNAPSHOT_ID}",
        }, task["base_parameters"])

    def test_the_cli_banner_before_the_json_is_ignored(self):
        self.assertEqual({"run_id": 7}, up.parse_json_output(BANNER + '{"run_id": 7}'))
        self.assertEqual([1], up.parse_json_output("hint\n[1]"))
        with self.assertRaises(up.CliFailure):
            up.parse_json_output("no json here")


class PrivacyTests(unittest.TestCase):
    SCRIPTS = Path(__file__).resolve().parent.parent

    def test_no_script_hard_codes_a_user_folder(self):
        for path in self.SCRIPTS.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"C:\\Users|C:/Users|/Users/", f"{path.name} hard-codes a user folder")

    def test_the_cli_location_comes_from_the_environment_the_path_or_the_home_folder(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"DATABRICKS_CLI_PATH": "/opt/databricks"}):
            self.assertEqual("/opt/databricks", up.default_cli())
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(up.shutil, "which", return_value=None):
            os.environ.pop("DATABRICKS_CLI_PATH", None)
            self.assertEqual(str(Path.home() / "tools" / "databricks-cli" / "databricks.exe"), up.default_cli())


class FailureTests(UploadTestCase):
    def test_a_failed_upload_stops_before_the_job_is_submitted(self):
        code, runner = self.run_upload(self.snapshot(), runner=FakeRunner(fail_on=["fs", "cp"]))

        self.assertEqual(3, code)
        self.assertFalse(any(c[1:3] == ["jobs", "submit"] for c in runner.calls))

    def test_a_missing_cli_stops_everything_before_any_upload(self):
        runner = FakeRunner()
        runner.__class__ = type("NoCli", (FakeRunner,), {"__call__": lambda self, a: (self.calls.append(list(a)) or (1, "", "not found"))})

        code, _ = self.run_upload(self.snapshot(), runner=runner)

        self.assertEqual(3, code)
        self.assertEqual(1, len(runner.calls))

    def test_a_missing_notebook_stops_before_the_cli_is_called(self):
        code, runner = self.run_upload(self.snapshot(), notebook=self.root / "missing.py")

        self.assertEqual(3, code)
        self.assertEqual([], runner.calls)

    def test_a_failed_job_gives_exit_code_4(self):
        code, _ = self.run_upload(self.snapshot(), runner=FakeRunner(run_states=[("TERMINATED", "FAILED")]))

        self.assertEqual(4, code)
        self.assertTrue(any("did not succeed" in m for m in self.messages))

    def test_it_polls_until_the_run_finishes(self):
        runner = FakeRunner(run_states=[("PENDING", None), ("RUNNING", None), ("TERMINATED", "SUCCESS")])

        code, _ = self.run_upload(self.snapshot(), runner=runner)

        self.assertEqual(0, code)
        self.assertEqual(3, sum(1 for c in runner.calls if c[1:3] == ["jobs", "get-run"]))

    def test_no_wait_submits_and_returns_without_polling(self):
        code, runner = self.run_upload(self.snapshot(), wait=False)

        self.assertEqual(0, code)
        self.assertFalse(any(c[1:3] == ["jobs", "get-run"] for c in runner.calls))


if __name__ == "__main__":
    unittest.main()
