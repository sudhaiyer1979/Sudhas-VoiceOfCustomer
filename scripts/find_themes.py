#!/usr/bin/env python3
"""Identify recurring player themes from collected Steam reviews.

Reusable, stdlib-only pipeline:

1. Tokenize every review's text, dropping stopwords -- common English
   function words, generic gaming filler ("game", "play", "player", ...),
   and the game's own name/title words (so it works for any game, not
   just the one it was first run against).
2. Lightly stem tokens (kill/killed/killing -> kill) so morphological
   variants aren't scattered across separate keywords.
3. Score how often each remaining term recurs across *distinct* reviews.
4. Greedily group co-occurring terms (Dice coefficient over the reviews
   each term appears in) into keyword clusters -- each cluster is one
   theme candidate.
5. Keep the largest ~8-12 clusters as the major recurring themes.
6. For each theme, pull real example review IDs and verbatim quotes
   (exact substrings of the review text) -- nothing is invented, and
   sentiment is derived from the reviews' own `recommended` field, not
   guessed from the words.

Themes are written to data/review_themes.json as an object:
    {
      "game_name": str,
      "themes": [
        {
          "theme_id": "T001",
          "theme": str,
          "sentiment": "positive" | "negative" | "mixed",
          "mention_count": int,
          "keywords": [str, ...],
          "example_review_ids": [str, ...],
          "quotes": [str, ...]   # quotes[i] is a verbatim excerpt from
                                  # the review at example_review_ids[i]
        },
        ...
      ]
    }

Usage:
    python3 scripts/find_themes.py
    python3 scripts/find_themes.py --input data/reviews.json --output data/review_themes.json
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "data" / "reviews.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "review_themes.json"

MIN_DOC_FREQUENCY = 5      # a term must recur in at least this many reviews to be a candidate
MAX_CANDIDATE_TERMS = 250  # cap the vocabulary considered for clustering
CO_OCCURRENCE_THRESHOLD = 0.15  # Dice coefficient threshold to merge two terms into one theme
MAX_KEYWORDS_PER_THEME = 6
MIN_THEMES = 8
MAX_THEMES = 12
MAX_EXAMPLES_PER_THEME = 5

BASE_STOPWORDS = set("""
a about above after again against all am an and any are arent as at be because been before being below
between both but by can cant cannot could couldnt did didnt do does doesnt doing dont down during each
few for from further had hadnt has hasnt have havent having he hed hell hes her here heres hers herself
him himself his how hows i id ill im ive if in into is isnt it its itself just lets me more most mustnt
my myself no nor not of off on once only or other ought our ours ourselves out over own same shant she
shed shell shes should shouldnt so some such than that thats the their theirs them themselves then there
theres these they theyd theyll theyre theyve this those through to too under until up very was wasnt we
wed well were weve werent what whats when whens where wheres which while who whos whom why whys with
wont would wouldnt you youd youll youre youve your yours yourself yourselves
""".split())

# Generic gaming filler and evaluative/weak words that recur constantly but
# don't identify a specific theme on their own (per the task: ignore words
# like "game", "play", "player" -- these are that category, generalized).
GENERIC_FILLER = set("""
game games gameplay play played playing player players
really very much so quite pretty super extremely totally basically honestly
literally actually definitely especially still even also just only lot lots
good bad great nice amazing awesome terrible horrible worst best fun boring
awful decent okay ok fine cool sucks sucked suck love loved loves hate hated hates
make makes made want wanted wants know knew find found finds
see saw seen get gets getting got go goes going went come comes coming came
say says said think thought feel feels felt look looks looking looked
try tries trying tried
time times thing things way ways someone everyone everything something anything
nothing everybody somebody anybody nobody day days year years yes now ever every
first back full many worth called experience recommend
like one will never always around away without another different little enough
real give put run end start started take takes new better hard need wanna word
reason absolute man job trust ass bit
""".split())

_SUFFIXES = ["ing", "ies", "ed", "es", "s"]
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_MAX_QUOTE_LEN = 280


def build_stopwords(game_name: str) -> set:
    return BASE_STOPWORDS | GENERIC_FILLER | set(re.findall(r"[a-z]+", game_name.lower()))


def stem(word: str) -> str:
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[:-3] + "y" if suf == "ies" else word[: -len(suf)]
    return word


def tokenize(text: str, stopwords: set):
    normalized = text.lower().replace("'", "")
    return [w for w in re.findall(r"[a-z]+", normalized) if len(w) >= 3 and w not in stopwords]


def build_term_index(reviews, stopwords: set):
    """Returns (doc_freq, token_docs, surface_forms):
    doc_freq[stem] = number of distinct reviews containing that stem
    token_docs[stem] = set of review indices containing that stem
    surface_forms[stem] = Counter of the original words that stemmed to it
    """
    doc_freq = collections.Counter()
    token_docs = collections.defaultdict(set)
    surface_forms = collections.defaultdict(collections.Counter)

    for i, review in enumerate(reviews):
        raw_tokens = tokenize(review.get("text", ""), stopwords)
        stems_in_doc = set()
        for word in raw_tokens:
            s = stem(word)
            stems_in_doc.add(s)
            surface_forms[s][word] += 1
        for s in stems_in_doc:
            token_docs[s].add(i)
        doc_freq.update(stems_in_doc)

    return doc_freq, token_docs, surface_forms


def dice_coefficient(a, b, doc_freq, token_docs):
    co = len(token_docs[a] & token_docs[b])
    if co == 0:
        return 0.0
    return 2 * co / (doc_freq[a] + doc_freq[b])


def cluster_terms(candidate_terms, doc_freq, token_docs):
    """Greedy seed-based clustering: process terms from most to least
    frequent; each unclaimed term starts a new cluster and absorbs the
    unclaimed terms most strongly tied to it (by Dice coefficient) until
    the cluster hits MAX_KEYWORDS_PER_THEME or runs out of matches."""
    unclaimed = set(candidate_terms)
    clusters = []
    for seed in candidate_terms:
        if seed not in unclaimed:
            continue
        unclaimed.discard(seed)
        members = [seed]
        ranked = sorted(unclaimed, key=lambda t: dice_coefficient(seed, t, doc_freq, token_docs),
                         reverse=True)
        for term in ranked:
            if len(members) >= MAX_KEYWORDS_PER_THEME:
                break
            if dice_coefficient(seed, term, doc_freq, token_docs) >= CO_OCCURRENCE_THRESHOLD:
                members.append(term)
                unclaimed.discard(term)
        clusters.append(members)
    return clusters


def label_theme(members, surface_forms):
    top = members[: min(3, len(members))]
    words = [surface_forms[m].most_common(1)[0][0] for m in top]
    return " & ".join(w.title() for w in words)


def classify_sentiment(review_indices, reviews):
    positive = sum(1 for i in review_indices if reviews[i].get("recommended") is True)
    negative = sum(1 for i in review_indices if reviews[i].get("recommended") is False)
    total = positive + negative
    if total == 0:
        return "mixed"
    ratio = positive / total
    if ratio >= 0.6:
        return "positive"
    if ratio <= 0.4:
        return "negative"
    return "mixed"


def extract_quote(text: str, keyword_surface_forms):
    """Return a verbatim excerpt of `text` -- an exact substring -- that
    mentions one of the theme's real keywords, so every quote is
    traceable back to the source review. Never rewrites the text."""
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in keyword_surface_forms) + r")\w*",
        re.IGNORECASE,
    )
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    candidates = sentences if sentences else [text.strip()]

    chosen = None
    for sentence in candidates:
        if pattern.search(sentence):
            chosen = sentence
            break
    if chosen is None:
        chosen = candidates[0]

    if len(chosen) <= _MAX_QUOTE_LEN:
        return chosen
    truncated = chosen[:_MAX_QUOTE_LEN]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated


def build_themes(reviews, doc_freq, token_docs, surface_forms, clusters):
    scored = []
    for members in clusters:
        review_indices = set()
        for m in members:
            review_indices |= token_docs[m]
        scored.append((len(review_indices), members, review_indices))
    scored.sort(key=lambda x: -x[0])

    selected = [c for c in scored if len(c[1]) >= 2][:MAX_THEMES]
    if len(selected) < MIN_THEMES:
        selected = scored[:MAX_THEMES]

    themes = []
    for idx, (mention_count, members, review_indices) in enumerate(selected, start=1):
        keyword_surface_forms = [surface_forms[m].most_common(1)[0][0] for m in members]

        ranked_reviews = sorted(
            review_indices,
            key=lambda i: (
                -sum(1 for m in members if i in token_docs[m]),
                -len(reviews[i].get("text", "")),
            ),
        )[:MAX_EXAMPLES_PER_THEME]

        example_review_ids = [reviews[i]["review_id"] for i in ranked_reviews]
        quotes = [extract_quote(reviews[i]["text"], keyword_surface_forms) for i in ranked_reviews]

        themes.append({
            "theme_id": f"T{idx:03d}",
            "theme": label_theme(members, surface_forms),
            "sentiment": classify_sentiment(review_indices, reviews),
            "mention_count": mention_count,
            "keywords": keyword_surface_forms,
            "example_review_ids": example_review_ids,
            "quotes": quotes,
        })

    return themes


def verify_themes(themes, reviews_by_id):
    """Internal integrity check: every example_review_id and quote must
    trace back to a real review. Raises AssertionError otherwise."""
    for theme in themes:
        assert len(theme["example_review_ids"]) == len(theme["quotes"]), (
            f"{theme['theme_id']}: example_review_ids/quotes length mismatch"
        )
        for review_id, quote in zip(theme["example_review_ids"], theme["quotes"]):
            assert review_id in reviews_by_id, (
                f"{theme['theme_id']}: review_id {review_id!r} not found in reviews.json"
            )
            source_text = reviews_by_id[review_id].get("text", "")
            assert quote in source_text, (
                f"{theme['theme_id']}: quote for review {review_id!r} is not a substring "
                f"of that review's text -- quote={quote!r}"
            )


def find_themes(data: dict):
    reviews = data.get("reviews", [])
    game_name = data.get("game_name", "")

    if not reviews:
        raise ValueError("No reviews found in input data; cannot identify themes.")

    stopwords = build_stopwords(game_name)
    doc_freq, token_docs, surface_forms = build_term_index(reviews, stopwords)

    candidate_terms = [t for t, c in doc_freq.items() if c >= MIN_DOC_FREQUENCY]
    candidate_terms.sort(key=lambda t: -doc_freq[t])
    candidate_terms = candidate_terms[:MAX_CANDIDATE_TERMS]

    clusters = cluster_terms(candidate_terms, doc_freq, token_docs)
    themes = build_themes(reviews, doc_freq, token_docs, surface_forms, clusters)

    reviews_by_id = {r["review_id"]: r for r in reviews}
    verify_themes(themes, reviews_by_id)

    return {"game_name": game_name, "themes": themes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                         help=f"Path to reviews.json (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                         help=f"Path to write review_themes.json (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    try:
        result = find_themes(data)
    except (ValueError, AssertionError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Identified {len(result['themes'])} themes from {len(data['reviews'])} reviews.")
    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
