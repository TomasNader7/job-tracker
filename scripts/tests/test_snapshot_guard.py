import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot_fixtures as fx  # noqa: E402

g = fx.g


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def validate(self, folder):
        return g.validate_snapshot(folder, fx.GENERATOR)

    def assertRefused(self, folder, fragment):
        with self.assertRaises(g.GuardError) as ctx:
            self.validate(folder)
        self.assertIn(fragment.lower(), str(ctx.exception).lower())


class ValidSnapshotTests(GuardTestCase):
    def test_a_valid_snapshot_passes_and_returns_the_manifest(self):
        manifest = self.validate(fx.make_snapshot(self.root))

        self.assertEqual("synthetic", manifest["lane"])
        self.assertEqual("snap-0123456789ab", manifest["snapshot_id"])

    def test_every_title_the_generator_can_produce_is_allowed(self):
        titles = g.load_allowed_titles(fx.GENERATOR)

        self.assertGreaterEqual(len(titles), 9)
        self.assertIn("Data Scientist, New Grad", titles)
        self.assertIn("Data Analyst", titles)


class IdentityAndLayoutTests(GuardTestCase):
    def test_unsafe_snapshot_ids_are_refused(self):
        for bad in ["../evil", "Snap-0123", "ab", "has space", "-leading", "UPPER", "a/b", "latest "]:
            with self.subTest(id=bad):
                try:
                    folder = fx.make_snapshot(self.root / "x", snapshot_id=bad, folder_name=bad.replace("/", "_"))
                except (OSError, ValueError):
                    continue  # the OS would not even create it; nothing to validate
                with self.assertRaises(g.GuardError):
                    g.validate_snapshot(folder, fx.GENERATOR)

    def test_a_manifest_id_that_differs_from_the_folder_name_is_refused(self):
        folder = fx.make_snapshot(self.root, snapshot_id="snap-aaaaaaaaaaaa", folder_name="snap-bbbbbbbbbbbb")

        self.assertRefused(folder, "does not match the folder name")

    def test_an_extra_file_is_refused(self):
        folder = fx.make_snapshot(self.root)
        (folder / "notes.txt").write_text("hello")

        self.assertRefused(folder, "exactly")

    def test_an_extra_subfolder_is_refused(self):
        folder = fx.make_snapshot(self.root)
        (folder / "latest").mkdir()

        self.assertRefused(folder, "exactly")

    def test_a_missing_file_is_refused(self):
        folder = fx.make_snapshot(self.root)
        (folder / "status_events.csv").unlink()

        self.assertRefused(folder, "exactly")

    def test_a_folder_that_does_not_exist_is_refused(self):
        self.assertRefused(self.root / "snap-0123456789ab", "not a plain folder")


class ManifestTests(GuardTestCase):
    def edit(self, change, **kwargs):
        return fx.make_snapshot(self.root, edit_manifest=change, **kwargs)

    def test_a_real_lane_is_refused(self):
        self.assertRefused(self.edit(lambda m: m.update(lane="real")), "lane")

    def test_an_unknown_contract_version_is_refused(self):
        self.assertRefused(self.edit(lambda m: m.update(contract_version="3")), "contract_version")

    def test_an_unknown_manifest_field_is_refused(self):
        self.assertRefused(self.edit(lambda m: m.update(owner="someone")), "wrong fields")

    def test_a_wrong_row_count_is_refused(self):
        def change(m):
            m["files"][0]["rows"] = 99
        self.assertRefused(self.edit(change), "rows")

    def test_a_changed_schema_in_the_manifest_is_refused(self):
        def change(m):
            m["files"][0]["columns"][1]["type"] = "int64"
        self.assertRefused(self.edit(change), "schema")

    def test_an_extra_column_in_the_manifest_schema_is_refused(self):
        def change(m):
            m["files"][1]["columns"].append({"name": "owner_email", "type": "string"})
        self.assertRefused(self.edit(change), "schema")

    def test_a_wrong_hash_is_refused(self):
        def change(m):
            m["files"][0]["sha256"] = "0" * 64
        self.assertRefused(self.edit(change), "sha-256")

    def test_a_bad_timestamp_is_refused(self):
        self.assertRefused(self.edit(lambda m: m.update(as_of="2026-11-16")), "timestamp")

    def test_inconsistent_problem_counts_are_refused(self):
        def change(m):
            m["expected_problems"]["total"] = 5
        self.assertRefused(self.edit(change), "problem")

    def test_a_bad_dimension_hash_or_row_count_is_refused(self):
        for key, bad in [("gold_funnel_by_dimension", "ZZ"), ("gold_funnel_by_dimension_rows", -1),
                         ("gold_funnel_by_dimension_rows", "8"), ("gold_funnel_by_dimension_rows", True)]:
            with self.subTest(key=key, bad=bad):
                def change(m, key=key, bad=bad):
                    m["gold_hash"][key] = bad
                self.assertRefused(self.edit(change), "gold_hash")
                self.setUp()

    def test_a_non_hex_gold_hash_is_refused(self):
        def change(m):
            m["gold_hash"]["gold_funnel_summary"] = "not-a-hash"
        self.assertRefused(self.edit(change), "gold_hash")

    def test_invalid_json_is_refused(self):
        folder = fx.make_snapshot(self.root)
        (folder / "manifest.json").write_text("{not json", encoding="utf-8")

        self.assertRefused(folder, "not valid json")


class TamperingTests(GuardTestCase):
    def test_changing_one_byte_of_a_csv_is_refused(self):
        folder = fx.make_snapshot(self.root)
        path = folder / "applications.csv"
        path.write_bytes(path.read_bytes().replace(b"Company 001", b"Company 002", 1))

        self.assertRefused(folder, "sha-256")

    def test_editing_the_manifest_after_export_is_refused(self):
        folder = fx.make_snapshot(self.root)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        manifest["files"][1]["sha256"] = "f" * 64
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        self.assertRefused(folder, "sha-256")


class CsvFormatTests(GuardTestCase):
    def test_a_unknown_column_in_the_csv_is_refused(self):
        header = g.APPLICATION_COLUMNS + ["owner_email"]
        data = fx.csv_bytes(header, [row + ["x"] for row in fx.default_apps()])

        self.assertRefused(fx.make_snapshot(self.root, apps_bytes=data), "wrong columns")

    def test_a_missing_column_in_the_csv_is_refused(self):
        data = fx.csv_bytes(g.APPLICATION_COLUMNS[:-1], [row[:-1] for row in fx.default_apps()])

        self.assertRefused(fx.make_snapshot(self.root, apps_bytes=data), "wrong columns")

    def test_a_row_with_the_wrong_number_of_fields_is_refused(self):
        rows = fx.default_apps()
        rows[0].append("extra")
        data = fx.csv_bytes(g.APPLICATION_COLUMNS, rows)

        self.assertRefused(fx.make_snapshot(self.root, apps_bytes=data), "fields")

    def test_a_byte_order_mark_is_refused(self):
        data = b"\xef\xbb\xbf" + fx.csv_bytes(g.APPLICATION_COLUMNS, fx.default_apps())

        self.assertRefused(fx.make_snapshot(self.root, apps_bytes=data), "byte order mark")

    def test_crlf_line_endings_are_refused(self):
        data = fx.csv_bytes(g.APPLICATION_COLUMNS, fx.default_apps()).replace(b"\n", b"\r\n")

        self.assertRefused(fx.make_snapshot(self.root, apps_bytes=data), "carriage return")

    def test_a_missing_final_newline_is_refused(self):
        data = fx.csv_bytes(g.APPLICATION_COLUMNS, fx.default_apps()).rstrip(b"\n")

        self.assertRefused(fx.make_snapshot(self.root, apps_bytes=data), "newline")


class SyntheticOnlyTests(GuardTestCase):
    def apps_with(self, company=None, title=None):
        rows = fx.default_apps()
        if company is not None:
            rows[0][1] = company
        if title is not None:
            rows[0][2] = title
        return fx.make_snapshot(self.root, apps=rows)

    def test_company_names_must_look_like_company_nnn(self):
        for bad in ["Acme Corp", "Company 12", "Company 0001", "company 001", "Company 001 ", "Google", ""]:
            with self.subTest(company=bad):
                with self.assertRaises(g.GuardError):
                    g.validate_snapshot(self.apps_with(company=bad), fx.GENERATOR)
                self.setUp()

    def test_job_titles_must_come_from_the_generator(self):
        for bad in ["Data Analyst at Acme", "Senior Data Analyst", "data analyst", "Analyst (Remote, Austin)", ""]:
            with self.subTest(title=bad):
                with self.assertRaises(g.GuardError):
                    g.validate_snapshot(self.apps_with(title=bad), fx.GENERATOR)
                self.setUp()

    def event_with_note(self, note):
        events = fx.default_events()
        events[1][8] = note
        return fx.make_snapshot(self.root, events=events)

    def test_free_text_notes_that_look_like_pii_are_refused_with_a_clear_reason(self):
        cases = {
            "email me at jane@example.com": "email",
            "see https://linkedin.com/in/someone": "web address",
            "see www.example.org": "web address",
            "call 512-555-0134": "phone",
            "call +54 11 5555 1234": "phone",
        }
        for note, reason in cases.items():
            with self.subTest(note=note):
                with self.assertRaises(g.GuardError) as ctx:
                    g.validate_snapshot(self.event_with_note(note), fx.GENERATOR)
                self.assertIn(reason, str(ctx.exception))
                self.setUp()

    def test_any_other_free_text_note_is_refused_too(self):
        for note in ["talked to the recruiter", "Synthetic Seed", "synthetic seed ", "hello"]:
            with self.subTest(note=note):
                with self.assertRaises(g.GuardError):
                    g.validate_snapshot(self.event_with_note(note), fx.GENERATOR)
                self.setUp()

    def test_the_two_allowed_notes_and_an_empty_note_pass(self):
        self.validate(fx.make_snapshot(self.root))

    def test_unknown_statuses_and_sources_are_refused(self):
        for index, value, fragment in [(3, "Hired", "new_status"), (2, "Offered", "previous_status"), (6, "Scraped", "source")]:
            with self.subTest(field=fragment):
                events = fx.default_events()
                events[1][index] = value
                with self.assertRaises(g.GuardError) as ctx:
                    g.validate_snapshot(fx.make_snapshot(self.root, events=events), fx.GENERATOR)
                self.assertIn(fragment, str(ctx.exception))
                self.setUp()


class RowConsistencyTests(GuardTestCase):
    def test_duplicate_application_ids_are_refused(self):
        rows = fx.default_apps()
        rows[1][0] = "1"
        self.assertRefused(fx.make_snapshot(self.root, apps=rows), "duplicate")

    def test_an_event_for_an_unknown_application_is_refused(self):
        events = fx.default_events()
        events[4][1] = "99"
        self.assertRefused(fx.make_snapshot(self.root, events=events), "not in applications.csv")

    def test_event_sequences_must_be_dense_from_one(self):
        events = fx.default_events()
        events[2][0] = "7"
        self.assertRefused(fx.make_snapshot(self.root, events=events), "dense")

    def test_a_correction_must_point_at_an_earlier_event(self):
        events = fx.default_events()
        events[2][7] = "9"
        self.assertRefused(fx.make_snapshot(self.root, events=events), "earlier event")

    def test_timestamps_must_be_utc_microsecond_text(self):
        for bad in ["2026-10-01T00:00:00Z", "2026-10-01 00:00:00.000000Z", "2026-13-01T00:00:00.000000Z"]:
            with self.subTest(ts=bad):
                events = fx.default_events()
                events[0][4] = bad
                with self.assertRaises(g.GuardError):
                    g.validate_snapshot(fx.make_snapshot(self.root, events=events), fx.GENERATOR)
                self.setUp()

    def test_dates_must_be_real(self):
        rows = fx.default_apps()
        rows[0][3] = "2026-02-30"
        self.assertRefused(fx.make_snapshot(self.root, apps=rows), "real date")


class TitleListTests(GuardTestCase):
    def test_a_missing_generator_file_fails_closed(self):
        with self.assertRaises(g.GuardError):
            g.load_allowed_titles(self.root / "missing.py")

    def test_a_generator_without_the_title_table_fails_closed(self):
        path = self.root / "gen.py"
        path.write_text("OTHER = 1\n", encoding="utf-8")

        with self.assertRaises(g.GuardError):
            g.load_allowed_titles(path)

    def test_the_title_table_is_read_without_running_the_file(self):
        path = self.root / "gen.py"
        path.write_text("raise SystemExit('must not run')\nTITLE_BY_TYPE = {'a': 'Data Analyst'}\n", encoding="utf-8")

        self.assertEqual({"Data Analyst"}, g.load_allowed_titles(path))


class OAuthOnlyTests(GuardTestCase):
    def test_a_token_in_the_environment_is_refused(self):
        for name in ["DATABRICKS_TOKEN", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_PASSWORD"]:
            with self.subTest(name=name):
                with self.assertRaises(g.GuardError):
                    g.ensure_oauth_only({name: "abc"}, self.root / "none.cfg", "jobtracker-demo")

    def test_an_empty_environment_and_a_missing_config_pass(self):
        g.ensure_oauth_only({}, self.root / "none.cfg", "jobtracker-demo")

    def test_a_profile_with_a_token_is_refused(self):
        cfg = self.root / ".databrickscfg"
        cfg.write_text("[jobtracker-demo]\nhost = https://example.cloud.databricks.com\ntoken = dapi123\n", encoding="utf-8")

        with self.assertRaises(g.GuardError):
            g.ensure_oauth_only({}, cfg, "jobtracker-demo")

    def test_a_token_in_a_different_profile_is_not_this_profiles_problem(self):
        cfg = self.root / ".databrickscfg"
        cfg.write_text("[other]\ntoken = dapi123\n\n[jobtracker-demo]\nhost = https://x\nauth_type = databricks-cli\n", encoding="utf-8")

        g.ensure_oauth_only({}, cfg, "jobtracker-demo")


if __name__ == "__main__":
    unittest.main()


class OptionalFieldTests(GuardTestCase):
    """Contract v2: job_type, resume_variant, priority, referral_type and location (columns 4 to 8)."""

    def apps_with(self, index, value):
        rows = fx.default_apps()
        rows[0][index] = value
        return fx.make_snapshot(self.root, apps=rows)

    def test_empty_fields_are_allowed_because_null_is_empty(self):
        rows = fx.default_apps()
        for row in rows:
            row[4:9] = [""] * 5

        self.validate(fx.make_snapshot(self.root, apps=rows))

    def test_every_allowed_value_passes(self):
        for index, values in [(4, g.JOB_TYPES), (5, g.RESUME_VARIANTS), (6, g.PRIORITIES), (7, g.REFERRAL_TYPES)]:
            for value in sorted(values):
                with self.subTest(column=g.APPLICATION_COLUMNS[index], value=value):
                    self.validate(self.apps_with(index, value))
                    self.setUp()

    def test_values_outside_the_allowed_sets_are_refused(self):
        cases = [(4, "Data Analyst"), (4, "Astronaut"), (5, "Unspecified"), (5, "datanalytics"),
                 (6, "Medium Priority"), (6, "Unspecified"), (7, "Referral"), (7, "coldapply")]
        for index, bad in cases:
            with self.subTest(column=g.APPLICATION_COLUMNS[index], value=bad):
                with self.assertRaises(g.GuardError):
                    self.validate(self.apps_with(index, bad))
                self.setUp()

    def test_the_allowed_enum_names_match_the_csharp_enums(self):
        # Reads the C# source so the two lists cannot drift apart silently.
        domain = Path(__file__).resolve().parents[2] / "src" / "JobTracker.Domain"
        import re
        for name, allowed in [("JobType", g.JOB_TYPES), ("ResumeVariant", g.RESUME_VARIANTS),
                              ("Priority", g.PRIORITIES), ("ReferralType", g.REFERRAL_TYPES)]:
            text = (domain / f"{name}.cs").read_text(encoding="utf-8")
            body = text[text.index("{", text.index(f"enum {name}")) + 1:]
            members = set(re.findall(r"^\s*(\w+)\s*(?:=\s*\d+)?\s*,?\s*(?://.*)?$", body.split("}")[0], re.MULTILINE))
            with self.subTest(enum=name):
                self.assertEqual(members, allowed)

    def test_location_must_be_a_city_the_generator_can_produce(self):
        self.validate(self.apps_with(8, "Seattle, WA"))
        for bad in ["Austin", "Austin, TX ", "1600 Pennsylvania Ave, Washington, DC", "Springfield, IL", "remote (us)"]:
            with self.subTest(location=bad):
                self.setUp()
                with self.assertRaises(g.GuardError):
                    self.validate(self.apps_with(8, bad))

    def test_location_with_an_email_is_refused_with_a_clear_reason(self):
        self.assertRefused(self.apps_with(8, "me@example.com"), "email address")

    def test_every_city_the_generator_can_produce_is_allowed(self):
        cities = g.load_allowed_locations(fx.GENERATOR)

        self.assertEqual(12, len(cities))
        self.assertIn("Remote (US)", cities)
