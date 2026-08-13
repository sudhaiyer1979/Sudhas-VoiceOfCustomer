#!/usr/bin/env python3
"""Voice of Customer Brand Analyzer -- a small local Flask wrapper around
the existing WS1-WS7 pipeline plus the WS8 marketing-insight layer.

Start with:
    python app.py
then open:
    http://127.0.0.1:5000

This file only orchestrates the EXISTING scripts (collect_reviews.py,
collect_marketing.py, find_themes.py, vocab_gap.py, find_gaps.py,
prepare_marketing_insights.py, build_marketing_dashboard.py) as
subprocesses -- none of them are rewritten or modified here.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from flask import Flask, redirect, render_template_string, request, send_file, url_for

PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = PROJECT_ROOT / "output"
MARKETING_DASHBOARD_PATH = OUTPUT_DIR / "marketing_dashboard.html"

RUST_APP_ID = "252490"  # the guaranteed, pre-cached demo path (see WS8 spec Part 9)
REQUIRED_CACHE_FILES = ["reviews.json", "marketing.json", "review_themes.json", "gaps.json", "vocabulary.json"]

sys.path.insert(0, str(SCRIPTS_DIR))
from resolve_game import ResolutionError, resolve_game  # noqa: E402
from validate_project import PIPELINE as VALIDATE_PIPELINE  # noqa: E402
from validate_project import Report, validate_json_schema  # noqa: E402

app = Flask(__name__)

SCHEMA_BY_FILENAME = {
    Path(stage["data_file"]).name: stage["schema"]
    for stage in VALIDATE_PIPELINE
    if stage["schema"] is not None
}


class PipelineError(Exception):
    """Raised when a subprocess step fails. Carries the real stdout/stderr."""

    def __init__(self, step: str, detail: str):
        super().__init__(detail)
        self.step = step
        self.detail = detail


def is_cache_valid(app_id: str) -> bool:
    cache_dir = CACHE_DIR / app_id
    for filename in REQUIRED_CACHE_FILES:
        path = cache_dir / filename
        if not path.is_file():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        schema = SCHEMA_BY_FILENAME.get(filename)
        if schema is not None:
            report = Report()
            if not validate_json_schema(filename, doc, schema, report):
                return False
    return True


def run_script(args, input_text=None, timeout=600):
    """Run one of the existing pipeline scripts as a subprocess.
    Raises PipelineError with the real stdout/stderr on failure."""
    result = subprocess.run(
        [sys.executable, *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "No output.").strip()
        raise PipelineError(step=str(args[0]), detail=detail)
    return result


def backup_top_level_data():
    return {name: (DATA_DIR / name).read_bytes() for name in REQUIRED_CACHE_FILES if (DATA_DIR / name).is_file()}


def restore_top_level_data(backup: dict):
    for name, content in backup.items():
        (DATA_DIR / name).write_bytes(content)


def collect_fresh_data(resolved, steps):
    """Runs the existing WS2-WS6 scripts, in the order specified for WS8,
    against the top-level data/ files, then copies the results into the
    per-game cache. Restores Rust's canonical top-level data afterward so
    the original WS7 dashboard's evidence is never disturbed."""
    stdin_text = f"{resolved.source_url}\n{resolved.game_name}\n"
    steps.append("→ No cached analysis found -- collecting fresh data from Steam")

    run_script(["scripts/collect_reviews.py"], input_text=stdin_text, timeout=900)
    steps.append("✓ Collected real player reviews from Steam")

    run_script(["scripts/collect_marketing.py"], input_text=stdin_text, timeout=120)
    steps.append("✓ Collected real marketing claims from Steam")

    run_script(["scripts/find_themes.py", "--input", "data/reviews.json",
                "--output", "data/review_themes.json"], timeout=120)
    steps.append("✓ Identified major player themes")

    run_script(["scripts/vocab_gap.py", "--reviews", "data/reviews.json",
                "--marketing", "data/marketing.json", "--output", "data/vocabulary.json"], timeout=120)
    steps.append("✓ Analyzed marketing vs. player vocabulary")

    run_script(["scripts/find_gaps.py", "--themes", "data/review_themes.json",
                "--marketing", "data/marketing.json", "--output", "data/gaps.json"], timeout=120)
    steps.append("✓ Identified hidden strengths and marketing disconnects")

    cache_dir = CACHE_DIR / resolved.app_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_CACHE_FILES:
        shutil.copyfile(DATA_DIR / name, cache_dir / name)
    steps.append(f"✓ Saved results to data/cache/{resolved.app_id}/")


def run_analysis(game_name: str, steam_url: str):
    """Orchestrates resolution -> cache check -> pipeline -> insights ->
    dashboard. Returns a dict describing the outcome; never raises for
    expected failure modes (mismatch, missing API key, pipeline errors)
    -- those come back as {"success": False, "error": ...}."""
    steps = []

    try:
        resolved = resolve_game(game_name, steam_url)
    except ResolutionError as e:
        return {"success": False, "error": e.message, "steps": steps}

    steps.append(f"✓ Game verified: {resolved.game_name}")
    if resolved.note:
        steps.append(f"ℹ {resolved.note}")

    cache_dir = CACHE_DIR / resolved.app_id
    backup = None

    try:
        if is_cache_valid(resolved.app_id):
            steps.append("✓ Existing customer review data found")
            steps.append("✓ Existing marketing data found")
            steps.append("✓ Existing analysis found")
        else:
            backup = backup_top_level_data()
            collect_fresh_data(resolved, steps)
            if resolved.app_id != RUST_APP_ID:
                restore_top_level_data(backup)

        steps.append("→ Building marketing recommendations")
        run_script(["scripts/prepare_marketing_insights.py", "--data-dir", str(cache_dir)], timeout=300)
        shutil.copyfile(cache_dir / "marketing_insights.json", DATA_DIR / "marketing_insights.json")

        steps.append("→ Building dashboard")
        run_script(["scripts/build_marketing_dashboard.py", "--data-dir", str(cache_dir),
                    "--output", str(MARKETING_DASHBOARD_PATH)], timeout=60)
        steps.append("✓ Dashboard ready")

    except PipelineError as e:
        if backup is not None and resolved.app_id != RUST_APP_ID:
            restore_top_level_data(backup)
        return {
            "success": False,
            "error": f"{e.step} failed:\n\n{e.detail}",
            "steps": steps,
            "game_name": resolved.game_name,
        }
    except subprocess.TimeoutExpired as e:
        if backup is not None and resolved.app_id != RUST_APP_ID:
            restore_top_level_data(backup)
        return {"success": False, "error": f"Timed out running {e.cmd}.", "steps": steps}

    return {"success": True, "steps": steps, "game_name": resolved.game_name, "app_id": resolved.app_id}


PAGE_CSS = """
:root { --ink:#1a1d23; --muted:#5a6270; --bg:#ffffff; --panel:#f6f7f9; --border:#e1e4e9; --accent:#2f5fdb; }
* { box-sizing: border-box; }
body { margin:0; font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; background:var(--bg); color:var(--ink); font-size:18px; line-height:1.5; }
.page { max-width: 720px; margin: 0 auto; padding: 56px 24px 96px; }
h1 { font-size: 32px; letter-spacing: 0.02em; text-align:center; margin-bottom: 8px; }
.tagline { text-align:center; color: var(--muted); margin-bottom: 40px; }
label { display:block; font-weight:600; margin-bottom: 8px; margin-top: 24px; }
input[type=text] { width:100%; font-size:18px; padding:12px 14px; border:1px solid var(--border); border-radius:8px; }
.hint { color: var(--muted); font-size: 15px; margin-top: 6px; }
button { margin-top: 32px; width:100%; font-size:20px; font-weight:700; padding:16px; border:none; border-radius:10px; background:var(--accent); color:#fff; cursor:pointer; }
button:hover { opacity: 0.92; }
.steps { list-style:none; padding:0; margin: 24px 0; }
.steps li { padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 17px; }
.error-box { background:#fdecea; border:1px solid #f3b4ac; border-radius:10px; padding:20px 22px; margin-top:24px; white-space:pre-wrap; font-family: ui-monospace, monospace; font-size: 15px; color:#7a231d; }
.success-box { background:#eafaf1; border:1px solid #a9e0c1; border-radius:10px; padding:20px 22px; margin-top:24px; text-align:center; font-weight:700; font-size:22px; color:#1e7e4a; }
.view-btn { display:block; text-align:center; margin-top:20px; padding:16px; background:var(--ink); color:#fff; border-radius:10px; text-decoration:none; font-weight:700; font-size:19px; }
.back-link { display:block; text-align:center; margin-top:28px; color: var(--accent); text-decoration:none; }
"""

HOME_TEMPLATE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Voice of Customer Brand Analyzer</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{{ css }}</style></head><body><div class="page">
<h1>VOICE OF CUSTOMER BRAND ANALYZER</h1>
<p class="tagline">Compare what customers say against how the brand is marketed.</p>
<form method="post" action="{{ url_for('analyze') }}">
  <label for="game_name">Game Name</label>
  <input type="text" id="game_name" name="game_name" required placeholder="e.g. Rust">

  <label for="steam_url">Steam URL - optional</label>
  <input type="text" id="steam_url" name="steam_url" placeholder="e.g. https://store.steampowered.com/app/252490">
  <p class="hint">Optional. Version 1 analyzes Steam games.</p>

  <button type="submit">Analyze Brand Perception</button>
</form>
</div></body></html>
"""

RESULT_TEMPLATE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Voice of Customer Brand Analyzer</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{{ css }}</style></head><body><div class="page">
<h1>VOICE OF CUSTOMER BRAND ANALYZER</h1>

{% if steps %}
<ul class="steps">
  {% for step in steps %}<li>{{ step }}</li>{% endfor %}
</ul>
{% endif %}

{% if success %}
  <div class="success-box">ANALYSIS COMPLETE</div>
  <a class="view-btn" href="{{ url_for('view_dashboard') }}" target="_blank">View Marketing Dashboard</a>
{% else %}
  <div class="error-box">{{ error }}</div>
{% endif %}

<a class="back-link" href="{{ url_for('home') }}">&larr; Analyze another game</a>
</div></body></html>
"""


@app.route("/", methods=["GET"])
def home():
    return render_template_string(HOME_TEMPLATE, css=PAGE_CSS)


@app.route("/analyze", methods=["POST"])
def analyze():
    game_name = request.form.get("game_name", "")
    steam_url = request.form.get("steam_url", "")
    result = run_analysis(game_name, steam_url)
    return render_template_string(
        RESULT_TEMPLATE, css=PAGE_CSS,
        success=result["success"],
        steps=result.get("steps", []),
        error=result.get("error") if not result["success"] else None,
    )


@app.route("/view-dashboard", methods=["GET"])
def view_dashboard():
    if not MARKETING_DASHBOARD_PATH.is_file():
        return redirect(url_for("home"))
    return send_file(MARKETING_DASHBOARD_PATH)


if __name__ == "__main__":
    print("Voice of Customer Brand Analyzer starting...")
    print("Open http://127.0.0.1:5000 in your browser.")
    app.run(host="127.0.0.1", port=5000, debug=False)
