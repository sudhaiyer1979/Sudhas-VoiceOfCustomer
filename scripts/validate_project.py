#!/usr/bin/env python3
"""Validate the Voice of Customer project structure and pipeline outputs.

Checks (see CLAUDE.md for the authoritative description):

1. That scripts/, data/, and output/ exist.
2. That CLAUDE.md and README.md exist.
3. That each pipeline script under scripts/ exists.
4. For each data/*.json file that exists, that its required top-level
   fields (and, where applicable, per-item fields) are present.

A pipeline script or data file that hasn't been generated yet is reported
as PENDING, not an error -- the pipeline is expected to be built up over
multiple workstreams. This script only exits non-zero when core project
scaffolding is missing, or when a data file that DOES exist is missing
required fields (i.e. it doesn't match the documented schema).

Usage:
    python3 scripts/validate_project.py
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_DIRS = ["scripts", "data", "output"]
REQUIRED_PROJECT_FILES = ["CLAUDE.md", "README.md", "scripts/validate_project.py"]

# Pipeline stage -> (script path, data file path, schema)
# schema = {
#   "top_level": [required top-level keys],
#   "list_field": name of a top-level key holding a list of items, or None,
#   "item_fields": [required keys on each item of list_field], or None,
# }
PIPELINE = [
    {
        "script": "scripts/collect_reviews.py",
        "data_file": "data/reviews.json",
        "schema": {
            "top_level": ["game_name", "source_url", "app_id", "review_count", "reviews"],
            "list_field": "reviews",
            "item_fields": ["review_id", "text", "recommended", "date"],
        },
    },
    {
        "script": "scripts/collect_marketing.py",
        "data_file": "data/marketing.json",
        "schema": {
            "top_level": ["game_name", "source_url", "claims"],
            "list_field": "claims",
            "item_fields": ["claim_id", "text"],
        },
    },
    {
        "script": "scripts/find_themes.py",
        "data_file": "data/review_themes.json",
        "schema": {
            "top_level": ["game_name", "themes"],
            "list_field": "themes",
            "item_fields": [
                "theme_id", "theme", "sentiment", "mention_count",
                "keywords", "example_review_ids", "quotes",
            ],
        },
    },
    {
        "script": "scripts/find_gaps.py",
        "data_file": "data/gaps.json",
        "schema": {
            "top_level": ["hidden_strengths", "marketing_disconnects"],
            "list_field": None,
            "item_fields": None,
        },
    },
    {
        "script": "scripts/vocab_gap.py",
        "data_file": "data/vocabulary.json",
        "schema": {
            "top_level": ["review_only_terms", "marketing_only_terms", "same_idea_different_words"],
            "list_field": None,
            "item_fields": None,
        },
    },
    {
        "script": "scripts/build_dashboard.py",
        "data_file": "output/dashboard.html",
        "schema": None,  # not JSON; existence-only check
    },
]


class Report:
    def __init__(self):
        self.errors = []
        self.pending = []
        self.ok = []

    def error(self, msg):
        self.errors.append(msg)

    def pending_item(self, msg):
        self.pending.append(msg)

    def pass_item(self, msg):
        self.ok.append(msg)


def check_dirs(report: Report):
    print("== Directories ==")
    for d in REQUIRED_DIRS:
        path = PROJECT_ROOT / d
        if path.is_dir():
            print(f"  OK      {d}/")
            report.pass_item(f"dir:{d}")
        else:
            print(f"  MISSING {d}/")
            report.error(f"Required directory missing: {d}/")


def check_project_files(report: Report):
    print("\n== Core project files ==")
    for f in REQUIRED_PROJECT_FILES:
        path = PROJECT_ROOT / f
        if path.is_file():
            print(f"  OK      {f}")
            report.pass_item(f"file:{f}")
        else:
            print(f"  MISSING {f}")
            report.error(f"Required file missing: {f}")


def check_pipeline_scripts(report: Report):
    print("\n== Pipeline scripts ==")
    for stage in PIPELINE:
        script = stage["script"]
        path = PROJECT_ROOT / script
        if path.is_file():
            print(f"  OK      {script}")
            report.pass_item(f"script:{script}")
        else:
            print(f"  PENDING {script} (not yet implemented)")
            report.pending_item(f"script:{script}")


def validate_json_schema(name, data, schema, report: Report):
    """Validate a loaded JSON document against a schema dict. Returns True if valid."""
    valid = True

    if not isinstance(data, dict):
        report.error(f"{name}: expected a JSON object at the top level, got {type(data).__name__}")
        return False

    missing_top = [k for k in schema["top_level"] if k not in data]
    if missing_top:
        report.error(f"{name}: missing required top-level field(s): {', '.join(missing_top)}")
        valid = False

    list_field = schema.get("list_field")
    item_fields = schema.get("item_fields")
    if list_field and item_fields and list_field in data:
        items = data[list_field]
        if not isinstance(items, list):
            report.error(f"{name}: field '{list_field}' should be a list, got {type(items).__name__}")
            valid = False
        else:
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    report.error(f"{name}: {list_field}[{i}] should be an object, got {type(item).__name__}")
                    valid = False
                    continue
                missing_item = [k for k in item_fields if k not in item]
                if missing_item:
                    report.error(
                        f"{name}: {list_field}[{i}] (id={item.get(item_fields[0], '?')}) "
                        f"missing required field(s): {', '.join(missing_item)}"
                    )
                    valid = False

    return valid


def check_data_files(report: Report):
    print("\n== Pipeline data / output files ==")
    for stage in PIPELINE:
        data_file = stage["data_file"]
        schema = stage["schema"]
        path = PROJECT_ROOT / data_file

        if not path.is_file():
            print(f"  PENDING {data_file} (not yet generated)")
            report.pending_item(f"data:{data_file}")
            continue

        if schema is None:
            # Non-JSON output (e.g. the dashboard HTML): existence is enough.
            print(f"  OK      {data_file}")
            report.pass_item(f"data:{data_file}")
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except json.JSONDecodeError as e:
            print(f"  INVALID {data_file} (malformed JSON: {e})")
            report.error(f"{data_file}: malformed JSON: {e}")
            continue

        if validate_json_schema(data_file, doc, schema, report):
            count = ""
            if schema.get("list_field") and schema["list_field"] in doc:
                count = f" ({len(doc[schema['list_field']])} {schema['list_field']})"
            print(f"  OK      {data_file}{count}")
            report.pass_item(f"data:{data_file}")
        else:
            print(f"  INVALID {data_file} (see errors below)")


def main():
    report = Report()

    check_dirs(report)
    check_project_files(report)
    check_pipeline_scripts(report)
    check_data_files(report)

    print("\n== Summary ==")
    print(f"  Passed:  {len(report.ok)}")
    print(f"  Pending: {len(report.pending)}")
    print(f"  Errors:  {len(report.errors)}")

    if report.errors:
        print("\nErrors:")
        for e in report.errors:
            print(f"  - {e}")
        print("\nFAIL: validation found errors.")
        sys.exit(1)

    if report.pending:
        print("\nPending (not yet built, not an error):")
        for p in report.pending:
            print(f"  - {p}")

    print("\nPASS: no errors found.")
    sys.exit(0)


if __name__ == "__main__":
    main()
