#!/usr/bin/env python3
"""Compare player themes against marketing claims to find gaps.

Reusable, stdlib-only script. Reads the outputs of the theme-analysis and
marketing-collection stages (data/review_themes.json, data/marketing.json)
and cross-references their keywords to find two kinds of gaps:

1. Hidden strengths: recurring, positively-sentiment player themes whose
   keywords appear in few or none of the marketing claims -- things
   players value that the store page barely mentions.
2. Marketing disconnects: marketing claims whose keywords appear in none
   of the identified player themes -- things marketing emphasizes that
   don't show up as a major, recurring topic in what players actually
   discuss.

Matching is done by stemming both the theme keywords and the marketing
claim text with the same lightweight stemmer used in find_themes.py, and
checking for shared stems (a small set of overly generic/collision-prone
stems is excluded from counting as a "real" match -- see
WEAK_MATCH_STEMS). This is a heuristic keyword-overlap method, not a
semantic one, so results are reported with the evidence (which claims /
themes actually matched) rather than as unqualified claims of absence.

Gaps are written to data/gaps.json as an object:
    {
      "hidden_strengths": [
        {
          "theme_id": "T005",
          "theme": str,
          "sentiment": "positive",
          "mention_count": int,
          "marketing_claim_ids": [str, ...],   # claims that DO overlap (may be empty)
          "example_review_ids": [str, ...],     # copied from review_themes.json
          "description": str
        },
        ...
      ],
      "marketing_disconnects": [
        {
          "claim_id": "M022",
          "claim_text": str,
          "matched_theme_ids": [str, ...],      # themes that DO overlap (empty here by definition)
          "player_mention_count": int,
          "description": str
        },
        ...
      ]
    }

Usage:
    python3 scripts/find_gaps.py
    python3 scripts/find_gaps.py --themes data/review_themes.json --marketing data/marketing.json --output data/gaps.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import find_themes as ft  # reuse the same tokenizer/stemmer/stopwords as WS4

DEFAULT_THEMES = Path(__file__).resolve().parent.parent / "data" / "review_themes.json"
DEFAULT_MARKETING = Path(__file__).resolve().parent.parent / "data" / "marketing.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "gaps.json"

# Stems that are too generic, or prone to false collisions after naive
# stemming (e.g. "goods" -> "good", colliding with the evaluative filler
# word "good"), to count as a genuine topical match between a theme and a
# marketing claim. Found by inspecting real overlaps during development.
WEAK_MATCH_STEMS = {"happen", "already", "since", "system", "good", "bad", "one", "won"}

# A theme must have this few (or fewer) matching marketing claims, and be
# positively received, to count as a "hidden strength".
HIDDEN_STRENGTH_MAX_MATCHES = 2


def meaningful_stems(text: str, stopwords: set) -> set:
    return {ft.stem(w) for w in ft.tokenize(text, stopwords)} - WEAK_MATCH_STEMS


def find_gaps(themes_doc: dict, marketing_doc: dict) -> dict:
    themes = themes_doc.get("themes", [])
    claims = marketing_doc.get("claims", [])
    game_name = marketing_doc.get("game_name", "") or themes_doc.get("game_name", "")

    if not themes:
        raise ValueError("No themes found in review_themes.json; cannot compare against marketing.")
    if not claims:
        raise ValueError("No claims found in marketing.json; cannot compare against player themes.")

    stopwords = ft.build_stopwords(game_name)

    theme_stems = {
        t["theme_id"]: {ft.stem(kw.lower()) for kw in t["keywords"]} - WEAK_MATCH_STEMS
        for t in themes
    }
    claim_stems = {c["claim_id"]: meaningful_stems(c["text"], stopwords) for c in claims}

    theme_to_claims = {
        tid: sorted(cid for cid, cs in claim_stems.items() if tstems & cs)
        for tid, tstems in theme_stems.items()
    }
    claim_to_themes = {
        cid: sorted(tid for tid, tstems in theme_stems.items() if tstems & cs)
        for cid, cs in claim_stems.items()
    }

    themes_by_id = {t["theme_id"]: t for t in themes}
    claims_by_id = {c["claim_id"]: c for c in claims}

    # -- Hidden strengths --------------------------------------------------
    candidates = []
    for t in themes:
        if t["sentiment"] != "positive":
            continue
        matched_claim_ids = theme_to_claims[t["theme_id"]]
        if len(matched_claim_ids) > HIDDEN_STRENGTH_MAX_MATCHES:
            continue
        candidates.append((t, matched_claim_ids))

    candidates.sort(key=lambda tc: (len(tc[1]), -tc[0]["mention_count"]))

    hidden_strengths = []
    for t, matched_claim_ids in candidates:
        if not matched_claim_ids:
            description = (
                f"No marketing claim shares matching keywords with this theme "
                f"(0 of {len(claims)} claims), despite {t['mention_count']} player mentions."
            )
        else:
            description = (
                f"Only {len(matched_claim_ids)} of {len(claims)} marketing claim(s) "
                f"({', '.join(matched_claim_ids)}) share matching keywords with this theme, "
                f"despite {t['mention_count']} player mentions."
            )
        hidden_strengths.append({
            "theme_id": t["theme_id"],
            "theme": t["theme"],
            "sentiment": t["sentiment"],
            "mention_count": t["mention_count"],
            "marketing_claim_ids": matched_claim_ids,
            "example_review_ids": list(t["example_review_ids"]),
            "description": description,
        })

    # -- Marketing disconnects ----------------------------------------------
    disconnect_candidates = []
    for c in claims:
        matched_theme_ids = claim_to_themes[c["claim_id"]]
        if matched_theme_ids:
            continue  # players do discuss this in at least one major theme
        if not claim_stems[c["claim_id"]]:
            continue  # no meaningful content to compare (e.g. a lead-in fragment)
        disconnect_candidates.append(c)

    disconnect_candidates.sort(
        key=lambda c: (-len(claim_stems[c["claim_id"]]), c["claim_id"])
    )

    marketing_disconnects = []
    for c in disconnect_candidates:
        description = (
            f"No player theme shares matching keywords with this claim "
            f"(0 of {len(themes)} major themes identified from the collected reviews)."
        )
        marketing_disconnects.append({
            "claim_id": c["claim_id"],
            "claim_text": c["text"],
            "matched_theme_ids": [],
            "player_mention_count": 0,
            "description": description,
        })

    return {
        "hidden_strengths": hidden_strengths,
        "marketing_disconnects": marketing_disconnects,
    }, themes_by_id, claims_by_id


def verify_gaps(result: dict, themes_by_id: dict, claims_by_id: dict):
    """Internal integrity check: every theme_id, claim_id, and
    example_review_id referenced must trace back to the source files."""
    for hs in result["hidden_strengths"]:
        assert hs["theme_id"] in themes_by_id, f"Unknown theme_id: {hs['theme_id']}"
        source_theme = themes_by_id[hs["theme_id"]]
        for rid in hs["example_review_ids"]:
            assert rid in source_theme["example_review_ids"], (
                f"{hs['theme_id']}: review_id {rid!r} not in that theme's example_review_ids"
            )
        for cid in hs["marketing_claim_ids"]:
            assert cid in claims_by_id, f"{hs['theme_id']}: unknown marketing claim_id {cid!r}"

    for md in result["marketing_disconnects"]:
        assert md["claim_id"] in claims_by_id, f"Unknown claim_id: {md['claim_id']}"
        for tid in md["matched_theme_ids"]:
            assert tid in themes_by_id, f"{md['claim_id']}: unknown theme_id {tid!r}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--themes", type=Path, default=DEFAULT_THEMES,
                         help=f"Path to review_themes.json (default: {DEFAULT_THEMES})")
    parser.add_argument("--marketing", type=Path, default=DEFAULT_MARKETING,
                         help=f"Path to marketing.json (default: {DEFAULT_MARKETING})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                         help=f"Path to write gaps.json (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    for path, label in ((args.themes, "themes"), (args.marketing, "marketing")):
        if not path.is_file():
            print(f"Error: {label} file not found: {path}", file=sys.stderr)
            sys.exit(1)

    with open(args.themes, "r", encoding="utf-8") as f:
        themes_doc = json.load(f)
    with open(args.marketing, "r", encoding="utf-8") as f:
        marketing_doc = json.load(f)

    try:
        result, themes_by_id, claims_by_id = find_gaps(themes_doc, marketing_doc)
        verify_gaps(result, themes_by_id, claims_by_id)
    except (ValueError, AssertionError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Hidden strengths found: {len(result['hidden_strengths'])}")
    print(f"Marketing disconnects found: {len(result['marketing_disconnects'])}")
    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
