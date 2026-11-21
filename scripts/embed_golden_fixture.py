"""Embeds tests/contract/projection_cases_v1.json into the Databricks notebook's self-test.

The notebook is uploaded as a single file, so the fixture has to live inside it.
    python scripts/embed_golden_fixture.py            rewrite the embedded copy
    python scripts/embed_golden_fixture.py --check    exit 1 if the embedded copy differs from the JSON file
Standard library only.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "contract" / "projection_cases_v1.json"
NOTEBOOK = REPO / "databricks" / "notebooks" / "02_snapshot_pipeline.py"
BLOCK = re.compile(r"(# BEGIN GOLDEN FIXTURE[^\n]*\n)GOLDEN_FIXTURE_JSON = r'''(.*?)'''\n(# END GOLDEN FIXTURE)", re.DOTALL)


def render(fixture: dict) -> str:
    """Compact JSON, one case per line, so diffs stay readable."""
    cases = ",\n".join(json.dumps(case, separators=(",", ":"), ensure_ascii=False) for case in fixture["cases"])
    head = json.dumps({k: v for k, v in fixture.items() if k != "cases"}, separators=(",", ":"), ensure_ascii=False)[:-1]
    return f'{head},"cases":[\n{cases}\n]}}'


def embedded_json(notebook_text: str) -> str:
    match = BLOCK.search(notebook_text)
    if not match:
        raise SystemExit("Could not find the GOLDEN FIXTURE block in the notebook.")
    return match.group(2)


def main(argv) -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    text = NOTEBOOK.read_text(encoding="utf-8")
    new_block = render(fixture)
    if "'''" in new_block:
        raise SystemExit("The fixture contains ''' which cannot be embedded.")

    if "--check" in argv:
        same = json.loads(embedded_json(text)) == fixture
        print("embedded fixture matches" if same else "embedded fixture is STALE: run scripts/embed_golden_fixture.py")
        return 0 if same else 1

    updated = BLOCK.sub(lambda m: f"{m.group(1)}GOLDEN_FIXTURE_JSON = r'''{new_block}'''\n{m.group(3)}", text, count=1)
    NOTEBOOK.write_text(updated, encoding="utf-8", newline="\n")
    print(f"embedded {len(fixture['cases'])} cases ({len(new_block):,} characters)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
