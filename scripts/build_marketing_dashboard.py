#!/usr/bin/env python3
"""Build a self-contained marketing-executive dashboard.

Reusable, stdlib-only script. Reads reviews.json, marketing.json,
vocabulary.json, and marketing_insights.json (the LLM-generated,
evidence-validated output of prepare_marketing_insights.py) and renders
output/marketing_dashboard.html: inline CSS only, no JS, no external
stylesheet/font/CDN references, no runtime JSON fetch, no Steam/API
calls. Once generated the file opens by double-clicking it, fully
offline.

Unlike the WS7 analyst dashboard (output/dashboard.html, left
untouched), this one is written for a marketing executive: it leads
with concrete recommendations and human-readable customer perceptions,
never the raw machine-generated theme labels (e.g. "Friends & Solo &
Wipe") produced by find_themes.py.

Every number, quote, and phrase rendered is copied from the source JSON
files; nothing is invented. All embedded text is HTML-escaped.

Usage:
    python3 scripts/build_marketing_dashboard.py --data-dir data/cache/252490
"""

import argparse
import html
import json
import sys
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "output" / "marketing_dashboard.html"

MAX_EVIDENCE_QUOTES = 12
MAX_EVIDENCE_CLAIMS = 12

ACTION_COLORS = {
    "ADD": ("#eaf0ff", "#2f5fdb"),
    "KEEP": ("#eafaf1", "#1e7e4a"),
    "REDUCE": ("#fdecea", "#a4302a"),
    "REFRAME": ("#f5eefc", "#7a3fc4"),
}
ALIGNMENT_META = {
    "ALIGNED": ("Aligned", "#eafaf1", "#1e7e4a", "Customer perception matches current positioning."),
    "UNDERREPRESENTED": ("Underrepresented Opportunity", "#eaf0ff", "#2f5fdb",
                          "Customers strongly value this, but marketing underplays it."),
    "OVEREMPHASIZED": ("Overemphasized in Marketing", "#fff6e5", "#8a5a00",
                        "Marketing strongly emphasizes this; customers rarely discuss it."),
}


def e(value) -> str:
    return html.escape(str(value), quote=True)


def load_json(path: Path):
    if not path.is_file():
        print(f"Error: required data file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def action_word(recommended_action: str) -> str:
    for word in ACTION_COLORS:
        if recommended_action.upper().startswith(word):
            return word
    return "REFRAME"


def action_badge(recommended_action: str) -> str:
    word = action_word(recommended_action)
    bg, fg = ACTION_COLORS[word]
    return f'<span class="badge" style="background:{bg};color:{fg}">{e(word)}</span>'


def alignment_badge(alignment_type: str) -> str:
    label, bg, fg, _ = ALIGNMENT_META.get(alignment_type, (alignment_type, "#eee", "#333", ""))
    return f'<span class="badge" style="background:{bg};color:{fg}">{e(label)}</span>'


def render_hero(game_name, review_count, claim_count, perception_count):
    return f"""
    <header class="hero">
      <h1>{e(game_name)}<br><span class="hero-sub-title">BRAND PERCEPTION GAP</span></h1>
      <p class="subtitle">What customers experience vs. how the brand is positioned</p>
      <div class="summary-grid">
        <div class="stat-card">
          <div class="stat-number">{review_count:,}</div>
          <div class="stat-label">Reviews Analyzed</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">{claim_count:,}</div>
          <div class="stat-label">Marketing Claims Analyzed</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">{perception_count:,}</div>
          <div class="stat-label">Customer Perceptions Identified</div>
        </div>
      </div>
    </header>
    """


def render_recommendations(recommendations):
    if not recommendations:
        return '<p class="empty-note">No prioritized recommendations were generated.</p>'
    cards = []
    for rec in recommendations:
        evidence_count = len(rec["evidence_review_ids"]) + len(rec["evidence_claim_ids"])
        cards.append(f"""
        <div class="card rec-card">
          <div class="rec-top">
            <span class="priority-pill">Priority {e(rec['priority'])}</span>
            {action_badge(rec['recommended_action'])}
          </div>
          <h3>{e(rec['title'])}</h3>
          <div class="rec-row"><span class="rec-label">What customers are telling us</span>
            <p>{e(rec['what_customers_are_saying'])}</p></div>
          <div class="rec-row"><span class="rec-label">What current marketing communicates</span>
            <p>{e(rec['what_marketing_says_today'])}</p></div>
          <div class="rec-row"><span class="rec-label">The gap</span>
            <p>{e(rec['gap'])}</p></div>
          <div class="rec-action-box">
            <span class="rec-label">Recommended action</span>
            <p class="rec-action-text">{e(rec['recommended_action'])}</p>
          </div>
          <div class="copy-direction">
            <span class="rec-label">Suggested messaging direction <em>(not final copy)</em></span>
            <p>&ldquo;{e(rec['suggested_copy_direction'])}&rdquo;</p>
          </div>
          <div class="evidence-count">Backed by {evidence_count} piece(s) of real evidence</div>
        </div>
        """)
    return f'<div class="rec-grid">{"".join(cards)}</div>'


def render_perceptions(perceptions):
    if not perceptions:
        return '<p class="empty-note">No customer perceptions were generated.</p>'
    cards = []
    for p in sorted(perceptions, key=lambda x: -x["mention_count"]):
        quote_html = ""
        if p["supporting_quotes"]:
            quote_html = f'<blockquote class="mini-quote">&ldquo;{e(p["supporting_quotes"][0])}&rdquo;</blockquote>'
        cards.append(f"""
        <div class="card perception-card">
          <div class="card-header">
            <h3>{e(p['customer_perception'])}</h3>
            {alignment_badge(p['alignment_type'])}
          </div>
          <div class="mention-count">{p['mention_count']:,} customer mentions</div>
          <p class="card-note">{e(p['customer_description'])}</p>
          {quote_html}
        </div>
        """)
    return f'<div class="card-grid">{"".join(cards)}</div>'


def render_alignment(perceptions):
    groups = {"ALIGNED": [], "UNDERREPRESENTED": [], "OVEREMPHASIZED": []}
    for p in perceptions:
        groups.setdefault(p["alignment_type"], []).append(p)

    sections = []
    for key in ("ALIGNED", "UNDERREPRESENTED", "OVEREMPHASIZED"):
        label, bg, fg, desc = ALIGNMENT_META[key]
        items = groups.get(key, [])
        rows = "".join(
            f'<li><strong>{e(p["customer_perception"])}</strong>'
            f'<span class="align-meta">{e(p["current_marketing_position"] or "Not addressed in current marketing.")}</span></li>'
            for p in sorted(items, key=lambda x: -x["mention_count"])
        ) or '<li class="empty-note">None identified.</li>'
        sections.append(f"""
        <div class="align-column" style="border-top: 6px solid {fg}">
          <h3 style="color:{fg}">{e(label)}</h3>
          <p class="align-desc">{e(desc)}</p>
          <ul class="align-list">{rows}</ul>
        </div>
        """)
    return f'<div class="align-grid">{"".join(sections)}</div>'


def render_language(same_idea_pairs):
    if not same_idea_pairs:
        return '<p class="empty-note">No side-by-side language comparisons were identified.</p>'
    rows = []
    for pair in same_idea_pairs:
        rows.append(f"""
        <tr>
          <td class="concept-cell">{e(pair['concept'])}</td>
          <td><span class="phrase-chip marketing-chip">{e(pair['marketing_phrase'])}</span></td>
          <td><span class="phrase-chip player-chip">{e(pair['player_phrase'])}</span></td>
        </tr>
        """)
    return f"""
    <table class="compare-table">
      <thead><tr><th>Concept</th><th>Marketing Says</th><th>Customers Say</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
    """


def collect_evidence(perceptions, recommendations, claims_by_id):
    quotes = []
    seen_quotes = set()
    claim_ids_used = []
    seen_claims = set()

    for p in perceptions:
        for rid, quote in zip(p["supporting_review_ids"], p["supporting_quotes"]):
            if quote not in seen_quotes:
                seen_quotes.add(quote)
                quotes.append((rid, quote))
        for cid in p["marketing_claim_ids"]:
            if cid not in seen_claims:
                seen_claims.add(cid)
                claim_ids_used.append(cid)

    for rec in recommendations:
        for cid in rec["evidence_claim_ids"]:
            if cid not in seen_claims:
                seen_claims.add(cid)
                claim_ids_used.append(cid)

    return quotes[:MAX_EVIDENCE_QUOTES], claim_ids_used[:MAX_EVIDENCE_CLAIMS]


def render_evidence(perceptions, recommendations, claims_by_id):
    quotes, claim_ids_used = collect_evidence(perceptions, recommendations, claims_by_id)

    quote_cards = "".join(
        f'<div class="card evidence-card"><blockquote>&ldquo;{e(quote)}&rdquo;</blockquote>'
        f'<div class="quote-meta">Review {e(rid)}</div></div>'
        for rid, quote in quotes
    ) or '<p class="empty-note">No customer quotes available.</p>'

    claim_cards = "".join(
        f'<div class="card evidence-card"><div class="claim-id">{e(cid)}</div>'
        f'<blockquote>&ldquo;{e(claims_by_id[cid]["text"])}&rdquo;</blockquote></div>'
        for cid in claim_ids_used if cid in claims_by_id
    ) or '<p class="empty-note">No marketing claims were used as evidence.</p>'

    return f"""
    <h3 class="evidence-subhead">Real Customer Quotes</h3>
    <div class="evidence-grid">{quote_cards}</div>
    <h3 class="evidence-subhead">Marketing Claims Used In This Analysis</h3>
    <div class="evidence-grid">{claim_cards}</div>
    """


CSS = """
:root {
  --ink: #1a1d23;
  --muted: #5a6270;
  --bg: #ffffff;
  --panel: #f6f7f9;
  --border: #e1e4e9;
  --accent: #2f5fdb;
  --accent-soft: #eaf0ff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--ink);
  font-size: 17px;
  line-height: 1.55;
}
.page { max-width: 1320px; margin: 0 auto; padding: 40px 48px 100px; }
header.hero {
  text-align: center;
  padding: 36px 24px 40px;
  border-bottom: 4px solid var(--accent);
  margin-bottom: 56px;
}
header.hero h1 {
  font-size: 40px;
  margin: 0 0 6px;
  letter-spacing: -0.01em;
}
.hero-sub-title { color: var(--accent); font-size: 28px; letter-spacing: 0.04em; }
header.hero .subtitle { font-size: 19px; color: var(--muted); margin: 0 0 32px; }
.summary-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }
.stat-card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
  padding: 26px 18px; text-align: center;
}
.stat-number { font-size: 44px; font-weight: 700; color: var(--accent); line-height: 1; margin-bottom: 8px; }
.stat-label { font-size: 16px; color: var(--muted); }
section.block { margin-bottom: 64px; }
section.block h2 {
  font-size: 28px; margin: 0 0 8px; padding-bottom: 10px; border-bottom: 3px solid var(--border);
}
.section-hint { color: var(--muted); font-size: 16px; margin: 0 0 24px; }
.rec-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); gap: 24px; }
.card { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 26px; }
.rec-top { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.priority-pill {
  background: var(--ink); color: #fff; font-weight: 700; font-size: 14px;
  padding: 4px 12px; border-radius: 999px;
}
.badge { display: inline-block; padding: 4px 14px; border-radius: 999px; font-size: 14px; font-weight: 700; }
.rec-card h3 { font-size: 22px; margin: 0 0 16px; }
.rec-row { margin-bottom: 14px; }
.rec-label { display: block; font-size: 13px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 3px; }
.rec-row p, .align-desc { margin: 0; font-size: 16px; }
.rec-action-box { background: var(--accent-soft); border-radius: 10px; padding: 14px 16px; margin: 16px 0; }
.rec-action-text { font-weight: 600; margin: 4px 0 0; }
.copy-direction { border-left: 4px solid var(--accent); padding: 4px 0 4px 14px; margin-bottom: 14px; font-style: italic; color: var(--ink); }
.copy-direction em { font-style: normal; color: var(--muted); font-weight: 400; }
.evidence-count { color: var(--muted); font-size: 14px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 22px; }
.card-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.card-header h3 { font-size: 20px; margin: 0; }
.mention-count { font-size: 15px; font-weight: 600; color: var(--accent); margin-bottom: 8px; }
.card-note { color: var(--muted); font-size: 16px; margin: 0; }
.mini-quote { margin: 12px 0 0; padding: 10px 14px; background: var(--bg); border-left: 4px solid var(--accent); font-style: italic; font-size: 15px; border-radius: 4px; }
.align-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }
.align-column { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 22px; }
.align-column h3 { margin: 0 0 6px; font-size: 19px; }
.align-desc { color: var(--muted); font-size: 14px; margin-bottom: 14px; }
.align-list { list-style: none; margin: 0; padding: 0; }
.align-list li { padding: 10px 0; border-top: 1px solid var(--border); font-size: 15px; }
.align-list li:first-child { border-top: none; }
.align-meta { display: block; color: var(--muted); font-size: 13px; margin-top: 3px; }
.compare-table { width: 100%; border-collapse: collapse; font-size: 16px; }
.compare-table th, .compare-table td { text-align: left; padding: 12px 14px; border-bottom: 1px solid var(--border); }
.compare-table th { color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em; }
.concept-cell { font-weight: 600; }
.phrase-chip { display: inline-block; padding: 3px 12px; border-radius: 999px; font-weight: 600; font-size: 15px; }
.marketing-chip { background: var(--accent-soft); color: var(--accent); }
.player-chip { background: #eafaf1; color: #1e7e4a; }
.evidence-subhead { font-size: 19px; margin: 28px 0 14px; }
.evidence-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; margin-bottom: 12px; }
.evidence-card blockquote { margin: 0 0 10px; font-size: 15px; }
.claim-id { display: inline-block; font-size: 13px; font-weight: 700; color: var(--accent); background: var(--accent-soft); padding: 2px 10px; border-radius: 6px; margin-bottom: 8px; }
.quote-meta { color: var(--muted); font-size: 13px; }
.empty-note { color: var(--muted); font-style: italic; }
footer.page-footer { text-align: center; color: var(--muted); font-size: 15px; margin-top: 80px; padding-top: 24px; border-top: 1px solid var(--border); }
@media (max-width: 900px) {
  .summary-grid, .align-grid { grid-template-columns: 1fr; }
  body { font-size: 16px; }
  header.hero h1 { font-size: 30px; }
}
"""


def build_dashboard(reviews_doc, marketing_doc, vocab_doc, insights_doc) -> str:
    game_name = insights_doc.get("game_name") or reviews_doc.get("game_name", "")
    claims_by_id = {c["claim_id"]: c for c in marketing_doc.get("claims", [])}
    perceptions = insights_doc.get("customer_perceptions", [])
    recommendations = insights_doc.get("top_recommendations", [])

    hero_html = render_hero(
        game_name, reviews_doc.get("review_count", len(reviews_doc.get("reviews", []))),
        len(marketing_doc.get("claims", [])), len(perceptions))
    recs_html = render_recommendations(recommendations)
    perceptions_html = render_perceptions(perceptions)
    alignment_html = render_alignment(perceptions)
    language_html = render_language(vocab_doc.get("same_idea_different_words", []))
    evidence_html = render_evidence(perceptions, recommendations, claims_by_id)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{e(game_name)} - Brand Perception Gap</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
</head>
<body>
<div class="page">
  {hero_html}

  <section class="block">
    <h2>What Marketing Should Do Next</h2>
    <p class="section-hint">Top prioritized recommendations, based on real customer evidence.</p>
    {recs_html}
  </section>

  <section class="block">
    <h2>How Customers See The Brand</h2>
    <p class="section-hint">Human-readable customer perceptions, not machine-generated topic labels.</p>
    {perceptions_html}
  </section>

  <section class="block">
    <h2>Brand Alignment</h2>
    <p class="section-hint">Where marketing matches, underplays, or overemphasizes what customers actually value.</p>
    {alignment_html}
  </section>

  <section class="block">
    <h2>Speak Like Your Customers</h2>
    <p class="section-hint">Same idea, different words -- marketing's language vs. customers' own words.</p>
    {language_html}
  </section>

  <section class="block">
    <h2>Evidence</h2>
    {evidence_html}
  </section>

  <footer class="page-footer">
    Generated from {reviews_doc.get('review_count', len(reviews_doc.get('reviews', []))):,} real customer reviews
    and {len(marketing_doc.get('claims', [])):,} real marketing claims. This file is self-contained and works fully offline.
  </footer>
</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                         help=f"Directory containing the pipeline JSON files (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                         help=f"Path to write marketing_dashboard.html (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    reviews_doc = load_json(args.data_dir / "reviews.json")
    marketing_doc = load_json(args.data_dir / "marketing.json")
    vocab_doc = load_json(args.data_dir / "vocabulary.json")
    insights_doc = load_json(args.data_dir / "marketing_insights.json")

    html_doc = build_dashboard(reviews_doc, marketing_doc, vocab_doc, insights_doc)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_doc)

    print(f"Marketing dashboard written to {args.output} ({len(html_doc):,} bytes)")


if __name__ == "__main__":
    main()
