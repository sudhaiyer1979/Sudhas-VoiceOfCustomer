#!/usr/bin/env python3
"""Build a self-contained HTML dashboard from the pipeline's JSON outputs.

Reusable, stdlib-only script. Reads data/reviews.json, data/marketing.json,
data/review_themes.json, data/gaps.json, and data/vocabulary.json and
renders output/dashboard.html: a single static HTML file with all CSS
inlined and all data baked directly into the markup at build time. There
is no JavaScript, no external stylesheet/font/CDN reference, and no
runtime fetch of the JSON files -- once generated, dashboard.html can be
opened by double-clicking it in any browser, offline, with nothing else
installed.

Every number, quote, and phrase rendered is copied from the source JSON
files; nothing is invented or reworded. All embedded text is HTML-escaped
so real review/claim text can never break the page markup.

Usage:
    python3 scripts/build_dashboard.py
    python3 scripts/build_dashboard.py --data-dir data --output output/dashboard.html
"""

import argparse
import html
import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "output" / "dashboard.html"

MAX_DISCONNECTS_SHOWN = 8
MAX_TERMS_SHOWN = 10
MAX_QUOTES_SHOWN = 10

SENTIMENT_COLORS = {
    "positive": ("#eafaf1", "#1e7e4a", "#1e7e4a"),
    "negative": ("#fdecea", "#a4302a", "#a4302a"),
    "mixed": ("#fff6e5", "#8a5a00", "#8a5a00"),
}


def e(value) -> str:
    """HTML-escape any value for safe embedding in the page."""
    return html.escape(str(value), quote=True)


def sentiment_badge(sentiment: str) -> str:
    bg, fg, border = SENTIMENT_COLORS.get(sentiment, ("#eee", "#333", "#333"))
    return (f'<span class="badge" style="background:{bg};color:{fg};'
            f'border:1px solid {border}">{e(sentiment)}</span>')


def load_json(path: Path):
    if not path.is_file():
        print(f"Error: required data file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def render_summary(game_name, review_count, claim_count, theme_count):
    return f"""
    <section class="summary-grid">
      <div class="stat-card">
        <div class="stat-number">{review_count:,}</div>
        <div class="stat-label">Reviews Analyzed</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">{claim_count:,}</div>
        <div class="stat-label">Marketing Claims Analyzed</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">{theme_count:,}</div>
        <div class="stat-label">Major Player Themes</div>
      </div>
    </section>
    """


def render_hidden_strengths(hidden_strengths, themes_by_id):
    if not hidden_strengths:
        return '<p class="empty-note">No hidden strengths were identified in the data.</p>'
    cards = []
    for hs in hidden_strengths:
        theme = themes_by_id.get(hs["theme_id"])
        quote_html = ""
        if theme and theme.get("quotes"):
            quote_html = f'<blockquote class="mini-quote">&ldquo;{e(theme["quotes"][0])}&rdquo;</blockquote>'
        claims_note = (
            f"Touched on by {len(hs['marketing_claim_ids'])} of the marketing claims "
            f"({e(', '.join(hs['marketing_claim_ids']))})."
            if hs["marketing_claim_ids"]
            else "Not mentioned in the marketing copy at all."
        )
        cards.append(f"""
        <div class="card strength-card">
          <div class="card-header">
            <h3>{e(hs['theme'])}</h3>
            {sentiment_badge(hs['sentiment'])}
          </div>
          <div class="mention-count">{hs['mention_count']:,} player mentions</div>
          <p class="card-note">{e(claims_note)}</p>
          {quote_html}
        </div>
        """)
    return f'<div class="card-grid">{"".join(cards)}</div>'


def render_marketing_disconnects(marketing_disconnects):
    if not marketing_disconnects:
        return '<p class="empty-note">No marketing disconnects were identified in the data.</p>'
    shown = marketing_disconnects[:MAX_DISCONNECTS_SHOWN]
    remaining = len(marketing_disconnects) - len(shown)
    cards = []
    for md in shown:
        cards.append(f"""
        <div class="card disconnect-card">
          <div class="claim-id">{e(md['claim_id'])}</div>
          <blockquote class="mini-quote">&ldquo;{e(md['claim_text'])}&rdquo;</blockquote>
          <p class="card-note">{e(md['description'])}</p>
        </div>
        """)
    footer = (f'<p class="list-footer">+ {remaining} more marketing claims '
              f'not reflected in any major player theme.</p>' if remaining > 0 else "")
    return f'<div class="card-grid">{"".join(cards)}</div>{footer}'


def render_term_list(terms):
    if not terms:
        return '<p class="empty-note">None found.</p>'
    shown = terms[:MAX_TERMS_SHOWN]
    items = []
    for t in shown:
        items.append(
            f'<li><span class="term-word">{e(t["term"])}</span>'
            f'<span class="term-count">{t["mention_count"]}&times;</span></li>'
        )
    remaining = len(terms) - len(shown)
    footer = f'<li class="list-footer-item">+ {remaining} more</li>' if remaining > 0 else ""
    return f'<ul class="term-list">{"".join(items)}{footer}</ul>'


def render_same_idea(pairs):
    if not pairs:
        return '<p class="empty-note">No side-by-side language comparisons were identified.</p>'
    rows = []
    for p in pairs:
        rows.append(f"""
        <tr>
          <td class="concept-cell">{e(p['concept'])}</td>
          <td><span class="phrase-chip marketing-chip">{e(p['marketing_phrase'])}</span></td>
          <td><span class="phrase-chip player-chip">{e(p['player_phrase'])}</span></td>
        </tr>
        """)
    return f"""
    <table class="compare-table">
      <thead>
        <tr><th>Concept</th><th>Marketing Says</th><th>Players Say</th></tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
    """


def render_language_section(vocab):
    same_idea_html = render_same_idea(vocab["same_idea_different_words"])
    player_terms_html = render_term_list(vocab["review_only_terms"])
    marketing_terms_html = render_term_list(vocab["marketing_only_terms"])
    return f"""
    <div class="language-compare">{same_idea_html}</div>
    <div class="term-columns">
      <div class="term-column">
        <h3>Words Players Use That Marketing Never Does</h3>
        {player_terms_html}
      </div>
      <div class="term-column">
        <h3>Words Marketing Uses That Players Never Do</h3>
        {marketing_terms_html}
      </div>
    </div>
    """


def render_themes(themes):
    if not themes:
        return '<p class="empty-note">No themes were identified.</p>'
    cards = []
    for t in sorted(themes, key=lambda x: -x["mention_count"]):
        keywords = " &middot; ".join(e(k) for k in t["keywords"])
        cards.append(f"""
        <div class="card theme-card">
          <div class="card-header">
            <h3>{e(t['theme'])}</h3>
            {sentiment_badge(t['sentiment'])}
          </div>
          <div class="mention-count">{t['mention_count']:,} mentions</div>
          <p class="keywords">{keywords}</p>
        </div>
        """)
    return f'<div class="card-grid">{"".join(cards)}</div>'


def render_quotes(themes, reviews_by_id):
    quotes = []
    for t in sorted(themes, key=lambda x: -x["mention_count"]):
        for rid, quote in zip(t["example_review_ids"], t["quotes"]):
            quotes.append((t, rid, quote))
            break  # one representative quote per theme, most-mentioned themes first
        if len(quotes) >= MAX_QUOTES_SHOWN:
            break

    if not quotes:
        return '<p class="empty-note">No quotes available.</p>'

    cards = []
    for theme, rid, quote in quotes:
        review = reviews_by_id.get(rid)
        meta_bits = [f"Review {e(rid)}"]
        if review:
            rec = review.get("recommended")
            if rec is True:
                meta_bits.append('<span class="rec-yes">Recommended</span>')
            elif rec is False:
                meta_bits.append('<span class="rec-no">Not Recommended</span>')
            if review.get("date"):
                meta_bits.append(e(review["date"]))
        cards.append(f"""
        <div class="card quote-card">
          <blockquote>&ldquo;{e(quote)}&rdquo;</blockquote>
          <div class="quote-meta">{' &middot; '.join(meta_bits)} &middot; theme: {e(theme['theme'])}</div>
        </div>
        """)
    return f'<div class="quote-grid">{"".join(cards)}</div>'


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
  font-size: 20px;
  line-height: 1.5;
}
.page {
  max-width: 1400px;
  margin: 0 auto;
  padding: 48px 56px 96px;
}
header.hero {
  text-align: center;
  padding: 40px 24px 48px;
  border-bottom: 4px solid var(--accent);
  margin-bottom: 48px;
}
header.hero h1 {
  font-size: 52px;
  margin: 0 0 12px;
  letter-spacing: -0.01em;
}
header.hero .subtitle {
  font-size: 24px;
  color: var(--muted);
  margin: 0;
}
section.block {
  margin-bottom: 72px;
}
section.block h2 {
  font-size: 36px;
  margin: 0 0 28px;
  padding-bottom: 12px;
  border-bottom: 3px solid var(--border);
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 32px;
}
.stat-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 40px 24px;
  text-align: center;
}
.stat-number {
  font-size: 64px;
  font-weight: 700;
  color: var(--accent);
  line-height: 1;
  margin-bottom: 12px;
}
.stat-label {
  font-size: 22px;
  color: var(--muted);
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  gap: 28px;
}
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 28px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}
.card-header h3 {
  font-size: 24px;
  margin: 0;
}
.badge {
  display: inline-block;
  padding: 4px 14px;
  border-radius: 999px;
  font-size: 16px;
  font-weight: 600;
  text-transform: capitalize;
  white-space: nowrap;
}
.mention-count {
  font-size: 18px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 10px;
}
.card-note {
  color: var(--muted);
  font-size: 18px;
  margin: 10px 0 0;
}
.mini-quote {
  margin: 14px 0 0;
  padding: 12px 16px;
  background: var(--bg);
  border-left: 4px solid var(--accent);
  font-style: italic;
  font-size: 18px;
  border-radius: 4px;
}
.claim-id {
  display: inline-block;
  font-size: 15px;
  font-weight: 700;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 2px 10px;
  border-radius: 6px;
  margin-bottom: 10px;
}
.list-footer {
  color: var(--muted);
  font-size: 17px;
  margin-top: 20px;
  text-align: center;
}
.language-compare { margin-bottom: 40px; }
.compare-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 19px;
}
.compare-table th, .compare-table td {
  text-align: left;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}
.compare-table th {
  color: var(--muted);
  font-size: 16px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.concept-cell { font-weight: 600; }
.phrase-chip {
  display: inline-block;
  padding: 4px 14px;
  border-radius: 999px;
  font-weight: 600;
}
.marketing-chip { background: var(--accent-soft); color: var(--accent); }
.player-chip { background: #eafaf1; color: #1e7e4a; }
.term-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 40px;
}
.term-column h3 {
  font-size: 22px;
  margin-bottom: 14px;
}
.term-list { list-style: none; margin: 0; padding: 0; }
.term-list li {
  display: flex;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 19px;
}
.term-word { font-weight: 600; }
.term-count { color: var(--muted); }
.list-footer-item { color: var(--muted); font-style: italic; justify-content: center !important; }
.keywords { color: var(--muted); font-size: 17px; margin: 0; }
.quote-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 24px;
}
.quote-card blockquote {
  margin: 0 0 14px;
  font-size: 21px;
  line-height: 1.5;
}
.quote-meta {
  color: var(--muted);
  font-size: 16px;
}
.rec-yes { color: #1e7e4a; font-weight: 600; }
.rec-no { color: #a4302a; font-weight: 600; }
.empty-note { color: var(--muted); font-style: italic; }
footer.page-footer {
  text-align: center;
  color: var(--muted);
  font-size: 16px;
  margin-top: 80px;
  padding-top: 24px;
  border-top: 1px solid var(--border);
}
@media (max-width: 900px) {
  .summary-grid, .term-columns { grid-template-columns: 1fr; }
  body { font-size: 18px; }
  header.hero h1 { font-size: 38px; }
}
"""


def build_dashboard(reviews_doc, marketing_doc, themes_doc, gaps_doc, vocab_doc) -> str:
    game_name = reviews_doc.get("game_name", "")
    reviews = reviews_doc.get("reviews", [])
    claims = marketing_doc.get("claims", [])
    themes = themes_doc.get("themes", [])

    themes_by_id = {t["theme_id"]: t for t in themes}
    reviews_by_id = {r["review_id"]: r for r in reviews}

    summary_html = render_summary(
        game_name, reviews_doc.get("review_count", len(reviews)), len(claims), len(themes))
    strengths_html = render_hidden_strengths(gaps_doc.get("hidden_strengths", []), themes_by_id)
    disconnects_html = render_marketing_disconnects(gaps_doc.get("marketing_disconnects", []))
    language_html = render_language_section(vocab_doc)
    themes_html = render_themes(themes)
    quotes_html = render_quotes(themes, reviews_by_id)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{e(game_name)} - Voice of Customer vs Marketing</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
</head>
<body>
<div class="page">
  <header class="hero">
    <h1>{e(game_name)} &mdash; Voice of Customer vs Marketing</h1>
    <p class="subtitle">What real players say, compared with what the store page says</p>
  </header>

  <section class="block">
    <h2>Summary</h2>
    {summary_html}
  </section>

  <section class="block">
    <h2>What Players Value That Marketing Misses</h2>
    {strengths_html}
  </section>

  <section class="block">
    <h2>What Marketing Says That Players Rarely Mention</h2>
    {disconnects_html}
  </section>

  <section class="block">
    <h2>Marketing Language vs Player Language</h2>
    {language_html}
  </section>

  <section class="block">
    <h2>Major Player Themes</h2>
    {themes_html}
  </section>

  <section class="block">
    <h2>Real Player Quotes</h2>
    {quotes_html}
  </section>

  <footer class="page-footer">
    Generated from {len(reviews):,} real Steam reviews and {len(claims):,} real marketing claims.
    This file is self-contained and works fully offline.
  </footer>
</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR,
                         help=f"Directory containing the pipeline JSON files (default: {DATA_DIR})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                         help=f"Path to write dashboard.html (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    reviews_doc = load_json(args.data_dir / "reviews.json")
    marketing_doc = load_json(args.data_dir / "marketing.json")
    themes_doc = load_json(args.data_dir / "review_themes.json")
    gaps_doc = load_json(args.data_dir / "gaps.json")
    vocab_doc = load_json(args.data_dir / "vocabulary.json")

    html_doc = build_dashboard(reviews_doc, marketing_doc, themes_doc, gaps_doc, vocab_doc)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_doc)

    print(f"Dashboard written to {args.output} ({len(html_doc):,} bytes)")


if __name__ == "__main__":
    main()
