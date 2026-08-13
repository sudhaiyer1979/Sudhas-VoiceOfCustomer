# CLAUDE.md

Guidance for Claude Code (and any other agent or contributor) working in this
repository.

## Project

Voice of Customer analyzes Steam games by comparing what players actually say
in reviews against what the game's marketing/store copy claims — surfacing
themes, hidden strengths, marketing disconnects, and vocabulary gaps between
the two, and rendering the results as a dashboard.

## Directory layout

- `scripts/` — pipeline scripts (one script per pipeline stage)
- `data/` — JSON data produced by the pipeline scripts
- `output/` — generated report/dashboard output (e.g. `dashboard.html`)

## Pipeline

Each stage reads the previous stage's output and writes one JSON file to
`data/`, except the last stage, which renders `output/dashboard.html`.

```
scripts/collect_reviews.py    -> data/reviews.json
scripts/collect_marketing.py  -> data/marketing.json
scripts/find_themes.py        -> data/review_themes.json
scripts/find_gaps.py          -> data/gaps.json
scripts/vocab_gap.py          -> data/vocabulary.json
scripts/build_dashboard.py    -> output/dashboard.html
```

Run `python3 scripts/validate_project.py` at any point to check that the
expected scripts and data files exist and that existing data files contain
the required fields below.

## Required data formats

### data/reviews.json

Produced by `scripts/collect_reviews.py`.

Top-level fields:

- `game_name`
- `source_url`
- `app_id`
- `review_count`
- `reviews` — list of review objects

Each entry in `reviews`:

- `review_id`
- `text`
- `recommended`
- `date`

### data/marketing.json

Produced by `scripts/collect_marketing.py`.

Top-level fields:

- `game_name`
- `source_url`
- `claims` — list of claim objects

Each entry in `claims`:

- `claim_id`
- `text`

### data/review_themes.json

Produced by `scripts/find_themes.py`.

Top-level fields:

- `game_name`
- `themes` — list of theme objects

Each entry in `themes`:

- `theme_id`
- `theme`
- `sentiment`
- `mention_count`
- `keywords`
- `example_review_ids`
- `quotes`

### data/gaps.json

Produced by `scripts/find_gaps.py`.

Top-level fields:

- `hidden_strengths` — things players praise that marketing doesn't claim
- `marketing_disconnects` — things marketing claims that players push back on

### data/vocabulary.json

Produced by `scripts/vocab_gap.py`.

Top-level fields:

- `review_only_terms` — vocabulary players use that marketing never does
- `marketing_only_terms` — vocabulary marketing uses that players never do
- `same_idea_different_words` — pairs/groups where both sides mean the same
  thing but say it differently

## Validation

`scripts/validate_project.py` checks:

- that `scripts/`, `data/`, and `output/` exist
- that `CLAUDE.md` and `README.md` exist
- that each pipeline script in `scripts/` exists
- for each `data/*.json` file that exists, that its required top-level and
  per-item fields (as documented above) are present

Missing pipeline scripts or data files that simply haven't been generated
yet are reported, not treated as fatal — the check only fails hard when a
file that *does* exist is missing required fields, or when core project
scaffolding (the three directories, `CLAUDE.md`, `README.md`) is absent.
