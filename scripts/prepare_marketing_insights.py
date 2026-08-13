#!/usr/bin/env python3
"""Turn the raw WS4-WS6 analytical outputs into marketer-readable insights.

Reusable script. Reads (read-only, never modifies):
    reviews.json, marketing.json, review_themes.json, gaps.json, vocabulary.json

and uses an LLM to translate machine-generated cluster labels like
"Friends & Solo & Wipe" into a senior-brand-strategist-style read of how
customers actually perceive the game versus how it's currently marketed,
plus a prioritized set of marketing recommendations.

The API key is read ONLY from the OPENAI_API_KEY environment variable.
It is never hardcoded, never logged, and never written to any output
file. If the key is missing, this script prints a clear setup error and
exits non-zero -- it never substitutes a mock/fabricated response.

Evidence discipline: the LLM is only ever shown IDs/quotes that already
exist in the curated WS4-WS6 outputs (never the raw 1000-review corpus),
and after the LLM responds, every supporting_review_id, supporting_quote,
and marketing_claim_id it returns is independently re-validated against
the real reviews.json/marketing.json. Anything that doesn't check out is
stripped; an insight or recommendation left with zero valid evidence is
dropped entirely rather than shown ungrounded.

Usage:
    OPENAI_API_KEY=sk-... python3 scripts/prepare_marketing_insights.py --data-dir data/cache/252490
"""

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_MODEL = "gpt-4o-mini"
MAX_VOCAB_TERMS_IN_PROMPT = 15

ALLOWED_ALIGNMENT_TYPES = {"ALIGNED", "UNDERREPRESENTED", "OVEREMPHASIZED"}
ALLOWED_ACTIONS = {"ADD", "KEEP", "REDUCE", "REFRAME"}

SYSTEM_PROMPT = """You are a senior brand strategist and marketing analyst.

You are given, for one video game:
- a list of major recurring PLAYER THEMES, each with a machine-generated
  raw label (e.g. "Friends & Solo & Wipe"), sentiment, how many player
  reviews mention it, some keywords, and a handful of REAL verbatim
  review quotes with their real review IDs
- the game's official MARKETING CLAIMS, each with a real claim ID and
  its exact text
- pre-computed GAPS: player themes marketing barely mentions ("hidden
  strengths"), and marketing claims players rarely discuss ("marketing
  disconnects")
- pre-computed VOCABULARY differences between how players talk and how
  marketing talks, including verified same-idea-different-words pairs

Your job: translate this into what a marketing executive actually needs.

STRICT RULES:
1. Never invent a review ID, a quote, or a marketing claim ID. Only use
   IDs and quotes that appear VERBATIM in the data given to you below.
2. Every quote you cite must be copied exactly, character for character,
   from the data given to you. Do not paraphrase or shorten a quote.
3. Turn each raw machine theme label (like "Friends & Solo & Wipe") into
   a natural, human, marketing-readable sentence describing what the
   customer perception actually is. NEVER output the raw "&"-joined
   label as the customer_perception text.
4. alignment_type must be exactly one of: ALIGNED, UNDERREPRESENTED,
   OVEREMPHASIZED.
5. In top_recommendations, recommended_action must start with one of:
   ADD, KEEP, REDUCE, REFRAME -- followed by a specific instruction.
   Never write vague advice like "improve messaging" or "focus on
   customers."
6. suggested_copy_direction is a SUGGESTED MESSAGING DIRECTION, not
   approved final ad copy -- keep it short (one sentence) and clearly
   a direction, not a finished slogan presented as final.
7. Output ONLY a single JSON object matching the schema you are given.
   No prose outside the JSON.
"""

USER_PROMPT_TEMPLATE = """GAME: {game_name}

PLAYER THEMES (produce exactly one customer_perceptions entry per theme_id, using "source_theme_id" to reference it):
{themes_json}

MARKETING CLAIMS (the game's real store-page claims):
{claims_json}

GAPS ALREADY COMPUTED (hidden strengths = player themes marketing barely mentions; marketing disconnects = claims players rarely discuss):
{gaps_json}

VOCABULARY DIFFERENCES ALREADY COMPUTED:
{vocab_json}

Respond with a single JSON object with exactly this shape:
{{
  "customer_perceptions": [
    {{
      "source_theme_id": "T001",
      "customer_perception": "one natural sentence describing what customers actually experience/value",
      "customer_description": "1-2 sentences of further explanation",
      "current_marketing_position": "1-2 sentences on what the marketing claims currently say about this, or that it isn't addressed",
      "alignment_type": "ALIGNED" | "UNDERREPRESENTED" | "OVEREMPHASIZED",
      "mention_count": <integer, copy the theme's real mention_count>,
      "supporting_review_ids": ["<real review_id from the theme's quotes>", ...],
      "supporting_quotes": ["<the exact matching quote for each review_id above, same order>", ...],
      "marketing_claim_ids": ["<real claim_id if relevant, else empty list>"],
      "recommended_action": "one sentence telling the marketer exactly what to DO",
      "suggested_copy_direction": "one short sentence, a suggested messaging direction, not final copy"
    }},
    ...
  ],
  "top_recommendations": [
    {{
      "priority": 1,
      "title": "short specific title",
      "what_customers_are_saying": "1-2 sentences",
      "what_marketing_says_today": "1-2 sentences",
      "gap": "1-2 sentences describing the specific gap",
      "recommended_action": "ADD/KEEP/REDUCE/REFRAME <specific instruction>",
      "suggested_copy_direction": "one short sentence, a suggested messaging direction",
      "evidence_review_ids": ["<real review_id>", ...],
      "evidence_claim_ids": ["<real claim_id>", ...]
    }},
    ...
  ]
}}

Produce 3 to 5 top_recommendations, ordered by priority (1 = most important).
"""


class InsightsError(Exception):
    pass


def load_json(path: Path):
    if not path.is_file():
        raise InsightsError(f"Required data file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_prompt(reviews_doc, marketing_doc, themes_doc, gaps_doc, vocab_doc):
    game_name = reviews_doc.get("game_name") or marketing_doc.get("game_name") or themes_doc.get("game_name", "")

    themes_for_prompt = [
        {
            "theme_id": t["theme_id"],
            "raw_label": t["theme"],
            "sentiment": t["sentiment"],
            "mention_count": t["mention_count"],
            "keywords": t["keywords"],
            "quotes_with_review_ids": [
                {"review_id": rid, "quote": quote}
                for rid, quote in zip(t["example_review_ids"], t["quotes"])
            ],
        }
        for t in themes_doc.get("themes", [])
    ]

    claims_for_prompt = [{"claim_id": c["claim_id"], "text": c["text"]} for c in marketing_doc.get("claims", [])]

    vocab_for_prompt = {
        "same_idea_different_words": vocab_doc.get("same_idea_different_words", []),
        "top_player_only_terms": vocab_doc.get("review_only_terms", [])[:MAX_VOCAB_TERMS_IN_PROMPT],
        "top_marketing_only_terms": vocab_doc.get("marketing_only_terms", [])[:MAX_VOCAB_TERMS_IN_PROMPT],
    }

    user_prompt = USER_PROMPT_TEMPLATE.format(
        game_name=game_name,
        themes_json=json.dumps(themes_for_prompt, ensure_ascii=False, indent=2),
        claims_json=json.dumps(claims_for_prompt, ensure_ascii=False, indent=2),
        gaps_json=json.dumps(gaps_doc, ensure_ascii=False, indent=2),
        vocab_json=json.dumps(vocab_for_prompt, ensure_ascii=False, indent=2),
    )
    return game_name, user_prompt


def call_llm(user_prompt: str, model: str):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise InsightsError(
            "OPENAI_API_KEY is not set.\n\n"
            "Set it before running this script, e.g. on Windows PowerShell:\n"
            '  $env:OPENAI_API_KEY="YOUR_KEY_HERE"\n'
            "Never put the key in source code or commit it to GitHub."
        )

    try:
        from openai import OpenAI
    except ImportError as e:
        raise InsightsError(
            "The 'openai' package is not installed. Run: "
            "python -m pip install -r requirements-ws8.txt"
        ) from e

    client = OpenAI(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
    except Exception as e:  # openai raises various *Error subclasses
        raise InsightsError(f"The LLM API call failed: {e}") from e

    content = response.choices[0].message.content
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise InsightsError(f"The LLM did not return valid JSON: {e}\nRaw response: {content[:2000]}") from e


def validate_perception(item, reviews_by_id, claims_by_id, themes_by_id):
    """Strip any unsupported evidence; return None if nothing checks out."""
    source_theme_id = item.get("source_theme_id")
    theme = themes_by_id.get(source_theme_id)

    review_ids = item.get("supporting_review_ids", []) or []
    quotes = item.get("supporting_quotes", []) or []

    valid_review_ids, valid_quotes = [], []
    for rid, quote in zip(review_ids, quotes):
        review = reviews_by_id.get(rid)
        if review and isinstance(quote, str) and quote in review.get("text", ""):
            valid_review_ids.append(rid)
            valid_quotes.append(quote)

    valid_claim_ids = [cid for cid in (item.get("marketing_claim_ids") or []) if cid in claims_by_id]

    alignment_type = item.get("alignment_type")
    if alignment_type not in ALLOWED_ALIGNMENT_TYPES:
        alignment_type = "UNDERREPRESENTED" if not valid_claim_ids else "ALIGNED"

    if not valid_review_ids and not valid_claim_ids:
        return None  # zero real evidence -- do not display

    return {
        "source_theme_id": source_theme_id,
        "customer_perception": item.get("customer_perception", "").strip(),
        "customer_description": item.get("customer_description", "").strip(),
        "current_marketing_position": item.get("current_marketing_position", "").strip(),
        "alignment_type": alignment_type,
        # mention_count is always taken from the real theme data, never trusted from the LLM.
        "mention_count": theme["mention_count"] if theme else item.get("mention_count", 0),
        "supporting_review_ids": valid_review_ids,
        "supporting_quotes": valid_quotes,
        "marketing_claim_ids": valid_claim_ids,
        "recommended_action": item.get("recommended_action", "").strip(),
        "suggested_copy_direction": item.get("suggested_copy_direction", "").strip(),
    }


def validate_recommendation(item, reviews_by_id, claims_by_id, priority_fallback):
    evidence_review_ids = [rid for rid in (item.get("evidence_review_ids") or []) if rid in reviews_by_id]
    evidence_claim_ids = [cid for cid in (item.get("evidence_claim_ids") or []) if cid in claims_by_id]

    if not evidence_review_ids and not evidence_claim_ids:
        return None

    action = (item.get("recommended_action") or "").strip()
    if not any(action.upper().startswith(a) for a in ALLOWED_ACTIONS):
        action = f"REFRAME: {action}" if action else "REFRAME: revisit this positioning."

    try:
        priority = int(item.get("priority", priority_fallback))
    except (TypeError, ValueError):
        priority = priority_fallback

    return {
        "priority": priority,
        "title": (item.get("title") or "").strip(),
        "what_customers_are_saying": (item.get("what_customers_are_saying") or "").strip(),
        "what_marketing_says_today": (item.get("what_marketing_says_today") or "").strip(),
        "gap": (item.get("gap") or "").strip(),
        "recommended_action": action,
        "suggested_copy_direction": (item.get("suggested_copy_direction") or "").strip(),
        "evidence_review_ids": evidence_review_ids,
        "evidence_claim_ids": evidence_claim_ids,
    }


def prepare_insights(reviews_doc, marketing_doc, themes_doc, gaps_doc, vocab_doc, model: str):
    game_name, user_prompt = build_prompt(reviews_doc, marketing_doc, themes_doc, gaps_doc, vocab_doc)
    llm_output = call_llm(user_prompt, model)

    reviews_by_id = {r["review_id"]: r for r in reviews_doc.get("reviews", [])}
    claims_by_id = {c["claim_id"]: c for c in marketing_doc.get("claims", [])}
    themes_by_id = {t["theme_id"]: t for t in themes_doc.get("themes", [])}

    perceptions = []
    for item in llm_output.get("customer_perceptions", []) or []:
        cleaned = validate_perception(item, reviews_by_id, claims_by_id, themes_by_id)
        if cleaned:
            perceptions.append(cleaned)

    recommendations = []
    for i, item in enumerate((llm_output.get("top_recommendations", []) or []), start=1):
        cleaned = validate_recommendation(item, reviews_by_id, claims_by_id, priority_fallback=i)
        if cleaned:
            recommendations.append(cleaned)
    recommendations.sort(key=lambda r: r["priority"])

    return {
        "game_name": game_name,
        "model": model,
        "customer_perceptions": perceptions,
        "top_recommendations": recommendations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                         help=f"Directory containing reviews.json etc. (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--output", type=Path, default=None,
                         help="Path to write marketing_insights.json (default: <data-dir>/marketing_insights.json)")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
                         help=f"OpenAI model to use (default: {DEFAULT_MODEL}, or $OPENAI_MODEL)")
    args = parser.parse_args()

    output_path = args.output or (args.data_dir / "marketing_insights.json")

    try:
        reviews_doc = load_json(args.data_dir / "reviews.json")
        marketing_doc = load_json(args.data_dir / "marketing.json")
        themes_doc = load_json(args.data_dir / "review_themes.json")
        gaps_doc = load_json(args.data_dir / "gaps.json")
        vocab_doc = load_json(args.data_dir / "vocabulary.json")

        result = prepare_insights(reviews_doc, marketing_doc, themes_doc, gaps_doc, vocab_doc, args.model)
    except InsightsError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not result["customer_perceptions"]:
        print("Error: the LLM response produced zero verifiable customer perceptions. "
              "Nothing was written.", file=sys.stderr)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Generated {len(result['customer_perceptions'])} customer perceptions and "
          f"{len(result['top_recommendations'])} recommendations.")
    print(f"Saved to {output_path}")


if __name__ == "__main__":
    main()
