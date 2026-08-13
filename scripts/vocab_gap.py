#!/usr/bin/env python3
"""Find vocabulary gaps between player reviews and marketing copy.

Reusable, stdlib-only script. Reads data/reviews.json and
data/marketing.json and compares the words and short phrases (1-3 word
n-grams) each side actually uses, dropping common/stopword filler (e.g.
"game", "play", "player", and the game's own name -- reusing the same
stopword list as find_themes.py) so only meaningful vocabulary is
compared.

Three outputs:

1. review_only_terms: words/phrases players use often that never appear
   in the marketing copy.
2. marketing_only_terms: words/phrases marketing uses often that players
   never use.
3. same_idea_different_words: cases where a small set of generic (not
   game-specific) synonym groups -- e.g. {"weapon", "gun"},
   {"survivor", "survival"} -- show marketing and players both talking
   about the same concept but with different real, verified vocabulary.

Every phrase in the output is copied verbatim (case preserved) from a
real review or a real marketing claim -- nothing is invented or
reworded. An in-script assertion pass verifies every review_id/claim_id
referenced actually exists in the source files before anything is
written.

Vocabulary gaps are written to data/vocabulary.json as an object:
    {
      "review_only_terms": [
        {"term": str, "mention_count": int, "example_review_ids": [str, ...]},
        ...
      ],
      "marketing_only_terms": [
        {"term": str, "mention_count": int, "claim_ids": [str, ...]},
        ...
      ],
      "same_idea_different_words": [
        {
          "concept": str,
          "marketing_phrase": str,
          "player_phrase": str,
          "claim_ids": [str, ...],
          "review_ids": [str, ...]
        },
        ...
      ]
    }

Usage:
    python3 scripts/vocab_gap.py
    python3 scripts/vocab_gap.py --reviews data/reviews.json --marketing data/marketing.json --output data/vocabulary.json
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import find_themes as ft  # reuse the same tokenizer/stemmer/stopwords as WS4/WS5

DEFAULT_REVIEWS = Path(__file__).resolve().parent.parent / "data" / "reviews.json"
DEFAULT_MARKETING = Path(__file__).resolve().parent.parent / "data" / "marketing.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "vocabulary.json"

MAX_NGRAM = 3
MIN_PLAYER_DF_UNIGRAM = 8
MIN_PLAYER_DF_PHRASE = 3
MIN_MARKETING_DF_UNIGRAM = 2
MIN_MARKETING_DF_PHRASE = 2
MAX_EXAMPLE_IDS = 5

# Generic (not game-specific) concept groups: sets of near-synonym words
# commonly seen contrasting a marketing/store-page register against an
# informal player-review register. Both singular and plural forms are
# listed where the naive stemmer in find_themes.py doesn't reliably merge
# them on its own (e.g. "alliance" vs "alliances" stem differently).
# These are just candidate associations -- a pair is only ever reported
# if BOTH a real marketing claim and a real review actually use a term
# from the group.
SYNONYM_GROUPS = [
    ("Weapons & combat gear", {"weapon", "weapons", "gun", "guns", "firearm"}),
    ("Survival", {"survivor", "survivors", "survival", "survive"}),
    ("Player groups & alliances",
     {"alliance", "alliances", "clan", "clans", "team", "teams", "squad", "tribe", "guild"}),
    ("Player-built home/structure",
     {"territory", "territories", "structure", "structures", "dwelling",
      "residence", "residences", "base", "bases"}),
    ("Purchasing", {"purchase", "purchases", "buy"}),
    ("Vehicles & transport", {"vehicle", "vehicles", "car", "cars", "transportation"}),
    ("Resource-gathering & crafting depth",
     {"complex", "automation", "system", "systems", "grind", "tedious", "repetitive", "chore"}),
    ("Toxicity & hostility", {"toxic", "hostile", "abusive"}),
    ("Cooperation", {"cooperate", "together", "teamwork"}),
    ("Exploration", {"explore", "discover", "exploration"}),
    ("Defense", {"defend", "protect", "guard"}),
    ("Difficulty & danger", {"dangerous", "brutal", "harsh", "unforgiving", "punish"}),
    ("Addictiveness", {"addict", "addictive", "compelling"}),
    ("Bugs & technical issues", {"crash", "crashes", "bug", "bugs", "glitch", "glitches"}),
    ("Refunds", {"refund", "refunds", "reimburse"}),
]


def content_runs(text: str, stopwords: set):
    """Split text into runs of consecutive content words (non-stopword,
    len>=3), preserving original casing for display alongside the
    lowercased/apostrophe-stripped form used for stopword/stem checks."""
    orig_words = re.findall(r"[A-Za-z']+", text)
    runs, current = [], []
    for ow in orig_words:
        lw = ow.lower().replace("'", "")
        if len(lw) >= 3 and lw not in stopwords:
            current.append((lw, ow))
        else:
            if current:
                runs.append(current)
                current = []
    if current:
        runs.append(current)
    return runs


def extract_ngrams(text: str, stopwords: set, max_n: int = MAX_NGRAM):
    """Yields (stem_key_tuple, display_phrase) for every 1..max_n gram
    made of consecutive real content words (never spanning a stopword)."""
    for run in content_runs(text, stopwords):
        n_words = len(run)
        for n in range(1, max_n + 1):
            for i in range(n_words - n + 1):
                window = run[i:i + n]
                key = tuple(ft.stem(lw) for lw, _ow in window)
                display = " ".join(ow for _lw, ow in window)
                yield key, display


def build_index(documents, id_field, text_field, stopwords):
    """Returns (doc_freq, surface_forms, doc_ids):
    doc_freq[key] = number of distinct documents containing that n-gram
    surface_forms[key] = Counter of the real displayed phrases seen
    doc_ids[key] = set of document ids (review_id / claim_id) containing it
    """
    doc_freq = collections.Counter()
    surface_forms = collections.defaultdict(collections.Counter)
    doc_ids = collections.defaultdict(set)

    for doc in documents:
        seen_keys = set()
        for key, display in extract_ngrams(doc.get(text_field, ""), stopwords):
            seen_keys.add(key)
            surface_forms[key][display] += 1
        for key in seen_keys:
            doc_ids[key].add(doc[id_field])
        doc_freq.update(seen_keys)

    return doc_freq, surface_forms, doc_ids


def rank_only_terms(doc_freq, other_doc_freq, surface_forms, doc_ids,
                     min_df_unigram, min_df_phrase):
    """Terms recurring at least the threshold in this corpus and never
    appearing in the other corpus, ranked by frequency descending."""
    results = []
    for key, df in doc_freq.items():
        if other_doc_freq.get(key, 0) > 0:
            continue
        threshold = min_df_unigram if len(key) == 1 else min_df_phrase
        if df < threshold:
            continue
        term = surface_forms[key].most_common(1)[0][0]
        results.append((df, term, sorted(doc_ids[key])))
    results.sort(key=lambda x: (-x[0], x[1]))
    return results


def pick_best_in_group(stems, doc_freq, other_doc_freq):
    """Among candidate stems with df>0 in `doc_freq`, prefer the highest
    frequency; break ties by preferring the term that appears LEAST in
    the other corpus (most exclusive to this side), then alphabetically
    for determinism."""
    scored = []
    for s in stems:
        df = doc_freq.get((s,), 0)
        if df == 0:
            continue
        cross_df = other_doc_freq.get((s,), 0)
        scored.append((s, df, cross_df))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[1], x[2], x[0]))
    return scored[0]  # (stem, df, cross_df)


def find_same_idea_pairs(player_doc_freq, player_surface, player_doc_ids,
                          mkt_doc_freq, mkt_surface, mkt_doc_ids):
    pairs = []
    for concept, words in SYNONYM_GROUPS:
        stems = {ft.stem(w) for w in words}
        mkt_best = pick_best_in_group(stems, mkt_doc_freq, player_doc_freq)
        player_best = pick_best_in_group(stems, player_doc_freq, mkt_doc_freq)
        if not mkt_best or not player_best:
            continue
        if mkt_best[0] == player_best[0]:
            continue  # same word used on both sides -- not a language gap

        marketing_phrase = mkt_surface[(mkt_best[0],)].most_common(1)[0][0]
        player_phrase = player_surface[(player_best[0],)].most_common(1)[0][0]
        claim_ids = sorted(mkt_doc_ids[(mkt_best[0],)])
        review_ids = sorted(player_doc_ids[(player_best[0],)])[:MAX_EXAMPLE_IDS]

        pairs.append({
            "concept": concept,
            "marketing_phrase": marketing_phrase,
            "player_phrase": player_phrase,
            "claim_ids": claim_ids,
            "review_ids": review_ids,
        })
    return pairs


def find_vocab_gaps(reviews_doc: dict, marketing_doc: dict):
    reviews = reviews_doc.get("reviews", [])
    claims = marketing_doc.get("claims", [])
    game_name = reviews_doc.get("game_name", "") or marketing_doc.get("game_name", "")

    if not reviews:
        raise ValueError("No reviews found in reviews.json; cannot compare vocabulary.")
    if not claims:
        raise ValueError("No claims found in marketing.json; cannot compare vocabulary.")

    stopwords = ft.build_stopwords(game_name)

    player_doc_freq, player_surface, player_doc_ids = build_index(
        reviews, "review_id", "text", stopwords)
    mkt_doc_freq, mkt_surface, mkt_doc_ids = build_index(
        claims, "claim_id", "text", stopwords)

    player_only = rank_only_terms(
        player_doc_freq, mkt_doc_freq, player_surface, player_doc_ids,
        MIN_PLAYER_DF_UNIGRAM, MIN_PLAYER_DF_PHRASE)
    marketing_only = rank_only_terms(
        mkt_doc_freq, player_doc_freq, mkt_surface, mkt_doc_ids,
        MIN_MARKETING_DF_UNIGRAM, MIN_MARKETING_DF_PHRASE)

    review_only_terms = [
        {"term": term, "mention_count": df, "example_review_ids": ids[:MAX_EXAMPLE_IDS]}
        for df, term, ids in player_only
    ]
    marketing_only_terms = [
        {"term": term, "mention_count": df, "claim_ids": ids}
        for df, term, ids in marketing_only
    ]

    same_idea_different_words = find_same_idea_pairs(
        player_doc_freq, player_surface, player_doc_ids,
        mkt_doc_freq, mkt_surface, mkt_doc_ids)

    result = {
        "review_only_terms": review_only_terms,
        "marketing_only_terms": marketing_only_terms,
        "same_idea_different_words": same_idea_different_words,
    }

    review_ids = {r["review_id"] for r in reviews}
    claim_ids = {c["claim_id"] for c in claims}
    return result, review_ids, claim_ids


def verify_vocab_gaps(result: dict, review_ids: set, claim_ids: set):
    for entry in result["review_only_terms"]:
        for rid in entry["example_review_ids"]:
            assert rid in review_ids, f"review_only_terms {entry['term']!r}: unknown review_id {rid!r}"
    for entry in result["marketing_only_terms"]:
        for cid in entry["claim_ids"]:
            assert cid in claim_ids, f"marketing_only_terms {entry['term']!r}: unknown claim_id {cid!r}"
    for entry in result["same_idea_different_words"]:
        for cid in entry["claim_ids"]:
            assert cid in claim_ids, f"{entry['concept']!r}: unknown claim_id {cid!r}"
        for rid in entry["review_ids"]:
            assert rid in review_ids, f"{entry['concept']!r}: unknown review_id {rid!r}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEWS,
                         help=f"Path to reviews.json (default: {DEFAULT_REVIEWS})")
    parser.add_argument("--marketing", type=Path, default=DEFAULT_MARKETING,
                         help=f"Path to marketing.json (default: {DEFAULT_MARKETING})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                         help=f"Path to write vocabulary.json (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    for path, label in ((args.reviews, "reviews"), (args.marketing, "marketing")):
        if not path.is_file():
            print(f"Error: {label} file not found: {path}", file=sys.stderr)
            sys.exit(1)

    with open(args.reviews, "r", encoding="utf-8") as f:
        reviews_doc = json.load(f)
    with open(args.marketing, "r", encoding="utf-8") as f:
        marketing_doc = json.load(f)

    try:
        result, review_ids, claim_ids = find_vocab_gaps(reviews_doc, marketing_doc)
        verify_vocab_gaps(result, review_ids, claim_ids)
    except (ValueError, AssertionError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Player-only terms: {len(result['review_only_terms'])}")
    print(f"Marketing-only terms: {len(result['marketing_only_terms'])}")
    print(f"Same-idea-different-words pairs: {len(result['same_idea_different_words'])}")
    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
