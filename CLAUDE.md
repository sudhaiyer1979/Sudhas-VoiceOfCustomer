# Voice of Customer — Game Review vs. Marketing Comparison Tool

## What this tool does

Given a **website URL** (a store page, review aggregator, or the publisher's
own site) and a **game name**, this tool answers one question: *does the
marketing match what players actually experience?*

It does this by:

1. Collecting **1,000 player reviews** for the game from the given site.
2. Collecting the game's **marketing copy** (store description, key
   feature bullets, trailers' on-screen text, press-kit blurbs — whatever
   promotional text is available from or linked off the given URL).
3. Comparing the two to surface three things:
   - **Praise marketing misses** — things players consistently love that
     the marketing copy never mentions.
   - **Ignored marketing claims** — things marketing emphasizes that
     reviews rarely or never bring up.
   - **The vocabulary gap** — the difference between the words marketing
     uses to describe the game and the words players actually use,
     side by side.

## Inputs

| Input | Description |
|---|---|
| `website_url` | URL to start from (store page, review site, etc.) |
| `game_name` | Name of the game to analyze |

## Pipeline & scripts

Scripts run in this order; each one reads the previous script's output
from `data/` and writes its own output back to `data/`.

### 1. `scripts/collect_reviews.py`
Fetches up to 1,000 player reviews for `game_name` starting from
`website_url`.
- **Produces:** `data/<game_name>_reviews.json`
  — list of review objects (text, rating, date, source URL).

### 2. `scripts/collect_marketing.py`
Fetches the game's marketing copy (store description, feature bullets,
tagline, press-kit text) reachable from `website_url`.
- **Produces:** `data/<game_name>_marketing.json`
  — list of marketing text snippets with their source/section.

### 3. `scripts/analyze_gap.py`
Loads both JSON files above and compares them:
- Extracts frequent themes/phrases from reviews vs. marketing.
- Flags praised-but-unmentioned themes, ignored marketing claims, and
  the vocabulary/word-choice gap between the two corpora.
- **Produces:** `data/<game_name>_analysis.json`
  — structured results for the three comparison categories.

### 4. `scripts/generate_report.py`
Turns `data/<game_name>_analysis.json` into a human-readable summary.
- **Produces:** `data/<game_name>_report.md`
  — final report: what players praise that marketing misses, what
  marketing says that players ignore, and the vocabulary gap between them.

## Folders

- `data/` — all collected reviews, marketing copy, analysis results, and
  final reports, one set of files per game (named `<game_name>_*`).
- `scripts/` — the four pipeline scripts above.
