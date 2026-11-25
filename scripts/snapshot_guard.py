"""Safety checks for a snapshot before anything leaves this machine (Phase 3).

Everything here fails closed: if a check cannot be completed, the snapshot is refused.
Standard library only. See docs/snapshot-contract.md for the contract these checks enforce.
"""
import ast
import csv
import hashlib
import io
import json
import os
import re
from datetime import date, datetime
from pathlib import Path

CONTRACT_VERSION = "2"
LANE = "synthetic"
ALLOWED_FILES = {"applications.csv", "status_events.csv", "manifest.json"}
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")

APPLICATION_COLUMNS = ["application_id", "company_name", "job_title", "date_applied", "job_type", "resume_variant",
                       "priority", "referral_type", "location", "expected_projected_status", "expected_is_no_reply"]
APPLICATION_TYPES = ["int64", "string", "string", "date", "string", "string", "string", "string", "string", "string", "int"]
EVENT_COLUMNS = ["event_sequence", "application_id", "previous_status", "new_status", "effective_at",
                 "recorded_at", "source", "supersedes_event_sequence", "note"]
EVENT_TYPES = ["int64", "int64", "string", "string", "timestamp_utc_us", "timestamp_utc_us", "string", "int64", "string"]

STATUSES = {"NotStarted", "Applied", "InterviewScheduled", "Interviewed", "Rejected", "Withdrawn", "Accepted"}
SOURCES = {"Manual", "Import", "Correction"}
# Enum names of the app (src/JobTracker.Domain). A Python test reads the C# files to keep these in step.
JOB_TYPES = {"DataAnalyst", "BiAnalyst", "BusinessAnalyst", "DataScientist", "MlAiEngineer", "DataEngineer",
             "SoftwareEngineer", "NewGradProgram", "Other"}
RESUME_VARIANTS = {"DataAnalytics", "BackendSwe", "BusinessIntelligence", "MlDataScience", "DataEngineering",
                   "Automation", "FrontendSwe"}
PRIORITIES = {"TopPriority", "MediumPriority", "LowPriority"}
REFERRAL_TYPES = {"NetworkingContact", "ColdApply"}
ALLOWED_NOTES = {"", "synthetic seed", "synthetic correction"}
COMPANY = re.compile(r"Company \d{3}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
HEX64 = re.compile(r"[0-9a-f]{64}")
INTEGER = re.compile(r"\d{1,18}")

MEASURE_COUNTS = ["Applications", "Responses", "Interviews", "Offers", "Rejections", "No_Replies", "Rows_with_interview_date"]
MEASURE_RATES = ["Responses_Rate", "Interviews_Rate", "Offers_Rate", "Rejections_Rate", "No_Replies_Rate"]
PROBLEM_CODES = ["ILLEGAL_FIRST", "FIRST_PREVIOUS_NOT_NULL", "ILLEGAL_TRANSITION", "PREVIOUS_MISMATCH", "CORRECTION_TARGET_INVALID"]

# Things that must never appear in free text, checked first so the error says what was found.
SUSPICIOUS = [
    ("an email address", re.compile(r"[^@\s]+@[^@\s]+")),
    ("a web address", re.compile(r"https?://|www\.|\b\w+\.(com|net|org|io|edu|gov)\b", re.IGNORECASE)),
    ("a phone number", re.compile(r"\+?\d[\d\s().-]{7,}\d")),
]


class GuardError(Exception):
    """The snapshot (or the environment) is not safe to upload."""


def load_allowed_titles(generator_path: Path) -> set:
    """Job titles the synthetic generator can produce. Read without importing it. Fails closed."""
    try:
        tree = ast.parse(Path(generator_path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise GuardError(f"Cannot read the title list from {generator_path}: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TITLE_BY_TYPE" for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError as exc:
                raise GuardError("TITLE_BY_TYPE is not a plain literal") from exc
            titles = set(value.values())
            if not titles or not all(isinstance(t, str) for t in titles):
                raise GuardError("TITLE_BY_TYPE holds no usable titles")
            return titles
    raise GuardError(f"TITLE_BY_TYPE not found in {generator_path}")


def load_allowed_locations(generator_path: Path) -> set:
    """Cities the synthetic generator can produce (its CITIES list). Read without importing it. Fails closed."""
    try:
        tree = ast.parse(Path(generator_path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise GuardError(f"Cannot read the city list from {generator_path}: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CITIES" for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError as exc:
                raise GuardError("CITIES is not a plain literal") from exc
            if not value or not all(isinstance(c, str) for c in value):
                raise GuardError("CITIES holds no usable cities")
            return set(value)
    raise GuardError(f"CITIES not found in {generator_path}")


def scan_free_text(value: str, label: str) -> None:
    for what, pattern in SUSPICIOUS:
        if pattern.search(value):
            raise GuardError(f"{label} looks like it contains {what}")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_keys(obj, expected, label):
    if not isinstance(obj, dict):
        raise GuardError(f"{label} must be an object")
    keys = list(obj.keys())
    if sorted(keys) != sorted(expected):
        extra = sorted(set(keys) - set(expected))
        missing = sorted(set(expected) - set(keys))
        raise GuardError(f"{label} has the wrong fields (unknown: {extra}, missing: {missing})")


def _read_csv(path: Path, expected_columns: list, label: str) -> list:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise GuardError(f"{label} has a byte order mark")
    if b"\r" in raw:
        raise GuardError(f"{label} has carriage returns; the contract is LF only")
    if not raw.endswith(b"\n"):
        raise GuardError(f"{label} must end with a newline")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GuardError(f"{label} is not valid UTF-8") from exc
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise GuardError(f"{label} is not valid CSV: {exc}") from exc
    if not rows or rows[0] != expected_columns:
        raise GuardError(f"{label} has the wrong columns. Expected {expected_columns}, found {rows[0] if rows else 'nothing'}")
    for number, row in enumerate(rows[1:], start=2):
        if len(row) != len(expected_columns):
            raise GuardError(f"{label} line {number} has {len(row)} fields, expected {len(expected_columns)}")
    return rows[1:]


def _valid_date(text: str, label: str) -> None:
    if not DATE.fullmatch(text):
        raise GuardError(f"{label} is not a date: {text!r}")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise GuardError(f"{label} is not a real date: {text!r}") from exc


def _valid_timestamp(text: str, label: str) -> None:
    if not TIMESTAMP.fullmatch(text):
        raise GuardError(f"{label} is not a UTC microsecond timestamp: {text!r}")
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise GuardError(f"{label} is not a real timestamp: {text!r}") from exc


def _check_applications(rows: list, titles: set, locations: set) -> set:
    ids = set()
    for number, (app_id, company, title, applied, job_type, variant, priority, referral, location,
                 status, no_reply) in enumerate(rows, start=2):
        where = f"applications.csv line {number}"
        if not INTEGER.fullmatch(app_id):
            raise GuardError(f"{where}: application_id is not a whole number")
        if app_id in ids:
            raise GuardError(f"{where}: duplicate application_id {app_id}")
        ids.add(app_id)
        scan_free_text(company, f"{where} company_name")
        if not COMPANY.fullmatch(company):
            raise GuardError(f"{where}: company_name must look like 'Company 001', found {company!r}")
        scan_free_text(title, f"{where} job_title")
        if title not in titles:
            raise GuardError(f"{where}: job_title {title!r} is not one of the synthetic generator's titles")
        _valid_date(applied, f"{where} date_applied")
        for label, value, allowed in [("job_type", job_type, JOB_TYPES), ("resume_variant", variant, RESUME_VARIANTS),
                                      ("priority", priority, PRIORITIES), ("referral_type", referral, REFERRAL_TYPES)]:
            if value != "" and value not in allowed:
                raise GuardError(f"{where}: unknown {label} {value!r}")
        scan_free_text(location, f"{where} location")
        if location != "" and location not in locations:
            raise GuardError(f"{where}: location {location!r} is not one of the synthetic generator's cities")
        if status != "" and status not in STATUSES:
            raise GuardError(f"{where}: unknown expected_projected_status {status!r}")
        if no_reply not in ("0", "1"):
            raise GuardError(f"{where}: expected_is_no_reply must be 0 or 1")
    return ids


def _check_events(rows: list, application_ids: set) -> None:
    seen = set()
    for number, (seq, app_id, prev, new, effective, recorded, source, supersedes, note) in enumerate(rows, start=2):
        where = f"status_events.csv line {number}"
        if not INTEGER.fullmatch(seq):
            raise GuardError(f"{where}: event_sequence is not a whole number")
        if int(seq) != number - 1:
            raise GuardError(f"{where}: event_sequence must be dense from 1 (expected {number - 1}, found {seq})")
        if app_id not in application_ids:
            raise GuardError(f"{where}: application_id {app_id} is not in applications.csv")
        if prev != "" and prev not in STATUSES:
            raise GuardError(f"{where}: unknown previous_status {prev!r}")
        if new not in STATUSES:
            raise GuardError(f"{where}: unknown new_status {new!r}")
        _valid_timestamp(effective, f"{where} effective_at")
        _valid_timestamp(recorded, f"{where} recorded_at")
        if source not in SOURCES:
            raise GuardError(f"{where}: unknown source {source!r}")
        if supersedes != "":
            if not INTEGER.fullmatch(supersedes) or int(supersedes) not in seen:
                raise GuardError(f"{where}: supersedes_event_sequence must point at an earlier event")
        scan_free_text(note, f"{where} note")
        if note not in ALLOWED_NOTES:
            raise GuardError(f"{where}: note {note!r} is not one of the allowed synthetic notes")
        seen.add(int(seq))


def _check_manifest_shape(manifest: dict) -> None:
    _check_keys(manifest, ["contract_version", "lane", "snapshot_id", "as_of", "exported_at", "files", "applications",
                           "expected_measures", "expected_problems", "gold_hash"], "manifest")
    if manifest["contract_version"] != CONTRACT_VERSION:
        raise GuardError(f"Unsupported contract_version {manifest['contract_version']!r}")
    if manifest["lane"] != LANE:
        raise GuardError(f"Lane must be {LANE!r}, found {manifest['lane']!r}. Only synthetic data may be uploaded.")
    for key in ("as_of", "exported_at"):
        _valid_timestamp(str(manifest[key]), f"manifest {key}")

    files = manifest["files"]
    if not isinstance(files, list) or len(files) != 2:
        raise GuardError("manifest files must list exactly applications.csv and status_events.csv")
    for entry in files:
        _check_keys(entry, ["name", "columns", "rows", "sha256"], "manifest file entry")
        if not _is_int(entry["rows"]) or entry["rows"] < 0:
            raise GuardError("manifest file rows must be a whole number")
        if not isinstance(entry["sha256"], str) or not HEX64.fullmatch(entry["sha256"]):
            raise GuardError("manifest sha256 must be 64 lowercase hex characters")

    apps = manifest["applications"]
    _check_keys(apps, ["accepted", "rejected", "rejected_reasons"], "manifest applications")
    _check_keys(apps["rejected_reasons"], ["NO_EVENTS", "TIMESTAMP_PRECISION"], "manifest rejected_reasons")
    for value in (apps["accepted"], apps["rejected"], *apps["rejected_reasons"].values()):
        if not _is_int(value) or value < 0:
            raise GuardError("manifest application counts must be whole numbers")

    measures = manifest["expected_measures"]
    _check_keys(measures, MEASURE_COUNTS + ["Avg_Days_to_Interview"] + MEASURE_RATES, "manifest expected_measures")
    for key in MEASURE_COUNTS:
        if not _is_int(measures[key]) or measures[key] < 0:
            raise GuardError(f"expected_measures {key} must be a whole number")
    avg = measures["Avg_Days_to_Interview"]
    if avg is not None and not (isinstance(avg, str) and re.fullmatch(r"\d+\.\d{2}", avg)):
        raise GuardError("expected_measures Avg_Days_to_Interview must be null or a 2-decimal string")
    for key in MEASURE_RATES:
        rate = measures[key]
        if rate is not None and not (isinstance(rate, str) and re.fullmatch(r"\d+\.\d{4}", rate)):
            raise GuardError(f"expected_measures {key} must be null or a 4-decimal string")

    problems = manifest["expected_problems"]
    _check_keys(problems, ["total", "by_code"], "manifest expected_problems")
    _check_keys(problems["by_code"], PROBLEM_CODES, "manifest expected_problems by_code")
    if not all(_is_int(v) and v >= 0 for v in problems["by_code"].values()) or problems["total"] != sum(problems["by_code"].values()):
        raise GuardError("expected_problems counts are inconsistent")

    gold = manifest["gold_hash"]
    _check_keys(gold, ["algorithm", "definition", "gold_application_status", "gold_funnel_summary",
                       "gold_funnel_by_dimension", "gold_funnel_by_dimension_rows"], "manifest gold_hash")
    if gold["algorithm"] != "sha256":
        raise GuardError("gold_hash algorithm must be sha256")
    if not isinstance(gold["definition"], str) or not re.fullmatch(r"[A-Za-z0-9 ,./_-]{1,200}", gold["definition"]):
        raise GuardError("gold_hash definition has unexpected characters")
    for key in ("gold_application_status", "gold_funnel_summary", "gold_funnel_by_dimension"):
        if not isinstance(gold[key], str) or not HEX64.fullmatch(gold[key]):
            raise GuardError(f"gold_hash {key} must be 64 lowercase hex characters")
    if not _is_int(gold["gold_funnel_by_dimension_rows"]) or gold["gold_funnel_by_dimension_rows"] < 0:
        raise GuardError("gold_hash gold_funnel_by_dimension_rows must be a whole number, zero or more")


def validate_snapshot(folder, generator_path) -> dict:
    """Return the manifest if the snapshot is safe to upload, otherwise raise GuardError."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise GuardError(f"{folder} is not a plain folder")

    snapshot_id = folder.name
    if not SAFE_ID.fullmatch(snapshot_id):
        raise GuardError(f"Unsafe snapshot id {snapshot_id!r}: use 3-64 characters a-z, 0-9 and '-', not starting with '-'")

    entries = list(folder.iterdir())
    names = {e.name for e in entries}
    if names != ALLOWED_FILES:
        raise GuardError(f"The snapshot folder must contain exactly {sorted(ALLOWED_FILES)}; found {sorted(names)}")
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise GuardError(f"{entry.name} is not a regular file")

    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardError(f"manifest.json is not valid JSON: {exc}") from exc
    _check_manifest_shape(manifest)

    if manifest["snapshot_id"] != snapshot_id:
        raise GuardError(f"manifest snapshot_id {manifest['snapshot_id']!r} does not match the folder name {snapshot_id!r}")

    expected = {"applications.csv": (APPLICATION_COLUMNS, APPLICATION_TYPES), "status_events.csv": (EVENT_COLUMNS, EVENT_TYPES)}
    by_name = {entry["name"]: entry for entry in manifest["files"]}
    if set(by_name) != set(expected):
        raise GuardError("manifest files must be exactly applications.csv and status_events.csv")

    parsed = {}
    for name, (columns, types) in expected.items():
        entry = by_name[name]
        schema = [(c.get("name"), c.get("type")) for c in entry["columns"] if isinstance(c, dict)]
        if schema != list(zip(columns, types)):
            raise GuardError(f"manifest schema for {name} does not match the contract")
        actual = sha256_hex((folder / name).read_bytes())
        if actual != entry["sha256"]:
            raise GuardError(f"{name} does not match its recorded SHA-256 (the file or the manifest was changed)")
        rows = _read_csv(folder / name, columns, name)
        if len(rows) != entry["rows"]:
            raise GuardError(f"{name} has {len(rows)} rows but the manifest says {entry['rows']}")
        parsed[name] = rows

    app_ids = _check_applications(parsed["applications.csv"], load_allowed_titles(generator_path),
                              load_allowed_locations(generator_path))
    _check_events(parsed["status_events.csv"], app_ids)

    if manifest["applications"]["accepted"] != len(parsed["applications.csv"]):
        raise GuardError("manifest accepted count does not match applications.csv")
    return manifest


def ensure_oauth_only(env: dict, config_path, profile: str) -> None:
    """No personal access tokens anywhere in play: not in the environment, not in the CLI profile."""
    for name in ("DATABRICKS_TOKEN", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_PASSWORD"):
        if env.get(name):
            raise GuardError(f"{name} is set. This pipeline uses browser OAuth only, never tokens or secrets.")

    path = Path(config_path)
    if not path.exists():
        return
    section = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
        elif section == profile and "=" in line:
            key = line.split("=", 1)[0].strip().lower()
            if key in ("token", "client_secret", "password"):
                raise GuardError(f"Profile {profile!r} in {path.name} holds a {key}. Use browser OAuth only.")
