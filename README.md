# Sudhas-VoiceOfCustomer

Voice of Customer analyzes Steam games by comparing what players say in
reviews against what the game's marketing/store copy claims — surfacing
themes, hidden strengths, marketing disconnects, and vocabulary gaps, and
rendering the results as a dashboard.

## Layout

- `scripts/` — pipeline scripts
- `data/` — JSON data produced by the pipeline
- `output/` — generated dashboard/report output

## Pipeline

```
scripts/collect_reviews.py    -> data/reviews.json
scripts/collect_marketing.py  -> data/marketing.json
scripts/find_themes.py        -> data/review_themes.json
scripts/find_gaps.py          -> data/gaps.json
scripts/vocab_gap.py          -> data/vocabulary.json
scripts/build_dashboard.py    -> output/dashboard.html
```

See [CLAUDE.md](CLAUDE.md) for the required JSON schema of each data file.

## Validating the project

```
python3 scripts/validate_project.py
```

Checks that the expected scripts and data directories/files exist and that
any data files already present contain the required fields.
